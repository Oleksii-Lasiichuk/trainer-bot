from __future__ import annotations

import pytest

from trainer_bot.storage.repositories import MessageRepository, UserRepository


@pytest.mark.asyncio
async def test_user_get_or_create_and_settings(db) -> None:
    async with db.session_factory() as session:
        repo = UserRepository(session)
        user = await repo.get_or_create(7, "lee")
        assert user.telegram_id == 7
        assert user.telegram_username == "lee"

        same = await repo.get_or_create(7, "lee")
        assert same.telegram_id == 7

        await repo.upsert_settings(
            7,
            intervals_athlete_id="i1",
            intervals_api_key="k",
            timezone="Europe/Kyiv",
        )
        await session.commit()

        s = await repo.get_settings(7)
        assert s is not None
        assert s.is_configured
        assert s.intervals_athlete_id == "i1"


@pytest.mark.asyncio
async def test_message_roundtrip_and_clear(db) -> None:
    async with db.session_factory() as session:
        users = UserRepository(session)
        await users.get_or_create(8, "m")
        msgs = MessageRepository(session)
        await msgs.add_user_message(8, "hi")
        await msgs.add_assistant_message(
            8,
            content=None,
            tool_calls=[{"id": "call_1", "type": "function",
                          "function": {"name": "t", "arguments": "{}"}}],
        )
        await msgs.add_tool_message(8, "call_1", "t", "{\"ok\": true}")
        await msgs.add_assistant_message(8, "answer", None)
        await session.commit()

        recent = await msgs.get_recent(8, limit=10)
        assert [m.role.value for m in recent] == ["user", "assistant", "tool", "assistant"]
        assert recent[1].tool_calls_json is not None
        assert recent[2].tool_call_id == "call_1"

        removed = await msgs.clear_for_user(8)
        await session.commit()
        assert removed == 4
        assert await msgs.get_recent(8, limit=5) == []
