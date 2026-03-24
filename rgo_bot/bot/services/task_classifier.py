from __future__ import annotations

import datetime
import json

from loguru import logger
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.bot.services.chat_registry import get_active_chat_ids, get_chat_title
from rgo_bot.bot.services.claude_client import (
    BudgetExceededError,
    CircuitOpenError,
    claude_client,
    load_prompt,
)
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.tasks import (
    create_task,
    get_open_tasks,
    mark_messages_processed,
    get_unprocessed_messages,
    update_task_status,
)


def _format_messages_for_prompt(messages: list) -> str:
    """Format DB messages into readable text for Claude prompt."""
    lines: list[str] = []
    for msg in messages:
        ts = msg.timestamp.strftime("%H:%M") if msg.timestamp else "?"
        name = msg.full_name or msg.username or str(msg.user_id)
        text = msg.text or msg.voice_transcript or f"[{msg.message_type}]"
        lines.append(f"[{ts}] {name} (user_id={msg.user_id}, msg_id={msg.id}): {text}")
    return "\n".join(lines)


def _parse_tasks_response(text: str) -> list[dict]:
    """Parse JSON array from Claude response, tolerating markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        logger.warning("task_classifier invalid_json response_len={}", len(text))
    return []


async def classify_tasks_l1() -> int:
    """Level 1: Batch-classify unprocessed messages for task detection.

    Runs every N minutes (configured via task_classifier_interval_min).
    Returns number of tasks detected.
    """
    tz = ZoneInfo(settings.timezone)
    since = datetime.datetime.now(tz) - datetime.timedelta(
        minutes=settings.task_classifier_interval_min + 10  # overlap buffer
    )

    total_detected = 0

    for chat_id in get_active_chat_ids():
        try:
            async with async_session() as session:
                messages = await get_unprocessed_messages(session, chat_id, since)

            if not messages:
                continue

            logger.info(
                "task_l1 chat_id={} messages={}",
                chat_id, len(messages),
            )

            # Call Claude for task detection
            prompt_template = load_prompt("task_detect")
            user_prompt = prompt_template.format(
                chat_title=get_chat_title(chat_id),
                messages_text=_format_messages_for_prompt(messages),
            )

            response = await claude_client.complete(
                system_prompt=load_prompt("system"),
                user_prompt=user_prompt,
                max_tokens=2048,
                temperature=0.2,
                call_type="task_detect_l1",
            )

            detected_tasks = _parse_tasks_response(response.text)

            # Build message_id -> db_id mapping
            msg_id_map: dict[int, int] = {}
            for msg in messages:
                msg_id_map[msg.id] = msg.id
                msg_id_map[msg.message_id] = msg.id  # telegram msg_id -> db id

            # Save tasks above confidence threshold
            task_message_ids: set[int] = set()
            async with async_session() as session:
                for task_data in detected_tasks:
                    confidence = task_data.get("confidence", 0.0)
                    if confidence < settings.task_confidence_threshold:
                        continue

                    source_msg_id = task_data.get("source_message_id")
                    db_msg_id = msg_id_map.get(source_msg_id)
                    if db_msg_id is None:
                        continue

                    # Parse due_date
                    due_date = None
                    if task_data.get("due_date"):
                        try:
                            due_date = datetime.date.fromisoformat(task_data["due_date"])
                        except ValueError:
                            pass

                    task = await create_task(
                        session,
                        source_message_id=db_msg_id,
                        chat_id=chat_id,
                        assigner_user_id=task_data.get("assigner_user_id", 0),
                        assignee_user_id=task_data.get("assignee_user_id"),
                        task_text=task_data.get("task_text", "")[:500],
                        confidence=confidence,
                        due_date=due_date,
                    )
                    if task:
                        total_detected += 1
                        task_message_ids.add(db_msg_id)

            # Mark all messages as processed
            all_msg_ids = [m.id for m in messages]
            async with async_session() as session:
                # Messages with detected tasks
                if task_message_ids:
                    await mark_messages_processed(
                        session, list(task_message_ids), task_detected=True
                    )
                # Messages without tasks
                no_task_ids = [mid for mid in all_msg_ids if mid not in task_message_ids]
                if no_task_ids:
                    await mark_messages_processed(session, no_task_ids, task_detected=False)

            logger.info(
                "task_l1_done chat_id={} detected={}",
                chat_id, total_detected,
            )

        except (BudgetExceededError, CircuitOpenError) as e:
            logger.warning("task_l1 stopped: {}", str(e))
            break
        except Exception:
            logger.exception("task_l1_error chat_id={}", chat_id)

    return total_detected


async def validate_tasks_l2() -> int:
    """Level 2: Validate statuses of open tasks against recent messages.

    Runs daily at 18:30. Returns number of status changes.
    """
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()
    since = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz)

    total_changes = 0

    for chat_id in get_active_chat_ids():
        try:
            # Get open tasks for this chat
            async with async_session() as session:
                open_tasks = await get_open_tasks(session, chat_id)

            if not open_tasks:
                continue

            # Get today's messages for context
            from rgo_bot.db.crud.messages import get_messages_for_report

            async with async_session() as session:
                messages = await get_messages_for_report(session, chat_id, today, tz)

            # Format tasks as JSON for prompt
            tasks_json = json.dumps(
                [
                    {
                        "task_id": t.task_id,
                        "task_text": t.task_text,
                        "assigner_user_id": t.assigner_user_id,
                        "assignee_user_id": t.assignee_user_id,
                        "due_date": t.due_date.isoformat() if t.due_date else None,
                        "status": t.status,
                        "detected_at": t.detected_at.isoformat() if t.detected_at else None,
                    }
                    for t in open_tasks
                ],
                ensure_ascii=False,
            )

            prompt_template = load_prompt("task_validate")
            user_prompt = prompt_template.format(
                chat_title=get_chat_title(chat_id),
                tasks_json=tasks_json,
                messages_text=_format_messages_for_prompt(messages) if messages else "(нет сообщений за сегодня)",
                today=today.isoformat(),
            )

            response = await claude_client.complete(
                system_prompt=load_prompt("system"),
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.2,
                call_type="task_validate_l2",
            )

            updates = _parse_tasks_response(response.text)

            # Apply status changes
            valid_task_ids = {t.task_id for t in open_tasks}
            async with async_session() as session:
                for upd in updates:
                    task_id = upd.get("task_id")
                    new_status = upd.get("new_status")
                    if task_id not in valid_task_ids:
                        continue
                    if new_status not in ("open", "closed", "overdue"):
                        continue
                    if new_status == "open":
                        continue  # No change needed

                    await update_task_status(
                        session,
                        task_id=task_id,
                        new_status=new_status,
                        close_message_id=upd.get("close_message_id"),
                    )
                    total_changes += 1
                    logger.info(
                        "task_l2_update task_id={} status={} reason={}",
                        task_id, new_status, upd.get("reason", ""),
                    )

        except (BudgetExceededError, CircuitOpenError) as e:
            logger.warning("task_l2 stopped: {}", str(e))
            break
        except Exception:
            logger.exception("task_l2_error chat_id={}", chat_id)

    logger.info("task_l2_done total_changes={}", total_changes)
    return total_changes
