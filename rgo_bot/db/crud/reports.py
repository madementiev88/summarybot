from __future__ import annotations

import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import DailyReport


async def save_daily_report(
    session: AsyncSession,
    report_date: datetime.date,
    report_type: str,
    content_text: str,
    chat_id: int | None = None,
    stats_json: dict | None = None,
    model_version: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> DailyReport:
    report = DailyReport(
        report_date=report_date,
        report_type=report_type,
        content_text=content_text,
        chat_id=chat_id,
        stats_json=stats_json,
        model_version=model_version,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


async def get_report_by_date(
    session: AsyncSession,
    report_date: datetime.date,
    report_type: str = "daily",
) -> DailyReport | None:
    result = await session.execute(
        select(DailyReport).where(
            DailyReport.report_date == report_date,
            DailyReport.report_type == report_type,
        )
    )
    return result.scalar_one_or_none()


async def get_chat_summaries_by_date(
    session: AsyncSession,
    report_date: datetime.date,
) -> list[DailyReport]:
    result = await session.execute(
        select(DailyReport).where(
            DailyReport.report_date == report_date,
            DailyReport.report_type == "chat_summary",
        )
    )
    return list(result.scalars().all())


async def mark_report_sent(
    session: AsyncSession,
    report_id: int,
) -> None:
    await session.execute(
        update(DailyReport)
        .where(DailyReport.id == report_id)
        .values(
            sent_to_admin=True,
            sent_at=datetime.datetime.now(datetime.UTC),
        )
    )
    await session.commit()
