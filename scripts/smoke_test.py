"""Manual smoke test — hits real intervals.icu and (optionally) Groq APIs.

Requires env:
  INTERVALS_ATHLETE_ID
  INTERVALS_API_KEY
  GROQ_API_KEY          (optional — only needed for full agent run)
  TELEGRAM_BOT_TOKEN    (optional — only needed so config.py loads)

Run:
  python scripts/smoke_test.py                    # client-only smoke
  python scripts/smoke_test.py --agent            # include agent run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from trainer_bot.config import Settings
from trainer_bot.intervals.client import IntervalsClient
from trainer_bot.llm.agent import Agent
from trainer_bot.llm.client import GroqChat
from trainer_bot.storage.db import Database
from trainer_bot.storage.repositories import UserRepository
from trainer_bot.utils.logging import configure_logging, get_logger


async def _client_smoke(athlete_id: str, api_key: str) -> None:
    log = get_logger("smoke.client")
    async with IntervalsClient(athlete_id, api_key) as ic:
        profile = await ic.get_athlete()
        log.info("athlete", name=profile.name, tz=profile.timezone, ftp=profile.icu_ftp)

        today = date.today()
        acts = await ic.list_activities(
            oldest=today - timedelta(days=14), newest=today, limit=5
        )
        log.info("activities", count=len(acts))
        for a in acts[:3]:
            print(json.dumps(a.model_dump(mode="json"), default=str, indent=2)[:1000])

        w = await ic.get_wellness(today - timedelta(days=7), today)
        log.info("wellness", count=len(w))
        if w:
            print(json.dumps(w[-1].model_dump(mode="json"), default=str, indent=2))

        fitness = await ic.get_fitness_and_form(days=30)
        log.info(
            "fitness",
            points=len(fitness.points),
            latest=(fitness.points[-1].model_dump(mode="json") if fitness.points else None),
        )


async def _agent_smoke(athlete_id: str, api_key: str) -> None:
    log = get_logger("smoke.agent")
    env: dict[str, str] = {
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", "dummy"),
        "GROQ_API_KEY": os.environ["GROQ_API_KEY"],
        "DATABASE_URL": "sqlite+aiosqlite:///./data/smoke.db",
        "MAX_HISTORY_MESSAGES": os.getenv("MAX_HISTORY_MESSAGES", "10"),
        "MAX_TOOL_ITERATIONS": os.getenv("MAX_TOOL_ITERATIONS", "5"),
    }
    os.environ.update(env)
    settings = Settings()  # type: ignore[call-arg]
    db = Database(settings.database_url)
    await db.create_all()
    try:
        async with db.session_factory() as session:  # type: AsyncSession
            repo = UserRepository(session)
            await repo.get_or_create(1, "smoke")
            await repo.upsert_settings(
                1, intervals_athlete_id=athlete_id, intervals_api_key=api_key
            )
            await session.commit()

        groq = GroqChat(settings)
        agent = Agent(settings, groq)
        async with IntervalsClient(athlete_id, api_key) as ic:
            async with db.session_factory() as session:
                result = await agent.run(
                    session=session,
                    user_id=1,
                    user_message="What was my last run? Be brief.",
                    intervals=ic,
                )
        log.info(
            "agent.result",
            iterations=result.iterations,
            tool_calls=result.tool_calls,
        )
        print("=== ASSISTANT ===")
        print(result.text)
        await groq.aclose()
    finally:
        await db.dispose()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="store_true", help="also run full agent turn")
    args = parser.parse_args()

    configure_logging("INFO")
    athlete_id = os.environ.get("INTERVALS_ATHLETE_ID")
    api_key = os.environ.get("INTERVALS_API_KEY")
    if not athlete_id or not api_key:
        print(
            "Set INTERVALS_ATHLETE_ID and INTERVALS_API_KEY env vars before running.",
            file=sys.stderr,
        )
        sys.exit(2)

    await _client_smoke(athlete_id, api_key)

    if args.agent:
        if not os.environ.get("GROQ_API_KEY"):
            print("GROQ_API_KEY required for --agent mode.", file=sys.stderr)
            sys.exit(2)
        await _agent_smoke(athlete_id, api_key)


if __name__ == "__main__":
    asyncio.run(main())
