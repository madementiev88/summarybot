from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import Message


async def insert_message(session: AsyncSession, **kwargs: object) -> Message:
    msg = Message(**kwargs)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def get_messages_count(
    session: AsyncSession,
    chat_id: int | None = None,
    since: datetime.datetime | None = None,
) -> int:
    stmt = select(func.count(Message.id))
    if chat_id is not None:
        stmt = stmt.where(Message.chat_id == chat_id)
    if since is not None:
        stmt = stmt.where(Message.timestamp >= since)
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_messages_for_report(
    session: AsyncSession,
    chat_id: int,
    report_date: datetime.date,
    tz: datetime.tzinfo,
) -> list[Message]:
    day_start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=tz)
    day_end = datetime.datetime.combine(
        report_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
    )
    result = await session.execute(
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.timestamp >= day_start,
            Message.timestamp < day_end,
        )
        .order_by(Message.timestamp.asc())
    )
    return list(result.scalars().all())
