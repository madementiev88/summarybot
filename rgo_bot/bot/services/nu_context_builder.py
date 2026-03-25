"""Context builder for NU (admin) advisor.

Collects data from DB to provide Claude with rich context
about all 7 RGOs and their advisor usage.
"""
from __future__ import annotations

import datetime
import re

from loguru import logger
from sqlalchemy import func, select, text
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.models import (
    Message,
    MonitoredChat,
    Participant,
    ParticipantChat,
    RGOAdvisorLog,
    Task,
)


def detect_question_type(
    question: str, rgo_names: list[dict]
) -> tuple[str, int | None]:
    """Determine if question is about a specific RGO or the whole team.

    Returns: ('single_rgo', rgo_user_id) | ('team', None) | ('no_data', None)
    """
    q_lower = question.lower()

    # Check if any RGO name is mentioned
    for rgo in rgo_names:
        name = rgo.get("name", "").lower()
        if not name:
            continue
        # Match first name, last name, or full name
        name_parts = name.split()
        for part in name_parts:
            if len(part) >= 3 and part in q_lower:
                return "single_rgo", rgo["user_id"]

    # Team keywords
    team_keywords = [
        "команд", "все", "рейтинг", "сравни", "кто", "лучше", "хуже",
        "список", "общий", "план", "сеть", "итог", "вся", "всех",
        "светофор", "приоритет",
    ]
    for kw in team_keywords:
        if kw in q_lower:
            return "team", None

    # Default to team (more data is better)
    return "team", None


async def get_rgo_list() -> list[dict]:
    """Get list of all RGO users with their chat IDs."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT mc.rgo_user_id, mc.chat_id, mc.chat_title, p.full_name
                FROM monitored_chats mc
                LEFT JOIN participants p ON p.user_id = mc.rgo_user_id
                WHERE mc.rgo_user_id IS NOT NULL AND mc.is_active = true
                ORDER BY mc.chat_title
            """)
        )
        rows = result.fetchall()
        return [
            {
                "user_id": r[0],
                "chat_id": r[1],
                "chat_title": r[2],
                "name": r[3] or r[2] or "Unknown",
            }
            for r in rows
        ]


async def build_block_a(rgo_user_id: int, chat_id: int) -> str:
    """Block A — data for a specific RGO."""
    try:
        async with async_session() as session:
            # Messages last 7 days
            msg_result = await session.execute(
                text("""
                    SELECT COUNT(*) FROM messages
                    WHERE chat_id = :cid
                    AND timestamp > now() - interval '7 days'
                """),
                {"cid": chat_id},
            )
            msg_count = msg_result.scalar() or 0

            # Peak hours
            peak_result = await session.execute(
                text("""
                    SELECT EXTRACT(HOUR FROM timestamp AT TIME ZONE :tz) AS hr,
                           COUNT(*) AS cnt
                    FROM messages
                    WHERE chat_id = :cid
                    AND timestamp > now() - interval '7 days'
                    GROUP BY hr ORDER BY cnt DESC LIMIT 3
                """),
                {"cid": chat_id, "tz": settings.timezone},
            )
            peak_rows = peak_result.fetchall()
            peak_hours = ", ".join(f"{int(r[0])}:00" for r in peak_rows) if peak_rows else "нет данных"

            # Tasks
            task_result = await session.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'open') AS open_t,
                        COUNT(*) FILTER (WHERE status = 'overdue') AS overdue_t
                    FROM tasks WHERE chat_id = :cid
                """),
                {"cid": chat_id},
            )
            task_row = task_result.first()
            open_tasks = task_row[0] if task_row else 0
            overdue_tasks = task_row[1] if task_row else 0

            # RGO name
            name_result = await session.execute(
                text("SELECT full_name FROM participants WHERE user_id = :uid"),
                {"uid": rgo_user_id},
            )
            name_row = name_result.first()
            rgo_name = name_row[0] if name_row else "РГО"

            return (
                f"[ДАННЫЕ ПО РГО: {rgo_name}]\n"
                f"Активность в чате за 7 дней: {msg_count} сообщений\n"
                f"Часы пиковой активности: {peak_hours}\n"
                f"Открытых поручений: {open_tasks}\n"
                f"Просроченных поручений: {overdue_tasks}\n"
                f"[КОНЕЦ ДАННЫХ РГО]"
            )
    except Exception:
        logger.exception("nu_block_a_error rgo_user_id={}", rgo_user_id)
        return ""


async def build_block_b() -> str:
    """Block B — comparative rating of all 7 RGOs."""
    try:
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT
                        mc.chat_title,
                        p.full_name,
                        COUNT(m.id) AS msg_7d,
                        (SELECT COUNT(*) FROM tasks t WHERE t.chat_id = mc.chat_id AND t.status = 'open') AS open_tasks,
                        (SELECT COUNT(*) FROM tasks t WHERE t.chat_id = mc.chat_id AND t.status = 'overdue') AS overdue_tasks
                    FROM monitored_chats mc
                    LEFT JOIN participants p ON p.user_id = mc.rgo_user_id
                    LEFT JOIN messages m ON m.chat_id = mc.chat_id
                        AND m.timestamp > now() - interval '7 days'
                    WHERE mc.is_active = true AND mc.rgo_user_id IS NOT NULL
                    GROUP BY mc.chat_id, mc.chat_title, p.full_name
                    ORDER BY msg_7d DESC
                """)
            )
            rows = result.fetchall()

            if not rows:
                return ""

            lines = ["[РЕЙТИНГ КОМАНДЫ — 7 РГО за 7 дней]"]
            for i, r in enumerate(rows, 1):
                name = r[1] or r[0]
                lines.append(
                    f"{i}. {name}: {r[2]} сообщ., "
                    f"открыто {r[3]}, просрочено {r[4]}"
                )
            lines.append("[КОНЕЦ РЕЙТИНГА]")
            return "\n".join(lines)

    except Exception:
        logger.exception("nu_block_b_error")
        return ""


async def build_block_v(rgo_user_id: int, chat_id: int) -> str:
    """Block V — RGO advisor data (what RGO asked, what plan they got, did they act)."""
    try:
        async with async_session() as session:
            # Last 5 advisor questions
            result = await session.execute(
                text("""
                    SELECT id, question, answer, created_at
                    FROM rgo_advisor_log
                    WHERE rgo_user_id = :uid
                    ORDER BY created_at DESC LIMIT 5
                """),
                {"uid": rgo_user_id},
            )
            logs = result.fetchall()

            if not logs:
                return "[ДАННЫЕ СОВЕТНИКА РГО]\nРГО советником не пользовался — данных о планах нет.\n[КОНЕЦ ДАННЫХ СОВЕТНИКА]"

            lines = ["[ДАННЫЕ СОВЕТНИКА РГО]"]
            for log in reversed(logs):
                log_id, question, answer, created_at = log

                # Check activity in chat after recommendation
                activity_result = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM messages
                        WHERE chat_id = :cid
                        AND user_id = :uid
                        AND timestamp BETWEEN :start AND :start + interval '48 hours'
                    """),
                    {
                        "cid": chat_id,
                        "uid": rgo_user_id,
                        "start": created_at,
                    },
                )
                msgs_after = activity_result.scalar() or 0

                q_short = question[:200] + "..." if len(question) > 200 else question
                a_short = answer[:200] + "..." if len(answer) > 200 else answer
                status = (
                    "✅ активность в чате зафиксирована"
                    if msgs_after > 0
                    else "❌ активности в чате после рекомендации НЕ зафиксировано"
                )

                dt_str = created_at.strftime("%d.%m %H:%M") if created_at else "?"
                lines.append(
                    f"— {dt_str} | Вопрос: {q_short}\n"
                    f"  Рекомендация: {a_short}\n"
                    f"  Реализация: {status}"
                )

            lines.append("[КОНЕЦ ДАННЫХ СОВЕТНИКА]")
            return "\n".join(lines)

    except Exception:
        logger.exception("nu_block_v_error rgo_user_id={}", rgo_user_id)
        return ""


async def build_context(
    question: str,
) -> tuple[str, str, int | None]:
    """Build full context for NU advisor.

    Returns: (context_text, question_type, target_rgo_user_id)
    """
    rgo_list = await get_rgo_list()
    rgo_names = [{"name": r["name"], "user_id": r["user_id"]} for r in rgo_list]

    q_type, target_rgo_id = detect_question_type(question, rgo_names)

    parts = []

    if q_type == "single_rgo" and target_rgo_id:
        # Find chat_id for this RGO
        rgo_info = next((r for r in rgo_list if r["user_id"] == target_rgo_id), None)
        if rgo_info:
            block_a = await build_block_a(target_rgo_id, rgo_info["chat_id"])
            if block_a:
                parts.append(block_a)

            block_v = await build_block_v(target_rgo_id, rgo_info["chat_id"])
            if block_v:
                parts.append(block_v)

    elif q_type == "team":
        block_b = await build_block_b()
        if block_b:
            parts.append(block_b)

        # Add Block V for all RGOs (shortened)
        for rgo in rgo_list:
            block_v = await build_block_v(rgo["user_id"], rgo["chat_id"])
            if block_v and "не пользовался" not in block_v:
                parts.append(block_v)

    context_text = "\n\n".join(parts) if parts else ""
    return context_text, q_type, target_rgo_id
