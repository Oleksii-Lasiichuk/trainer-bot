from __future__ import annotations

from trainer_bot.bot.formatting import TELEGRAM_MAX, split_message


def test_split_returns_single_chunk_when_short() -> None:
    assert split_message("hello") == ["hello"]


def test_split_empty() -> None:
    assert split_message("") == [""]
    assert split_message("   \n ") == [""]


def test_split_honours_limit() -> None:
    text = "a" * (TELEGRAM_MAX + 500)
    chunks = split_message(text)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= TELEGRAM_MAX


def test_split_prefers_paragraph_boundary() -> None:
    para = ("abc. " * 400).strip()  # ~2000 chars
    text = para + "\n\n" + para + "\n\n" + para
    chunks = split_message(text, limit=2500)
    assert len(chunks) >= 2
    # should not cut mid-sentence — each chunk ends without trailing partial word
    for c in chunks:
        assert not c.endswith(" abc")
