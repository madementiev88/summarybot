"""Feedback (bug report) API route.

POST /api/feedback — send bug report to developer via bot.
"""
from __future__ import annotations

from aiohttp import web
from loguru import logger

from rgo_bot.bot.config import settings


def setup_feedback_routes(app: web.Application) -> None:
    app.router.add_post("/api/feedback", handle_feedback)


async def handle_feedback(request: web.Request) -> web.Response:
    """Receive feedback text and send to admin via bot."""
    body = await request.json()
    text = body.get("text", "").strip()

    if not text:
        return web.json_response({"error": "Текст не может быть пустым"}, status=400)

    if len(text) > 2000:
        return web.json_response({"error": "Слишком длинное сообщение"}, status=400)

    bot = request.app["bot"]
    user = request["tg_user"]
    user_name = user.get("first_name", "Unknown")

    try:
        await bot.send_message(
            settings.admin_telegram_id,
            f"🐛 <b>[BUG REPORT]</b>\n\n"
            f"<b>От:</b> {user_name} (ID: {user.get('id')})\n\n"
            f"{text}",
        )
        logger.info("feedback_sent user_id={} len={}", user.get("id"), len(text))
        return web.json_response({"status": "ok"})
    except Exception:
        logger.exception("feedback_error")
        return web.json_response({"error": "Ошибка отправки"}, status=500)
