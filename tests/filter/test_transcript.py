"""Tests for m4bmaker.filter.transcript — native transcript artifact (PRD §10.3)."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from m4bmaker.filter.models import SchemaValidationError
from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
    find_compatible_transcript,
    read_transcript,
    shift_segment,
    transcript_from_dict,
    transcript_to_dict,
    write_transcript,
)


def _sample_transcript() -> Transcript:
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
            parameters={"language": "en"},
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
                        text="world",
                        normalized="world",
                        start_ms=450,
                        end_ms=800,
                        confidence=0.95,
                    ),
                ),
            ),
            TranscriptSegment(
                id="chunk-000001",
                start_ms=30_000,
                end_ms=60_000,
                status=SegmentStatus.COMPLETED,
                words=(
                    TranscriptWord(
                        text="again", normalized="again", start_ms=30_100, end_ms=30_500
                    ),
                ),
            ),
        ),
    )


class TestTranscriptWordValidation:
    def test_negative_start_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            TranscriptWord(text="a", normalized="a", start_ms=-1, end_ms=100)

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            TranscriptWord(text="a", normalized="a", start_ms=100, end_ms=50)

    def test_confidence_defaults_to_none(self) -> None:
        w = TranscriptWord(text="a", normalized="a", start_ms=0, end_ms=100)
        assert w.confidence is None


class TestTranscriptWordsAcrossSegments:
    def test_words_flattens_in_order(self) -> None:
        t = _sample_transcript()
        words = t.words()
        assert [w.text for w in words] == ["Hello", "world", "again"]

    def test_empty_segments_yields_empty_words(self) -> None:
        t = _sample_transcript()
        empty = Transcript(
            schema_version=1,
            status=TranscriptStatus.DRAFT,
            source=t.source,
            engine=t.engine,
            segments=(),
        )
        assert empty.words() == []


class TestRoundTrip:
    def test_to_dict_then_from_dict_preserves_content(self) -> None:
        t = _sample_transcript()
        d = transcript_to_dict(t)
        restored = transcript_from_dict(d)
        assert restored == t

    def test_to_dict_uses_prd_camel_case_keys(self) -> None:
        t = _sample_transcript()
        d = transcript_to_dict(t)
        assert d["schemaVersion"] == 1
        assert d["source"]["durationMs"] == 3_723_456
        assert d["source"]["selectedAudioStream"] == 0
        assert d["engine"]["modelChecksum"] == "sha256:def"
        assert d["segments"][0]["startMs"] == 0
        assert d["segments"][0]["words"][0]["startMs"] == 100

    def test_write_then_read_file_round_trip(self, tmp_path: Path) -> None:
        t = _sample_transcript()
        path = tmp_path / "book.m4bt.json"
        write_transcript(path, t)
        restored = read_transcript(path)
        assert restored == t

    def test_null_confidence_round_trips_as_none(self) -> None:
        t = _sample_transcript()
        d = transcript_to_dict(t)
        assert d["segments"][0]["words"][0]["confidence"] is None
        restored = transcript_from_dict(d)
        assert restored.segments[0].words[0].confidence is None


class TestShiftSegment:
    def test_shifts_all_word_timestamps(self) -> None:
        seg = TranscriptSegment(
            id="chunk-1",
            start_ms=25_000,
            end_ms=55_000,
            status=SegmentStatus.COMPLETED,
            words=(
                TranscriptWord(text="hi", normalized="hi", start_ms=100, end_ms=400),
                TranscriptWord(
                    text="there", normalized="there", start_ms=500, end_ms=900
                ),
            ),
        )
        shifted = shift_segment(seg, offset_ms=25_000)
        assert [w.start_ms for w in shifted.words] == [25_100, 25_500]
        assert [w.end_ms for w in shifted.words] == [25_400, 25_900]

    def test_preserves_text_and_confidence(self) -> None:
        seg = TranscriptSegment(
            id="chunk-1",
            start_ms=0,
            end_ms=1000,
            status=SegmentStatus.COMPLETED,
            words=(
                TranscriptWord(
                    text="Hi", normalized="hi", start_ms=0, end_ms=100, confidence=0.9
                ),
            ),
        )
        shifted = shift_segment(seg, offset_ms=5_000)
        assert shifted.words[0].text == "Hi"
        assert shifted.words[0].confidence == 0.9

    def test_zero_offset_is_a_no_op_value_wise(self) -> None:
        seg = TranscriptSegment(
            id="chunk-1",
            start_ms=0,
            end_ms=1000,
            status=SegmentStatus.COMPLETED,
            words=(TranscriptWord(text="a", normalized="a", start_ms=10, end_ms=20),),
        )
        shifted = shift_segment(seg, offset_ms=0)
        assert shifted == seg

    def test_segment_container_bounds_are_untouched(self) -> None:
        seg = TranscriptSegment(
            id="chunk-1", start_ms=25_000, end_ms=55_000, status=SegmentStatus.COMPLETED
        )
        shifted = shift_segment(seg, offset_ms=25_000)
        assert shifted.start_ms == 25_000
        assert shifted.end_ms == 55_000

    def test_empty_words_shifts_to_empty(self) -> None:
        seg = TranscriptSegment(
            id="chunk-1", start_ms=0, end_ms=1000, status=SegmentStatus.COMPLETED
        )
        assert shift_segment(seg, offset_ms=500).words == ()


class TestStatusEnums:
    def test_transcript_status_values(self) -> None:
        assert {s.value for s in TranscriptStatus} == {
            "draft",
            "partial",
            "complete",
            "failed",
            "incompatible",
        }

    def test_segment_status_values(self) -> None:
        assert {s.value for s in SegmentStatus} == {"pending", "completed", "failed"}


class TestFindCompatibleTranscript:
    def test_finds_matching_complete_transcript(self, tmp_path: Path) -> None:
        t = _sample_transcript()
        write_transcript(tmp_path / "book.m4bt.json", t)

        found = find_compatible_transcript("sha256:abc", transcripts_dir=tmp_path)
        assert found == t

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        write_transcript(tmp_path / "book.m4bt.json", _sample_transcript())
        assert (
            find_compatible_transcript("sha256:different", transcripts_dir=tmp_path)
            is None
        )

    def test_missing_directory_returns_none(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        assert find_compatible_transcript("sha256:abc", transcripts_dir=missing) is None

    def test_draft_transcript_is_not_compatible(self, tmp_path: Path) -> None:
        draft = replace(_sample_transcript(), status=TranscriptStatus.DRAFT)
        write_transcript(tmp_path / "book.m4bt.json", draft)

        assert (
            find_compatible_transcript("sha256:abc", transcripts_dir=tmp_path) is None
        )

    def test_failed_transcript_is_not_compatible(self, tmp_path: Path) -> None:
        failed = replace(_sample_transcript(), status=TranscriptStatus.FAILED)
        write_transcript(tmp_path / "book.m4bt.json", failed)

        assert (
            find_compatible_transcript("sha256:abc", transcripts_dir=tmp_path) is None
        )

    def test_picks_most_recently_saved_when_multiple_match(
        self, tmp_path: Path
    ) -> None:
        older = replace(
            _sample_transcript(),
            engine=replace(_sample_transcript().engine, model="base.en"),
        )
        newer = replace(
            _sample_transcript(),
            engine=replace(_sample_transcript().engine, model="small.en"),
        )
        write_transcript(tmp_path / "a.m4bt.json", older)
        write_transcript(tmp_path / "b.m4bt.json", newer)
        # Force an unambiguous mtime ordering rather than relying on
        # write order alone (filesystem timestamp resolution varies).
        now = time.time()
        os.utime(tmp_path / "a.m4bt.json", (now - 10, now - 10))
        os.utime(tmp_path / "b.m4bt.json", (now, now))

        found = find_compatible_transcript("sha256:abc", transcripts_dir=tmp_path)
        assert found is not None
        assert found.engine.model == "small.en"

    def test_corrupted_file_is_skipped_not_raised(self, tmp_path: Path) -> None:
        (tmp_path / "corrupt.m4bt.json").write_text("{not valid json", encoding="utf-8")
        write_transcript(tmp_path / "good.m4bt.json", _sample_transcript())

        found = find_compatible_transcript("sha256:abc", transcripts_dir=tmp_path)
        assert found == _sample_transcript()

    def test_non_transcript_json_files_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        assert (
            find_compatible_transcript("sha256:abc", transcripts_dir=tmp_path) is None
        )
