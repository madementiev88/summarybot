"""In-memory registry of active monitored chats.

Single source of truth for all modules that need the list of monitored chats.
Updated dynamically via /add_chat and /remove_chat without bot restart.
"""
from __future__ import annotations

from loguru import logger

from rgo_bot.db.base import async_session
from rgo_bot.db.crud.monitored_chats import (
    add_chat as _db_add_chat,
    get_active_chats,
    remove_chat as _db_remove_chat,
    sync_from_config,
)

# In-memory cache: {chat_id: chat_title}
_active_chats: dict[int, str] = {}


async def init_registry(config_chat_ids: list[int]) -> None:
    """Initialize registry at bot startup.

    1. Sync .env chat IDs into DB if table is empty
    2. Load active chats from DB into memory
    """
    async with async_session() as session:
        synced = await sync_from_config(session, config_chat_ids)
        if synced:
            logger.info("chat_registry synced {} chats from config", synced)

        chats = await get_active_chats(session)

    _active_chats.clear()
    for chat in chats:
        _active_chats[chat.chat_id] = chat.chat_title or str(chat.chat_id)

    logger.info("chat_registry initialized with {} chats", len(_active_chats))


def get_active_chat_ids() -> list[int]:
    """Get list of active monitored chat IDs (from memory, no DB call)."""
    return list(_active_chats.keys())


def get_chat_title(chat_id: int) -> str:
    """Get cached chat title by ID."""
    return _active_chats.get(chat_id, str(chat_id))


def get_all_chat_titles() -> dict[int, str]:
    """Get full {chat_id: title} mapping."""
    return dict(_active_chats)


def is_monitored(chat_id: int) -> bool:
    """Check if chat is in active monitoring."""
    return chat_id in _active_chats


async def add_chat(chat_id: int, title: str) -> None:
    """Add chat to DB and update in-memory cache."""
    async with async_session() as session:
        await _db_add_chat(session, chat_id, chat_title=title)
    _active_chats[chat_id] = title
    logger.info("chat_registry added chat_id={} title={}", chat_id, title)


async def remove_chat(chat_id: int) -> None:
    """Deactivate chat in DB and remove from in-memory cache."""
    async with async_session() as session:
        await _db_remove_chat(session, chat_id)
    _active_chats.pop(chat_id, None)
    logger.info("chat_registry removed chat_id={}", chat_id)
