from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _split_str(v: str | list, cast: type = str) -> list:
    if isinstance(v, list):
        return [cast(x) for x in v]
    if isinstance(v, str) and v.strip():
        return [cast(x.strip()) for x in v.split(",") if x.strip()]
    return []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram ───────────────────────────────────
    bot_token: str
    admin_telegram_id: int
    report_recipients: str | int | list[int] = ""
    admin_name_aliases: str | list[str] = ""
    monitored_chat_ids: str | int | list[int] = ""

    # ── AI API ─────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_proxy_url: str = ""  # HTTP proxy for Anthropic API (e.g. http://127.0.0.1:10809)
    claude_model: str = "claude-sonnet-4-20250514"
    openai_api_key: str = ""
    groq_api_key: str = ""
    groq_proxy_url: str = ""  # HTTP proxy for Groq API (e.g. http://127.0.0.1:10809)
    whisper_enabled: bool = True
    daily_ai_budget_usd: float = 5.0
    initial_balance_usd: float = 25.0  # Starting Anthropic balance, set once

    # ── Database ───────────────────────────────────
    database_url: str = "postgresql+asyncpg://rgo_bot:rgo_bot_password@localhost:5432/rgo_bot"

    # ── Schedule ───────────────────────────────────
    timezone: str = "Asia/Yekaterinburg"
    work_days: str | list[int] = "1,2,3,4,5"
    daily_report_time: str = "19:00"
    morning_rec_time: str = "08:30"
    task_classifier_interval_min: int = 60

    # ── Thresholds ─────────────────────────────────
    task_confidence_threshold: float = 0.70
    silence_alert_hours: int = 3
    silence_work_start: int = 9
    silence_work_end: int = 19
    mass_forward_threshold: int = 5
    self_control_low_threshold: float = 0.40

    # ── Web (Mini App) ────────────────────────────────
    web_port: int = 8080
    webapp_url: str = ""  # e.g. https://your-domain.com

    # ── Logging ────────────────────────────────────
    log_level: str = "INFO"
    log_retention_days: int = 30

    @model_validator(mode="after")
    def parse_comma_fields(self) -> Settings:
        if isinstance(self.admin_name_aliases, str):
            object.__setattr__(
                self, "admin_name_aliases", _split_str(self.admin_name_aliases)
            )
        if isinstance(self.report_recipients, int):
            object.__setattr__(
                self, "report_recipients", [self.report_recipients]
            )
        elif isinstance(self.report_recipients, str):
            rr = _split_str(self.report_recipients, int)
            if not rr:
                rr = [self.admin_telegram_id]
            object.__setattr__(self, "report_recipients", rr)
        if isinstance(self.monitored_chat_ids, int):
            object.__setattr__(
                self, "monitored_chat_ids", [self.monitored_chat_ids]
            )
        elif isinstance(self.monitored_chat_ids, str):
            object.__setattr__(
                self, "monitored_chat_ids", _split_str(self.monitored_chat_ids, int)
            )
        if isinstance(self.work_days, str):
            object.__setattr__(
                self, "work_days", _split_str(self.work_days, int)
            )
        return self


settings = Settings()  # type: ignore[call-arg]
