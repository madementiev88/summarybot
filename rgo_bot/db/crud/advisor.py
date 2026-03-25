"""CRUD operations for RGO Advisor log."""
from __future__ import annotations

from loguru import logger
from sqlalchemy import select, func, text

from rgo_bot.db.base import async_session
from rgo_bot.db.models import RGOAdvisorLog


async def save_advisor_log(
    rgo_user_id: int,
    question: str,
    answer: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """Save advisor Q&A to log."""
    async with async_session() as session:
        log = RGOAdvisorLog(
            rgo_user_id=rgo_user_id,
            question=question,
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        session.add(log)
        await session.commit()
    logger.info(
        "advisor_log_saved user_id={} tokens_in={} tokens_out={}",
        rgo_user_id,
        prompt_tokens,
        completion_tokens,
    )


async def get_advisor_history(
    rgo_user_id: int,
    limit: int = 6,
) -> list[dict]:
    """Get last N advisor messages for history context."""
    async with async_session() as session:
        result = await session.execute(
            select(RGOAdvisorLog)
            .where(RGOAdvisorLog.rgo_user_id == rgo_user_id)
            .order_by(RGOAdvisorLog.created_at.desc())
            .limit(limit // 2)  # pairs
        )
        rows = result.scalars().all()

    # Build history in chronological order
    history: list[dict] = []
    for row in reversed(rows):
        history.append({"role": "user", "content": row.question})
        history.append({"role": "assistant", "content": row.answer})
    return history
