# trainer-bot

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

A self-hostable Telegram bot that acts as a personal training/health advisor. Chat with it in natural language — "how was my sleep this week?", "should I run hard tomorrow given my load?", "compare my last two long runs" — and it pulls your data from **intervals.icu** (which aggregates Garmin + Strava natively) and answers using an LLM via **Groq** (free tier).

## Features

- Natural-language Q&A over your training and wellness data.
- Tool-calling agent: the LLM decides exactly which data to fetch from intervals.icu for each question — cheap on tokens, accurate on stats.
- Multi-user: each Telegram user attaches their own intervals.icu credentials via `/setkey`. No shared state.
- Conversation memory persisted in SQLite (or Postgres).
- Graceful rate-limit handling against Groq's free tier, with automatic fallback from `llama-3.3-70b-versatile` to `llama-3.1-8b-instant`.
- One-command Docker deploy; configs for Fly.io, Oracle Cloud Free Tier and Railway included.

## Architecture

```
Telegram ─▶ python-telegram-bot ─▶ Agent loop ─▶ Groq (LLM)
                                       │           │
                                       │           ▼ tool calls
                                       │   intervals.icu REST API
                                       ▼
                                 SQLite (users, settings, messages)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for details on the agent loop, tool schemas and data flow.

## Quick start

Full walkthrough is in **[INSTRUCTIONS.md](INSTRUCTIONS.md)** — it takes a developer who has never used Telegram bots, intervals.icu or Groq from zero to a running bot in under 30 minutes.

Shortest path once you have `.env` filled in:

```bash
docker compose up -d
```

In Telegram: `/start` → `/setkey` → paste your intervals.icu athlete ID and API key → ask a question.

## Stack

- Python 3.11+, async everywhere.
- [python-telegram-bot](https://python-telegram-bot.org/) v22+
- [OpenAI SDK](https://github.com/openai/openai-python) pointed at Groq's OpenAI-compatible endpoint
- [httpx](https://www.python-httpx.org/) for intervals.icu
- [SQLAlchemy 2.0 async](https://docs.sqlalchemy.org/en/20/) + SQLite/Postgres
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) for env-driven config
- [structlog](https://www.structlog.org/) for logging
- [tenacity](https://tenacity.readthedocs.io/) for retries

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src/ tests/
```

## Roadmap

- OAuth multi-tenant SaaS mode (so users don't paste API keys)
- Voice messages via Whisper on Groq
- Planned-workout uploads (`POST /planned` to intervals.icu)
- Webhook mode for lower latency than polling
- RAG over long activity history

## License

MIT — see [LICENSE](LICENSE).
