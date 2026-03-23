"""aiohttp web application for Telegram Mini App.

Serves static files and API routes. Embedded in the bot process.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web
from aiogram import Bot
from loguru import logger

from rgo_bot.web.auth import auth_middleware

STATIC_DIR = Path(__file__).parent / "static"

# In-memory task storage for long-running operations
# {task_id: {"status": "processing"|"done"|"error", "result": ..., "step": ..., "created": float}}
_tasks: dict[str, dict[str, Any]] = {}
_TASK_TTL = 600  # 10 min


def create_task_entry(step: str = "") -> str:
    """Create a new task entry and return its ID."""
    task_id = uuid.uuid4().hex[:12]
    _tasks[task_id] = {
        "status": "processing",
        "result": None,
        "step": step,
        "created": asyncio.get_event_loop().time(),
    }
    return task_id


def update_task(task_id: str, *, status: str | None = None,
                result: Any = None, step: str | None = None) -> None:
    """Update task status/result/step."""
    if task_id not in _tasks:
        return
    if status is not None:
        _tasks[task_id]["status"] = status
    if result is not None:
        _tasks[task_id]["result"] = result
    if step is not None:
        _tasks[task_id]["step"] = step


def get_task(task_id: str) -> dict[str, Any] | None:
    """Get task entry by ID."""
    return _tasks.get(task_id)


async def _cleanup_tasks() -> None:
    """Periodically remove expired tasks."""
    while True:
        await asyncio.sleep(60)
        now = asyncio.get_event_loop().time()
        expired = [k for k, v in _tasks.items() if now - v["created"] > _TASK_TTL]
        for k in expired:
            del _tasks[k]


def create_web_app(bot: Bot) -> web.Application:
    """Create and configure aiohttp application."""
    app = web.Application(middlewares=[auth_middleware])

    # Store bot instance for routes to use
    app["bot"] = bot

    # Register routes
    from rgo_bot.web.routes.commands import setup_command_routes
    from rgo_bot.web.routes.kos import setup_kos_routes
    from rgo_bot.web.routes.preza import setup_preza_routes
    from rgo_bot.web.routes.feedback import setup_feedback_routes

    setup_command_routes(app)
    setup_kos_routes(app)
    setup_preza_routes(app)
    setup_feedback_routes(app)

    # Static files (Mini App frontend)
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")

    # Serve index.html at root
    async def index_handler(request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    app.router.add_get("/", index_handler)

    # Task status endpoint (generic)
    async def task_status_handler(request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        task = get_task(task_id)
        if task is None:
            return web.json_response({"error": "Task not found"}, status=404)
        return web.json_response({
            "status": task["status"],
            "step": task["step"],
            "result": task["result"],
        })

    app.router.add_get("/api/task/{task_id}", task_status_handler)

    # Start cleanup task on startup
    async def on_startup(app: web.Application) -> None:
        app["cleanup_task"] = asyncio.create_task(_cleanup_tasks())
        logger.info("webapp_started static_dir={}", STATIC_DIR)

    async def on_cleanup(app: web.Application) -> None:
        app["cleanup_task"].cancel()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app
