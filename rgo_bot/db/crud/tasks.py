from __future__ import annotations

import datetime
import hashlib

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import Task


async def create_task(
    session: AsyncSession,
    *,
    source_message_id: int,
    chat_id: int,
    assigner_user_id: int,
    assignee_user_id: int | None,
    task_text: str,
    confidence: float,
    due_date: datetime.date | None = None,
    detection_method: str = "ai_context",
) -> Task | None:
    """Insert task with deduplication via (source_message_id, task_text_hash)."""
    text_hash = hashlib.sha256(task_text.encode()).hexdigest()

    stmt = (
        pg_insert(Task)
        .values(
            source_message_id=source_message_id,
            chat_id=chat_id,
            assigner_user_id=assigner_user_id,
            assignee_user_id=assignee_user_id,
            task_text=task_text,
            task_text_hash=text_hash,
            confidence=confidence,
            due_date=due_date,
            detection_method=detection_method,
        )
        .on_conflict_do_nothing(constraint="uq_task_source_hash")
        .returning(Task)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.scalar_one_or_none()


async def get_open_tasks(
    session: AsyncSession,
    chat_id: int | None = None,
) -> list[Task]:
    """Get all open/overdue tasks, optionally filtered by chat."""
    stmt = select(Task).where(Task.status.in_(("open", "overdue")))
    if chat_id is not None:
        stmt = stmt.where(Task.chat_id == chat_id)
    stmt = stmt.order_by(Task.detected_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_tasks_by_status(
    session: AsyncSession,
    status: str = "open",
    limit: int = 50,
) -> list[Task]:
    stmt = (
        select(Task)
        .where(Task.status == status)
        .order_by(Task.detected_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_task_status(
    session: AsyncSession,
    task_id: int,
    new_status: str,
    close_message_id: int | None = None,
) -> None:
    values: dict = {"status": new_status, "last_status_check_at": datetime.datetime.now(datetime.UTC)}
    if new_status == "closed":
        values["closed_at"] = datetime.datetime.now(datetime.UTC)
    if close_message_id is not None:
        values["close_message_id"] = close_message_id

    await session.execute(
        update(Task).where(Task.task_id == task_id).values(**values)
    )
    await session.commit()


async def get_unprocessed_messages(
    session: AsyncSession,
    chat_id: int,
    since: datetime.datetime,
) -> list:
    """Get messages not yet processed for task detection."""
    from rgo_bot.db.models import Message

    result = await session.execute(
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.timestamp >= since,
            Message.ai_processed == False,  # noqa: E712
        )
        .order_by(Message.timestamp.asc())
        .limit(100)
    )
    return list(result.scalars().all())


async def mark_messages_processed(
    session: AsyncSession,
    message_ids: list[int],
    task_detected: bool = False,
) -> None:
    """Mark messages as processed by task classifier."""
    from rgo_bot.db.models import Message

    if not message_ids:
        return
    await session.execute(
        update(Message)
        .where(Message.id.in_(message_ids))
        .values(ai_processed=True, ai_task_detected=task_detected)
    )
    await session.commit()
