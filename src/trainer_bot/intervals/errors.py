"""Typed exceptions for intervals.icu interactions."""

from __future__ import annotations


class IntervalsAPIError(Exception):
    """Base class for intervals.icu API failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class IntervalsAuthError(IntervalsAPIError):
    """401 — API key is wrong/revoked or athlete ID doesn't match."""


class IntervalsNotFoundError(IntervalsAPIError):
    """404 — resource (activity/athlete) does not exist."""


class IntervalsRateLimitError(IntervalsAPIError):
    """429 — rate limited by intervals.icu."""


class IntervalsServerError(IntervalsAPIError):
    """5xx — transient server error; retryable."""
