"""KOS (meeting summarization) API routes.

POST /api/kos/upload — upload audio file, transcribe, summarize
GET  /api/kos/status/{task_id} — check processing status
"""
from __future__ import annotations

import asyncio

from aiohttp import web
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.web.app import create_task_entry, update_task


def setup_kos_routes(app: web.Application) -> None:
    app.router.add_post("/api/kos/upload", handle_kos_upload)


async def handle_kos_upload(request: web.Request) -> web.Response:
    """Receive audio file, create background task for transcription + summarization."""
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
            audio_data = await part.read()
            filename = part.filename or filename

    if not audio_data:
        return web.json_response({"error": "Аудио файл не получен"}, status=400)

    if len(audio_data) > 25 * 1024 * 1024:
        return web.json_response({"error": "Файл слишком большой (макс. 25 МБ)"}, status=400)

    # Create background task
    task_id = create_task_entry(step="transcribing")

    async def _process() -> None:
        try:
            from rgo_bot.web.services.meeting_summarizer import summarize_audio_bytes
            result = await summarize_audio_bytes(audio_data, filename, bot, source="miniapp")
            update_task(task_id, status="done", result=result)
        except Exception:
            logger.exception("kos_process_error task_id={}", task_id)
            update_task(task_id, status="error", result={"error": "Ошибка обработки"})

    asyncio.create_task(_process())

    return web.json_response({"status": "processing", "task_id": task_id})
