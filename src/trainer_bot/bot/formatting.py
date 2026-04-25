"""Text helpers — split long messages on paragraph boundaries for Telegram's 4096-char limit."""

from __future__ import annotations

TELEGRAM_MAX = 4096


def split_message(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Split *text* into chunks <= *limit* characters preferring paragraph then newline then word boundaries."""
    if text is None:
        return [""]
    text = text.strip()
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
