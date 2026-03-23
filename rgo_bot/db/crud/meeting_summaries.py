"""CRUD for meeting_summaries table."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import MeetingSummary


async def get_recent_summaries(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[MeetingSummary]:
    """Get recent meeting summaries for a user."""
    result = await session.execute(
        select(MeetingSummary)
        .where(MeetingSummary.user_id == user_id)
        .order_by(MeetingSummary.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
