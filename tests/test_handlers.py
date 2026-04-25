"""Smoke tests for handler plumbing (whitelist, reset, ping)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from trainer_bot.bot import handlers as h
from trainer_bot.bot.handlers import AppServices
from trainer_bot.config import Settings
from trainer_bot.storage.repositories import MessageRepository, UserRepository


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, **_kw: Any) -> None:
        self.messages.append(text)

    async def send_chat_action(self, *_a: Any, **_kw: Any) -> None:
        return None


def make_update(user_id: int, text: str | None = None) -> Any:
    chat = FakeChat()
    user = SimpleNamespace(id=user_id, username="tester", first_name="T")
    message = SimpleNamespace(text=text, reply_text=chat.send_message)
    return SimpleNamespace(
        effective_user=user,
        effective_chat=chat,
        message=message,
    ), chat


def make_context(services: AppServices, user_data: dict | None = None) -> Any:
    app = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(application=app, user_data=user_data or {})


@pytest.mark.asyncio
async def test_reject_when_not_in_whitelist(db) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="t",
        GROQ_API_KEY="g",
        ALLOWED_TELEGRAM_USER_IDS="1,2",
        _env_file=None,
    )  # type: ignore[call-arg]
    svc = AppServices(settings=settings, database=db, agent=None)  # type: ignore[arg-type]
    upd, chat = make_update(99, "hi there")
    ctx = make_context(svc)
    await h.handle_message(upd, ctx)
    assert any("allowlist" in m.lower() for m in chat.messages)


@pytest.mark.asyncio
async def test_message_without_setkey_nudges(db) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="t",
        GROQ_API_KEY="g",
        ALLOWED_TELEGRAM_USER_IDS="",
        _env_file=None,
    )  # type: ignore[call-arg]
    svc = AppServices(settings=settings, database=db, agent=None)  # type: ignore[arg-type]
    upd, chat = make_update(55, "hi")
    ctx = make_context(svc)
    await h.handle_message(upd, ctx)
    assert any("/setkey" in m for m in chat.messages)


@pytest.mark.asyncio
async def test_reset_clears_history(db) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="t",
        GROQ_API_KEY="g",
        ALLOWED_TELEGRAM_USER_IDS="",
        _env_file=None,
    )  # type: ignore[call-arg]
    svc = AppServices(settings=settings, database=db, agent=None)  # type: ignore[arg-type]

    async with db.session_factory() as s:
        await UserRepository(s).get_or_create(77, "r")
        msgs = MessageRepository(s)
        await msgs.add_user_message(77, "hi")
        await msgs.add_assistant_message(77, "hello", None)
        await s.commit()

    upd, chat = make_update(77)
    await h.cmd_reset(upd, make_context(svc))

    assert any("cleared" in m.lower() for m in chat.messages)
    async with db.session_factory() as s:
        remaining = await MessageRepository(s).get_recent(77, limit=10)
    assert remaining == []


@pytest.mark.asyncio
async def test_ping_ok_and_auth_error(db) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="t",
        GROQ_API_KEY="g",
        ALLOWED_TELEGRAM_USER_IDS="",
        _env_file=None,
    )  # type: ignore[call-arg]
    svc = AppServices(settings=settings, database=db, agent=None)  # type: ignore[arg-type]

    async with db.session_factory() as s:
        await UserRepository(s).get_or_create(88, "p")
        await UserRepository(s).upsert_settings(
            88, intervals_athlete_id="i88", intervals_api_key="k"
        )
        await s.commit()

    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i88").mock(
            return_value=httpx.Response(
                200, json={"name": "Me", "timezone": "UTC", "icu_ftp": 250}
            )
        )
        upd, chat = make_update(88)
        await h.cmd_ping(upd, make_context(svc))
    assert any("Connected" in m for m in chat.messages)

    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i88").mock(
            return_value=httpx.Response(401, text="no")
        )
        upd, chat = make_update(88)
        await h.cmd_ping(upd, make_context(svc))
    assert any("401" in m or "/setkey" in m for m in chat.messages)


@pytest.mark.asyncio
async def test_whoami_without_setup(db) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="t",
        GROQ_API_KEY="g",
        ALLOWED_TELEGRAM_USER_IDS="",
        _env_file=None,
    )  # type: ignore[call-arg]
    svc = AppServices(settings=settings, database=db, agent=None)  # type: ignore[arg-type]
    upd, chat = make_update(33)
    await h.cmd_whoami(upd, make_context(svc))
    assert any("setkey" in m.lower() for m in chat.messages)
