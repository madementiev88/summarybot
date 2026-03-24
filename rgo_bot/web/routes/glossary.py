"""Glossary (НУ orders for RGO) API routes.

POST /api/glossary/upload — upload audio, transcribe, extract orders, save
"""
from __future__ import annotations

import asyncio

from aiohttp import web
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.web.app import create_task_entry, update_task


def setup_glossary_routes(app: web.Application) -> None:
    app.router.add_post("/api/glossary/upload", handle_glossary_upload)


async def handle_glossary_upload(request: web.Request) -> web.Response:
    """Receive audio file with НУ orders, transcribe, extract, save."""
    bot = request.app["bot"]
    tg_user = request.get("tg_user", {})
    user_id = tg_user.get("id", settings.admin_telegram_id)

    # Read multipart form data
    reader = await request.multipart()
    audio_data = None
    filename = "glossary.webm"

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
            update_task(task_id, step="transcribing")
            from rgo_bot.web.services.glossary_processor import process_glossary_audio

            result = await process_glossary_audio(audio_data, filename, user_id)

            if "error" in result and "orders" not in result:
                update_task(task_id, status="error", result=result)
                return

            update_task(task_id, status="done", result=result)

            # Send confirmation to bot
            orders = result.get("orders", [])
            if orders:
                target_date = result.get("target_date", "завтра")
                lines = [f"📋 <b>Глоссарий — {len(orders)} поручений на {target_date}:</b>\n"]
                for i, o in enumerate(orders, 1):
                    target = ", ".join(o["target"]) if o["target"] != ["all"] else "все РГО"
                    priority = " 🔴" if o["priority"] == "urgent" else ""
                    lines.append(f"{i}. {o['text']}{priority}\n   → {target}")
                text = "\n".join(lines)
                try:
                    await bot.send_message(user_id, text, parse_mode="HTML")
                except Exception:
                    logger.exception("glossary_bot_send_error")
            else:
                try:
                    await bot.send_message(user_id, "📋 Поручений не найдено в записи.")
                except Exception:
                    pass

        except Exception:
            logger.exception("glossary_process_error task_id={}", task_id)
            update_task(task_id, status="error", result={"error": "Ошибка обработки"})

    asyncio.create_task(_process())

    return web.json_response({"status": "processing", "task_id": task_id})
