from __future__ import annotations

from trainer_bot.utils.ratelimit import compute_backoff, parse_retry_after


def test_parse_retry_after_numeric() -> None:
    assert parse_retry_after("42") == 42.0


def test_parse_retry_after_duration_strings() -> None:
    assert parse_retry_after("1m30s") == 90.0
    assert parse_retry_after("250ms") == 0.25
    assert parse_retry_after("12.5s") == 12.5
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None


def test_compute_backoff_prefers_retry_after() -> None:
    assert compute_backoff({"retry-after": "5"}) == 5.0


def test_compute_backoff_clamps() -> None:
    assert compute_backoff({"retry-after": "3600"}) == 90.0
    assert compute_backoff({"retry-after": "0.1"}) == 1.0


def test_compute_backoff_uses_reset_headers() -> None:
    headers = {"x-ratelimit-reset-requests": "10s"}
    assert compute_backoff(headers) == 10.0


def test_compute_backoff_default_when_missing() -> None:
    assert compute_backoff({}, default=17.0) == 17.0
