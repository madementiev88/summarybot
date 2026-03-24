"""Morning recommendations for subscribed RGO managers (08:30, workdays only).

Each RGO gets a personalized recommendation based on:
- Yesterday's chat activity summary
- Open tasks assigned to/by them
"""
from __future__ import annotations

import datetime

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.claude_client import (
    BudgetExceededError,
    CircuitOpenError,
    claude_client,
    load_prompt,
)
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.tasks import get_open_tasks
from rgo_bot.db.models import Participant, ParticipantChat, RgoRecommendation


async def send_morning_recommendations(bot: Bot) -> int:
    """Generate and send recommendations to all subscribed RGOs.

    Returns number of recommendations sent.
    """
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)

    # Get subscribed RGOs
    async with async_session() as session:
        result = await session.execute(
            select(Participant).where(Participant.subscribed_to_recs == True)  # noqa: E712
        )
        rgo_users = result.scalars().all()

    if not rgo_users:
        logger.info("rgo_recs no_subscribers")
        return 0

    sent_count = 0

    for rgo in rgo_users:
        try:
            # Find which chat this RGO belongs to
            async with async_session() as session:
                result = await session.execute(
                    select(ParticipantChat.chat_id)
                    .where(
                        ParticipantChat.user_id == rgo.user_id,
                        ParticipantChat.role == "rgo",
                    )
                )
                chat_row = result.first()
                chat_id = chat_row[0] if chat_row else None

            # Get yesterday's messages summary for context
            messages_summary = "Нет данных за вчера."
            if chat_id:
                from rgo_bot.db.crud.messages import get_messages_for_report

                async with async_session() as session:
                    msgs = await get_messages_for_report(session, chat_id, yesterday, tz)
                if msgs:
                    msg_lines = []
                    for m in msgs[-50:]:  # last 50 messages
                        ts = m.timestamp.strftime("%H:%M") if m.timestamp else "?"
                        name = m.full_name or "?"
                        text = m.text or m.voice_transcript or f"[{m.message_type}]"
                        msg_lines.append(f"[{ts}] {name}: {text}")
                    messages_summary = "\n".join(msg_lines)

            # Get open tasks
            async with async_session() as session:
                tasks = await get_open_tasks(session, chat_id)
            if tasks:
                task_lines = [
                    f"• {t.task_text[:100]} (статус: {t.status})"
                    for t in tasks[:10]
                ]
                open_tasks_text = "\n".join(task_lines)
            else:
                open_tasks_text = "Нет открытых поручений."

            # Get glossary orders for this RGO
            from rgo_bot.db.crud.glossary_orders import get_active_orders_for_chat

            async with async_session() as session:
                glossary_orders = await get_active_orders_for_chat(
                    session, chat_id, today
                ) if chat_id else []
            if glossary_orders:
                glossary_lines = [
                    f"• {o.order_text}" for o in glossary_orders
                ]
                glossary_text = "\n".join(glossary_lines)
            else:
                glossary_text = "Нет поручений от НУ."

            # Generate recommendation via Claude
            prompt = load_prompt("rgo_recommendation").format(
                rgo_name=rgo.full_name or str(rgo.user_id),
                yesterday=yesterday.isoformat(),
                messages_summary=messages_summary[:3000],
                open_tasks=open_tasks_text,
                glossary_orders=glossary_text,
            )

            response = await claude_client.complete(
                system_prompt=load_prompt("system"),
                user_prompt=prompt,
                max_tokens=512,
                temperature=0.4,
                call_type="rgo_recommendation",
            )

            rec_text = response.text.strip()

            # Save to DB
            async with async_session() as session:
                rec = RgoRecommendation(
                    rgo_user_id=rgo.user_id,
                    rec_date=today,
                    recommendation_text=rec_text,
                    morning_context_summary=messages_summary[:500],
                    delivery_status="pending",
                )
                session.add(rec)
                await session.commit()
                rec_id = rec.id

            # Send to RGO
            try:
                await bot.send_message(
                    rgo.user_id,
                    f"☀️ <b>Доброе утро!</b>\n\n{rec_text}",
                    parse_mode="HTML",
                )

                # Mark as sent
                async with async_session() as session:
                    result = await session.execute(
                        select(RgoRecommendation)
                        .where(RgoRecommendation.id == rec_id)
                    )
                    db_rec = result.scalar_one()
                    db_rec.sent_at = datetime.datetime.now(datetime.UTC)
                    db_rec.delivery_status = "sent"
                    await session.commit()

                sent_count += 1
                logger.info(
                    "rgo_rec_sent user_id={} name={}",
                    rgo.user_id, rgo.full_name,
                )

            except Exception:
                logger.exception("rgo_rec_delivery_failed user_id={}", rgo.user_id)
                async with async_session() as session:
                    result = await session.execute(
                        select(RgoRecommendation)
                        .where(RgoRecommendation.id == rec_id)
                    )
                    db_rec = result.scalar_one()
                    db_rec.delivery_status = "failed"
                    await session.commit()

        except (BudgetExceededError, CircuitOpenError) as e:
            logger.warning("rgo_recs stopped: {}", str(e))
            break
        except Exception:
            logger.exception("rgo_rec_error user_id={}", rgo.user_id)

    logger.info("rgo_recs_done sent={}/{}", sent_count, len(rgo_users))
    return sent_count
