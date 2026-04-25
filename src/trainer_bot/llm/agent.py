"""Tool-calling agent loop: LLM ↔ intervals.icu tools ↔ SQLite history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..intervals.client import IntervalsClient
from ..storage.models import Message, MessageRole
from ..storage.repositories import MessageRepository
from ..utils.logging import get_logger
from .client import GroqChat
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, ToolContext, dispatch_tool

log = get_logger(__name__)


STUCK_REPLY = "I got stuck trying to answer that. Try rephrasing or ask for something more specific."


@dataclass
class AgentResult:
    text: str
    tool_calls: int
    iterations: int


def history_to_openai_format(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert stored ORM messages to OpenAI chat format."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role.value if isinstance(m.role, MessageRole) else str(m.role)
        if role == MessageRole.USER.value:
            out.append({"role": "user", "content": m.content or ""})
        elif role == MessageRole.ASSISTANT.value:
            payload: dict[str, Any] = {"role": "assistant", "content": m.content}
            if m.tool_calls_json:
                payload["tool_calls"] = m.tool_calls_json
            out.append(payload)
        elif role == MessageRole.TOOL.value:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id or "",
                    "content": m.content or "",
                }
            )
        elif role == MessageRole.SYSTEM.value:
            out.append({"role": "system", "content": m.content or ""})
    return out


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Cheap conservative token estimate for an OpenAI-style messages list.

    ~3 chars per token (English-leaning, biased high) + small per-message overhead.
    Good enough to keep us under Groq's per-request TPM ceiling without pulling in tiktoken.
    """
    total_chars = 0
    per_msg_overhead = 0
    for m in messages:
        per_msg_overhead += 4
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif content is not None:
            total_chars += len(json.dumps(content, default=str, ensure_ascii=False))
        if m.get("tool_calls"):
            total_chars += len(json.dumps(m["tool_calls"], default=str, ensure_ascii=False))
        if m.get("tool_call_id"):
            total_chars += len(str(m["tool_call_id"]))
        if m.get("name"):
            total_chars += len(str(m["name"]))
    return per_msg_overhead + (total_chars // 3) + 1


def prune_messages_for_budget(
    messages: list[dict[str, Any]], budget_tokens: int
) -> list[dict[str, Any]]:
    """Drop oldest non-pinned messages until estimated tokens fit budget.

    Pinned: leading system messages and the most recent user message. When dropping
    an assistant message that has tool_calls, the corresponding tool messages are
    dropped too so the remaining list stays valid for the OpenAI/Groq API
    (every tool message must follow an assistant message that called it).
    """
    if estimate_message_tokens(messages) <= budget_tokens:
        return list(messages)

    head_end = 0
    while head_end < len(messages) and messages[head_end].get("role") == "system":
        head_end += 1
    head = list(messages[:head_end])
    body = list(messages[head_end:])

    last_user_idx = -1
    for i, m in enumerate(body):
        if m.get("role") == "user":
            last_user_idx = i

    while body and last_user_idx > 0 and estimate_message_tokens(head + body) > budget_tokens:
        dropped = body.pop(0)
        last_user_idx -= 1

        if dropped.get("role") == "assistant" and dropped.get("tool_calls"):
            tool_call_ids = {
                tc.get("id") for tc in dropped["tool_calls"] if isinstance(tc, dict)
            }
            i = 0
            while i < last_user_idx:
                if (
                    body[i].get("role") == "tool"
                    and body[i].get("tool_call_id") in tool_call_ids
                ):
                    body.pop(i)
                    last_user_idx -= 1
                else:
                    i += 1

        # Orphan tool messages at the new head have no assistant tool_calls before them
        # and would be rejected by the API.
        while body and last_user_idx > 0 and body[0].get("role") == "tool":
            body.pop(0)
            last_user_idx -= 1

    return head + body


def _serialize_tool_calls(tool_calls: list[Any] | None) -> list[dict[str, Any]] | None:
    if not tool_calls:
        return None
    out: list[dict[str, Any]] = []
    for call in tool_calls:
        out.append(
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
        )
    return out


class Agent:
    def __init__(self, settings: Settings, groq: GroqChat) -> None:
        self._settings = settings
        self._groq = groq

    async def run(
        self,
        *,
        session: AsyncSession,
        user_id: int,
        user_message: str,
        intervals: IntervalsClient,
        user_timezone: str = "Europe/Kyiv",
    ) -> AgentResult:
        repo = MessageRepository(session)
        history = await repo.get_recent(
            user_id, limit=self._settings.max_history_messages
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        messages.extend(history_to_openai_format(history))
        messages.append({"role": "user", "content": user_message})

        await repo.add_user_message(user_id, user_message)
        await session.commit()

        tool_ctx = ToolContext(
            intervals=intervals, user_id=user_id, user_timezone=user_timezone
        )

        total_tool_calls = 0
        final_text: str | None = None
        iterations = 0

        # Tool schemas are sent on every call; reserve headroom for them.
        schema_tokens = estimate_message_tokens(
            [{"role": "system", "content": json.dumps(TOOL_SCHEMAS, ensure_ascii=False)}]
        )
        effective_budget = max(1000, self._settings.groq_token_budget - schema_tokens)

        for i in range(1, self._settings.max_tool_iterations + 1):
            iterations = i
            messages = prune_messages_for_budget(messages, effective_budget)
            completion = await self._groq.chat(
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.3,
            )
            if not completion.choices:
                log.warning("agent.empty_choices")
                break
            choice = completion.choices[0]
            msg = choice.message
            tool_calls = list(msg.tool_calls or [])

            serialized_tool_calls = _serialize_tool_calls(tool_calls)

            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content,
            }
            if serialized_tool_calls:
                assistant_entry["tool_calls"] = serialized_tool_calls
            messages.append(assistant_entry)

            await repo.add_assistant_message(
                user_id,
                content=msg.content,
                tool_calls=serialized_tool_calls,
            )

            if not tool_calls:
                final_text = msg.content or ""
                await session.commit()
                break

            for call in tool_calls:
                total_tool_calls += 1
                name = call.function.name
                raw_args = call.function.arguments or "{}"
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}
                result = await dispatch_tool(name, args, tool_ctx)
                result_text = json.dumps(result, default=str, ensure_ascii=False)
                # Hard cap on tool payload size to protect against TPM ceiling (Groq free tier
                # llama-3.3-70b = 12k TPM). 6k chars ≈ 1.5k tokens.
                _MAX_TOOL_CHARS = 6000
                if len(result_text) > _MAX_TOOL_CHARS:
                    result_text = (
                        result_text[:_MAX_TOOL_CHARS]
                        + f'..."[truncated, original {len(result_text)} chars]"}}'
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result_text,
                    }
                )
                await repo.add_tool_message(user_id, call.id, name, result_text)

            await session.commit()

        if final_text is None:
            log.warning(
                "agent.stuck_after_max_iterations",
                iterations=iterations,
                tool_calls=total_tool_calls,
            )
            final_text = STUCK_REPLY
            await repo.add_assistant_message(user_id, final_text, None)
            await session.commit()

        return AgentResult(
            text=final_text, tool_calls=total_tool_calls, iterations=iterations
        )
