"""API proxy for existing bot commands.

POST /api/command/{name} — execute a bot command and return result.
Commands that return data immediately get JSON response.
Commands that trigger long operations return {"status": "processing"} and send result via bot.
"""
from __future__ import annotations

import asyncio
import datetime

from aiohttp import web
from loguru import logger
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.models import Message as MessageModel, Participant, Task, AlertKeyword


def setup_command_routes(app: web.Application) -> None:
    app.router.add_post("/api/command/{name}", handle_command)


async def handle_command(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    body = await request.json() if request.content_length else {}
    args = body.get("args", "")
    bot = request.app["bot"]

    logger.info("webapp_command name={} args={}", name, args)

    try:
        result = await _dispatch_command(name, args, bot)
        return web.json_response(result)
    except Exception:
        logger.exception("webapp_command_error name={}", name)
        return web.json_response(
            {"error": "Ошибка выполнения команды"}, status=500
        )


async def _dispatch_command(name: str, args: str, bot) -> dict:
    """Route command to appropriate service function."""
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    if name == "status":
        return await _cmd_status(today, tz)
    elif name == "rgo_list":
        return await _cmd_rgo_list()
    elif name == "tasks":
        return await _cmd_tasks()
    elif name == "tasks_week":
        return await _cmd_tasks_week(today)
    elif name == "mentions":
        return await _cmd_mentions(today, tz)
    elif name == "forwards":
        return await _cmd_forwards(today, tz)
    elif name == "search":
        return await _cmd_search(args)
    elif name == "participants":
        return await _cmd_participants()
    elif name in ("report_now", "report", "week"):
        await _trigger_report(name, args, bot)
        return {"status": "processing", "message": "Отчёт формируется, результат придёт в бот"}
    elif name in ("load", "hours", "activity"):
        await _trigger_chart(name, bot)
        return {"status": "processing", "message": "График формируется, результат придёт в бот"}
    elif name == "ask":
        await _trigger_ask(args, bot)
        return {"status": "processing", "message": "AI обрабатывает вопрос, ответ придёт в бот"}
    elif name == "add_chat":
        return await _cmd_add_chat(args)
    elif name == "remove_chat":
        return await _cmd_remove_chat(args)
    elif name == "add_keyword":
        return await _cmd_add_keyword(args)
    elif name == "set_role":
        return await _cmd_set_role(args)
    else:
        return {"error": f"Неизвестная команда: {name}"}


# ── Immediate commands (inline queries, matching admin_private.py) ──


async def _cmd_status(today, tz) -> dict:
    from rgo_bot.bot.services.chat_registry import get_active_chat_ids

    chat_ids = get_active_chat_ids()
    today_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)

    async with async_session() as session:
        result = await session.execute(select(func.count(MessageModel.id)))
        total_messages = result.scalar_one()

        result = await session.execute(
            select(func.count(MessageModel.id)).where(
                MessageModel.timestamp >= today_start
            )
        )
        today_messages = result.scalar_one()

        result = await session.execute(select(func.count(Participant.user_id)))
        total_participants = result.scalar_one()

    return {
        "status": "ok",
        "html": (
            f"📊 <b>Статус мониторинга</b>\n\n"
            f"Чатов: <b>{len(chat_ids)}</b>\n"
            f"Всего сообщений: <b>{total_messages}</b>\n"
            f"Сегодня: <b>{today_messages}</b>\n"
            f"Участников: <b>{total_participants}</b>\n"
            f"Часовой пояс: {settings.timezone}\n"
            f"Отчёт: {settings.daily_report_time}"
        ),
    }


async def _cmd_rgo_list() -> dict:
    from rgo_bot.bot.services.chat_registry import get_active_chat_ids, get_chat_title

    chat_ids = get_active_chat_ids()
    lines = ["📋 <b>Мониторируемые чаты</b>\n"]
    for i, cid in enumerate(chat_ids, 1):
        title = get_chat_title(cid) or str(cid)
        lines.append(f"{i}. {title}")
    if not chat_ids:
        lines.append("<i>Нет активных чатов</i>")

    return {"status": "ok", "html": "\n".join(lines)}


async def _cmd_tasks() -> dict:
    from rgo_bot.db.crud.tasks import get_open_tasks
    from rgo_bot.bot.services.chat_registry import get_chat_title

    async with async_session() as session:
        tasks = await get_open_tasks(session)

    if not tasks:
        return {"status": "ok", "html": "✅ <b>Открытых поручений нет</b>"}

    lines = [f"📋 <b>Открытые поручения ({len(tasks)})</b>\n"]
    for i, t in enumerate(tasks[:30], 1):
        status_icon = "🔴" if t.status == "overdue" else "🟡"
        due = f" (до {t.due_date})" if t.due_date else ""
        chat_title = get_chat_title(t.chat_id) or str(t.chat_id)
        lines.append(
            f"{status_icon} <b>{i}.</b> {t.task_text}\n"
            f"   Чат: {chat_title}{due}\n"
            f"   Уверенность: {t.confidence:.0%}\n"
        )

    if len(tasks) > 30:
        lines.append(f"\n... и ещё {len(tasks) - 30} поручений")

    return {"status": "ok", "html": "\n".join(lines)}


async def _cmd_tasks_week(today) -> dict:
    week_ago = today - datetime.timedelta(days=7)
    week_start = datetime.datetime.combine(week_ago, datetime.time.min, tzinfo=datetime.UTC)

    async with async_session() as session:
        # Created this week
        result = await session.execute(
            select(func.count(Task.task_id)).where(Task.detected_at >= week_start)
        )
        created = result.scalar_one()

        # Closed this week
        result = await session.execute(
            select(func.count(Task.task_id)).where(
                Task.status == "closed", Task.closed_at >= week_start
            )
        )
        closed = result.scalar_one()

        # Open now
        result = await session.execute(
            select(func.count(Task.task_id)).where(Task.status == "open")
        )
        open_count = result.scalar_one()

        # Overdue now
        result = await session.execute(
            select(func.count(Task.task_id)).where(Task.status == "overdue")
        )
        overdue = result.scalar_one()

    return {
        "status": "ok",
        "html": (
            f"📊 <b>Поручения за неделю</b>\n\n"
            f"Создано: <b>{created}</b>\n"
            f"Закрыто: <b>{closed}</b>\n"
            f"Открыто: <b>{open_count}</b>\n"
            f"Просрочено: <b>{overdue}</b>"
        ),
    }


async def _cmd_mentions(today, tz) -> dict:
    from rgo_bot.bot.services.chat_registry import get_chat_title

    day_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
    day_end = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz)

    async with async_session() as session:
        result = await session.execute(
            select(MessageModel)
            .where(
                MessageModel.mentions_admin == True,  # noqa: E712
                MessageModel.timestamp >= day_start,
                MessageModel.timestamp < day_end,
            )
            .order_by(MessageModel.timestamp.desc())
            .limit(20)
        )
        mentions = list(result.scalars().all())

    if not mentions:
        return {"status": "ok", "html": "🔇 <b>Упоминаний сегодня нет</b>"}

    lines = [f"📢 <b>Упоминания НУ ({len(mentions)})</b>\n"]
    for m in mentions:
        title = get_chat_title(m.chat_id) or str(m.chat_id)
        ts = m.timestamp.strftime("%H:%M") if m.timestamp else "?"
        name = m.full_name or "?"
        text = (m.text or "")[:200]
        lines.append(f"<b>{ts}</b> [{title}] {name}:\n<i>{text}</i>\n")

    return {"status": "ok", "html": "\n".join(lines)}


async def _cmd_forwards(today, tz) -> dict:
    from rgo_bot.bot.services.chat_registry import get_chat_title

    day_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)
    day_end = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz)

    async with async_session() as session:
        result = await session.execute(
            select(
                MessageModel.chat_id,
                func.count(MessageModel.id).label("cnt"),
            )
            .where(
                MessageModel.is_forwarded == True,  # noqa: E712
                MessageModel.timestamp >= day_start,
                MessageModel.timestamp < day_end,
            )
            .group_by(MessageModel.chat_id)
            .order_by(func.count(MessageModel.id).desc())
        )
        stats = result.all()

    if not stats:
        return {"status": "ok", "html": "📭 <b>Пересланных сообщений сегодня нет</b>"}

    lines = ["↪️ <b>Пересланные сообщения</b>\n"]
    for chat_id, count in stats:
        title = get_chat_title(chat_id) or str(chat_id)
        lines.append(f"• {title}: <b>{count}</b>")

    return {"status": "ok", "html": "\n".join(lines)}


async def _cmd_search(query: str) -> dict:
    if not query.strip():
        return {"error": "Введите текст для поиска"}

    from rgo_bot.bot.services.chat_registry import get_chat_title

    q = query.strip()

    async with async_session() as session:
        result = await session.execute(
            select(MessageModel)
            .where(MessageModel.text.ilike(f"%{q}%"))
            .order_by(MessageModel.timestamp.desc())
            .limit(20)
        )
        results = list(result.scalars().all())

    if not results:
        return {"status": "ok", "html": f"🔍 По запросу «{q}» ничего не найдено"}

    lines = [f"🔍 <b>Результаты «{q}»</b> ({len(results)})\n"]
    for msg in results:
        title = get_chat_title(msg.chat_id) or str(msg.chat_id)
        text = (msg.text or "")[:80]
        ts = msg.timestamp.strftime("%d.%m %H:%M")
        lines.append(f"<b>{ts}</b> [{title}] {msg.full_name}: {text}")

    return {"status": "ok", "html": "\n".join(lines)}


async def _cmd_participants() -> dict:
    from rgo_bot.db.crud.participants import get_all_participants

    async with async_session() as session:
        data = await get_all_participants(session)

    if not data:
        return {"status": "ok", "html": "👥 <b>Участники не найдены</b>"}

    lines = ["👥 <b>Участники</b>\n"]
    for p, chats in data:
        roles = ", ".join(c.role for c in chats) if chats else "—"
        lines.append(f"• {p.full_name} ({roles}) [ID: {p.user_id}]")

    return {"status": "ok", "html": "\n".join(lines)}


# ── Long-running commands (delegate to bot) ────────────


async def _trigger_report(name: str, args: str, bot) -> None:
    """Trigger report generation as a background task."""
    cmd = f"/{name}"
    if args:
        cmd += f" {args}"
    await bot.send_message(
        settings.admin_telegram_id,
        f"⏳ Команда <code>{cmd}</code> запущена через Mini App...",
    )

    async def _gen() -> None:
        try:
            tz = ZoneInfo(settings.timezone)

            if name == "report_now":
                today = datetime.datetime.now(tz).date()
                from rgo_bot.db.crud.reports import get_report_by_date
                from rgo_bot.bot.services.reporter import send_report_to_admin
                from rgo_bot.bot.services.summarizer import generate_daily_report

                result = await generate_daily_report(today, force=True)
                if result:
                    async with async_session() as session:
                        report = await get_report_by_date(session, today, "daily")
                    report_id = report.id if report else None
                    await send_report_to_admin(bot, result.report_text, report_id)

                    # Charts
                    from rgo_bot.bot.services.chart_generator import (
                        generate_load_chart, generate_heatmap, generate_activity_chart,
                    )
                    from rgo_bot.bot.services.chat_registry import get_all_chat_titles
                    from rgo_bot.bot.services.reporter import send_chart_to_admin

                    chat_titles = get_all_chat_titles()
                    async with async_session() as session:
                        chart = await generate_load_chart(session, today, chat_titles)
                        if chart:
                            await send_chart_to_admin(bot, chart, "📊 Нагрузка по чатам")
                        chart = await generate_heatmap(session, today, chat_titles)
                        if chart:
                            await send_chart_to_admin(bot, chart, "🕐 Активность по часам")
                        chart = await generate_activity_chart(session, today, chat_titles)
                        if chart:
                            await send_chart_to_admin(bot, chart, "👥 Рейтинг участников")
                else:
                    await bot.send_message(
                        settings.admin_telegram_id,
                        "📭 Нет данных за сегодня для генерации отчёта.",
                    )

            elif name == "report" and args.strip():
                # Report for specific date
                try:
                    report_date = datetime.date.fromisoformat(args.strip())
                except ValueError:
                    await bot.send_message(
                        settings.admin_telegram_id,
                        "❌ Формат даты: YYYY-MM-DD",
                    )
                    return
                from rgo_bot.bot.services.summarizer import generate_daily_report
                from rgo_bot.bot.services.reporter import send_report_to_admin

                result = await generate_daily_report(report_date, force=True)
                if result:
                    await send_report_to_admin(bot, result.report_text, None)
                else:
                    await bot.send_message(
                        settings.admin_telegram_id,
                        f"📭 Нет данных за {report_date} для генерации отчёта.",
                    )

            elif name == "week":
                from rgo_bot.bot.services.summarizer import generate_daily_report
                from rgo_bot.bot.services.reporter import send_report_to_admin

                today = datetime.datetime.now(tz).date()
                # Generate brief weekly summary
                week_lines = [f"📊 <b>Сводка за 7 дней</b>\n"]
                for i in range(6, -1, -1):
                    d = today - datetime.timedelta(days=i)
                    day_start = datetime.datetime.combine(d, datetime.time.min, tzinfo=tz)
                    day_end = datetime.datetime.combine(d + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz)
                    async with async_session() as session:
                        result = await session.execute(
                            select(func.count(MessageModel.id)).where(
                                MessageModel.timestamp >= day_start,
                                MessageModel.timestamp < day_end,
                            )
                        )
                        count = result.scalar_one()
                    day_name = d.strftime("%a %d.%m")
                    bar = "█" * min(count // 5, 20) if count > 0 else "—"
                    week_lines.append(f"{day_name}: <b>{count}</b> {bar}")

                await bot.send_message(
                    settings.admin_telegram_id,
                    "\n".join(week_lines),
                )

        except Exception:
            logger.exception("webapp_report_error name={}", name)
            await bot.send_message(
                settings.admin_telegram_id,
                f"❌ Ошибка генерации отчёта /{name}",
            )

    asyncio.create_task(_gen())


async def _trigger_chart(name: str, bot) -> None:
    """Trigger chart generation as a background task, send result via bot."""
    from rgo_bot.bot.services.chart_generator import (
        generate_load_chart,
        generate_heatmap,
        generate_activity_chart,
    )
    from rgo_bot.bot.services.chat_registry import get_active_chat_ids, get_all_chat_titles
    from rgo_bot.bot.services.reporter import send_chart_to_admin

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    async def _gen() -> None:
        try:
            chat_titles = get_all_chat_titles()
            async with async_session() as session:
                if name == "load":
                    chart = await generate_load_chart(session, today, chat_titles)
                    caption = "📊 Нагрузка по чатам"
                elif name == "hours":
                    chart = await generate_heatmap(session, today, chat_titles)
                    caption = "🕐 Активность по часам"
                elif name == "activity":
                    chart = await generate_activity_chart(session, today, chat_titles)
                    caption = "👥 Рейтинг участников"
                else:
                    return
            if chart:
                await send_chart_to_admin(bot, chart, caption)
            else:
                await bot.send_message(
                    settings.admin_telegram_id,
                    f"📭 Нет данных для графика /{name}",
                )
        except Exception:
            logger.exception("webapp_chart_error name={}", name)
            await bot.send_message(
                settings.admin_telegram_id,
                f"❌ Ошибка генерации графика /{name}",
            )

    asyncio.create_task(_gen())


async def _trigger_ask(question: str, bot) -> None:
    if not question.strip():
        await bot.send_message(settings.admin_telegram_id, "❌ Укажите вопрос")
        return

    async def _gen() -> None:
        try:
            from rgo_bot.db.crud.messages import get_messages_for_report
            from rgo_bot.bot.services.claude_client import claude_client, load_prompt
            from rgo_bot.bot.services.chat_registry import get_active_chat_ids, get_chat_title

            tz = ZoneInfo(settings.timezone)
            today = datetime.datetime.now(tz).date()

            # Gather messages from all chats
            all_messages = []
            async with async_session() as session:
                for chat_id in get_active_chat_ids():
                    msgs = await get_messages_for_report(session, chat_id, today, tz)
                    all_messages.extend(msgs)

            if not all_messages:
                await bot.send_message(
                    settings.admin_telegram_id,
                    "ℹ️ Нет данных за сегодня для ответа на вопрос",
                )
                return

            # Build context
            context_lines = []
            for m in all_messages:
                title = get_chat_title(m.chat_id) or str(m.chat_id)
                text = m.text or m.voice_transcript or ""
                if text:
                    context_lines.append(f"[{title}] {m.full_name}: {text}")

            context = "\n".join(context_lines[-200:])
            system_prompt = load_prompt("system")
            user_prompt = load_prompt("ask_question").format(
                question=question, context=context
            )

            response = await claude_client.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1024,
                call_type="ask_question",
            )
            await bot.send_message(
                settings.admin_telegram_id,
                f"🤖 <b>Ответ AI:</b>\n\n{response.text}",
            )
        except Exception:
            logger.exception("webapp_ask_error")
            await bot.send_message(
                settings.admin_telegram_id,
                "❌ Ошибка обработки вопроса AI",
            )

    asyncio.create_task(_gen())


# ── Config commands ────────────────────────────────────


async def _cmd_add_chat(args: str) -> dict:
    if not args.strip():
        return {"error": "Укажите ID чата"}
    try:
        chat_id = int(args.strip())
    except ValueError:
        return {"error": "ID чата должен быть числом"}

    from rgo_bot.bot.services.chat_registry import add_chat
    await add_chat(chat_id, title=str(chat_id))
    return {"status": "ok", "html": f"✅ Чат {chat_id} добавлен в мониторинг"}


async def _cmd_remove_chat(args: str) -> dict:
    if not args.strip():
        return {"error": "Укажите ID чата"}
    try:
        chat_id = int(args.strip())
    except ValueError:
        return {"error": "ID чата должен быть числом"}

    from rgo_bot.bot.services.chat_registry import remove_chat
    await remove_chat(chat_id)
    return {"status": "ok", "html": f"✅ Чат {chat_id} удалён из мониторинга"}


async def _cmd_add_keyword(args: str) -> dict:
    if not args.strip():
        return {"error": "Укажите ключевое слово"}

    keyword = args.strip()
    async with async_session() as session:
        session.add(AlertKeyword(keyword=keyword))
        await session.commit()

    return {"status": "ok", "html": f"✅ Ключевое слово «{keyword}» добавлено"}


async def _cmd_set_role(args: str) -> dict:
    parts = args.strip().split()
    if len(parts) < 2:
        return {"error": "Формат: user_id role (rgo/ro/nu/other)"}

    try:
        user_id = int(parts[0])
    except ValueError:
        return {"error": "user_id должен быть числом"}

    role = parts[1].lower()
    if role not in ("rgo", "ro", "nu", "other"):
        return {"error": "Роль должна быть: rgo, ro, nu или other"}

    from rgo_bot.db.crud.participants import set_participant_role

    async with async_session() as session:
        await set_participant_role(session, user_id, role)

    return {"status": "ok", "html": f"✅ Роль {role} назначена пользователю {user_id}"}
