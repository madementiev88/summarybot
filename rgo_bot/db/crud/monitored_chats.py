from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import MonitoredChat


async def add_chat(
    session: AsyncSession,
    chat_id: int,
    chat_title: str | None = None,
    rgo_user_id: int | None = None,
) -> MonitoredChat:
    """Add or reactivate a monitored chat."""
    stmt = (
        pg_insert(MonitoredChat)
        .values(
            chat_id=chat_id,
            chat_title=chat_title,
            rgo_user_id=rgo_user_id,
            is_active=True,
        )
        .on_conflict_do_update(
            index_elements=["chat_id"],
            set_={"is_active": True, "chat_title": chat_title},
        )
        .returning(MonitoredChat)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.scalar_one()


async def remove_chat(session: AsyncSession, chat_id: int) -> None:
    """Deactivate a monitored chat (soft delete)."""
    await session.execute(
        update(MonitoredChat)
        .where(MonitoredChat.chat_id == chat_id)
        .values(is_active=False)
    )
    await session.commit()


async def get_active_chats(session: AsyncSession) -> list[MonitoredChat]:
    """Get all active monitored chats."""
    result = await session.execute(
        select(MonitoredChat)
        .where(MonitoredChat.is_active == True)  # noqa: E712
        .order_by(MonitoredChat.added_at)
    )
    return list(result.scalars().all())


async def sync_from_config(session: AsyncSession, chat_ids: list[int]) -> int:
    """Seed monitored_chats from .env config if table is empty.

    Returns number of chats synced.
    """
    result = await session.execute(select(MonitoredChat.chat_id))
    existing = {r[0] for r in result.all()}

    added = 0
    for chat_id in chat_ids:
        if chat_id not in existing:
            session.add(MonitoredChat(chat_id=chat_id, is_active=True))
            added += 1

    if added:
        await session.commit()
    return added
