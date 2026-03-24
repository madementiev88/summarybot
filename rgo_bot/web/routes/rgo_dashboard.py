"""RGO Dashboard API routes.

Provides personalized data for RGO managers:
- /api/rgo/role — determine user role (admin/rgo/denied)
- /api/rgo/tips — morning recommendation + glossary + AI tips
- /api/rgo/tasks — open tasks for RGO's chat
- /api/rgo/tasks/{id}/close — close a task
- /api/rgo/team — team metrics (messages, activity, silent members)
"""
from __future__ import annotations

import datetime

from aiohttp import web
from loguru import logger
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.models import (
    GlossaryOrder,
    Message,
    MonitoredChat,
    Participant,
    ParticipantChat,
    RgoRecommendation,
    Task,
)


def setup_rgo_dashboard_routes(app: web.Application) -> None:
    app.router.add_get("/api/rgo/role", handle_role)
    app.router.add_get("/api/rgo/tips", handle_tips)
    app.router.add_get("/api/rgo/tasks", handle_tasks)
    app.router.add_post("/api/rgo/tasks/{task_id}/close", handle_task_close)
    app.router.add_get("/api/rgo/team", handle_team)


async def handle_role(request: web.Request) -> web.Response:
    """Return user role and chat info."""
    role = request.get("role", "denied")
    chat_id = request.get("rgo_chat_id")
    user = request.get("tg_user", {})

    data = {"role": role, "user_id": user.get("id"), "first_name": user.get("first_name")}

    if role == "rgo" and chat_id:
        from rgo_bot.bot.services.chat_registry import get_chat_title
        data["chat_id"] = chat_id
        data["chat_title"] = get_chat_title(chat_id)

        # Compute activity ranking
        tz = ZoneInfo(settings.timezone)
        week_ago = datetime.datetime.now(tz) - datetime.timedelta(days=7)
        async with async_session() as session:
            # Get message counts per monitored chat for the week
            result = await session.execute(
                select(Message.chat_id, func.count(Message.id))
                .where(Message.timestamp >= week_ago)
                .group_by(Message.chat_id)
            )
            chat_counts = {row[0]: row[1] for row in result.all()}

        # Sort by count descending
        sorted_chats = sorted(chat_counts.items(), key=lambda x: x[1], reverse=True)
        rank = next(
            (i + 1 for i, (cid, _) in enumerate(sorted_chats) if cid == chat_id),
            len(sorted_chats),
        )
        data["rank"] = rank
        data["total_rgo"] = len(sorted_chats)

    return web.json_response(data)


async def handle_tips(request: web.Request) -> web.Response:
    """Return morning recommendation + glossary orders + AI tips."""
    role = request.get("role")
    chat_id = request.get("rgo_chat_id")
    user = request.get("tg_user", {})
    user_id = user.get("id")

    if role != "rgo" or not chat_id:
        return web.json_response({"error": "RGO only"}, status=403)

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    # Get today's recommendation
    rec_text = None
    generated_at = None
    async with async_session() as session:
        result = await session.execute(
            select(RgoRecommendation)
            .where(
                RgoRecommendation.rgo_user_id == user_id,
                RgoRecommendation.rec_date == today,
            )
            .order_by(RgoRecommendation.id.desc())
            .limit(1)
        )
        rec = result.scalar_one_or_none()
        if rec:
            rec_text = rec.recommendation_text
            generated_at = rec.sent_at.isoformat() if rec.sent_at else None

    # Get glossary orders for today targeting this chat
    glossary = []
    async with async_session() as session:
        result = await session.execute(
            select(GlossaryOrder)
            .where(
                GlossaryOrder.target_date == today,
                GlossaryOrder.status == "active",
            )
        )
        for order in result.scalars().all():
            if order.target_rgo_ids is None or chat_id in order.target_rgo_ids:
                glossary.append({
                    "id": order.id,
                    "text": order.order_text,
                    "priority": "urgent" if "🔴" in (order.order_text or "") else "normal",
                })

    # Get focus items (overdue tasks)
    focus = []
    async with async_session() as session:
        result = await session.execute(
            select(Task)
            .where(
                Task.chat_id == chat_id,
                Task.status.in_(["overdue", "open"]),
            )
            .order_by(
                # Overdue first
                Task.status.desc(),
                Task.detected_at.desc(),
            )
            .limit(5)
        )
        for t in result.scalars().all():
            focus.append({
                "text": t.task_text[:150],
                "status": t.status,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            })

    return web.json_response({
        "recommendation": rec_text,
        "generated_at": generated_at,
        "glossary": glossary,
        "focus": focus,
    })


async def handle_tasks(request: web.Request) -> web.Response:
    """Return open/overdue tasks for RGO's chat."""
    role = request.get("role")
    chat_id = request.get("rgo_chat_id")

    if role != "rgo" or not chat_id:
        return web.json_response({"error": "RGO only"}, status=403)

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    tasks_list = []
    stats = {"open": 0, "overdue": 0, "closed_today": 0}

    async with async_session() as session:
        # Open and overdue tasks
        result = await session.execute(
            select(Task)
            .where(
                Task.chat_id == chat_id,
                Task.status.in_(["open", "overdue"]),
            )
            .order_by(Task.status.desc(), Task.detected_at.desc())
            .limit(30)
        )
        for t in result.scalars().all():
            # Get assigner name
            assigner_name = None
            if t.assigner_user_id:
                p_result = await session.execute(
                    select(Participant.full_name)
                    .where(Participant.user_id == t.assigner_user_id)
                )
                row = p_result.first()
                assigner_name = row[0] if row else None

            tasks_list.append({
                "id": t.task_id,
                "text": t.task_text[:200],
                "status": t.status,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "assigner": assigner_name,
                "detected_at": t.detected_at.isoformat() if t.detected_at else None,
            })

            if t.status == "open":
                stats["open"] += 1
            elif t.status == "overdue":
                stats["overdue"] += 1

        # Closed today count
        day_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
        result = await session.execute(
            select(func.count(Task.task_id))
            .where(
                Task.chat_id == chat_id,
                Task.status == "closed",
                Task.closed_at >= day_start,
            )
        )
        stats["closed_today"] = result.scalar_one() or 0

    # Get glossary orders
    glossary = []
    async with async_session() as session:
        result = await session.execute(
            select(GlossaryOrder)
            .where(
                GlossaryOrder.target_date == today,
                GlossaryOrder.status == "active",
            )
        )
        for order in result.scalars().all():
            if order.target_rgo_ids is None or chat_id in order.target_rgo_ids:
                glossary.append({
                    "id": order.id,
                    "text": order.order_text,
                })

    return web.json_response({
        "tasks": tasks_list,
        "glossary": glossary,
        "stats": stats,
    })


async def handle_task_close(request: web.Request) -> web.Response:
    """Close a task."""
    role = request.get("role")
    chat_id = request.get("rgo_chat_id")

    if role != "rgo" or not chat_id:
        return web.json_response({"error": "RGO only"}, status=403)

    task_id = int(request.match_info["task_id"])

    async with async_session() as session:
        result = await session.execute(
            select(Task).where(Task.task_id == task_id, Task.chat_id == chat_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            return web.json_response({"error": "Задача не найдена"}, status=404)

        task.status = "closed"
        task.closed_at = datetime.datetime.now(datetime.UTC)
        await session.commit()

    return web.json_response({"ok": True})


async def handle_team(request: web.Request) -> web.Response:
    """Return team metrics for RGO's chat."""
    role = request.get("role")
    chat_id = request.get("rgo_chat_id")

    if role != "rgo" or not chat_id:
        return web.json_response({"error": "RGO only"}, status=403)

    tz = ZoneInfo(settings.timezone)
    now = datetime.datetime.now(tz)
    today = now.date()
    day_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
    week_ago = now - datetime.timedelta(days=7)

    async with async_session() as session:
        # Today's messages count
        result = await session.execute(
            select(func.count(Message.id))
            .where(Message.chat_id == chat_id, Message.timestamp >= day_start)
        )
        today_messages = result.scalar_one() or 0

        # Today's unique participants
        result = await session.execute(
            select(func.count(func.distinct(Message.user_id)))
            .where(Message.chat_id == chat_id, Message.timestamp >= day_start)
        )
        today_participants = result.scalar_one() or 0

        # Last message time
        result = await session.execute(
            select(func.max(Message.timestamp))
            .where(Message.chat_id == chat_id)
        )
        last_msg_ts = result.scalar_one()
        if last_msg_ts:
            delta = now - last_msg_ts.astimezone(tz)
            minutes = int(delta.total_seconds() / 60)
            if minutes < 60:
                last_message = f"{minutes} мин назад"
            elif minutes < 1440:
                last_message = f"{minutes // 60}ч назад"
            else:
                last_message = f"{minutes // 1440}д назад"
        else:
            last_message = "нет данных"

        # Silent members: active in last 7 days but not today
        result = await session.execute(
            select(Participant.full_name, Participant.user_id)
            .join(ParticipantChat, ParticipantChat.user_id == Participant.user_id)
            .where(
                ParticipantChat.chat_id == chat_id,
                Participant.last_active_at >= week_ago,
            )
        )
        active_last_week = result.all()

        result = await session.execute(
            select(func.distinct(Message.user_id))
            .where(Message.chat_id == chat_id, Message.timestamp >= day_start)
        )
        today_user_ids = {row[0] for row in result.all()}

        silent_members = [
            name for name, uid in active_last_week
            if uid not in today_user_ids and name
        ]

        # Top contributors this week
        result = await session.execute(
            select(Message.user_id, func.count(Message.id).label("cnt"))
            .where(Message.chat_id == chat_id, Message.timestamp >= week_ago)
            .group_by(Message.user_id)
            .order_by(func.count(Message.id).desc())
            .limit(5)
        )
        top_rows = result.all()

        top_week = []
        for uid, cnt in top_rows:
            p_result = await session.execute(
                select(Participant.full_name).where(Participant.user_id == uid)
            )
            row = p_result.first()
            name = row[0] if row else str(uid)
            top_week.append({"name": name, "messages": cnt})

    return web.json_response({
        "today": {
            "messages": today_messages,
            "participants": today_participants,
            "last_message": last_message,
        },
        "silent_members": silent_members[:10],
        "top_week": top_week,
    })
