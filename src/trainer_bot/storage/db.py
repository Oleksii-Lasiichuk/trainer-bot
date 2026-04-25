"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..utils.logging import get_logger
from .models import Base

log = get_logger(__name__)


def _ensure_sqlite_parent_dir(url: str) -> None:
    """For sqlite URLs, make sure the parent directory exists."""
    if not url.startswith("sqlite"):
        return
    # strip driver suffix (e.g. sqlite+aiosqlite:///./data/bot.db)
    parsed = urlparse(url)
    # sqlite URL path after '///': relative vs absolute
    path_str = parsed.path
    if not path_str:
        return
    if path_str.startswith("/./"):
        path_str = path_str[1:]
    # handle sqlite+aiosqlite:///./data/bot.db  -> path '/./data/bot.db'
    db_path = Path(path_str.lstrip("/")) if path_str.startswith("/./") else Path(path_str)
    parent = db_path.parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


class Database:
    def __init__(self, url: str) -> None:
        _ensure_sqlite_parent_dir(url)
        self._url = url
        self.engine: AsyncEngine = create_async_engine(
            url,
            echo=bool(os.getenv("SQL_ECHO")),
            future=True,
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("db.schema_ready", url=self._url)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as s:
            yield s


_instance: Database | None = None


def get_database(url: str | None = None) -> Database:
    global _instance
    if _instance is None:
        if url is None:
            raise RuntimeError("Database not initialized; pass url on first call.")
        _instance = Database(url)
    return _instance


def reset_database_singleton() -> None:
    """Test hook — clear the cached singleton."""
    global _instance
    _instance = None
