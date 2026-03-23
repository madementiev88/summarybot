from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import ChatMemberUpdated, Message
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.chat_registry import is_monitored
from rgo_bot.bot.services.collector import collect_message
from rgo_bot.db.base import async_session

router = Router(name="group_messages")


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def handle_group_message(message: Message) -> None:
    """Collect all messages from monitored group chats.

    Auto-registers new chats: if bot receives a message from an unknown group,
    it adds the chat to monitoring and notifies admin.
    """
    if not is_monitored(message.chat.id):
        # Safety: only auto-add group chats (negative IDs)
        if message.chat.id >= 0:
            return

        # Auto-add: bot is in this chat but it's not monitored yet
        from rgo_bot.bot.services.chat_registry import add_chat

        chat_title = message.chat.title or str(message.chat.id)
        await add_chat(message.chat.id, chat_title)

        try:
            await message.bot.send_message(
                settings.admin_telegram_id,
                f"📥 <b>Новый чат добавлен автоматически</b>\n\n"
                f"Название: {chat_title}\n"
                f"ID: <code>{message.chat.id}</code>\n\n"
                f"Мониторинг активирован. Для отключения:\n"
                f"/remove_chat {message.chat.id}",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("failed to notify admin about new chat")

        logger.info(
            "chat_auto_added chat_id={} title={}",
            message.chat.id, chat_title,
        )

    async with async_session() as session:
        try:
            await collect_message(session, message)
        except Exception:
            logger.exception(
                "failed to collect message chat_id={} message_id={}",
                message.chat.id,
                message.message_id,
            )
            return

    # Real-time alerts (non-blocking)
    try:
        from rgo_bot.bot.services.alerter import check_realtime_alerts
        from rgo_bot.bot.services.collector import _check_admin_mention

        text = message.text or message.caption
        mentions_admin, _ = _check_admin_mention(text)
        user = message.from_user
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else "?"
        chat_title = message.chat.title or str(message.chat.id)

        await check_realtime_alerts(
            bot=message.bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_name=full_name,
            text=text,
            mentions_admin=mentions_admin,
            is_forwarded=message.forward_date is not None,
            timestamp=message.date,
            chat_title=chat_title,
        )
    except Exception:
        logger.exception("alert_check_failed chat_id={}", message.chat.id)


@router.edited_message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def handle_edited_message(message: Message) -> None:
    """Track message edits in monitored chats."""
    if not is_monitored(message.chat.id):
        return

    from sqlalchemy import select, update

    from rgo_bot.db.models import Message as MessageModel

    async with async_session() as session:
        try:
            # Find original message
            stmt = select(MessageModel).where(
                MessageModel.message_id == message.message_id,
                MessageModel.chat_id == message.chat.id,
            )
            result = await session.execute(stmt)
            db_msg = result.scalar_one_or_none()

            if db_msg is None:
                return

            # Save old text to edit_history
            old_history = db_msg.edit_history or []
            if isinstance(old_history, dict):
                old_history = [old_history]
            old_history.append({
                "old_text": db_msg.text,
                "edited_at": message.date.isoformat() if message.date else None,
            })

            new_text = message.text or message.caption

            await session.execute(
                update(MessageModel)
                .where(MessageModel.id == db_msg.id)
                .values(text=new_text, edit_history=old_history)
            )
            await session.commit()

            logger.info(
                "message_edited chat_id={} message_id={}",
                message.chat.id,
                message.message_id,
            )
        except Exception:
            logger.exception(
                "failed to handle edit chat_id={} message_id={}",
                message.chat.id,
                message.message_id,
            )


@router.my_chat_member()
async def handle_bot_status_change(event: ChatMemberUpdated) -> None:
    """Handle bot added/removed from group chats."""
    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    chat_title = event.chat.title or str(event.chat.id)
    bot = event.bot

    # Bot added to a group
    if new_status in ("member", "administrator") and old_status in ("left", "kicked", "restricted"):
        from rgo_bot.bot.services.chat_registry import add_chat

        await add_chat(event.chat.id, chat_title)

        try:
            await bot.send_message(
                settings.admin_telegram_id,
                f"📥 <b>Бот добавлен в чат</b>\n\n"
                f"Название: {chat_title}\n"
                f"ID: <code>{event.chat.id}</code>\n\n"
                f"Мониторинг активирован автоматически.\n"
                f"Для отключения: /remove_chat {event.chat.id}",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("failed to notify admin about bot added")

        logger.info("bot_added chat_id={} title={}", event.chat.id, chat_title)

    # Bot removed from a group
    elif new_status in ("left", "kicked"):
        from rgo_bot.bot.services.chat_registry import remove_chat

        await remove_chat(event.chat.id)

        try:
            await bot.send_message(
                settings.admin_telegram_id,
                f"⚠️ <b>Бот удалён из чата</b>\n\n"
                f"Чат: {chat_title}\n"
                f"ID: <code>{event.chat.id}</code>\n\n"
                f"Мониторинг чата прекращён.",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("failed to send bot_removed alert")

        logger.warning("bot_removed chat_id={} title={}", event.chat.id, chat_title)
