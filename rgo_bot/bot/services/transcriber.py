"""Voice/video note transcription via Groq Whisper API.

- Downloads voice/video_note from Telegram (max 20 MB)
- Sends to Groq Whisper API for transcription (free tier)
- Returns text or None on failure
- Logs usage to api_usage table
"""
from __future__ import annotations

import io
from decimal import Decimal

from aiogram import Bot
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.api_usage import log_api_usage

# Groq Whisper is free, but track usage anyway
WHISPER_PRICE_PER_MIN = Decimal("0.000")
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB (Telegram Bot API limit)


async def transcribe_voice(bot: Bot, file_id: str, duration: int = 0) -> str | None:
    """Download voice/video_note from Telegram and transcribe via Whisper.

    Args:
        bot: aiogram Bot instance
        file_id: Telegram file_id of voice/video_note
        duration: duration in seconds (for cost estimation)

    Returns:
        Transcribed text or None if failed/disabled
    """
    if not settings.whisper_enabled:
        return None

    if not settings.groq_api_key:
        logger.debug("whisper_skip no_groq_key")
        return None

    try:
        # Get file info
        file = await bot.get_file(file_id)
        if not file.file_path:
            logger.warning("whisper_skip no_file_path file_id={}", file_id)
            return None

        if file.file_size and file.file_size > MAX_FILE_SIZE:
            logger.warning(
                "whisper_skip file_too_large size={} limit={}",
                file.file_size, MAX_FILE_SIZE,
            )
            return None

        # Download file to memory
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        buf.seek(0)
        buf.name = "voice.ogg"  # Whisper needs a filename with extension

        # Call Groq Whisper API (OpenAI-compatible)
        import openai

        client = openai.AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        transcript = await client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=buf,
            language="ru",
        )

        text = transcript.text.strip()
        if not text:
            return None

        # Log usage
        duration_min = max(duration / 60, 0.01)
        cost = WHISPER_PRICE_PER_MIN * Decimal(str(duration_min))
        async with async_session() as session:
            await log_api_usage(
                session,
                provider="groq",
                call_type="whisper_transcription",
                tokens_in=0,
                tokens_out=0,
                estimated_cost_usd=cost,
            )

        logger.info(
            "whisper_transcribed duration={}s chars={} via=groq",
            duration, len(text),
        )
        return text

    except Exception:
        logger.exception("whisper_error file_id={}", file_id)
        return None
