# Architecture

This document is for contributors. For setup/deploy instructions see [INSTRUCTIONS.md](INSTRUCTIONS.md).

## Data flow

```
┌──────────────────────────────────────────────────────────────┐
│                       Telegram User                          │
└──────────────────────────┬───────────────────────────────────┘
                           │ text message
                           ▼
┌──────────────────────────────────────────────────────────────┐
│   python-telegram-bot (async, long polling)                  │
│   Commands: /start /setkey /reset /whoami /ping /help        │
│   Message handler: free-text → Agent                         │
│   Whitelist enforced via ALLOWED_TELEGRAM_USER_IDS            │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    Agent loop (llm/agent.py)                 │
│   1. Load recent conversation history for this user          │
│   2. Build OpenAI messages list: system + history + user msg │
│   3. Call Groq with tool schemas                             │
│   4. While LLM returns tool_calls:                           │
│        - Dispatch to tools.py                                │
│        - Append tool result → messages                       │
│        - Call Groq again                                     │
│   5. Return final text                                       │
│   6. Persist user + assistant (+ tool) messages in SQLite    │
└────────────┬──────────────────────────┬──────────────────────┘
             │                          │
             ▼                          ▼
     ┌──────────────┐           ┌────────────────────┐
     │    Groq      │           │  intervals.icu API │
     │  OpenAI-com. │           │  HTTP Basic auth   │
     └──────────────┘           └────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│   SQLite (aiosqlite) — users, user_settings, messages        │
└──────────────────────────────────────────────────────────────┘
```

## Why tool-calling and not "dump everything into the prompt"

The free tier of Groq is 6 000 tokens per minute on the 70B model. Stuffing every activity + wellness record into the context burns that budget fast and wastes tokens on data the user didn't ask about. Tool-calling lets the LLM decide what to pull per question:

- *"How was my sleep this week?"* → `get_wellness_range(days=7)`
- *"Compare my two latest runs"* → `search_activities_by_type(sport_type="run", days=14, limit=2)` then `get_activity_detail(id=...)` twice
- *"Should I run hard tomorrow?"* → `get_fitness_trend(days=42)` plus `get_wellness_today()`

Tools format their output for LLM consumption — km not meters, minutes not seconds, pace as `min:sec/km`, explicit nulls when Garmin didn't push a field.

## Modules

| Module | Responsibility |
|---|---|
| `trainer_bot.config` | Env-driven settings via pydantic-settings |
| `trainer_bot.utils.logging` | structlog configuration (console or JSON) |
| `trainer_bot.utils.ratelimit` | Parse Groq `retry-after` / `x-ratelimit-*` headers |
| `trainer_bot.intervals.client` | Async httpx wrapper over intervals.icu REST |
| `trainer_bot.intervals.schemas` | Tolerant pydantic models (nullable fields everywhere) |
| `trainer_bot.intervals.errors` | Typed exceptions for 401/404/429/5xx |
| `trainer_bot.storage.models` | SQLAlchemy ORM: `User`, `UserSetting`, `Message` |
| `trainer_bot.storage.repositories` | CRUD helpers |
| `trainer_bot.llm.client` | Groq via OpenAI SDK, retry + fallback model |
| `trainer_bot.llm.tools` | Tool schemas (OpenAI function-calling) + dispatch |
| `trainer_bot.llm.agent` | Tool-calling loop |
| `trainer_bot.llm.prompts` | System prompt |
| `trainer_bot.bot.app` | Wires the Application together |
| `trainer_bot.bot.handlers` | Command + message handlers |
| `trainer_bot.bot.auth` | Whitelist check |
| `trainer_bot.bot.formatting` | Split long responses on Telegram's 4096-char limit |

## Multi-user isolation

Every user's intervals.icu credentials live in `user_settings` keyed by `telegram_id`. For every message, the handler instantiates a fresh `IntervalsClient` bound to that user's key. There is no cross-user data access. Use `ALLOWED_TELEGRAM_USER_IDS` (comma-separated numeric user IDs) to run a private instance. If left empty the bot is open — anyone can DM it and burn your Groq quota. Always set it.

## Rate limiting

- **Groq 429**: the client uses `retry-after` headers when present. Primary model is retried up to 3× with backoff; on persistent failure the call is retried once on `GROQ_MODEL_FALLBACK` (`llama-3.1-8b-instant` by default — 14 400 requests/day allowance).
- **intervals.icu 5xx / network**: `tenacity` retries with exponential backoff.
- **intervals.icu 429/401/404**: surfaced as typed exceptions; not retried.

## Conversation memory

The last `MAX_HISTORY_MESSAGES` (default 20) messages for each user are loaded at the start of each turn and passed to the LLM. Tool calls + tool results are stored too, so the next turn can see what data was fetched.

## Migrating to Postgres

The schema is driver-agnostic. Set `DATABASE_URL=postgresql+asyncpg://user:pass@host/db` and install the `postgres` extra: `pip install ".[postgres]"`.

## Testing

- `tests/test_intervals_client.py` — respx mocks the REST API; covers auth headers, 4xx/5xx, retries, wellness null handling, CTL/ATL/TSB derivation.
- `tests/test_tools.py` — tool dispatch, formatting, error mapping.
- `tests/test_agent_loop.py` — scripted Groq responses drive the loop: tool-call → answer, max-iterations cutoff, history inclusion.
- `tests/test_handlers.py` — whitelist enforcement, `/reset`, `/ping` ok+401, `/whoami`.
- `tests/test_repositories.py` — ORM CRUD on in-memory SQLite.
- `tests/test_config.py` / `test_ratelimit.py` / `test_formatting.py` — unit.

Run: `pytest -q`.

## Out of scope for v1

- Direct Garmin Connect integration (no public API; unreliable unofficial libs).
- Voice messages (separate Whisper quota; defer).
- Write path (workout generation, planned-workout uploads).
- Native web dashboard.
- RAG over full activity history (tool-calling already covers real use cases).
