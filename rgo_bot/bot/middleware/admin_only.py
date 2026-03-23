from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from rgo_bot.bot.config import settings


class AdminOnlyMiddleware(BaseMiddleware):
    """Blocks all private messages from non-admin users.

    Applied to the private-chat router. Non-admin messages are silently ignored
    (no response, no logging of message text).
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        if event.from_user and event.from_user.id == settings.admin_telegram_id:
            return await handler(event, data)

        # Silently ignore non-admin private messages
        return None
