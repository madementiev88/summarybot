from __future__ import annotations

import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import Participant, ParticipantChat


async def upsert_participant(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    full_name: str,
    chat_id: int,
    role: str = "other",
) -> Participant:
    """Create or update participant and their chat membership."""
    now = datetime.datetime.now(datetime.UTC)

    # Upsert participant
    stmt = select(Participant).where(Participant.user_id == user_id)
    result = await session.execute(stmt)
    participant = result.scalar_one_or_none()

    if participant is None:
        participant = Participant(
            user_id=user_id,
            username=username,
            full_name=full_name,
            first_seen_at=now,
            last_active_at=now,
            total_messages=1,
        )
        session.add(participant)
    else:
        participant.username = username
        participant.full_name = full_name
        participant.last_active_at = now
        participant.total_messages += 1

    # Upsert participant_chat
    stmt = select(ParticipantChat).where(
        ParticipantChat.user_id == user_id,
        ParticipantChat.chat_id == chat_id,
    )
    result = await session.execute(stmt)
    pc = result.scalar_one_or_none()

    if pc is None:
        pc = ParticipantChat(
            user_id=user_id,
            chat_id=chat_id,
            role=role,
            joined_at=now,
            last_active_at=now,
        )
        session.add(pc)
    else:
        pc.last_active_at = now

    await session.commit()
    return participant


VALID_ROLES = {"rgo", "ro", "nu", "other"}


async def set_participant_role(
    session: AsyncSession,
    user_id: int,
    role: str,
) -> int:
    """Set role for a participant across all their chats.

    Returns number of chat memberships updated.
    """
    result = await session.execute(
        update(ParticipantChat)
        .where(ParticipantChat.user_id == user_id)
        .values(role=role)
    )
    await session.commit()
    return result.rowcount  # type: ignore[return-value]


async def get_all_participants(
    session: AsyncSession,
    limit: int = 50,
) -> list[tuple[Participant, list[ParticipantChat]]]:
    """Get participants with their chat memberships."""
    result = await session.execute(
        select(Participant)
        .order_by(Participant.last_active_at.desc())
        .limit(limit)
    )
    participants = result.scalars().all()

    output = []
    for p in participants:
        result = await session.execute(
            select(ParticipantChat)
            .where(ParticipantChat.user_id == p.user_id)
        )
        chats = list(result.scalars().all())
        output.append((p, chats))
    return output
