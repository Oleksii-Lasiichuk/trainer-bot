"""Whitelist check."""

from __future__ import annotations

from ..config import Settings


def user_allowed(settings: Settings, telegram_id: int | None) -> bool:
    if telegram_id is None:
        return False
    return settings.is_user_allowed(telegram_id)
