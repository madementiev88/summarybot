from __future__ import annotations

import asyncio
import datetime
import functools
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import anthropic
from loguru import logger
from zoneinfo import ZoneInfo

from rgo_bot.bot.config import settings
from rgo_bot.db.base import async_session
from rgo_bot.db.crud.api_usage import get_daily_cost, log_api_usage

# ── Pricing (Claude Sonnet 4) ────────────────────────
PRICE_INPUT_PER_M = Decimal("3.00")
PRICE_OUTPUT_PER_M = Decimal("15.00")


class BudgetExceededError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


@dataclass
class ClaudeResponse:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    model: str


@dataclass
class _CircuitBreaker:
    consecutive_errors: int = 0
    opened_at: float | None = None
    THRESHOLD: int = 5
    COOLDOWN_SEC: int = 900  # 15 min

    def record_success(self) -> None:
        self.consecutive_errors = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.THRESHOLD:
            self.opened_at = time.monotonic()

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        elapsed = time.monotonic() - self.opened_at
        if elapsed >= self.COOLDOWN_SEC:
            # Cooldown passed, allow one attempt
            self.opened_at = None
            self.consecutive_errors = 0
            return False
        return True


class MockClaudeClient:
    """Mock client for testing without real API calls."""

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        call_type: str = "generic",
    ) -> ClaudeResponse:
        logger.info("mock_claude call_type={} prompt_len={}", call_type, len(user_prompt))
        if call_type == "chat_summary":
            text = (
                "1. <b>Основные темы:</b> обсуждение текущих задач и планов\n"
                "2. <b>Решения:</b> согласованы сроки выполнения\n"
                "3. <b>Проблемы:</b> не выявлены\n"
                "4. <b>Активность:</b> участники проявляли умеренную активность\n"
                "5. <b>Договорённости:</b> назначены ответственные"
            )
        elif call_type == "daily_report":
            text = (
                "<b>📊 Сводка дня</b>\n\n"
                "Общая активность: <b>средняя</b>.\n"
                "Топ-3 участника: данные из mock-режима.\n\n"
                "<b>🗂 Саммари чатов</b>\n\n"
                "Все чаты работали в штатном режиме. "
                "Подробности доступны в саммари каждого чата.\n\n"
                "<i>⚠️ Это mock-отчёт для тестирования. "
                "Подключите ANTHROPIC_API_KEY для реальной аналитики.</i>"
            )
        elif call_type == "task_detect_l1":
            text = "[]"
        elif call_type == "task_validate_l2":
            text = "[]"
        else:
            text = f"[Mock response for {call_type}]"

        return ClaudeResponse(
            text=text, tokens_in=100, tokens_out=50,
            cost_usd=Decimal("0.0001"), model="mock",
        )


class ClaudeClient:
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._cb = _CircuitBreaker()

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        call_type: str = "generic",
    ) -> ClaudeResponse:
        # Check circuit breaker
        if self._cb.is_open():
            raise CircuitOpenError(
                f"Circuit breaker open, {self._cb.COOLDOWN_SEC}s cooldown"
            )

        # Check budget
        await self._check_budget()

        # Retry with exponential backoff
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._call_api(
                    system_prompt, user_prompt, max_tokens, temperature
                )
                self._cb.record_success()

                # Extract result
                text = response.content[0].text
                tokens_in = response.usage.input_tokens
                tokens_out = response.usage.output_tokens
                cost = self._calculate_cost(tokens_in, tokens_out)

                # Log usage
                await self._log_usage(call_type, tokens_in, tokens_out, cost)

                return ClaudeResponse(
                    text=text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost,
                    model=response.model,
                )

            except (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ) as e:
                last_error = e
                self._cb.record_failure()
                if attempt == 2:
                    raise
                delay = (2 ** (attempt + 1)) + random.uniform(0, 1)
                logger.warning(
                    "claude_retry attempt={} delay={:.1f}s error={}",
                    attempt + 1,
                    delay,
                    str(e),
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> anthropic.types.Message:
        return await self._client.messages.create(
            model=settings.claude_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

    async def _check_budget(self) -> None:
        tz = ZoneInfo(settings.timezone)
        today = datetime.datetime.now(tz).date()
        async with async_session() as session:
            daily_cost = await get_daily_cost(session, today, tz)
        if daily_cost >= Decimal(str(settings.daily_ai_budget_usd)):
            raise BudgetExceededError(
                f"Daily budget exceeded: ${daily_cost:.2f} >= ${settings.daily_ai_budget_usd:.2f}"
            )

    def _calculate_cost(self, tokens_in: int, tokens_out: int) -> Decimal:
        cost_in = Decimal(tokens_in) * PRICE_INPUT_PER_M / Decimal("1000000")
        cost_out = Decimal(tokens_out) * PRICE_OUTPUT_PER_M / Decimal("1000000")
        return cost_in + cost_out

    async def _log_usage(
        self,
        call_type: str,
        tokens_in: int,
        tokens_out: int,
        cost: Decimal,
    ) -> None:
        async with async_session() as session:
            await log_api_usage(
                session,
                provider="anthropic",
                call_type=call_type,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                estimated_cost_usd=cost,
            )


@functools.lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    prompts_dir = Path(__file__).resolve().parents[3] / "prompts"
    path = prompts_dir / f"{name}.txt"
    return path.read_text(encoding="utf-8")


# Module singleton: use mock if API key is placeholder or empty
_is_mock = not settings.anthropic_api_key or settings.anthropic_api_key.startswith("sk-ant-test")
claude_client: ClaudeClient | MockClaudeClient = MockClaudeClient() if _is_mock else ClaudeClient()
if _is_mock:
    logger.warning("Claude API key not configured — using MOCK mode")
