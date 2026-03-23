from __future__ import annotations

import asyncio
import io

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.reports import mark_report_sent

SECTION_LIMIT = 4000
SEND_DELAY = 0.3  # seconds between messages


async def send_report_to_admin(
    bot: Bot,
    report_text: str,
    report_id: int | None = None,
) -> bool:
    sections = split_into_sections(report_text)
    recipients = settings.report_recipients
    logger.info("sending_report sections={} recipients={}", len(sections), len(recipients))

    for recipient_id in recipients:
        for i, section in enumerate(sections):
            await _send_section(bot, recipient_id, section)
            if i < len(sections) - 1:
                await asyncio.sleep(SEND_DELAY)

    # Mark as sent in DB
    if report_id is not None:
        async with async_session() as session:
            await mark_report_sent(session, report_id)

    logger.info("report_sent sections={}", len(sections))
    return True


def split_into_sections(text: str, limit: int = SECTION_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    paragraphs = text.split("\n\n")
    sections: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for \n\n separator

        if current_len + para_len > limit and current:
            sections.append("\n\n".join(current))
            current = []
            current_len = 0

        # Single paragraph exceeds limit — split by lines
        if len(para) > limit:
            if current:
                sections.append("\n\n".join(current))
                current = []
                current_len = 0
            for line_section in _split_by_lines(para, limit):
                sections.append(line_section)
            continue

        current.append(para)
        current_len += para_len

    if current:
        sections.append("\n\n".join(current))

    return sections


def _split_by_lines(text: str, limit: int) -> list[str]:
    lines = text.split("\n")
    sections: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for \n

        if current_len + line_len > limit and current:
            sections.append("\n".join(current))
            current = []
            current_len = 0

        # Single line exceeds limit — hard cut into chunks
        if len(line) > limit:
            if current:
                sections.append("\n".join(current))
                current = []
                current_len = 0
            for i in range(0, len(line), limit):
                sections.append(line[i : i + limit])
            continue

        current.append(line)
        current_len += line_len

    if current:
        sections.append("\n".join(current))

    return sections


async def send_chart_to_admin(
    bot: Bot,
    chart_bytes: "io.BytesIO",
    caption: str = "",
) -> None:
    """Send a chart image to admin via send_photo."""
    from aiogram.types import BufferedInputFile

    for recipient_id in settings.report_recipients:
        chart_bytes.seek(0)
        photo = BufferedInputFile(chart_bytes.read(), filename="chart.png")
        try:
            await bot.send_photo(
                recipient_id,
                photo=photo,
                caption=caption[:1024] if caption else None,
                parse_mode="HTML",
            )
        except TelegramRetryAfter as e:
            logger.warning("telegram_flood_control retry_after={}s", e.retry_after)
            await asyncio.sleep(e.retry_after)
            chart_bytes.seek(0)
            await bot.send_photo(
                recipient_id,
                photo=BufferedInputFile(chart_bytes.read(), filename="chart.png"),
                caption=caption[:1024] if caption else None,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("chart_send_failed recipient={}", recipient_id)


async def _send_section(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except TelegramRetryAfter as e:
        logger.warning("telegram_flood_control retry_after={}s", e.retry_after)
        await asyncio.sleep(e.retry_after)
        await bot.send_message(chat_id, text, parse_mode="HTML")
