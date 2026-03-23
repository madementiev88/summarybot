"""Preza (presentation generation) API routes.

POST /api/preza/generate — upload voice with requirements, generate PPTX
GET  /api/preza/preferences — get saved presentation preferences
"""
from __future__ import annotations

import asyncio

from aiohttp import web
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.web.app import create_task_entry, update_task


def setup_preza_routes(app: web.Application) -> None:
    app.router.add_post("/api/preza/generate", handle_preza_generate)
    app.router.add_get("/api/preza/preferences", handle_preza_preferences)


async def handle_preza_generate(request: web.Request) -> web.Response:
    """Receive voice with presentation requirements, generate PPTX."""
    bot = request.app["bot"]

    # Read multipart form data
    reader = await request.multipart()
    audio_data = None
    filename = "audio.webm"

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "audio":
            audio_data = await part.read(chunk_size=25 * 1024 * 1024)
            filename = part.filename or filename

    if not audio_data:
        return web.json_response({"error": "Аудио файл не получен"}, status=400)

    # Create background task
    task_id = create_task_entry(step="transcribing")
    user_id = request["tg_user"]["id"]

    async def _process() -> None:
        try:
            from rgo_bot.web.services.pptx_generator import generate_presentation
            result = await generate_presentation(
                audio_data, filename, bot, user_id, task_id
            )
            update_task(task_id, status="done", result=result)
        except Exception:
            logger.exception("preza_process_error task_id={}", task_id)
            update_task(task_id, status="error", result={"error": "Ошибка генерации"})

    asyncio.create_task(_process())

    return web.json_response({"status": "processing", "task_id": task_id})


async def handle_preza_preferences(request: web.Request) -> web.Response:
    """Return saved presentation preferences for the admin."""
    user_id = request["tg_user"]["id"]

    from rgo_bot.db.base import async_session
    from rgo_bot.db.crud.presentation_preferences import get_preferences

    async with async_session() as session:
        prefs = await get_preferences(session, user_id)

    if prefs is None:
        return web.json_response({"preferences": None})

    return web.json_response({"preferences": prefs.preferences_json})
