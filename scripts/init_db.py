"""Create all tables on the configured DATABASE_URL. Idempotent."""

from __future__ import annotations

import asyncio

from trainer_bot.config import get_settings
from trainer_bot.storage.db import Database
from trainer_bot.utils.logging import configure_logging, get_logger


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("init_db")
    db = Database(settings.database_url)
    await db.create_all()
    await db.dispose()
    log.info("init_db.done", url=settings.database_url)


if __name__ == "__main__":
    asyncio.run(main())
