"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

# Ensure required env vars exist before Settings() is constructed anywhere.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-telegram-token")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from trainer_bot.config import Settings  # noqa: E402
from trainer_bot.storage.db import Database  # noqa: E402


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="test-telegram-token",
        GROQ_API_KEY="test-groq-key",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        ALLOWED_TELEGRAM_USER_IDS="",
        MAX_HISTORY_MESSAGES=10,
        MAX_TOOL_ITERATIONS=3,
    )  # type: ignore[call-arg]


@pytest_asyncio.fixture
async def db() -> AsyncIterator[Database]:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    try:
        yield database
    finally:
        await database.dispose()
