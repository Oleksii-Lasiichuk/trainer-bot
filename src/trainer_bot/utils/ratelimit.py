"""Helpers for interpreting provider rate-limit headers."""

from __future__ import annotations

import re
from collections.abc import Mapping

_DURATION_RE = re.compile(
    r"(?:(?P<h>\d+(?:\.\d+)?)h)?"
    r"(?:(?P<m>\d+(?:\.\d+)?)m(?!s))?"
    r"(?:(?P<s>\d+(?:\.\d+)?)s)?"
    r"(?:(?P<ms>\d+(?:\.\d+)?)ms)?"
)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Groq/OpenAI style duration string like '1m30s' or '250ms' or '12.5s' or '42'."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    m = _DURATION_RE.fullmatch(value)
    if not m or not any(m.groupdict().values()):
        return None
    total = 0.0
    if m.group("h"):
        total += float(m.group("h")) * 3600
    if m.group("m"):
        total += float(m.group("m")) * 60
    if m.group("s"):
        total += float(m.group("s"))
    if m.group("ms"):
        total += float(m.group("ms")) / 1000.0
    return total or None


def compute_backoff(headers: Mapping[str, str], default: float = 30.0) -> float:
    """Pick a sensible sleep duration from rate-limit headers. Never exceeds 90s."""
    for key in ("retry-after", "Retry-After", "retry-after-ms"):
        if key in headers:
            parsed = parse_retry_after(headers[key])
            if parsed is not None:
                return min(max(parsed, 1.0), 90.0)
    for key in (
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    ):
        if key in headers:
            parsed = parse_retry_after(headers[key])
            if parsed is not None:
                return min(max(parsed, 1.0), 90.0)
    return default
