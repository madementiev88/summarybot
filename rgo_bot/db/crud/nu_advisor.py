"""CRUD operations for NU Advisor log."""
from __future__ import annotations

from loguru import logger

from rgo_bot.db.base import async_session
from rgo_bot.db.models import NUAdvisorLog


async def save_nu_advisor_log(
    question: str,
    answer: str,
    context_type: str,
    target_rgo_user_id: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """Save NU advisor Q&A to log."""
    async with async_session() as session:
        log = NUAdvisorLog(
            question=question,
            answer=answer,
            context_type=context_type,
            target_rgo_user_id=target_rgo_user_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        session.add(log)
        await session.commit()
    logger.info(
        "nu_advisor_log_saved context_type={} tokens_in={} tokens_out={}",
        context_type,
        prompt_tokens,
        completion_tokens,
    )
