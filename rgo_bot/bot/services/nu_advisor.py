"""AI Advisor service for НУ (admin).

Provides strategic advice using Claude API with context
about all 7 RGOs, their performance, and advisor usage.
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import text

from rgo_bot.bot.services.claude_client import claude_client, load_prompt
from rgo_bot.bot.services.nu_context_builder import build_context
from rgo_bot.db.base import async_session

MAX_QUESTIONS_PER_DAY = 20
MAX_QUESTION_LENGTH = 500


async def _get_daily_question_count() -> int:
    """Count NU advisor questions today."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM nu_advisor_log
                WHERE created_at::date = CURRENT_DATE
            """)
        )
        row = result.first()
        return row[0] if row else 0


async def get_nu_advisor_response(
    question: str,
    history: list[dict],
) -> dict:
    """Get NU advisor response from Claude API.

    Returns dict with keys: answer, tokens_in, tokens_out, error,
                            context_type, target_rgo_user_id
    """
    # Check daily limit
    daily_count = await _get_daily_question_count()
    if daily_count >= MAX_QUESTIONS_PER_DAY:
        return {
            "answer": f"На сегодня лимит вопросов исчерпан ({MAX_QUESTIONS_PER_DAY}). Продолжим завтра.",
            "tokens_in": 0,
            "tokens_out": 0,
            "error": None,
            "context_type": "no_data",
            "target_rgo_user_id": None,
        }

    # Validate question
    question = question.strip()
    if len(question) < 10:
        return {
            "answer": "Опиши ситуацию подробнее — так дам точный анализ.",
            "tokens_in": 0,
            "tokens_out": 0,
            "error": None,
            "context_type": "no_data",
            "target_rgo_user_id": None,
        }

    if len(question) > MAX_QUESTION_LENGTH:
        question = question[:MAX_QUESTION_LENGTH]

    # Load prompt
    system_prompt = load_prompt("nu_advisor")

    # Build context from DB
    context_text, q_type, target_rgo_id = await build_context(question)

    # Build user message
    if context_text:
        user_message = f"{context_text}\n\nВопрос НУ: {question}"
    else:
        user_message = question

    try:
        response = await claude_client.complete(
            system_prompt=system_prompt,
            user_prompt=user_message,
            max_tokens=1200,
            temperature=0.3,
            call_type="nu_advisor",
        )

        return {
            "answer": response.text,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "error": None,
            "context_type": q_type,
            "target_rgo_user_id": target_rgo_id,
        }

    except Exception as e:
        logger.exception("nu_advisor_api_error")
        return {
            "answer": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "error": str(e),
            "context_type": q_type,
            "target_rgo_user_id": target_rgo_id,
        }
