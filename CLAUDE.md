# CLAUDE.md — RGO Monitoring Bot

## Project Overview

Telegram-бот мониторинга деловой активности 7 региональных групп офисов (РГО). Бот молчит в группах, собирает сообщения, анализирует через Claude AI, отправляет отчёты Начальнику управления (НУ) в ЛС.

## Tech Stack

- Python 3.11+ / aiogram 3.x (async)
- PostgreSQL 16+ / SQLAlchemy async + Alembic
- Anthropic Claude API (claude-sonnet) / OpenAI Whisper API
- APScheduler 4.x / matplotlib / Docker

## Project Structure

```
rgo_bot/
├── bot/
│   ├── main.py              # Entry point
│   ├── config.py            # Pydantic Settings, loads .env
│   ├── middleware/           # AdminOnlyMiddleware
│   ├── handlers/             # group_messages, admin_private, rgo_private
│   └── services/             # Business logic modules
├── db/
│   ├── base.py              # async engine + session factory
│   ├── models.py            # SQLAlchemy ORM models
│   └── crud/                # CRUD operations per entity
├── prompts/                 # Claude prompt templates (.txt)
└── tests/
```

## Key Commands

```bash
# Run locally
docker-compose up -d

# Run bot without Docker
python -m rgo_bot.bot.main

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Run tests
pytest tests/ -v

# Check types
mypy rgo_bot/
```

## Code Style

- Async everywhere: use `async def`, `await`, `async with`
- Type hints on all function signatures
- Pydantic models for all Claude API responses (validate JSON)
- Environment variables via `config.py` (Pydantic Settings), never hardcode secrets
- Logging via `loguru` — log metadata only, never message text content
- All database queries through `crud/` modules, not inline SQL
- Error messages to user in Russian

## Architecture Decisions

- **No tags for task detection** — Claude AI analyzes context only, no #hashtags
- **Map-reduce for reports** — 7 parallel MAP calls (one per chat) + 1 REDUCE call
- **Bot is silent in groups** — never writes to group chats, only collects
- **Single admin** — ADMIN_TELEGRAM_ID from .env, checked via middleware
- **Partitioned messages** — pg_partman by month, auto-create 2 months ahead
- **BytesIO for charts** — matplotlib renders to memory, no temp files on disk
- **Report splitting** — 5 logical sections, each <= 4000 chars, 300ms delay between sends
- **Weekends off** — no reports/recommendations on Sat/Sun, alerts partially disabled
- **Timezone** — Asia/Yekaterinburg

## Claude API Integration

- All calls go through `claude_client.py` with retry (3 attempts, exponential backoff)
- Circuit breaker: 5 consecutive failures → 15 min pause + alert to admin
- Use Anthropic Tool Use (structured output) for JSON responses
- Daily budget cap: DAILY_AI_BUDGET_USD in .env
- Track all token usage in `api_usage` table

## Testing

- Unit tests: Pydantic parsing, chart generation, message splitting
- Integration tests: real PostgreSQL via testcontainers
- Claude API mocks: pre-recorded responses for deterministic tests
- Telegram mocks: aiogram MockedBot

## Development Plan

1. MVP: data collection (current phase)
2. Daily report (text)
3. Task detection
4. Charts
5. Admin commands
6. Extended analytics + alerts
7. Voice transcription
8. RGO recommendations
9. Deploy + tests
