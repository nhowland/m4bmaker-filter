"""Tests for m4bmaker.filter.matcher — exact token/phrase matching (PRD §9.3)."""

from __future__ import annotations

from m4bmaker.filter.matcher import scan_transcript
from m4bmaker.filter.models import (
    FilterProfileSnapshot,
    SnapshotEntry,
)
from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)

PROFANITY = "cat-profanity"


def _word(text: str, start_ms: int, end_ms: int, confidence: float | None = None):
    return TranscriptWord(
        text=text,
        normalized=text.lower(),
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=confidence,
    )


def _transcript(words: list[TranscriptWord]) -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint="sha256:x", duration_ms=60_000, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp", version="v", model="base.en", model_checksum="c"
        ),
        segments=(
            TranscriptSegment(
                id="chunk-0",
                start_ms=0,
                end_ms=60_000,
                status=SegmentStatus.COMPLETED,
                words=tuple(words),
            ),
        ),
    )


def _snapshot(entries: list[SnapshotEntry]) -> FilterProfileSnapshot:
    return FilterProfileSnapshot(
        snapshot_id="snap-1",
        profile_id="p-1",
        profile_revision=1,
        name="Test Profile",
        entries=tuple(entries),
    )


def _single_word_entry(entry_id: str, phrase: str) -> SnapshotEntry:
    return SnapshotEntry(
        entry_id=entry_id,
        category_id=PROFANITY,
        canonical_phrase=phrase,
        normalized_phrase=phrase.lower(),
        revision=1,
    )


class TestSingleTokenMatch:
    def test_matches_exact_normalized_token(self) -> None:
        words = [_word("well", 0, 300), _word("darn", 300, 700), _word("it", 700, 900)]
        transcript = _transcript(words)
        snapshot = _snapshot([_single_word_entry("e-1", "darn")])
        hits = scan_transcript(transcript, snapshot, "scan-1")
        assert len(hits) == 1
        assert hits[0].entry_id == "e-1"
        assert hits[0].category_id == PROFANITY
        assert hits[0].match_rule == "exact_token"
        assert hits[0].start_ms == 300
        assert hits[0].end_ms == 700
        assert hits[0].raw_tokens == ("darn",)

    def test_case_insensitive_via_normalization(self) -> None:
        words = [_word("DARN", 0, 400)]
        transcript = _transcript(words)
        snapshot = _snapshot([_single_word_entry("e-1", "darn")])
        hits = scan_transcript(transcript, snapshot, "scan-1")
        assert len(hits) == 1

    def test_no_match_returns_empty(self) -> None:
        words = [_word("hello", 0, 300)]
        transcript = _transcript(words)
        snapshot = _snapshot([_single_word_entry("e-1", "darn")])
        assert scan_transcript(transcript, snapshot, "scan-1") == []

    def test_scan_hit_ids_are_stable_and_unique(self) -> None:
        words = [_word("darn", 0, 300), _word("darn", 400, 700)]
        transcript = _transcript(words)
        snapshot = _snapshot([_single_word_entry("e-1", "darn")])
        hits = scan_transcript(transcript, snapshot, "scan-1")
        assert len(hits) == 2
        assert hits[0].id != hits[1].id
        assert all(h.id.startswith("scan-1-hit-") for h in hits)


class TestPhraseMatch:
    def test_matches_adjacent_phrase(self) -> None:
        words = [_word("go", 0, 200), _word("to", 250, 400), _word("hell", 450, 800)]
        transcript = _transcript(words)
        entry = SnapshotEntry(
            entry_id="e-1",
            category_id=PROFANITY,
            canonical_phrase="go to hell",
            normalized_phrase="go to hell",
            revision=1,
        )
        hits = scan_transcript(transcript, _snapshot([entry]), "scan-1")
        assert len(hits) == 1
        assert hits[0].match_rule == "exact_phrase"
        assert hits[0].raw_tokens == ("go", "to", "hell")
        assert hits[0].start_ms == 0
        assert hits[0].end_ms == 800

    def test_gap_at_exactly_750ms_still_matches(self) -> None:
        # word2 starts exactly 750ms after word1 ends — PRD says "at or below".
        words = [_word("go", 0, 200), _word("hell", 950, 1200)]
        transcript = _transcript(words)
        entry = SnapshotEntry(
            entry_id="e-1",
            category_id=PROFANITY,
            canonical_phrase="go hell",
            normalized_phrase="go hell",
            revision=1,
        )
        hits = scan_transcript(transcript, _snapshot([entry]), "scan-1")
        assert len(hits) == 1

    def test_gap_above_750ms_does_not_match(self) -> None:
        words = [_word("go", 0, 200), _word("hell", 951, 1200)]
        transcript = _transcript(words)
        entry = SnapshotEntry(
            entry_id="e-1",
            category_id=PROFANITY,
            canonical_phrase="go hell",
            normalized_phrase="go hell",
            revision=1,
        )
        hits = scan_transcript(transcript, _snapshot([entry]), "scan-1")
        assert hits == []

    def test_confidence_is_min_across_matched_words(self) -> None:
        words = [
            _word("go", 0, 200, confidence=0.99),
            _word("hell", 250, 500, confidence=0.6),
        ]
        transcript = _transcript(words)
        entry = SnapshotEntry(
            entry_id="e-1",
            category_id=PROFANITY,
            canonical_phrase="go hell",
            normalized_phrase="go hell",
            revision=1,
        )
        hits = scan_transcript(transcript, _snapshot([entry]), "scan-1")
        assert hits[0].confidence == 0.6

    def test_confidence_none_if_any_word_missing_confidence(self) -> None:
        words = [
            _word("go", 0, 200, confidence=0.99),
            _word("hell", 250, 500, confidence=None),
        ]
        transcript = _transcript(words)
        entry = SnapshotEntry(
            entry_id="e-1",
            category_id=PROFANITY,
            canonical_phrase="go hell",
            normalized_phrase="go hell",
            revision=1,
        )
        hits = scan_transcript(transcript, _snapshot([entry]), "scan-1")
        assert hits[0].confidence is None


class TestOverlappingHitsNotDeduplicated:
    def test_single_word_and_phrase_both_reported(self) -> None:
        words = [_word("go", 0, 200), _word("to", 250, 400), _word("hell", 450, 800)]
        transcript = _transcript(words)
        phrase_entry = SnapshotEntry(
            entry_id="e-phrase",
            category_id=PROFANITY,
            canonical_phrase="go to hell",
            normalized_phrase="go to hell",
            revision=1,
        )
        token_entry = _single_word_entry("e-token", "go")
        hits = scan_transcript(
            transcript, _snapshot([phrase_entry, token_entry]), "scan-1"
        )
        rules = sorted(h.match_rule for h in hits)
        assert rules == ["exact_phrase", "exact_token"]


class TestEmptyInputs:
    def test_empty_transcript_returns_no_hits(self) -> None:
        transcript = _transcript([])
        snapshot = _snapshot([_single_word_entry("e-1", "darn")])
        assert scan_transcript(transcript, snapshot, "scan-1") == []

    def test_empty_snapshot_returns_no_hits(self) -> None:
        words = [_word("darn", 0, 300)]
        transcript = _transcript(words)
        assert scan_transcript(transcript, _snapshot([]), "scan-1") == []
