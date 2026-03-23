"""Presentation generation service.

Voice → Whisper → Claude (plan + content) → python-pptx → send PPTX via bot.
"""
from __future__ import annotations

import io
import json

from aiogram import Bot
from aiogram.types import BufferedInputFile
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.web.app import update_task


async def generate_presentation(
    audio_data: bytes,
    filename: str,
    bot: Bot,
    user_id: int,
    task_id: str,
) -> dict:
    """Full pipeline: voice → transcript → plan → slides → PPTX.

    Args:
        audio_data: raw audio bytes with presentation requirements
        filename: original filename
        bot: aiogram Bot instance
        user_id: Telegram user ID
        task_id: task ID for progress updates

    Returns:
        dict with status
    """
    # Step 1: Transcribe
    update_task(task_id, step="transcribing")
    from rgo_bot.web.services.meeting_summarizer import _transcribe_bytes

    transcript = await _transcribe_bytes(audio_data, filename)
    if not transcript:
        await bot.send_message(
            settings.admin_telegram_id,
            "❌ Не удалось расшифровать аудио для презентации",
        )
        return {"error": "Transcription failed"}

    # Step 2: Load user preferences
    from rgo_bot.db.crud.presentation_preferences import get_preferences

    async with async_session() as session:
        prefs_record = await get_preferences(session, user_id)

    prefs_json = prefs_record.preferences_json if prefs_record else {}

    # Step 3: Generate presentation plan via Claude
    update_task(task_id, step="planning")
    plan = await _generate_plan(transcript, prefs_json)
    if not plan:
        await bot.send_message(
            settings.admin_telegram_id,
            "❌ Ошибка планирования презентации",
        )
        return {"error": "Planning failed"}

    # Step 4: Generate slide content
    update_task(task_id, step="generating")
    slides = await _generate_slide_content(plan)
    if not slides:
        await bot.send_message(
            settings.admin_telegram_id,
            "❌ Ошибка генерации контента слайдов",
        )
        return {"error": "Slide content generation failed"}

    # Step 5: Build PPTX
    update_task(task_id, step="building_pptx")
    pptx_bytes = _build_pptx(plan, slides)

    # Step 6: Send via bot
    title = plan.get("title", "Презентация")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:50]
    doc = BufferedInputFile(
        file=pptx_bytes,
        filename=f"{safe_title or 'Презентация'}.pptx",
    )
    await bot.send_document(
        settings.admin_telegram_id,
        document=doc,
        caption=f"📑 <b>{title}</b>\n\nСлайдов: {len(slides)}",
    )

    # Step 7: Save updated preferences
    updated_prefs = plan.get("updated_preferences", {})
    if updated_prefs:
        from rgo_bot.db.crud.presentation_preferences import upsert_preferences

        async with async_session() as session:
            await upsert_preferences(session, user_id, updated_prefs)

    return {"status": "ok", "title": title, "slides": len(slides)}


async def _generate_plan(transcript: str, preferences: dict) -> dict | None:
    """Generate presentation plan via Claude."""
    try:
        from rgo_bot.bot.services.claude_client import claude_client, load_prompt

        system_prompt = load_prompt("system")
        user_prompt = load_prompt("presentation_plan").format(
            user_request=transcript,
            preferences=json.dumps(preferences, ensure_ascii=False) if preferences else "Нет",
        )

        response = await claude_client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=2048,
            temperature=0.4,
            call_type="presentation_plan",
        )

        # Parse JSON from response
        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        return json.loads(text)

    except (json.JSONDecodeError, Exception):
        logger.exception("preza_plan_error")
        return None


async def _generate_slide_content(plan: dict) -> list[dict] | None:
    """Generate detailed slide content via Claude."""
    try:
        from rgo_bot.bot.services.claude_client import claude_client, load_prompt

        system_prompt = load_prompt("system")
        user_prompt = load_prompt("presentation_slides").format(
            plan_json=json.dumps(plan, ensure_ascii=False, indent=2)
        )

        response = await claude_client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4096,
            temperature=0.3,
            call_type="presentation_slides",
        )

        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        return json.loads(text)

    except (json.JSONDecodeError, Exception):
        logger.exception("preza_slides_error")
        return None


def _build_pptx(plan: dict, slides: list[dict]) -> bytes:
    """Build PPTX file from plan and slide content.

    Returns bytes of the PPTX file.
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Color scheme
    style = plan.get("style", "corporate")
    if style == "minimal":
        bg_color = RGBColor(0xFF, 0xFF, 0xFF)
        title_color = RGBColor(0x1A, 0x1A, 0x1A)
        text_color = RGBColor(0x33, 0x33, 0x33)
        accent_color = RGBColor(0x1A, 0x8C, 0x36)
    elif style == "creative":
        bg_color = RGBColor(0xF0, 0xF5, 0xF0)
        title_color = RGBColor(0x0D, 0x47, 0xA1)
        text_color = RGBColor(0x33, 0x33, 0x33)
        accent_color = RGBColor(0x0D, 0x47, 0xA1)
    else:  # corporate
        bg_color = RGBColor(0xFF, 0xFF, 0xFF)
        title_color = RGBColor(0x1A, 0x1A, 0x1A)
        text_color = RGBColor(0x44, 0x44, 0x44)
        accent_color = RGBColor(0x1A, 0x8C, 0x36)

    # Title slide
    title_text = plan.get("title", "Презентация")
    subtitle_text = plan.get("subtitle", "")

    slide_layout = prs.slide_layouts[6]  # Blank
    slide = prs.slides.add_slide(slide_layout)

    # Background
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = bg_color

    # Title
    left = Inches(1)
    top = Inches(2.5)
    width = Inches(11)
    height = Inches(1.5)
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title_text
    p.font.size = Pt(40)
    p.font.bold = True
    p.font.color.rgb = title_color
    p.alignment = PP_ALIGN.CENTER

    if subtitle_text:
        p2 = tf.add_paragraph()
        p2.text = subtitle_text
        p2.font.size = Pt(20)
        p2.font.color.rgb = text_color
        p2.alignment = PP_ALIGN.CENTER
        p2.space_before = Pt(12)

    # Content slides
    for slide_data in slides:
        slide = prs.slides.add_slide(slide_layout)

        # Background
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = bg_color

        # Accent line at top
        from pptx.shapes.autoshape import Shape
        line_shape = slide.shapes.add_shape(
            1,  # Rectangle
            Inches(0), Inches(0),
            prs.slide_width, Inches(0.05),
        )
        line_shape.fill.solid()
        line_shape.fill.fore_color.rgb = accent_color
        line_shape.line.fill.background()

        # Slide title
        stitle = slide_data.get("title", "")
        left = Inches(0.8)
        top = Inches(0.5)
        width = Inches(11.5)
        height = Inches(0.8)
        txBox = slide.shapes.add_textbox(left, top, width, height)
        tf = txBox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = stitle
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = title_color

        # Content
        body_text = slide_data.get("body_text", "")
        bullets = slide_data.get("bullet_points", [])

        content_top = Inches(1.5)
        content_height = Inches(5)
        txBox = slide.shapes.add_textbox(left, content_top, width, content_height)
        tf = txBox.text_frame
        tf.word_wrap = True

        if body_text:
            p = tf.paragraphs[0]
            p.text = body_text
            p.font.size = Pt(18)
            p.font.color.rgb = text_color
            p.space_after = Pt(12)

        for i, bullet in enumerate(bullets):
            p = tf.add_paragraph() if (body_text or i > 0) else tf.paragraphs[0]
            p.text = f"• {bullet}"
            p.font.size = Pt(16)
            p.font.color.rgb = text_color
            p.space_before = Pt(6)
            p.space_after = Pt(6)

        # Speaker notes
        notes = slide_data.get("speaker_notes", "")
        if notes:
            notes_slide = slide.notes_slide
            notes_tf = notes_slide.notes_text_frame
            notes_tf.text = notes

    # Save to bytes
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
