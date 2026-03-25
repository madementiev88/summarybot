from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func, select

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.chat_registry import get_active_chat_ids, get_chat_title as registry_get_title
from rgo_bot.bot.services.claude_client import (
    BudgetExceededError,
    ClaudeResponse,
    claude_client,
    load_prompt,
)
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.messages import get_messages_for_report
from rgo_bot.db.crud.reports import (
    get_chat_summaries_by_date,
    get_report_by_date,
    save_daily_report,
)
from rgo_bot.db.models import DailyReport, Message, Participant


@dataclass
class ChatSummaryResult:
    chat_id: int
    chat_title: str
    summary: str | None
    messages_count: int
    error: str | None = None


@dataclass
class DailyReportResult:
    report_text: str
    chat_summaries: list[ChatSummaryResult]
    total_messages: int
    total_participants: int
    failed_chats: list[int] = field(default_factory=list)


async def generate_daily_report(
    report_date: datetime.date,
    force: bool = False,
) -> DailyReportResult | None:
    tz = ZoneInfo(settings.timezone)

    # Idempotency: check if report already exists (skip if force=True)
    if not force:
        async with async_session() as session:
            existing = await get_report_by_date(session, report_date, "daily")
            if existing and existing.content_text:
                logger.info("daily_report already_exists date={}", report_date)
                return DailyReportResult(
                    report_text=existing.content_text,
                    chat_summaries=[],
                    total_messages=0,
                    total_participants=0,
                )

    # Clear old cache when force regenerating
    if force:
        from sqlalchemy import delete as sa_delete

        async with async_session() as session:
            await session.execute(
                sa_delete(DailyReport).where(
                    DailyReport.report_date == report_date,
                )
            )
            await session.commit()

    # MAP phase
    summaries = await _map_phase(report_date, tz)

    # Check if we have any data
    total_messages = sum(s.messages_count for s in summaries)
    if total_messages == 0:
        logger.info("daily_report no_messages date={}", report_date)
        return None

    # REDUCE phase
    report_text = await _reduce_phase(summaries, report_date, tz)

    # Count stats
    async with async_session() as session:
        day_start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=tz)
        day_end = datetime.datetime.combine(
            report_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
        )
        result = await session.execute(
            select(func.count(func.distinct(Message.user_id))).where(
                Message.timestamp >= day_start,
                Message.timestamp < day_end,
                Message.chat_id.in_(get_active_chat_ids()),
            )
        )
        total_participants = result.scalar_one()

    # Save final report
    async with async_session() as session:
        await save_daily_report(
            session,
            report_date=report_date,
            report_type="daily",
            content_text=report_text,
            stats_json={
                "total_messages": total_messages,
                "total_participants": total_participants,
                "chats_processed": len([s for s in summaries if s.error is None]),
                "chats_failed": len([s for s in summaries if s.error is not None]),
            },
        )

    failed_chats = [s.chat_id for s in summaries if s.error is not None]

    return DailyReportResult(
        report_text=report_text,
        chat_summaries=summaries,
        total_messages=total_messages,
        total_participants=total_participants,
        failed_chats=failed_chats,
    )


async def _map_phase(
    report_date: datetime.date,
    tz: ZoneInfo,
) -> list[ChatSummaryResult]:
    # Load cached MAP results from previous (failed) run
    async with async_session() as session:
        cached = await get_chat_summaries_by_date(session, report_date)
    cached_chat_ids = {r.chat_id for r in cached if r.chat_id is not None}

    system_prompt = load_prompt("system")
    chat_prompt_template = load_prompt("chat_summary")

    semaphore = asyncio.Semaphore(2)  # Max 2 parallel to avoid API overload

    async def bounded_summarize(chat_id: int, idx: int) -> ChatSummaryResult:
        # Stagger requests to reduce API pressure
        if idx > 0:
            await asyncio.sleep(idx * 1.5)
        async with semaphore:
            return await _summarize_single_chat(
                chat_id, report_date, tz, system_prompt, chat_prompt_template
            )

    # Only process chats not already cached
    tasks = []
    for idx, chat_id in enumerate(get_active_chat_ids()):
        if chat_id in cached_chat_ids:
            continue
        tasks.append(bounded_summarize(chat_id, idx))

    new_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Combine cached + new results
    results: list[ChatSummaryResult] = []

    # Add cached
    for r in cached:
        if r.chat_id is not None:
            results.append(
                ChatSummaryResult(
                    chat_id=r.chat_id,
                    chat_title=_get_chat_title(r.chat_id),
                    summary=r.content_text,
                    messages_count=0,  # Already processed
                )
            )

    # Add new
    for res in new_results:
        if isinstance(res, Exception):
            logger.exception("map_phase_error: {}", res)
            continue
        results.append(res)

    return results


async def _summarize_single_chat(
    chat_id: int,
    report_date: datetime.date,
    tz: ZoneInfo,
    system_prompt: str,
    chat_prompt_template: str,
) -> ChatSummaryResult:
    chat_title = _get_chat_title(chat_id)

    try:
        # Get messages from DB
        async with async_session() as session:
            messages = await get_messages_for_report(session, chat_id, report_date, tz)

        if not messages:
            summary = "Сообщений за день не было."
            # Save to cache
            async with async_session() as session:
                await save_daily_report(
                    session,
                    report_date=report_date,
                    report_type="chat_summary",
                    content_text=summary,
                    chat_id=chat_id,
                )
            return ChatSummaryResult(
                chat_id=chat_id,
                chat_title=chat_title,
                summary=summary,
                messages_count=0,
            )

        # Format messages for prompt
        messages_text = _format_messages_for_prompt(messages, tz)

        # Fill template
        user_prompt = chat_prompt_template.format(
            chat_title=chat_title,
            date=report_date.strftime("%d.%m.%Y"),
            messages_count=len(messages),
            messages_text=messages_text,
        )

        # Call Claude
        response: ClaudeResponse = await claude_client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1024,
            temperature=0.3,
            call_type="chat_summary",
        )

        # Save to cache
        async with async_session() as session:
            await save_daily_report(
                session,
                report_date=report_date,
                report_type="chat_summary",
                content_text=response.text,
                chat_id=chat_id,
                model_version=response.model,
                prompt_tokens=response.tokens_in,
                completion_tokens=response.tokens_out,
            )

        logger.info(
            "chat_summarized chat_id={} messages={} tokens_in={} tokens_out={}",
            chat_id,
            len(messages),
            response.tokens_in,
            response.tokens_out,
        )

        return ChatSummaryResult(
            chat_id=chat_id,
            chat_title=chat_title,
            summary=response.text,
            messages_count=len(messages),
        )

    except BudgetExceededError:
        raise  # Propagate to stop all processing
    except Exception as e:
        logger.exception("summarize_chat_failed chat_id={}", chat_id)
        return ChatSummaryResult(
            chat_id=chat_id,
            chat_title=chat_title,
            summary=None,
            messages_count=0,
            error=str(e),
        )


async def _reduce_phase(
    summaries: list[ChatSummaryResult],
    report_date: datetime.date,
    tz: ZoneInfo,
) -> str:
    system_prompt = load_prompt("system")
    report_template = load_prompt("daily_report")

    # Build chat stats
    chat_stats_lines = []
    for s in summaries:
        status = f"{s.messages_count} сообщ." if s.error is None else "❌ данные недоступны"
        chat_stats_lines.append(f"— {s.chat_title}: {status}")
    chat_stats = "\n".join(chat_stats_lines)

    # Build chat summaries text
    summaries_lines = []
    for s in summaries:
        if s.summary:
            summaries_lines.append(f"<b>{s.chat_title}:</b>\n{s.summary}")
        elif s.error:
            summaries_lines.append(
                f"<b>{s.chat_title}:</b>\n⚠️ Данные недоступны: {s.error[:100]}"
            )
    chat_summaries = "\n\n".join(summaries_lines)

    # Total stats
    total_messages = sum(s.messages_count for s in summaries)

    # Get unique participants count
    async with async_session() as session:
        day_start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=tz)
        day_end = datetime.datetime.combine(
            report_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
        )
        result = await session.execute(
            select(func.count(func.distinct(Message.user_id))).where(
                Message.timestamp >= day_start,
                Message.timestamp < day_end,
                Message.chat_id.in_(get_active_chat_ids()),
            )
        )
        total_participants = result.scalar_one()

    # Get glossary orders status for today
    from rgo_bot.db.crud.glossary_orders import get_active_orders_for_date

    async with async_session() as session:
        glossary_orders = await get_active_orders_for_date(session, report_date)

    if glossary_orders:
        glossary_lines = []
        for o in glossary_orders:
            target = "все РГО" if o.target_rgo_ids is None else ", ".join(
                registry_get_title(cid) for cid in o.target_rgo_ids
            )
            glossary_lines.append(f"• {o.order_text} → {target}")
        glossary_status = "\n".join(glossary_lines)
    else:
        glossary_status = "Нет поручений от НУ на сегодня."

    # Fill template
    user_prompt = report_template.format(
        date=report_date.strftime("%d.%m.%Y"),
        total_messages=total_messages,
        total_participants=total_participants,
        chat_stats=chat_stats,
        chat_summaries=chat_summaries,
        glossary_status=glossary_status,
    )

    response = await claude_client.complete(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=2048,
        temperature=0.3,
        call_type="daily_report",
    )

    logger.info(
        "daily_report_generated date={} tokens_in={} tokens_out={}",
        report_date,
        response.tokens_in,
        response.tokens_out,
    )

    return response.text


def _format_messages_for_prompt(messages: list[Message], tz: ZoneInfo) -> str:
    lines = []
    for msg in messages:
        local_time = msg.timestamp.astimezone(tz)
        username = f"@{msg.username}" if msg.username else msg.full_name
        if msg.text:
            text = msg.text
        elif msg.voice_transcript:
            text = f"[голосовое] {msg.voice_transcript}"
        else:
            text = f"[{msg.message_type}]"

        prefix = "[fwd] " if msg.is_forwarded else ""
        lines.append(f"{prefix}[{local_time:%H:%M}] {username}: {text}")

    return "\n".join(lines)


def _get_chat_title(chat_id: int) -> str:
    return registry_get_title(chat_id)
