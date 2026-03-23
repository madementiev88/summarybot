from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rgo_bot.db.models import ApiUsage


async def log_api_usage(
    session: AsyncSession,
    provider: str,
    call_type: str,
    tokens_in: int,
    tokens_out: int,
    estimated_cost_usd: Decimal,
) -> ApiUsage:
    usage = ApiUsage(
        provider=provider,
        call_type=call_type,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost_usd=estimated_cost_usd,
    )
    session.add(usage)
    await session.commit()
    return usage


async def get_daily_cost(
    session: AsyncSession,
    report_date: datetime.date,
    tz: datetime.tzinfo,
) -> Decimal:
    day_start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=tz)
    day_end = datetime.datetime.combine(
        report_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
    )
    result = await session.execute(
        select(func.coalesce(func.sum(ApiUsage.estimated_cost_usd), 0)).where(
            ApiUsage.timestamp >= day_start,
            ApiUsage.timestamp < day_end,
        )
    )
    return Decimal(str(result.scalar_one()))
