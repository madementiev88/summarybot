"""CRUD operations for glossary_orders table."""
from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import GlossaryOrder


async def create_order(
    session: AsyncSession,
    *,
    user_id: int,
    transcript_text: str | None,
    order_text: str,
    target_rgo_ids: list[int] | None,
    target_date: datetime.date,
) -> GlossaryOrder:
    """Create a new glossary order."""
    order = GlossaryOrder(
        user_id=user_id,
        transcript_text=transcript_text,
        order_text=order_text,
        target_rgo_ids=target_rgo_ids,
        status="active",
        target_date=target_date,
    )
    session.add(order)
    await session.commit()
    return order


async def get_active_orders_for_date(
    session: AsyncSession,
    target_date: datetime.date,
) -> list[GlossaryOrder]:
    """Get all active orders for a specific date."""
    result = await session.execute(
        select(GlossaryOrder)
        .where(
            GlossaryOrder.target_date == target_date,
            GlossaryOrder.status == "active",
        )
        .order_by(GlossaryOrder.created_at)
    )
    return list(result.scalars().all())


async def get_active_orders_for_chat(
    session: AsyncSession,
    chat_id: int,
    target_date: datetime.date,
) -> list[GlossaryOrder]:
    """Get active orders targeting a specific chat (or all chats) for a date."""
    all_orders = await get_active_orders_for_date(session, target_date)
    return [
        o for o in all_orders
        if o.target_rgo_ids is None or chat_id in o.target_rgo_ids
    ]


async def mark_order_done(session: AsyncSession, order_id: int) -> None:
    """Mark an order as done."""
    result = await session.execute(
        select(GlossaryOrder).where(GlossaryOrder.id == order_id)
    )
    order = result.scalar_one_or_none()
    if order:
        order.status = "done"
        await session.commit()
