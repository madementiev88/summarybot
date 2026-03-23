"""Chart generation: matplotlib → BytesIO (no temp files on disk).

Three chart types for daily report + admin commands:
1. RGO load bar chart (block 3)
2. Hourly activity heatmap (block 4)
3. Participant activity stacked bar (block 13)
"""
from __future__ import annotations

import datetime
import io
from collections import defaultdict

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.db.models import Message, ParticipantChat

matplotlib.use("Agg")

# ── Style ──────────────────────────────────────────────
plt.rcParams.update({
    "figure.facecolor": "#1e1e2e",
    "axes.facecolor": "#1e1e2e",
    "axes.edgecolor": "#444466",
    "axes.labelcolor": "#cdd6f4",
    "text.color": "#cdd6f4",
    "xtick.color": "#bac2de",
    "ytick.color": "#bac2de",
    "grid.color": "#313244",
    "font.size": 11,
})

COLORS = ["#89b4fa", "#a6e3a1", "#f9e2af", "#fab387", "#f38ba8", "#cba6f7", "#94e2d5"]


def _fig_to_bytes(fig: plt.Figure) -> io.BytesIO:
    """Render matplotlib figure to BytesIO PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


async def generate_load_chart(
    session: AsyncSession,
    report_date: datetime.date,
    chat_titles: dict[int, str],
) -> io.BytesIO | None:
    """Bar chart: message count per monitored chat (RGO load)."""
    tz = ZoneInfo(settings.timezone)
    day_start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=tz)
    day_end = day_start + datetime.timedelta(days=1)

    result = await session.execute(
        select(Message.chat_id, func.count(Message.id))
        .where(Message.timestamp >= day_start, Message.timestamp < day_end)
        .group_by(Message.chat_id)
    )
    rows = result.all()

    if not rows:
        return None

    chat_ids = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    labels = [chat_titles.get(cid, str(cid))[:20] for cid in chat_ids]

    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.barh(labels, counts, color=COLORS[:len(labels)])
    ax.set_xlabel("Сообщений")
    ax.set_title(f"Нагрузка по чатам — {report_date}", fontweight="bold")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    for bar, count in zip(bars, counts):
        ax.text(
            bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
            str(count), va="center", fontsize=10, color="#cdd6f4",
        )

    fig.tight_layout()
    logger.info("chart_generated type=load_bar chats={}", len(rows))
    return _fig_to_bytes(fig)


async def generate_heatmap(
    session: AsyncSession,
    report_date: datetime.date,
    chat_titles: dict[int, str],
) -> io.BytesIO | None:
    """Heatmap: activity per chat × hour of day."""
    tz = ZoneInfo(settings.timezone)
    day_start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=tz)
    day_end = day_start + datetime.timedelta(days=1)

    result = await session.execute(
        select(Message.chat_id, Message.timestamp)
        .where(Message.timestamp >= day_start, Message.timestamp < day_end)
    )
    rows = result.all()

    if not rows:
        return None

    # Build matrix: chat_index × 24 hours
    chat_ids = list(chat_titles.keys())
    if not chat_ids:
        chat_ids = list({r[0] for r in rows})

    chat_idx = {cid: i for i, cid in enumerate(chat_ids)}
    data = np.zeros((len(chat_ids), 24), dtype=int)

    for chat_id, ts in rows:
        if chat_id not in chat_idx:
            continue
        local_hour = ts.astimezone(tz).hour
        data[chat_idx[chat_id]][local_hour] += 1

    labels = [chat_titles.get(cid, str(cid))[:18] for cid in chat_ids]

    fig, ax = plt.subplots(figsize=(12, max(3, len(chat_ids) * 0.8)))
    im = ax.imshow(data, aspect="auto", cmap="YlOrRd", interpolation="nearest")

    ax.set_xticks(range(24))
    ax.set_xticklabels([f"{h:02d}" for h in range(24)], fontsize=8)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Час дня")
    ax.set_title(f"Активность по часам — {report_date}", fontweight="bold")

    # Add text annotations
    for i in range(len(chat_ids)):
        for j in range(24):
            val = data[i, j]
            if val > 0:
                color = "white" if val > data.max() / 2 else "black"
                ax.text(j, i, str(val), ha="center", va="center", fontsize=7, color=color)

    fig.colorbar(im, ax=ax, label="Сообщений", shrink=0.8)
    fig.tight_layout()
    logger.info("chart_generated type=heatmap chats={}", len(chat_ids))
    return _fig_to_bytes(fig)


async def generate_activity_chart(
    session: AsyncSession,
    report_date: datetime.date,
    chat_titles: dict[int, str],
    top_n: int = 5,
) -> io.BytesIO | None:
    """Stacked bar chart: top participants by message count per chat."""
    tz = ZoneInfo(settings.timezone)
    day_start = datetime.datetime.combine(report_date, datetime.time.min, tzinfo=tz)
    day_end = day_start + datetime.timedelta(days=1)

    # Get message counts per user per chat
    result = await session.execute(
        select(Message.user_id, Message.full_name, Message.chat_id, func.count(Message.id))
        .where(Message.timestamp >= day_start, Message.timestamp < day_end)
        .group_by(Message.user_id, Message.full_name, Message.chat_id)
    )
    rows = result.all()

    if not rows:
        return None

    # Aggregate by user
    user_totals: dict[int, int] = defaultdict(int)
    user_names: dict[int, str] = {}
    user_per_chat: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    for user_id, full_name, chat_id, count in rows:
        user_totals[user_id] += count
        user_names[user_id] = full_name or str(user_id)
        user_per_chat[user_id][chat_id] += count

    # Top N users
    top_users = sorted(user_totals.keys(), key=lambda uid: user_totals[uid], reverse=True)[:top_n]

    chat_ids = list(chat_titles.keys())
    if not chat_ids:
        chat_ids = list({r[2] for r in rows})

    names = [user_names[uid][:16] for uid in top_users]
    x = np.arange(len(names))

    fig, ax = plt.subplots(figsize=(10, 5))
    bottom = np.zeros(len(top_users))

    for i, cid in enumerate(chat_ids):
        values = [user_per_chat[uid].get(cid, 0) for uid in top_users]
        label = chat_titles.get(cid, str(cid))[:15]
        ax.bar(x, values, bottom=bottom, label=label, color=COLORS[i % len(COLORS)], width=0.7)
        bottom += np.array(values)

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Сообщений")
    ax.set_title(f"Рейтинг активности — {report_date}", fontweight="bold")
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.legend(loc="upper right", fontsize=8)

    fig.tight_layout()
    logger.info("chart_generated type=activity_stacked users={}", len(top_users))
    return _fig_to_bytes(fig)
