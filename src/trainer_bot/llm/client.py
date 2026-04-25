"""Groq client — thin wrapper over OpenAI SDK with rate-limit aware retries + fallback model."""

from __future__ import annotations

import asyncio
from typing import Any

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletion

from ..config import Settings
from ..utils.logging import get_logger
from ..utils.ratelimit import compute_backoff

log = get_logger(__name__)


class GroqChat:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            timeout=settings.request_timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float = 0.3,
        model: str | None = None,
    ) -> ChatCompletion:
        """Single chat completion with retry + fallback to smaller model on persistent 429."""
        primary = model or self._settings.groq_model_primary
        fallback = self._settings.groq_model_fallback
        attempts_primary = 3

        last_exc: Exception | None = None
        for attempt in range(attempts_primary):
            try:
                return await self._call(primary, messages, tools, tool_choice, temperature)
            except RateLimitError as e:
                last_exc = e
                delay = _backoff_from_exc(e, default=10.0 + 5.0 * attempt)
                log.warning(
                    "groq.rate_limited",
                    model=primary,
                    attempt=attempt + 1,
                    sleep_s=delay,
                )
                await asyncio.sleep(delay)
            except (APIConnectionError, APIStatusError) as e:
                last_exc = e
                # 413 = payload too big for model's TPM/context → stop retrying primary,
                # drop to fallback model (smaller + cheaper + larger TPM headroom).
                if isinstance(e, APIStatusError) and e.status_code == 413:
                    log.warning("groq.payload_too_large", model=primary)
                    break
                if isinstance(e, APIStatusError) and e.status_code and e.status_code < 500:
                    raise
                delay = 1.5 * (attempt + 1)
                log.warning(
                    "groq.transient_error",
                    model=primary,
                    attempt=attempt + 1,
                    error=str(e),
                    sleep_s=delay,
                )
                await asyncio.sleep(delay)

        # All primary attempts failed → try fallback once
        log.warning("groq.falling_back", fallback=fallback)
        try:
            return await self._call(fallback, messages, tools, tool_choice, temperature)
        except Exception as e:
            log.error("groq.fallback_failed", error=str(e))
            raise (last_exc or e) from e

    async def _call(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any],
        temperature: float,
    ) -> ChatCompletion:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        return await self._client.chat.completions.create(**kwargs)


def _backoff_from_exc(exc: RateLimitError, default: float) -> float:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is None:
        return default
    try:
        return compute_backoff(dict(headers), default=default)
    except Exception:
        return default
