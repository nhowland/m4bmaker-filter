"""Plain-text companion for a transcript, for a human to actually read
(ADR-0022, item 9/13 of the 2026-08-26 dry-run review).

The native ``.m4bt.json`` artifact (PRD §10.3) is full of per-word
timing/confidence metadata a User opening it directly would have to
wade through to find the words themselves. This writes just the words,
in reading order, to a plain ``.txt`` file alongside it — generated
lazily (only when a "View Transcript" action is actually used), not on
every :func:`~m4bmaker.filter.transcript.write_transcript` call, so
fixture-building code and tests that write transcripts don't grow a
surprise side file.
"""

from __future__ import annotations

from pathlib import Path

from .transcript import Transcript


def text_path_for(transcript_path: Path) -> Path:
    """Where a transcript's plain-text companion lives — alongside the
    ``.m4bt.json``, same stem, ``.txt`` extension."""
    return transcript_path.with_suffix(".txt")


def ensure_transcript_text(transcript: Transcript) -> Path:
    """Write *transcript*'s plain-text companion if it doesn't already
    exist, and return its path. Requires ``transcript.path`` to be set
    (ADR-0022) — raises :class:`ValueError` otherwise, since there is
    nowhere to put the companion file without knowing where the
    transcript itself lives."""
    if transcript.path is None:
        raise ValueError(
            "Cannot write a plain-text companion: this Transcript has no "
            "known path (transcript.path is None)."
        )
    text_path = text_path_for(transcript.path)
    if not text_path.exists():
        text = " ".join(word.text for word in transcript.words())
        text_path.write_text(text, encoding="utf-8")
    return text_path
