from __future__ import annotations

import re

from aiogram.types import Message
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.bot.config import settings
from rgo_bot.db.crud.messages import insert_message
from rgo_bot.db.crud.participants import upsert_participant


def _detect_message_type(message: Message) -> str:
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    if message.sticker:
        return "sticker"
    if message.animation:
        return "animation"
    if message.forward_date:
        return "forward"
    if message.text:
        return "text"
    return "other"


def _check_admin_mention(text: str | None) -> tuple[bool, str | None]:
    """Check if text mentions admin by any alias. Returns (found, context).

    Short aliases (<=3 chars) are matched case-sensitive with word boundaries
    to avoid false positives (e.g. "НУ" won't match "новую").
    Longer aliases are matched case-insensitive as substrings.
    """
    if not text or not settings.admin_name_aliases:
        return False, None

    for alias in settings.admin_name_aliases:
        if len(alias) <= 3:
            # Short alias: exact case, word boundaries
            pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
            match = re.search(pattern, text)
        else:
            # Long alias: case-insensitive substring
            text_norm = text.lower().replace("ё", "е")
            alias_norm = alias.lower().replace("ё", "е")
            match = re.search(re.escape(alias_norm), text_norm)

        if match:
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end]
            return True, context
    return False, None


def _sanitize_raw_json(raw: dict) -> dict:
    """Remove sensitive fields from raw Telegram message JSON."""
    sanitized = dict(raw)
    for key in ("phone_number", "vcard", "location", "contact"):
        sanitized.pop(key, None)
    if "forward_origin" in sanitized and isinstance(sanitized["forward_origin"], dict):
        sanitized["forward_origin"].pop("phone_number", None)
    return sanitized


async def collect_message(session: AsyncSession, message: Message) -> None:
    """Parse incoming group message and save to database."""
    if not message.from_user:
        return

    user = message.from_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Unknown"

    # Detect message type
    msg_type = _detect_message_type(message)

    # Get text content
    text = message.text or message.caption

    # Transcribe voice/video_note
    voice_transcript = None
    if msg_type in ("voice", "video_note"):
        from rgo_bot.bot.services.transcriber import transcribe_voice

        file_id = None
        duration = 0
        if message.voice:
            file_id = message.voice.file_id
            duration = message.voice.duration or 0
        elif message.video_note:
            file_id = message.video_note.file_id
            duration = message.video_note.duration or 0

        if file_id:
            voice_transcript = await transcribe_voice(message.bot, file_id, duration)

    # Check for admin mentions (in text and voice transcript)
    mentions_admin, mention_context = _check_admin_mention(text or voice_transcript)

    # Check if forwarded
    is_forwarded = message.forward_date is not None
    forward_from_user_id = None
    forward_is_from_admin = False
    if message.forward_from:
        forward_from_user_id = message.forward_from.id
        forward_is_from_admin = message.forward_from.id == settings.admin_telegram_id

    # Sanitize raw JSON
    raw_json = _sanitize_raw_json(message.model_dump(mode="json"))

    # Save message
    await insert_message(
        session,
        message_id=message.message_id,
        chat_id=message.chat.id,
        user_id=user.id,
        username=user.username,
        full_name=full_name,
        text=text,
        voice_transcript=voice_transcript,
        message_type=msg_type,
        timestamp=message.date,
        is_forwarded=is_forwarded,
        forward_from_user_id=forward_from_user_id,
        forward_is_from_admin=forward_is_from_admin,
        reply_to_message_id=message.reply_to_message.message_id
        if message.reply_to_message
        else None,
        mentions_admin=mentions_admin,
        admin_mention_context=mention_context,
        media_group_id=message.media_group_id,
        raw_json=raw_json,
    )

    # Upsert participant
    await upsert_participant(
        session,
        user_id=user.id,
        username=user.username,
        full_name=full_name,
        chat_id=message.chat.id,
    )

    # Check if reply to a task message → auto-close task
    reply_to_msg_id = (
        message.reply_to_message.message_id if message.reply_to_message else None
    )
    if reply_to_msg_id and (
        msg_type in ("photo", "sticker") or (text and _is_emoji_only(text))
    ):
        await _check_task_reply_close(
            session, message.chat.id, reply_to_msg_id, message.message_id
        )

    logger.info(
        "message_collected chat_id={} user_id={} type={}",
        message.chat.id,
        user.id,
        msg_type,
    )


# Emoji-only regex: matches strings consisting entirely of emoji characters
_EMOJI_RE = re.compile(
    r"^["
    r"\U0001F300-\U0001FFFF"  # Misc symbols, emoticons, transport, maps
    r"\u2600-\u27BF"  # Misc symbols, dingbats
    r"\u200d"  # Zero-width joiner
    r"\uFE0F"  # Variation selector
    r"\u2764"  # Heart
    r"\u2705"  # Check mark
    r"\u270C"  # Victory hand
    r"\u261D"  # Index pointing up
    r"\u2B50"  # Star
    r"\u2728"  # Sparkles
    r"\u2934"  # Arrow
    r"\u2935"  # Arrow
    r"\s"  # Whitespace between emojis
    r"]+$"
)


def _is_emoji_only(text: str) -> bool:
    """Check if text consists only of emoji characters."""
    return bool(_EMOJI_RE.match(text.strip()))


async def _check_task_reply_close(
    session: AsyncSession,
    chat_id: int,
    reply_to_telegram_msg_id: int,
    close_telegram_msg_id: int,
) -> None:
    """If reply targets a task-source message, auto-close the task."""
    from rgo_bot.db.crud.tasks import update_task_status
    from rgo_bot.db.models import Message as MessageModel
    from rgo_bot.db.models import Task

    # Find open task whose source message matches the reply target
    result = await session.execute(
        select(Task.task_id)
        .join(MessageModel, Task.source_message_id == MessageModel.id)
        .where(
            MessageModel.message_id == reply_to_telegram_msg_id,
            MessageModel.chat_id == chat_id,
            Task.status == "open",
        )
    )
    task_ids = result.scalars().all()

    for task_id in task_ids:
        await update_task_status(
            session, task_id, "closed", close_message_id=close_telegram_msg_id
        )
        logger.info(
            "task_auto_closed task_id={} by_reply_to={}", task_id, reply_to_telegram_msg_id
        )
