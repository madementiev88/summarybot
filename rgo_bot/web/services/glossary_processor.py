"""Glossary order processing: Audio → Whisper → Claude → DB.

Reuses transcription from meeting_summarizer and Claude client.
"""
from __future__ import annotations

import datetime
import json

from loguru import logger
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.chat_registry import get_all_chat_titles
from rgo_bot.bot.services.claude_client import claude_client, load_prompt
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.glossary_orders import create_order
from rgo_bot.web.services.meeting_summarizer import _transcribe_bytes


async def process_glossary_audio(
    audio_data: bytes,
    filename: str,
    user_id: int,
) -> dict:
    """Process glossary audio: transcribe → extract orders → save to DB.

    Returns dict with transcript and list of saved orders.
    """
    # 1. Transcribe
    transcript = await _transcribe_bytes(audio_data, filename)
    if not transcript:
        return {"error": "Не удалось расшифровать аудио"}

    logger.info("glossary_transcribed length={}", len(transcript))

    # 2. Build RGO list for prompt
    chat_titles = get_all_chat_titles()
    rgo_list = "\n".join(
        f"- {title} (chat_id: {cid})"
        for cid, title in chat_titles.items()
    )

    # 3. Extract orders via Claude
    prompt = load_prompt("glossary_order").format(
        transcript=transcript,
        rgo_list=rgo_list,
    )

    response = await claude_client.complete(
        system_prompt=load_prompt("system"),
        user_prompt=prompt,
        max_tokens=1024,
        temperature=0.2,
        call_type="glossary_order",
    )

    # 4. Parse JSON response
    try:
        raw_text = response.text.strip()
        # Extract JSON from possible markdown code blocks
        if "```" in raw_text:
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()
        orders_data = json.loads(raw_text)
    except (json.JSONDecodeError, IndexError):
        logger.error("glossary_parse_error response={}", response.text[:200])
        return {"error": "Не удалось разобрать поручения", "transcript": transcript}

    if not orders_data:
        return {"transcript": transcript, "orders": [], "message": "Поручений не найдено"}

    # 5. Resolve target RGO names to chat_ids
    title_to_id = {title: cid for cid, title in chat_titles.items()}

    # 6. Determine target date (tomorrow by default)
    tz = ZoneInfo(settings.timezone)
    tomorrow = (datetime.datetime.now(tz) + datetime.timedelta(days=1)).date()

    # 7. Save to DB
    saved_orders = []
    async with async_session() as session:
        for item in orders_data:
            order_text = item.get("order_text", "").strip()
            if not order_text:
                continue

            target_rgos = item.get("target_rgos", ["all"])
            priority = item.get("priority", "normal")

            # Resolve names to chat_ids
            target_ids = None  # None means all
            if target_rgos and target_rgos != ["all"]:
                resolved = []
                for name in target_rgos:
                    for title, cid in title_to_id.items():
                        if name.lower() in title.lower():
                            resolved.append(cid)
                            break
                if resolved:
                    target_ids = resolved

            order = await create_order(
                session,
                user_id=user_id,
                transcript_text=transcript,
                order_text=f"{'🔴 ' if priority == 'urgent' else ''}{order_text}",
                target_rgo_ids=target_ids,
                target_date=tomorrow,
            )
            saved_orders.append({
                "id": order.id,
                "text": order_text,
                "target": target_rgos,
                "priority": priority,
            })

    logger.info("glossary_saved count={} target_date={}", len(saved_orders), tomorrow)

    return {
        "transcript": transcript,
        "orders": saved_orders,
        "target_date": str(tomorrow),
    }
