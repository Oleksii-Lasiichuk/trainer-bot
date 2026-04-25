from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from trainer_bot.intervals.client import IntervalsClient
from trainer_bot.llm.agent import Agent
from trainer_bot.storage.repositories import MessageRepository, UserRepository


class ScriptedGroqChat:
    """Stub that returns pre-scripted completions and records call count."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, **_kwargs: Any) -> Any:
        self.calls += 1
        if not self._responses:
            raise AssertionError("ScriptedGroqChat exhausted")
        return self._responses.pop(0)


def _completion(content: str | None, tool_calls: list[dict[str, Any]] | None = None) -> Any:
    calls = []
    for tc in tool_calls or []:
        calls.append(
            SimpleNamespace(
                id=tc["id"],
                type="function",
                function=SimpleNamespace(
                    name=tc["name"], arguments=tc["arguments"]
                ),
            )
        )
    choice = SimpleNamespace(
        index=0,
        finish_reason="stop" if not calls else "tool_calls",
        message=SimpleNamespace(
            role="assistant",
            content=content,
            tool_calls=calls or None,
        ),
    )
    return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_agent_tool_call_then_answer(db, test_settings) -> None:
    async with db.session_factory() as session:
        await UserRepository(session).get_or_create(11, "x")
        await session.commit()

    groq = ScriptedGroqChat(
        [
            _completion(
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "get_recent_activities",
                        "arguments": '{"days": 7, "limit": 1}',
                    }
                ],
            ),
            _completion(content="Your last run was 5 km at 5:00/km pace."),
        ]
    )
    agent = Agent(test_settings, groq)  # type: ignore[arg-type]

    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i1/activities").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "iA",
                        "name": "Run",
                        "type": "Run",
                        "start_date_local": "2026-04-22T07:00:00",
                        "distance": 5000,
                        "moving_time": 1500,
                        "average_speed": 3.33,
                    }
                ],
            )
        )
        async with IntervalsClient("i1", "k") as ic, db.session_factory() as session:
            result = await agent.run(
                session=session,
                user_id=11,
                user_message="what was my last run?",
                intervals=ic,
            )

    assert groq.calls == 2
    assert "5 km" in result.text
    assert result.tool_calls == 1

    async with db.session_factory() as session:
        msgs = await MessageRepository(session).get_recent(11, limit=20)
    roles = [m.role.value for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].tool_calls_json is not None
    assert msgs[2].tool_call_id == "call_1"


@pytest.mark.asyncio
async def test_agent_hits_max_iterations(db, test_settings) -> None:
    async with db.session_factory() as session:
        await UserRepository(session).get_or_create(12, "y")
        await session.commit()

    # Settings says max_tool_iterations = 3 — return tool_calls every time.
    loop_resp = _completion(
        content=None,
        tool_calls=[
            {
                "id": "call_x",
                "name": "get_current_date_and_time",
                "arguments": "{}",
            }
        ],
    )
    groq = ScriptedGroqChat([loop_resp, loop_resp, loop_resp])
    agent = Agent(test_settings, groq)  # type: ignore[arg-type]

    async with IntervalsClient("i1", "k") as ic, db.session_factory() as session:
        result = await agent.run(
            session=session,
            user_id=12,
            user_message="loop",
            intervals=ic,
        )

    assert "stuck" in result.text.lower()
    assert groq.calls == 3
    assert result.tool_calls == 3


@pytest.mark.asyncio
async def test_agent_includes_history(db, test_settings) -> None:
    async with db.session_factory() as session:
        await UserRepository(session).get_or_create(13, "h")
        msgs = MessageRepository(session)
        await msgs.add_user_message(13, "first question")
        await msgs.add_assistant_message(13, "first answer", None)
        await session.commit()

    captured: dict[str, Any] = {}

    class CapturingChat(ScriptedGroqChat):
        async def chat(self, **kwargs: Any) -> Any:
            captured["messages"] = kwargs["messages"]
            return await super().chat(**kwargs)

    groq = CapturingChat([_completion(content="ok follow-up")])
    agent = Agent(test_settings, groq)  # type: ignore[arg-type]

    async with IntervalsClient("i1", "k") as ic, db.session_factory() as session:
        result = await agent.run(
            session=session,
            user_id=13,
            user_message="second question",
            intervals=ic,
        )

    msgs_sent = captured["messages"]
    assert msgs_sent[0]["role"] == "system"
    assert any(
        m.get("role") == "user" and m.get("content") == "first question" for m in msgs_sent
    )
    assert any(
        m.get("role") == "assistant" and m.get("content") == "first answer" for m in msgs_sent
    )
    # The agent mutates the same list `captured` references, so trailing entries
    # may include the assistant response. Just assert the new user message is present.
    assert any(
        m.get("role") == "user" and m.get("content") == "second question" for m in msgs_sent
    )
    assert result.text == "ok follow-up"
