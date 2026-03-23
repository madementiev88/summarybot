"""Meeting audio summarization service.

Audio bytes → Whisper transcription → Claude summary → send to bot.
Used by both Mini App upload and Telegram voice messages.
"""
from __future__ import annotations

import io
from decimal import Decimal

from aiogram import Bot
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.api_usage import log_api_usage
from rgo_bot.web.app import update_task

WHISPER_PRICE_PER_MIN = Decimal("0.006")


async def summarize_audio_bytes(
    audio_data: bytes,
    filename: str,
    bot: Bot,
    source: str = "miniapp",
    task_id: str | None = None,
) -> dict:
    """Transcribe audio and generate meeting summary.

    Args:
        audio_data: raw audio bytes
        filename: original filename (for Whisper format detection)
        bot: aiogram Bot instance
        source: "miniapp" or "voice_msg"
        task_id: optional task_id for progress updates

    Returns:
        dict with transcript and summary text
    """
    # Step 1: Transcribe via Whisper
    if task_id:
        update_task(task_id, step="transcribing")

    transcript = await _transcribe_bytes(audio_data, filename)
    if not transcript:
        error_msg = "Не удалось расшифровать аудио"
        await bot.send_message(settings.admin_telegram_id, f"❌ {error_msg}")
        return {"error": error_msg}

    # Step 2: Summarize via Claude
    if task_id:
        update_task(task_id, step="summarizing")

    summary = await _summarize_transcript(transcript)
    if not summary:
        # Fallback: send just the transcript
        await bot.send_message(
            settings.admin_telegram_id,
            f"📝 <b>Расшифровка (без AI-резюме):</b>\n\n{transcript[:3800]}",
        )
        return {"transcript": transcript, "summary": None}

    # Step 3: Send result to bot
    await bot.send_message(
        settings.admin_telegram_id,
        f"📋 <b>Резюме совещания (КОС)</b>\n\n{summary}",
    )

    # Step 4: Save to DB
    await _save_meeting_summary(
        user_id=settings.admin_telegram_id,
        transcript=transcript,
        summary=summary,
        source=source,
        duration_sec=0,  # Unknown for raw bytes
    )

    return {"transcript": transcript, "summary": summary}


async def summarize_voice_message(
    bot: Bot,
    file_id: str,
    duration: int,
) -> dict:
    """Handle voice message sent directly to bot.

    Downloads file from Telegram, then delegates to summarize_audio_bytes.
    """
    try:
        file = await bot.get_file(file_id)
        if not file.file_path:
            return {"error": "Не удалось получить файл"}

        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        audio_data = buf.getvalue()

        result = await summarize_audio_bytes(
            audio_data, "voice.ogg", bot, source="voice_msg"
        )
        return result
    except Exception:
        logger.exception("kos_voice_msg_error")
        return {"error": "Ошибка обработки голосового сообщения"}


async def _transcribe_bytes(audio_data: bytes, filename: str) -> str | None:
    """Transcribe audio bytes via Groq Whisper API."""
    if not settings.groq_api_key:
        logger.warning("kos_whisper_skip no_groq_key")
        return None

    try:
        import openai
        import httpx

        buf = io.BytesIO(audio_data)
        buf.name = filename

        http_client = None
        if settings.groq_proxy_url:
            http_client = httpx.AsyncClient(proxy=settings.groq_proxy_url)

        client = openai.AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            http_client=http_client,
        )
        transcript = await client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=buf,
            language="ru",
        )

        text = transcript.text.strip()
        if not text:
            return None

        async with async_session() as session:
            await log_api_usage(
                session,
                provider="groq",
                call_type="kos_transcription",
                tokens_in=0,
                tokens_out=0,
                estimated_cost_usd=Decimal("0"),
            )

        logger.info("kos_transcribed chars={} via=groq", len(text))
        return text

    except Exception:
        logger.exception("kos_transcription_error")
        return None


async def _summarize_transcript(transcript: str) -> str | None:
    """Summarize meeting transcript via Claude."""
    try:
        from rgo_bot.bot.services.claude_client import claude_client, load_prompt

        system_prompt = load_prompt("system")
        user_prompt = load_prompt("meeting_summary").format(transcript=transcript)

        response = await claude_client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=2048,
            call_type="meeting_summary",
        )
        return response.text

    except Exception:
        logger.exception("kos_summarize_error")
        return None


async def _save_meeting_summary(
    user_id: int,
    transcript: str,
    summary: str,
    source: str,
    duration_sec: int,
) -> None:
    """Save meeting summary to database."""
    try:
        from rgo_bot.db.models import MeetingSummary

        async with async_session() as session:
            session.add(MeetingSummary(
                user_id=user_id,
                audio_duration_sec=duration_sec,
                transcript_text=transcript,
                summary_text=summary,
                source=source,
            ))
            await session.commit()
    except Exception:
        logger.exception("kos_save_error")
