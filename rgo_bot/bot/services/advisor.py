"""AI Advisor service for RGO managers.

Provides personalized management advice using Claude API
with RGO-specific context from the database.
"""
from __future__ import annotations

import datetime

from loguru import logger
from sqlalchemy import func, select, text
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.claude_client import claude_client, load_prompt
from rgo_bot.db.base import async_session
from rgo_bot.db.models import Message, Task

# Daily limit per RGO
MAX_QUESTIONS_PER_DAY = 20
MAX_QUESTION_LENGTH = 500


async def _get_daily_question_count(user_id: int) -> int:
    """Count advisor questions from this user today."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM rgo_advisor_log
                WHERE rgo_user_id = :uid
                AND created_at::date = CURRENT_DATE
            """),
            {"uid": user_id},
        )
        row = result.first()
        return row[0] if row else 0


async def _build_rgo_context(user_id: int, chat_id: int) -> str | None:
    """Build RGO context string from database metrics."""
    try:
        async with async_session() as session:
            # Messages count last 7 days
            msg_result = await session.execute(
                select(func.count(Message.id)).where(
                    Message.chat_id == chat_id,
                    Message.timestamp > func.now() - text("interval '7 days'"),
                )
            )
            msg_count = msg_result.scalar() or 0

            # Peak activity hours
            peak_result = await session.execute(
                text("""
                    SELECT EXTRACT(HOUR FROM timestamp AT TIME ZONE :tz) AS hr,
                           COUNT(*) AS cnt
                    FROM messages
                    WHERE chat_id = :cid
                    AND timestamp > now() - interval '7 days'
                    GROUP BY hr ORDER BY cnt DESC LIMIT 3
                """),
                {"cid": chat_id, "tz": settings.timezone},
            )
            peak_rows = peak_result.fetchall()
            peak_hours = ", ".join(f"{int(r[0])}:00" for r in peak_rows) if peak_rows else "нет данных"

            # Open and overdue tasks
            task_result = await session.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'open') AS open_tasks,
                        COUNT(*) FILTER (WHERE status = 'overdue') AS overdue_tasks
                    FROM tasks
                    WHERE chat_id = :cid
                """),
                {"cid": chat_id},
            )
            task_row = task_result.first()
            open_tasks = task_row[0] if task_row else 0
            overdue_tasks = task_row[1] if task_row else 0

            if msg_count == 0 and open_tasks == 0:
                return None

            context = (
                "[ДАННЫЕ МОНИТОРИНГА ДЛЯ ПЕРСОНАЛИЗАЦИИ]\n"
                f"Активность в чате за последние 7 дней: {msg_count} сообщений\n"
                f"Часы пиковой активности: {peak_hours}\n"
                f"Открытых поручений: {open_tasks}\n"
                f"Просроченных поручений: {overdue_tasks}\n"
                "[КОНЕЦ ДАННЫХ]"
            )
            return context

    except Exception:
        logger.exception("advisor_context_error user_id={}", user_id)
        return None


async def get_advisor_response(
    question: str,
    history: list[dict],
    user_id: int,
    chat_id: int,
) -> dict:
    """Get advisor response from Claude API.

    Returns dict with keys: answer, tokens_in, tokens_out, error
    """
    # Check daily limit
    daily_count = await _get_daily_question_count(user_id)
    if daily_count >= MAX_QUESTIONS_PER_DAY:
        return {
            "answer": f"На сегодня лимит вопросов исчерпан ({MAX_QUESTIONS_PER_DAY}). Продолжим завтра.",
            "tokens_in": 0,
            "tokens_out": 0,
            "error": None,
        }

    # Validate question length
    question = question.strip()
    if len(question) < 10:
        return {
            "answer": "Напиши вопрос подробнее — так смогу дать точный совет.",
            "tokens_in": 0,
            "tokens_out": 0,
            "error": None,
        }

    if len(question) > MAX_QUESTION_LENGTH:
        question = question[:MAX_QUESTION_LENGTH]

    # Load system prompt
    system_prompt = load_prompt("rgo_advisor")

    # Build context
    rgo_context = await _build_rgo_context(user_id, chat_id)

    # Build user message with context
    if rgo_context:
        user_message = f"{rgo_context}\n\nВопрос РГО: {question}"
    else:
        user_message = question

    # Build messages array with history (last 6 messages = 3 pairs)
    messages_for_api: list[dict] = []
    for msg in history[-6:]:
        messages_for_api.append(msg)
    messages_for_api.append({"role": "user", "content": user_message})

    try:
        response = await claude_client.complete(
            system_prompt=system_prompt,
            user_prompt=user_message,
            max_tokens=1024,
            temperature=0.5,
            call_type="advisor",
        )

        return {
            "answer": response.text,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "error": None,
        }

    except Exception as e:
        logger.exception("advisor_api_error user_id={}", user_id)
        return {
            "answer": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "error": str(e),
        }
