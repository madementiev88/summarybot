from __future__ import annotations

import datetime
import time

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message
from loguru import logger
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.chat_registry import get_active_chat_ids
from rgo_bot.db.base import async_session
from rgo_bot.db.models import Message as MessageModel
from rgo_bot.db.models import MonitoredChat, Participant

# Rate limiting state
_last_report_request: float = 0.0
REPORT_COOLDOWN = 300  # 5 minutes
REPORT_CACHE_TTL = 1800  # 30 minutes

# Chat title cache: {chat_id: title}
_chat_titles: dict[int, str] = {}


async def _get_chat_title(bot, chat_id: int) -> str:
    """Get chat title: custom name from DB first, Telegram API as fallback."""
    if chat_id in _chat_titles:
        return _chat_titles[chat_id]
    # 1. Check registry (custom names from DB)
    from rgo_bot.bot.services.chat_registry import get_chat_title as registry_title
    title = registry_title(chat_id)
    if title != str(chat_id):
        _chat_titles[chat_id] = title
        return title
    # 2. Fallback to Telegram API
    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or str(chat_id)
    except Exception:
        title = str(chat_id)
    _chat_titles[chat_id] = title
    return title

router = Router(name="admin_private")


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/start")
async def cmd_start(message: Message) -> None:
    await message.answer(
        "✅ <b>Бот активирован</b>\n\n"
        f"Вы авторизованы как Начальник управления.\n"
        f"Мониторинг: {len(get_active_chat_ids())} чатов.\n"
        f"Часовой пояс: {settings.timezone}\n\n"
        f"Используйте /help для списка команд.",
        parse_mode="HTML",
    )


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/help")
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Доступные команды:</b>\n\n"
        "<b>Система</b>\n"
        "/start — Инициализация\n"
        "/help — Список команд\n"
        "/status — Статус бота\n"
        "/rgo_list — Список чатов\n\n"
        "<b>Отчёты</b>\n"
        "/report_now — Полный отчёт за сегодня\n"
        "/report YYYY-MM-DD — Отчёт за дату\n"
        "/week — Сводка за 7 дней\n\n"
        "<b>Поручения</b>\n"
        "/tasks — Открытые поручения\n"
        "/tasks_week — Статистика за неделю\n\n"
        "<b>Графики</b>\n"
        "/load — Нагрузка по чатам\n"
        "/hours — Активность по часам\n"
        "/activity — Рейтинг участников\n\n"
        "<b>Аналитика</b>\n"
        "/ask [вопрос] — Вопрос к AI по данным\n"
        "/search [текст] — Поиск по сообщениям\n"
        "/mentions — Упоминания НУ\n"
        "/forwards — Пересылки\n\n"
        "<b>Участники</b>\n"
        "/participants — Список участников с ролями\n"
        "/set_role [ID] [роль] — Назначить роль (rgo/ro/nu)\n\n"
        "<b>Настройки</b>\n"
        "/add_chat [ID] — Добавить чат в мониторинг\n"
        "/remove_chat [ID] — Убрать чат из мониторинга\n"
        "/add_keyword [слово] — Добавить триггер\n",
        parse_mode="HTML",
    )


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/status")
async def cmd_status(message: Message) -> None:
    async with async_session() as session:
        # Total messages
        result = await session.execute(select(func.count(MessageModel.id)))
        total_messages = result.scalar_one()

        # Today's messages
        today_start = datetime.datetime.now(datetime.UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = await session.execute(
            select(func.count(MessageModel.id)).where(
                MessageModel.timestamp >= today_start
            )
        )
        today_messages = result.scalar_one()

        # Unique participants
        result = await session.execute(select(func.count(Participant.user_id)))
        total_participants = result.scalar_one()

        # Messages per chat today
        chat_stats = []
        for chat_id in get_active_chat_ids():
            result = await session.execute(
                select(func.count(MessageModel.id)).where(
                    MessageModel.chat_id == chat_id,
                    MessageModel.timestamp >= today_start,
                )
            )
            count = result.scalar_one()
            title = await _get_chat_title(message.bot, chat_id)
            chat_stats.append(f"  {title}: {count} сообщ.")

    chats_text = "\n".join(chat_stats) if chat_stats else "  Нет данных"

    await message.answer(
        f"📊 <b>Статус бота</b>\n\n"
        f"Мониторинг: {len(get_active_chat_ids())} чатов\n"
        f"Всего сообщений: {total_messages}\n"
        f"Сегодня: {today_messages}\n"
        f"Участников: {total_participants}\n\n"
        f"<b>Сообщений сегодня по чатам:</b>\n{chats_text}",
        parse_mode="HTML",
    )


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/report_now")
async def cmd_report_now(message: Message) -> None:
    global _last_report_request
    now = time.monotonic()

    # Rate limiting
    if now - _last_report_request < REPORT_COOLDOWN:
        remaining = int(REPORT_COOLDOWN - (now - _last_report_request))
        await message.answer(f"⏳ Подождите {remaining} сек. перед повторным запросом.")
        return

    _last_report_request = now

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    # Check cache
    from rgo_bot.db.crud.reports import get_report_by_date

    async with async_session() as session:
        cached = await get_report_by_date(session, today, "daily")
        if (
            cached
            and cached.content_text
            and cached.created_at
            and (
                datetime.datetime.now(datetime.UTC)
                - cached.created_at.replace(tzinfo=datetime.UTC)
            ).total_seconds()
            < REPORT_CACHE_TTL
        ):
            from rgo_bot.bot.services.reporter import send_report_to_admin

            await send_report_to_admin(message.bot, cached.content_text, cached.id)
            return

    # Generate new report
    await message.answer("⏳ Генерирую отчёт, подождите ~30 сек...")

    from rgo_bot.bot.services.claude_client import BudgetExceededError
    from rgo_bot.bot.services.reporter import send_report_to_admin
    from rgo_bot.bot.services.summarizer import generate_daily_report

    try:
        result = await generate_daily_report(today, force=True)
        if result:
            async with async_session() as session:
                report = await get_report_by_date(session, today, "daily")
            report_id = report.id if report else None
            await send_report_to_admin(message.bot, result.report_text, report_id)

            # Send charts
            from rgo_bot.bot.services.chart_generator import (
                generate_activity_chart,
                generate_heatmap,
                generate_load_chart,
            )
            from rgo_bot.bot.services.chat_registry import get_all_chat_titles
            from rgo_bot.bot.services.reporter import send_chart_to_admin

            chat_titles = get_all_chat_titles()
            async with async_session() as session:
                load_chart = await generate_load_chart(session, today, chat_titles)
                if load_chart:
                    await send_chart_to_admin(message.bot, load_chart, "📊 Нагрузка по чатам")
                heatmap = await generate_heatmap(session, today, chat_titles)
                if heatmap:
                    await send_chart_to_admin(message.bot, heatmap, "🕐 Активность по часам")
                activity = await generate_activity_chart(session, today, chat_titles)
                if activity:
                    await send_chart_to_admin(message.bot, activity, "👥 Рейтинг участников")

            if result.failed_chats:
                await message.answer(
                    f"⚠️ Данные по {len(result.failed_chats)} чатам недоступны."
                )
        else:
            await message.answer("📭 Нет данных за сегодня для генерации отчёта.")
    except BudgetExceededError:
        await message.answer("💰 Дневной бюджет AI-API исчерпан.")
    except Exception as e:
        logger.exception("report_now_failed")
        await message.answer(f"❌ Ошибка генерации отчёта: {str(e)[:200]}")


@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/tasks"))
async def cmd_tasks(message: Message) -> None:
    """Show open tasks (porutcheniya)."""
    from rgo_bot.db.crud.tasks import get_open_tasks

    async with async_session() as session:
        tasks = await get_open_tasks(session)

    if not tasks:
        await message.answer("📋 Открытых поручений нет.")
        return

    lines: list[str] = ["📋 <b>Открытые поручения</b>\n"]
    for i, t in enumerate(tasks[:30], 1):
        status_icon = "🔴" if t.status == "overdue" else "🟡"
        due = f" (до {t.due_date})" if t.due_date else ""
        chat_title = await _get_chat_title(message.bot, t.chat_id)
        lines.append(
            f"{status_icon} <b>{i}.</b> {t.task_text}\n"
            f"   Чат: {chat_title}{due}\n"
            f"   Уверенность: {t.confidence:.0%}\n"
        )

    if len(tasks) > 30:
        lines.append(f"\n... и ещё {len(tasks) - 30} поручений")

    text = "\n".join(lines)
    # Split if too long
    if len(text) > 4000:
        from rgo_bot.bot.services.reporter import split_into_sections

        for section in split_into_sections(text):
            await message.answer(section, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


async def _get_chat_titles_map(bot) -> dict[int, str]:
    """Build {chat_id: title} map for all monitored chats."""
    result = {}
    for cid in get_active_chat_ids():
        result[cid] = await _get_chat_title(bot, cid)
    return result


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/load")
async def cmd_load(message: Message) -> None:
    """Send RGO load bar chart."""
    from rgo_bot.bot.services.chart_generator import generate_load_chart
    from rgo_bot.bot.services.reporter import send_chart_to_admin

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    titles = await _get_chat_titles_map(message.bot)

    async with async_session() as session:
        chart = await generate_load_chart(session, today, titles)

    if chart:
        await send_chart_to_admin(message.bot, chart, f"📊 Нагрузка по чатам — {today}")
    else:
        await message.answer("📭 Нет данных за сегодня.")


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/hours")
async def cmd_hours(message: Message) -> None:
    """Send hourly activity heatmap."""
    from rgo_bot.bot.services.chart_generator import generate_heatmap
    from rgo_bot.bot.services.reporter import send_chart_to_admin

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    titles = await _get_chat_titles_map(message.bot)

    async with async_session() as session:
        chart = await generate_heatmap(session, today, titles)

    if chart:
        await send_chart_to_admin(message.bot, chart, f"🕐 Активность по часам — {today}")
    else:
        await message.answer("📭 Нет данных за сегодня.")


@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/activity")
async def cmd_activity(message: Message) -> None:
    """Send participant activity stacked bar chart."""
    from rgo_bot.bot.services.chart_generator import generate_activity_chart
    from rgo_bot.bot.services.reporter import send_chart_to_admin

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    titles = await _get_chat_titles_map(message.bot)

    async with async_session() as session:
        chart = await generate_activity_chart(session, today, titles)

    if chart:
        await send_chart_to_admin(message.bot, chart, f"👥 Рейтинг активности — {today}")
    else:
        await message.answer("📭 Нет данных за сегодня.")


# ── Report by date ──────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/report "))
async def cmd_report_date(message: Message) -> None:
    """Generate report for a specific date: /report 2026-03-21"""
    text = message.text or ""
    date_str = text.replace("/report ", "").strip()
    try:
        report_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        await message.answer("❌ Формат даты: /report YYYY-MM-DD\nПример: /report 2026-03-21")
        return

    await message.answer(f"⏳ Генерирую отчёт за {report_date}...")

    from rgo_bot.bot.services.claude_client import BudgetExceededError
    from rgo_bot.bot.services.reporter import send_report_to_admin
    from rgo_bot.bot.services.summarizer import generate_daily_report

    try:
        result = await generate_daily_report(report_date)
        if result:
            await send_report_to_admin(message.bot, result.report_text)
        else:
            await message.answer(f"📭 Нет данных за {report_date}.")
    except BudgetExceededError:
        await message.answer("💰 Дневной бюджет AI-API исчерпан.")
    except Exception as e:
        logger.exception("report_date_failed")
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")


# ── Week summary ────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/week")
async def cmd_week(message: Message) -> None:
    """7-day summary: message counts, top participants, task stats."""
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    week_ago = today - datetime.timedelta(days=7)
    week_start = datetime.datetime.combine(week_ago, datetime.time.min, tzinfo=tz)

    async with async_session() as session:
        # Total messages this week
        result = await session.execute(
            select(func.count(MessageModel.id))
            .where(MessageModel.timestamp >= week_start)
        )
        total = result.scalar_one()

        # Per-chat breakdown
        result = await session.execute(
            select(MessageModel.chat_id, func.count(MessageModel.id))
            .where(MessageModel.timestamp >= week_start)
            .group_by(MessageModel.chat_id)
            .order_by(func.count(MessageModel.id).desc())
        )
        chat_rows = result.all()

        # Top 5 participants
        result = await session.execute(
            select(MessageModel.full_name, func.count(MessageModel.id))
            .where(MessageModel.timestamp >= week_start)
            .group_by(MessageModel.full_name)
            .order_by(func.count(MessageModel.id).desc())
            .limit(5)
        )
        top_users = result.all()

        # Per-day breakdown
        from sqlalchemy import cast, Date
        result = await session.execute(
            select(
                cast(MessageModel.timestamp, Date).label("day"),
                func.count(MessageModel.id),
            )
            .where(MessageModel.timestamp >= week_start)
            .group_by("day")
            .order_by("day")
        )
        daily_rows = result.all()

    # Format
    lines = [f"📅 <b>Сводка за 7 дней</b> ({week_ago} — {today})\n"]
    lines.append(f"Всего сообщений: <b>{total}</b>\n")

    lines.append("<b>По чатам:</b>")
    for cid, cnt in chat_rows:
        title = await _get_chat_title(message.bot, cid)
        lines.append(f"  {title}: {cnt}")

    lines.append("\n<b>Топ-5 участников:</b>")
    for i, (name, cnt) in enumerate(top_users, 1):
        lines.append(f"  {i}. {name}: {cnt} сообщ.")

    lines.append("\n<b>По дням:</b>")
    for day, cnt in daily_rows:
        weekday = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][day.weekday()]
        lines.append(f"  {day} ({weekday}): {cnt}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── Search ──────────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/search "))
async def cmd_search(message: Message) -> None:
    """Full-text search in messages: /search ключевое слово"""
    text = message.text or ""
    query = text.replace("/search ", "").strip()
    if len(query) < 2:
        await message.answer("❌ Минимум 2 символа для поиска.")
        return

    async with async_session() as session:
        result = await session.execute(
            select(MessageModel)
            .where(MessageModel.text.ilike(f"%{query}%"))
            .order_by(MessageModel.timestamp.desc())
            .limit(20)
        )
        messages_found = result.scalars().all()

    if not messages_found:
        await message.answer(f"🔍 По запросу «{query}» ничего не найдено.")
        return

    lines = [f"🔍 <b>Результаты поиска</b>: «{query}» ({len(messages_found)} совпад.)\n"]
    for msg in messages_found:
        ts = msg.timestamp.strftime("%d.%m %H:%M") if msg.timestamp else "?"
        name = msg.full_name or "?"
        snippet = (msg.text or "")[:100]
        lines.append(f"<b>{ts}</b> {name}:\n<i>{snippet}</i>\n")

    text_out = "\n".join(lines)
    if len(text_out) > 4000:
        from rgo_bot.bot.services.reporter import split_into_sections
        for section in split_into_sections(text_out):
            await message.answer(section, parse_mode="HTML")
    else:
        await message.answer(text_out, parse_mode="HTML")


# ── Ask Claude ──────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/ask "))
async def cmd_ask(message: Message) -> None:
    """Free question to Claude about today's data."""
    text = message.text or ""
    question = text.replace("/ask ", "").strip()
    if not question:
        await message.answer("❌ Использование: /ask Ваш вопрос")
        return

    await message.answer("🤔 Анализирую данные...")

    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    from rgo_bot.db.crud.messages import get_messages_for_report
    from rgo_bot.bot.services.claude_client import (
        claude_client, load_prompt, BudgetExceededError,
    )

    # Collect today's messages across all chats
    all_messages = []
    async with async_session() as session:
        for chat_id in get_active_chat_ids():
            msgs = await get_messages_for_report(session, chat_id, today, tz)
            all_messages.extend(msgs)

    if not all_messages:
        await message.answer("📭 Нет данных за сегодня для анализа.")
        return

    # Format messages
    msg_lines = []
    for msg in all_messages:
        ts = msg.timestamp.strftime("%H:%M") if msg.timestamp else "?"
        name = msg.full_name or "?"
        msg_text = msg.text or msg.voice_transcript or f"[{msg.message_type}]"
        msg_lines.append(f"[{ts}] {name}: {msg_text}")

    prompt = load_prompt("ask_question").format(
        question=question,
        date=today.isoformat(),
        messages_text="\n".join(msg_lines[-200:]),  # last 200 messages
    )

    try:
        response = await claude_client.complete(
            system_prompt=load_prompt("system"),
            user_prompt=prompt,
            max_tokens=1024,
            temperature=0.3,
            call_type="ask_question",
        )
        await message.answer(response.text, parse_mode="HTML")
    except BudgetExceededError:
        await message.answer("💰 Дневной бюджет AI-API исчерпан.")
    except Exception as e:
        logger.exception("ask_failed")
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")


# ── Mentions ────────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/mentions")
async def cmd_mentions(message: Message) -> None:
    """Show today's admin mentions."""
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    day_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)

    async with async_session() as session:
        result = await session.execute(
            select(MessageModel)
            .where(
                MessageModel.mentions_admin == True,  # noqa: E712
                MessageModel.timestamp >= day_start,
            )
            .order_by(MessageModel.timestamp.desc())
            .limit(20)
        )
        mentions = result.scalars().all()

    if not mentions:
        await message.answer("📭 Сегодня вас не упоминали в чатах.")
        return

    lines = [f"📢 <b>Упоминания НУ за {today}</b> ({len(mentions)} шт.)\n"]
    for m in mentions:
        ts = m.timestamp.strftime("%H:%M") if m.timestamp else "?"
        name = m.full_name or "?"
        title = await _get_chat_title(message.bot, m.chat_id)
        snippet = (m.text or "")[:150]
        lines.append(f"<b>{ts}</b> [{title}] {name}:\n<i>{snippet}</i>\n")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── Forwards ────────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/forwards")
async def cmd_forwards(message: Message) -> None:
    """Show today's forwarded messages stats."""
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    day_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)

    async with async_session() as session:
        result = await session.execute(
            select(MessageModel.chat_id, func.count(MessageModel.id))
            .where(
                MessageModel.is_forwarded == True,  # noqa: E712
                MessageModel.timestamp >= day_start,
            )
            .group_by(MessageModel.chat_id)
        )
        rows = result.all()

        total = sum(r[1] for r in rows)

    if not rows:
        await message.answer("📭 Сегодня пересылок не было.")
        return

    lines = [f"↪️ <b>Пересылки за {today}</b> (всего: {total})\n"]
    for cid, cnt in rows:
        title = await _get_chat_title(message.bot, cid)
        lines.append(f"  {title}: {cnt}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── RGO List ────────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/rgo_list")
async def cmd_rgo_list(message: Message) -> None:
    """List monitored chats with participant counts."""
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    day_start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)

    lines = ["📋 <b>Мониторируемые чаты</b>\n"]
    async with async_session() as session:
        for i, cid in enumerate(get_active_chat_ids(), 1):
            title = await _get_chat_title(message.bot, cid)

            # Count unique participants today
            result = await session.execute(
                select(func.count(func.distinct(MessageModel.user_id)))
                .where(
                    MessageModel.chat_id == cid,
                    MessageModel.timestamp >= day_start,
                )
            )
            participants_today = result.scalar_one()

            # Total messages today
            result = await session.execute(
                select(func.count(MessageModel.id))
                .where(
                    MessageModel.chat_id == cid,
                    MessageModel.timestamp >= day_start,
                )
            )
            msgs_today = result.scalar_one()

            lines.append(
                f"<b>{i}.</b> {title}\n"
                f"   ID: <code>{cid}</code>\n"
                f"   Сегодня: {msgs_today} сообщ., {participants_today} участн.\n"
            )

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── Add keyword ─────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/add_keyword "))
async def cmd_add_keyword(message: Message) -> None:
    """Add a trigger keyword for monitoring."""
    from rgo_bot.db.models import AlertKeyword

    text = message.text or ""
    keyword = text.replace("/add_keyword ", "").strip().lower()
    if not keyword:
        await message.answer("❌ Использование: /add_keyword слово")
        return

    async with async_session() as session:
        existing = await session.execute(
            select(AlertKeyword).where(AlertKeyword.keyword == keyword)
        )
        if existing.scalar_one_or_none():
            await message.answer(f"⚠️ Слово «{keyword}» уже в списке.")
            return

        session.add(AlertKeyword(keyword=keyword))
        await session.commit()

    await message.answer(f"✅ Слово-триггер «<b>{keyword}</b>» добавлено.", parse_mode="HTML")


# ── Tasks week stats ────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/tasks_week")
async def cmd_tasks_week(message: Message) -> None:
    """Task statistics for the last 7 days."""
    from rgo_bot.db.models import Task

    tz = ZoneInfo(settings.timezone)
    week_ago = datetime.datetime.now(tz) - datetime.timedelta(days=7)

    async with async_session() as session:
        # Created this week
        result = await session.execute(
            select(func.count(Task.task_id))
            .where(Task.detected_at >= week_ago)
        )
        created = result.scalar_one()

        # Closed this week
        result = await session.execute(
            select(func.count(Task.task_id))
            .where(Task.closed_at >= week_ago, Task.status == "closed")
        )
        closed = result.scalar_one()

        # Still open
        result = await session.execute(
            select(func.count(Task.task_id))
            .where(Task.status == "open")
        )
        open_count = result.scalar_one()

        # Overdue
        result = await session.execute(
            select(func.count(Task.task_id))
            .where(Task.status == "overdue")
        )
        overdue = result.scalar_one()

    await message.answer(
        f"📊 <b>Статистика поручений за 7 дней</b>\n\n"
        f"Создано: <b>{created}</b>\n"
        f"Закрыто: <b>{closed}</b>\n"
        f"Открыто: <b>{open_count}</b>\n"
        f"Просрочено: <b>{overdue}</b>",
        parse_mode="HTML",
    )


# ── Add/Remove chat ────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/add_chat "))
async def cmd_add_chat(message: Message) -> None:
    """Add a chat to monitoring: /add_chat -1001234567890"""
    from rgo_bot.bot.services.chat_registry import add_chat, is_monitored

    text = message.text or ""
    raw_id = text.replace("/add_chat ", "").strip()
    try:
        chat_id = int(raw_id)
    except ValueError:
        await message.answer("❌ Формат: /add_chat -100XXXXXXXXXX")
        return

    if is_monitored(chat_id):
        await message.answer(f"⚠️ Чат <code>{chat_id}</code> уже мониторится.")
        return

    # Try to get chat title from Telegram
    try:
        chat = await message.bot.get_chat(chat_id)
        title = chat.title or str(chat_id)
    except Exception:
        title = str(chat_id)

    await add_chat(chat_id, title)
    await message.answer(
        f"✅ Чат добавлен в мониторинг\n\n"
        f"Название: <b>{title}</b>\n"
        f"ID: <code>{chat_id}</code>",
        parse_mode="HTML",
    )


@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/remove_chat "))
async def cmd_remove_chat(message: Message) -> None:
    """Remove a chat from monitoring: /remove_chat -1001234567890"""
    from rgo_bot.bot.services.chat_registry import is_monitored, remove_chat

    text = message.text or ""
    raw_id = text.replace("/remove_chat ", "").strip()
    try:
        chat_id = int(raw_id)
    except ValueError:
        await message.answer("❌ Формат: /remove_chat -100XXXXXXXXXX")
        return

    if not is_monitored(chat_id):
        await message.answer(f"⚠️ Чат <code>{chat_id}</code> не в списке мониторинга.")
        return

    title = await _get_chat_title(message.bot, chat_id)
    await remove_chat(chat_id)
    await message.answer(
        f"🗑 Чат удалён из мониторинга\n\n"
        f"Название: <b>{title}</b>\n"
        f"ID: <code>{chat_id}</code>",
        parse_mode="HTML",
    )


# ── Set role ────────────────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/set_role "))
async def cmd_set_role(message: Message) -> None:
    """Assign role to participant: /set_role 123456789 rgo"""
    from rgo_bot.db.crud.participants import VALID_ROLES, set_participant_role

    text = message.text or ""
    parts = text.replace("/set_role ", "").strip().split()

    if len(parts) != 2:
        await message.answer(
            "❌ Формат: /set_role <user_id> <роль>\n"
            f"Роли: {', '.join(sorted(VALID_ROLES))}\n\n"
            "Пример: /set_role 123456789 rgo\n"
            "Используйте /participants для списка user_id",
        )
        return

    try:
        user_id = int(parts[0])
    except ValueError:
        await message.answer("❌ user_id должен быть числом.")
        return

    role = parts[1].lower()
    if role not in VALID_ROLES:
        await message.answer(f"❌ Недопустимая роль. Допустимые: {', '.join(sorted(VALID_ROLES))}")
        return

    # Check participant exists
    async with async_session() as session:
        result = await session.execute(
            select(Participant).where(Participant.user_id == user_id)
        )
        participant = result.scalar_one_or_none()

    if participant is None:
        await message.answer(f"❌ Участник с ID {user_id} не найден в базе.")
        return

    async with async_session() as session:
        updated = await set_participant_role(session, user_id, role)

    role_labels = {"rgo": "РГО", "ro": "РО", "nu": "НУ", "other": "Другое"}
    await message.answer(
        f"✅ Роль обновлена\n\n"
        f"Участник: <b>{participant.full_name}</b>\n"
        f"Роль: <b>{role_labels.get(role, role)}</b>\n"
        f"Обновлено чатов: {updated}",
        parse_mode="HTML",
    )


# ── Participants list ───────────────────────────────────────

@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/participants")
async def cmd_participants(message: Message) -> None:
    """List participants with their roles and user_ids."""
    from rgo_bot.db.crud.participants import get_all_participants

    async with async_session() as session:
        data = await get_all_participants(session)

    if not data:
        await message.answer("📭 Участников пока нет.")
        return

    role_icons = {"rgo": "🟢", "ro": "🔵", "nu": "👑", "other": "⚪"}
    lines = ["👥 <b>Участники</b>\n"]

    for p, chats in data:
        roles = set(c.role for c in chats)
        role_str = ", ".join(sorted(roles))
        icon = role_icons.get(list(roles)[0] if roles else "other", "⚪")
        lines.append(
            f"{icon} <b>{p.full_name}</b>\n"
            f"   ID: <code>{p.user_id}</code> | Роль: {role_str}\n"
            f"   Сообщений: {p.total_messages}\n"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        from rgo_bot.bot.services.reporter import split_into_sections
        for section in split_into_sections(text):
            await message.answer(section, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


# ── Voice message handler for KOS ────────────────────────────────


@router.message(F.chat.type == ChatType.PRIVATE, F.voice)
async def handle_voice_for_kos(message: Message) -> None:
    """Handle voice messages sent to bot — summarize as meeting via KOS."""
    await message.answer("⏳ Обрабатываю голосовое сообщение (КОС)...")

    from rgo_bot.web.services.meeting_summarizer import summarize_voice_message

    result = await summarize_voice_message(
        bot=message.bot,
        file_id=message.voice.file_id,
        duration=message.voice.duration or 0,
    )

    if result.get("error"):
        await message.answer(f"❌ {result['error']}")


@router.message(F.chat.type == ChatType.PRIVATE, F.video_note)
async def handle_video_note_for_kos(message: Message) -> None:
    """Handle video notes (circles) sent to bot — summarize as meeting via KOS."""
    await message.answer("⏳ Обрабатываю видеосообщение (КОС)...")

    from rgo_bot.web.services.meeting_summarizer import summarize_voice_message

    result = await summarize_voice_message(
        bot=message.bot,
        file_id=message.video_note.file_id,
        duration=message.video_note.duration or 0,
    )

    if result.get("error"):
        await message.answer(f"❌ {result['error']}")
