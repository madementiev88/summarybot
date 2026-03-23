"""CRUD for presentation_preferences table."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import PresentationPreference


async def get_preferences(
    session: AsyncSession, user_id: int
) -> PresentationPreference | None:
    """Get presentation preferences for a user."""
    result = await session.execute(
        select(PresentationPreference).where(
            PresentationPreference.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def upsert_preferences(
    session: AsyncSession, user_id: int, preferences_json: dict
) -> None:
    """Insert or update presentation preferences."""
    existing = await get_preferences(session, user_id)
    if existing:
        existing.preferences_json = preferences_json
    else:
        session.add(PresentationPreference(
            user_id=user_id,
            preferences_json=preferences_json,
        ))
    await session.commit()
