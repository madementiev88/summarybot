from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message
from sqlalchemy import select

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.models import Participant

router = Router(name="rgo_private")


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/start")
async def cmd_start_rgo(message: Message) -> None:
    """Handle /start from RGO users — subscribe to recommendations."""
    if not message.from_user:
        return

    user_id = message.from_user.id

    # Admin is handled by admin router, skip here
    if user_id == settings.admin_telegram_id:
        return

    async with async_session() as session:
        result = await session.execute(
            select(Participant).where(Participant.user_id == user_id)
        )
        participant = result.scalar_one_or_none()

        if participant is None:
            await message.answer(
                "Вы не зарегистрированы в системе. "
                "Ваши сообщения в рабочих чатах будут автоматически учтены."
            )
            return

        participant.subscribed_to_recs = True
        await session.commit()

        await message.answer(
            "✅ Вы подписаны на утренние рекомендации.\n"
            "Каждый рабочий день в 08:30 вы будете получать "
            "персональные рекомендации на день.\n\n"
            "Для отписки отправьте /stop"
        )


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/stop")
async def cmd_stop_rgo(message: Message) -> None:
    """Unsubscribe RGO from recommendations."""
    if not message.from_user:
        return

    user_id = message.from_user.id
    if user_id == settings.admin_telegram_id:
        return

    async with async_session() as session:
        result = await session.execute(
            select(Participant).where(Participant.user_id == user_id)
        )
        participant = result.scalar_one_or_none()

        if participant:
            participant.subscribed_to_recs = False
            await session.commit()

        await message.answer("Вы отписаны от утренних рекомендаций.")
