"""Telegram Mini App initData validation.

Validates HMAC-SHA-256 signature per Telegram docs:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

- Parses initData query string
- Verifies HMAC using BOT_TOKEN
- Checks auth_date freshness (max 1 hour)
- Checks user.id == ADMIN_TELEGRAM_ID
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, unquote

from aiohttp import web
from loguru import logger

from rgo_bot.bot.config import settings

AUTH_MAX_AGE_SEC = 3600  # 1 hour


def _validate_init_data(init_data: str) -> dict | None:
    """Validate Telegram initData and return parsed user dict or None."""
    if not init_data:
        return None

    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [""])[0]
        if not received_hash:
            return None

        # Build data-check string (sorted key=value pairs, excluding hash)
        pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            pairs.append(f"{key}={values[0]}")
        pairs.sort()
        data_check_string = "\n".join(pairs)

        # Compute HMAC
        secret_key = hmac.new(
            b"WebAppData", settings.bot_token.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning("webapp_auth hash_mismatch")
            return None

        # Check auth_date freshness
        auth_date_str = parsed.get("auth_date", ["0"])[0]
        auth_date = int(auth_date_str)
        if time.time() - auth_date > AUTH_MAX_AGE_SEC:
            logger.warning("webapp_auth expired auth_date={}", auth_date)
            return None

        # Parse user
        user_str = parsed.get("user", [""])[0]
        if not user_str:
            return None
        user = json.loads(unquote(user_str))
        return user

    except Exception:
        logger.exception("webapp_auth validation_error")
        return None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """aiohttp middleware: validate initData for /api/ routes."""
    # Skip auth for static files
    if not request.path.startswith("/api/"):
        return await handler(request)

    # Extract initData from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("tg-init-data "):
        return web.json_response(
            {"error": "Unauthorized"}, status=401
        )

    init_data = auth_header[len("tg-init-data "):]
    user = _validate_init_data(init_data)

    if user is None:
        return web.json_response(
            {"error": "Invalid initData"}, status=401
        )

    # Admin-only check
    user_id = user.get("id")
    if user_id != settings.admin_telegram_id:
        logger.warning("webapp_auth forbidden user_id={}", user_id)
        return web.json_response(
            {"error": "Forbidden"}, status=403
        )

    # Attach user to request
    request["tg_user"] = user
    return await handler(request)
