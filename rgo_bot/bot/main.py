from __future__ import annotations

import asyncio
import sys

import urllib.request

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from loguru import logger

from rgo_bot.bot.config import settings
from rgo_bot.bot.handlers import admin_private, group_messages, rgo_private
from rgo_bot.bot.middleware.admin_only import AdminOnlyMiddleware


def _setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
        "<level>{message}</level>",
    )
    logger.add(
        "logs/bot.log",
        level=settings.log_level,
        rotation="100 MB",
        retention=f"{settings.log_retention_days} days",
        compression="gz",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} | {message}",
    )


async def main() -> None:
    _setup_logging()

    logger.info("Starting RGO Monitoring Bot...")
    logger.info(
        "Monitoring {} chats, timezone={}",
        len(settings.monitored_chat_ids),
        settings.timezone,
    )

    # Create database tables
    from rgo_bot.db.base import engine
    from rgo_bot.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")

    # Initialize chat registry (sync .env → DB, load into memory)
    from rgo_bot.bot.services.chat_registry import init_registry

    await init_registry(settings.monitored_chat_ids)

    # Initialize bot (with system proxy if available)
    session = None
    system_proxies = urllib.request.getproxies()
    proxy_url = system_proxies.get("https") or system_proxies.get("http")
    if proxy_url:
        session = AiohttpSession(proxy=proxy_url)
        logger.info("Using proxy: {}", proxy_url)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )

    dp = Dispatcher()

    # Register routers
    # Admin private router (with AdminOnly middleware)
    admin_private.router.message.middleware(AdminOnlyMiddleware())
    dp.include_router(admin_private.router)

    # RGO private router (no middleware — accessible to all known users)
    dp.include_router(rgo_private.router)

    # Group messages router (no middleware — collects from all groups)
    dp.include_router(group_messages.router)

    # Setup scheduler
    from rgo_bot.bot.services.scheduler import setup_scheduler, stop_scheduler

    await setup_scheduler(bot)

    # Notify admin on startup
    try:
        from rgo_bot.bot.services.chat_registry import get_active_chat_ids

        await bot.send_message(
            settings.admin_telegram_id,
            "🟢 <b>Бот запущен</b>\n\n"
            f"Мониторинг: {len(get_active_chat_ids())} чатов\n"
            f"Часовой пояс: {settings.timezone}\n"
            f"Ежедневный отчёт: {settings.daily_report_time}",
        )
    except Exception:
        logger.warning("Could not send startup notification to admin")

    # Set bot commands menu
    from aiogram.types import BotCommand

    await bot.set_my_commands([
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="status", description="Статус мониторинга"),
        BotCommand(command="report_now", description="Отчёт за сегодня"),
        BotCommand(command="tasks", description="Открытые поручения"),
        BotCommand(command="load", description="Нагрузка по чатам"),
        BotCommand(command="hours", description="Активность по часам"),
        BotCommand(command="activity", description="Рейтинг участников"),
    ])

    # Set Mini App menu button for all admins (if webapp_url configured)
    if settings.webapp_url:
        from aiogram.types import MenuButtonWebApp, WebAppInfo

        all_admin_ids = {settings.admin_telegram_id}
        all_admin_ids.update(settings.admin_ids or [])

        for admin_id in all_admin_ids:
            try:
                await bot.set_chat_menu_button(
                    chat_id=admin_id,
                    menu_button=MenuButtonWebApp(
                        text="РСО",
                        web_app=WebAppInfo(url=settings.webapp_url),
                    ),
                )
                logger.info("Mini App menu button set for admin_id={}", admin_id)
            except Exception:
                logger.warning("Could not set Mini App menu button for admin_id={}", admin_id)

    # Start web server for Mini App
    from rgo_bot.web.app import create_web_app

    web_app = create_web_app(bot)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.web_port)
    await site.start()
    logger.info("Mini App web server started on port {}", settings.web_port)

    # Start polling
    logger.info("Bot is ready, starting polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await stop_scheduler()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
