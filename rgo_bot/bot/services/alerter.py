"""Real-time alerts for admin (НУ).

Alert types and weekend behavior:
  admin_mention     — immediate, works on weekends
  conflict_detected — <= 5 min, works on weekends
  silence_rgo       — hourly check, OFF on weekends
  mass_forward      — <= 5 min, works on weekends
  keyword_hit       — immediate, works on weekends
  task_overdue      — 12:00/18:00, OFF on weekends
  self_control_low  — 19:00, OFF on weekends
  bot_removed       — immediate, works on weekends (handled in group_messages.py)
"""
from __future__ import annotations

import datetime
from collections import defaultdict

from aiogram import Bot
from loguru import logger
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.chat_registry import get_active_chat_ids
from rgo_bot.db.base import async_session
from rgo_bot.db.models import AdminAlert, AlertKeyword, Message

# ── In-memory state ────────────────────────────────────────
# Track forwards per chat in sliding window: {chat_id: [timestamps]}
_forward_tracker: dict[int, list[datetime.datetime]] = defaultdict(list)

# Track last message time per chat for silence detection
_last_activity: dict[int, datetime.datetime] = {}


def _is_weekend() -> bool:
    tz = ZoneInfo(settings.timezone)
    return datetime.datetime.now(tz).isoweekday() not in settings.work_days


async def _save_and_send_alert(
    bot: Bot,
    alert_type: str,
    chat_id: int,
    description: str,
    trigger_message_id: int | None = None,
) -> None:
    """Save alert to DB and send to admin."""
    async with async_session() as session:
        alert = AdminAlert(
            alert_type=alert_type,
            chat_id=chat_id,
            trigger_message_id=trigger_message_id,
            description=description,
        )
        session.add(alert)
        await session.commit()

    try:
        await bot.send_message(
            settings.admin_telegram_id,
            description,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("alert_send_failed type={}", alert_type)

    logger.info("alert_sent type={} chat_id={}", alert_type, chat_id)


# ── Real-time alerts (called per message) ──────────────────

async def check_realtime_alerts(
    bot: Bot,
    chat_id: int,
    message_id: int,
    user_name: str,
    text: str | None,
    mentions_admin: bool,
    is_forwarded: bool,
    timestamp: datetime.datetime,
    chat_title: str,
) -> None:
    """Check all real-time alerts for a single incoming message."""
    # Update last activity
    _last_activity[chat_id] = timestamp

    # 1. Admin mention
    if mentions_admin:
        snippet = (text or "")[:200]
        await _save_and_send_alert(
            bot,
            alert_type="admin_mention",
            chat_id=chat_id,
            trigger_message_id=message_id,
            description=(
                f"📢 <b>Упоминание НУ</b>\n\n"
                f"Чат: {chat_title}\n"
                f"От: {user_name}\n"
                f"Текст: <i>{snippet}</i>"
            ),
        )

    # 2. Keyword hit
    if text:
        await _check_keyword_hit(bot, chat_id, message_id, user_name, text, chat_title)

    # 3. Mass forward detection
    if is_forwarded:
        await _check_mass_forward(bot, chat_id, message_id, timestamp, chat_title)


async def _check_keyword_hit(
    bot: Bot,
    chat_id: int,
    message_id: int,
    user_name: str,
    text: str,
    chat_title: str,
) -> None:
    """Check if message contains any trigger keywords."""
    async with async_session() as session:
        result = await session.execute(
            select(AlertKeyword.keyword)
            .where(AlertKeyword.is_active == True)  # noqa: E712
        )
        keywords = [r[0] for r in result.all()]

    if not keywords:
        return

    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            snippet = text[:200]
            await _save_and_send_alert(
                bot,
                alert_type="keyword_hit",
                chat_id=chat_id,
                trigger_message_id=message_id,
                description=(
                    f"🔑 <b>Слово-триггер: «{kw}»</b>\n\n"
                    f"Чат: {chat_title}\n"
                    f"От: {user_name}\n"
                    f"Текст: <i>{snippet}</i>"
                ),
            )
            break  # one alert per message


async def _check_mass_forward(
    bot: Bot,
    chat_id: int,
    message_id: int,
    timestamp: datetime.datetime,
    chat_title: str,
) -> None:
    """Alert if > N forwards in 60 min window."""
    window = datetime.timedelta(minutes=60)
    now = timestamp

    # Clean old entries
    _forward_tracker[chat_id] = [
        ts for ts in _forward_tracker[chat_id] if now - ts < window
    ]
    _forward_tracker[chat_id].append(now)

    count = len(_forward_tracker[chat_id])
    if count == settings.mass_forward_threshold + 1:  # alert once
        await _save_and_send_alert(
            bot,
            alert_type="mass_forward",
            chat_id=chat_id,
            trigger_message_id=message_id,
            description=(
                f"↪️ <b>Массовые пересылки</b>\n\n"
                f"Чат: {chat_title}\n"
                f"Пересылок за час: <b>{count}</b>\n"
                f"Порог: {settings.mass_forward_threshold}"
            ),
        )


# ── Scheduled alerts ───────────────────────────────────────

async def check_silence_alerts(bot: Bot) -> None:
    """Check if any RGO chat is silent > N hours during work time.
    OFF on weekends.
    """
    if _is_weekend():
        return

    tz = ZoneInfo(settings.timezone)
    now = datetime.datetime.now(tz)

    # Only check during work hours
    if not (settings.silence_work_start <= now.hour < settings.silence_work_end):
        return

    threshold = datetime.timedelta(hours=settings.silence_alert_hours)

    for chat_id in get_active_chat_ids():
        last = _last_activity.get(chat_id)

        if last is None:
            # No data yet — query DB for last message
            async with async_session() as session:
                result = await session.execute(
                    select(func.max(Message.timestamp))
                    .where(Message.chat_id == chat_id)
                )
                last = result.scalar_one_or_none()
                if last:
                    _last_activity[chat_id] = last

        if last is None:
            continue

        silence_duration = now - last.astimezone(tz)
        if silence_duration > threshold:
            hours = int(silence_duration.total_seconds() / 3600)
            from rgo_bot.bot.services.chat_registry import get_chat_title
            chat_title = get_chat_title(chat_id)

            await _save_and_send_alert(
                bot,
                alert_type="silence_rgo",
                chat_id=chat_id,
                description=(
                    f"🔇 <b>Тишина в чате</b>\n\n"
                    f"Чат: {chat_title}\n"
                    f"Молчит уже: <b>{hours}ч</b>\n"
                    f"Порог: {settings.silence_alert_hours}ч"
                ),
            )
            # Reset to avoid repeated alerts
            _last_activity[chat_id] = now


async def check_overdue_tasks(bot: Bot) -> None:
    """Mark overdue tasks and alert admin. OFF on weekends."""
    if _is_weekend():
        return

    from rgo_bot.db.crud.tasks import get_open_tasks, update_task_status

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    async with async_session() as session:
        open_tasks = await get_open_tasks(session)

    overdue_tasks = [
        t for t in open_tasks
        if t.due_date and t.due_date < today and t.status != "overdue"
    ]

    if not overdue_tasks:
        return

    async with async_session() as session:
        for task in overdue_tasks:
            await update_task_status(session, task.task_id, "overdue")

    lines = [f"⏰ <b>Просроченные поручения: {len(overdue_tasks)}</b>\n"]
    for t in overdue_tasks[:10]:
        lines.append(
            f"• {t.task_text[:100]}\n"
            f"  Дедлайн: {t.due_date}"
        )

    await _save_and_send_alert(
        bot,
        alert_type="task_overdue",
        chat_id=0,
        description="\n".join(lines),
    )
