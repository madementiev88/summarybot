from __future__ import annotations

import asyncio
import datetime

from aiogram import Bot
from loguru import logger
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.reports import get_report_by_date
from rgo_bot.db.models import SchedulerRun

_task: asyncio.Task | None = None


async def setup_scheduler(bot: Bot) -> None:
    global _task

    # Check missed reports on startup
    await _check_missed_reports(bot)

    # Start background cron loop
    _task = asyncio.create_task(_cron_loop(bot))

    tz = ZoneInfo(settings.timezone)
    logger.info(
        "scheduler_started daily_report_at={} tz={}",
        settings.daily_report_time,
        settings.timezone,
    )


async def stop_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    logger.info("scheduler_stopped")


async def _cron_loop(bot: Bot) -> None:
    """Simple cron loop: check every 30 seconds if it's time to run."""
    tz = ZoneInfo(settings.timezone)
    report_hour, report_minute = map(int, settings.daily_report_time.split(":"))
    last_report_date: datetime.date | None = None
    last_l1_run: datetime.datetime | None = None
    last_l2_date: datetime.date | None = None
    last_silence_check: datetime.datetime | None = None
    last_overdue_check: dict[int, datetime.date] = {}  # {hour: date}
    last_rec_date: datetime.date | None = None
    rec_hour, rec_minute = map(int, settings.morning_rec_time.split(":"))

    while True:
        try:
            await asyncio.sleep(30)

            now = datetime.datetime.now(tz)
            today = now.date()
            is_workday = today.isoweekday() in settings.work_days

            # ── Morning recommendations (08:30, workdays only) ──
            if is_workday and last_rec_date != today:
                if now.hour == rec_hour and now.minute >= rec_minute:
                    last_rec_date = today
                    logger.info("cron_trigger rgo_recs date={}", today)
                    await rgo_recommendations_job(bot)

            # ── L1: Task classification (every N minutes, workdays only) ──
            if is_workday and (
                last_l1_run is None
                or (now - last_l1_run).total_seconds()
                >= settings.task_classifier_interval_min * 60
            ):
                last_l1_run = now
                logger.info("cron_trigger task_l1 date={}", today)
                await task_classifier_l1_job(bot)

            # ── L2: Task validation (18:30, workdays only) ──
            if is_workday and last_l2_date != today:
                if now.hour == 18 and now.minute >= 30:
                    last_l2_date = today
                    logger.info("cron_trigger task_l2 date={}", today)
                    await task_classifier_l2_job(bot)

            # ── Silence check (every hour, workdays only) ──
            if is_workday and (
                last_silence_check is None
                or (now - last_silence_check).total_seconds() >= 3600
            ):
                last_silence_check = now
                await silence_check_job(bot)

            # ── Overdue tasks (12:00 and 18:00, workdays only) ──
            if is_workday and now.hour in (12, 18):
                if last_overdue_check.get(now.hour) != today:
                    last_overdue_check[now.hour] = today
                    await overdue_check_job(bot)

            # ── Daily report (workdays only) ──
            if is_workday and last_report_date != today:
                if now.hour == report_hour and now.minute >= report_minute:
                    last_report_date = today
                    logger.info("cron_trigger daily_report date={}", today)
                    await daily_report_job(bot)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cron_loop_error")
            await asyncio.sleep(60)


async def rgo_recommendations_job(bot: Bot) -> None:
    logger.info("rgo_recs_job_started")
    try:
        from rgo_bot.bot.services.recommender import send_morning_recommendations

        sent = await send_morning_recommendations(bot)
        logger.info("rgo_recs_job_done sent={}", sent)
        await _update_scheduler_run("rgo_recommendations")
    except Exception as e:
        logger.exception("rgo_recs_job_failed")
        await _update_scheduler_run("rgo_recommendations", error=str(e))


async def silence_check_job(bot: Bot) -> None:
    try:
        from rgo_bot.bot.services.alerter import check_silence_alerts
        await check_silence_alerts(bot)
    except Exception:
        logger.exception("silence_check_failed")


async def overdue_check_job(bot: Bot) -> None:
    try:
        from rgo_bot.bot.services.alerter import check_overdue_tasks
        await check_overdue_tasks(bot)
    except Exception:
        logger.exception("overdue_check_failed")


async def task_classifier_l1_job(bot: Bot) -> None:
    logger.info("task_l1_job_started")
    try:
        from rgo_bot.bot.services.task_classifier import classify_tasks_l1

        detected = await classify_tasks_l1()
        logger.info(f"task_l1_job_done detected={detected}")
        await _update_scheduler_run("task_classifier_l1")
    except Exception as e:
        logger.exception("task_l1_job_failed")
        await _update_scheduler_run("task_classifier_l1", error=str(e))


async def task_classifier_l2_job(bot: Bot) -> None:
    logger.info("task_l2_job_started")
    try:
        from rgo_bot.bot.services.task_classifier import validate_tasks_l2

        changes = await validate_tasks_l2()
        logger.info(f"task_l2_job_done changes={changes}")
        await _update_scheduler_run("task_classifier_l2")
    except Exception as e:
        logger.exception("task_l2_job_failed")
        await _update_scheduler_run("task_classifier_l2", error=str(e))


async def daily_report_job(bot: Bot) -> None:
    tz = ZoneInfo(settings.timezone)
    today = datetime.datetime.now(tz).date()

    logger.info("daily_report_job_started date={}", today)

    # Idempotency check
    async with async_session() as session:
        existing = await get_report_by_date(session, today, "daily")
        if existing and existing.sent_to_admin:
            logger.info("daily_report already_sent date={}", today)
            return

    try:
        from rgo_bot.bot.services.chart_generator import (
            generate_activity_chart,
            generate_heatmap,
            generate_load_chart,
        )
        from rgo_bot.bot.services.chat_registry import get_all_chat_titles
        from rgo_bot.bot.services.reporter import (
            send_chart_to_admin,
            send_report_to_admin,
        )
        from rgo_bot.bot.services.summarizer import generate_daily_report

        result = await generate_daily_report(today)
        if result:
            async with async_session() as session:
                report = await get_report_by_date(session, today, "daily")
            if report:
                await send_report_to_admin(bot, result.report_text, report.id)

            # Send charts after text report
            chat_titles = get_all_chat_titles()
            async with async_session() as session:
                load_chart = await generate_load_chart(session, today, chat_titles)
                if load_chart:
                    await send_chart_to_admin(bot, load_chart, "📊 Нагрузка по чатам")
                    await asyncio.sleep(0.3)

                heatmap = await generate_heatmap(session, today, chat_titles)
                if heatmap:
                    await send_chart_to_admin(bot, heatmap, "🕐 Активность по часам")
                    await asyncio.sleep(0.3)

                activity = await generate_activity_chart(session, today, chat_titles)
                if activity:
                    await send_chart_to_admin(bot, activity, "👥 Рейтинг участников")

            if result.failed_chats:
                from rgo_bot.bot.services.chat_registry import get_chat_title

                failed_names = ", ".join(
                    get_chat_title(cid) for cid in result.failed_chats
                )
                await bot.send_message(
                    settings.admin_telegram_id,
                    f"⚠️ Данные по чатам недоступны: {failed_names}",
                    parse_mode="HTML",
                )
        else:
            logger.info("daily_report no_data date={}", today)

        await _update_scheduler_run("daily_report")

    except Exception as e:
        logger.exception("daily_report_job_failed date={}", today)
        await _update_scheduler_run("daily_report", error=str(e))
        try:
            await bot.send_message(
                settings.admin_telegram_id,
                f"❌ <b>Ошибка генерации отчёта</b>\n\n"
                f"Дата: {today}\nОшибка: {str(e)[:200]}",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("failed to send error notification")


async def _check_missed_reports(bot: Bot) -> None:
    tz = ZoneInfo(settings.timezone)
    yesterday = datetime.datetime.now(tz).date() - datetime.timedelta(days=1)

    if yesterday.isoweekday() not in settings.work_days:
        return

    async with async_session() as session:
        report = await get_report_by_date(session, yesterday, "daily")

    if report is None:
        logger.warning("missed_report date={}, generating catch-up", yesterday)
        await daily_report_job(bot)


async def _update_scheduler_run(
    job_name: str, error: str | None = None
) -> None:
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(SchedulerRun).where(SchedulerRun.job_name == job_name)
        )
        run = result.scalar_one_or_none()

        now = datetime.datetime.now(datetime.UTC)
        if run is None:
            run = SchedulerRun(
                job_name=job_name,
                last_success_at=now if error is None else None,
                last_error=error,
            )
            session.add(run)
        else:
            if error is None:
                run.last_success_at = now
                run.last_error = None
            else:
                run.last_error = error

        await session.commit()
