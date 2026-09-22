"""Tests for m4bmaker.filter.transcript_text (ADR-0022)."""

from __future__ import annotations

from pathlib import Path

import pytest

from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)
from m4bmaker.filter.transcript_text import ensure_transcript_text, text_path_for


def _sample_transcript(path: Path | None = None) -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint="sha256:abc", duration_ms=3_723_456, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp",
            version="pinned-version",
            model="base.en",
            model_checksum="sha256:def",
        ),
        segments=(
            TranscriptSegment(
                id="chunk-000000",
                start_ms=0,
                end_ms=30_000,
                status=SegmentStatus.COMPLETED,
                words=(
                    TranscriptWord(
                        text="Hello", normalized="hello", start_ms=100, end_ms=400
                    ),
                    TranscriptWord(
                        text="world", normalized="world", start_ms=450, end_ms=800
                    ),
                ),
            ),
        ),
        path=path,
    )


class TestTextPathFor:
    def test_swaps_extension_for_txt(self) -> None:
        assert text_path_for(Path("/x/book.m4bt.json")) == Path("/x/book.m4bt.txt")


class TestEnsureTranscriptText:
    def test_raises_when_transcript_has_no_path(self) -> None:
        with pytest.raises(ValueError):
            ensure_transcript_text(_sample_transcript(path=None))

    def test_writes_words_in_reading_order(self, tmp_path: Path) -> None:
        transcript_path = tmp_path / "book.m4bt.json"
        text_path = ensure_transcript_text(_sample_transcript(path=transcript_path))
        assert text_path == tmp_path / "book.m4bt.txt"
        assert text_path.read_text(encoding="utf-8") == "Hello world"

    def test_does_not_rewrite_an_existing_companion(self, tmp_path: Path) -> None:
        transcript_path = tmp_path / "book.m4bt.json"
        text_path = text_path_for(transcript_path)
        text_path.write_text("hand-edited", encoding="utf-8")

        result = ensure_transcript_text(_sample_transcript(path=transcript_path))

        assert result == text_path
        assert text_path.read_text(encoding="utf-8") == "hand-edited"
