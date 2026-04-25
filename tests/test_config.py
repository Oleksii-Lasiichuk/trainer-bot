from __future__ import annotations

import pytest

from trainer_bot.config import Settings


def test_allowed_ids_parse_from_csv() -> None:
    s = Settings(
        TELEGRAM_BOT_TOKEN="t",
        GROQ_API_KEY="g",
        ALLOWED_TELEGRAM_USER_IDS=" 1, 2 ,3 ,,",
    )  # type: ignore[call-arg]
    assert s.allowed_telegram_user_ids == [1, 2, 3]
    assert s.whitelist_enabled is True
    assert s.is_user_allowed(1)
    assert not s.is_user_allowed(99)


def test_allowed_ids_empty_means_open() -> None:
    s = Settings(
        TELEGRAM_BOT_TOKEN="t",
        GROQ_API_KEY="g",
        ALLOWED_TELEGRAM_USER_IDS="",
    )  # type: ignore[call-arg]
    assert s.allowed_telegram_user_ids == []
    assert s.whitelist_enabled is False
    assert s.is_user_allowed(42)


def test_missing_required_errors() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(GROQ_API_KEY="g", TELEGRAM_BOT_TOKEN="")  # type: ignore[call-arg]
