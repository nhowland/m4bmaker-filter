"""Tests for m4bmaker.filter.filter_report (PRD §14.4; ADR-0007/0019).

Real file I/O against tmp_path — this module's whole job is writing a
real, persisted JSON file, so there's nothing meaningful to mock here.
"""

from __future__ import annotations

import json
from pathlib import Path

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.filter_report import report_path_for, write_filter_report
from m4bmaker.filter.interval_planner import build_render_plan
from m4bmaker.filter.models import NORMALIZATION_VERSION, ReviewStatus
from m4bmaker.filter.renderer import RenderResult
from m4bmaker.filter.scan import run_scan
from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)
from m4bmaker.filter.validator import Severity, ValidationIssue, ValidationReport


def _word(text: str, start_ms: int, end_ms: int) -> TranscriptWord:
    return TranscriptWord(
        text=text, normalized=text.lower(), start_ms=start_ms, end_ms=end_ms
    )


def _transcript(words: list[TranscriptWord], path: Path | None = None) -> Transcript:
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
        path=path,
    )


class TestReportPathFor:
    def test_named_from_output_stem(self) -> None:
        result = report_path_for(Path("/books/Dungeon Crawler Carl (filtered).m4b"))
        assert result == Path(
            "/books/Dungeon Crawler Carl (filtered).filter-report.json"
        )

    def test_two_different_outputs_never_collide(self) -> None:
        one = report_path_for(Path("/books/Book One (filtered).m4b"))
        two = report_path_for(Path("/books/Book Two (filtered).m4b"))
        assert one != two


class TestWriteFilterReport:
    def test_writes_real_json_with_passing_validation(self, tmp_path: Path) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=3_723_456)
        validation = ValidationReport(issues=())

        report_path = write_filter_report(output_path, result, validation, "128k")

        assert report_path == report_path_for(output_path)
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["schemaVersion"] == 2
        assert data["output"]["path"] == str(output_path)
        assert data["output"]["durationMs"] == 3_723_456
        assert data["output"]["bitrate"] == "128k"
        assert data["validation"]["passed"] is True
        assert data["validation"]["issues"] == []
        assert "generatedAt" in data

    def test_leaves_no_temp_file_behind(self, tmp_path: Path) -> None:
        # Written via storage.write_json_atomic() (stage-then-rename), not
        # a direct write -- a crash mid-write must never leave a partial
        # report next to a real render's otherwise-good output. Checked
        # here by real directory listing, not by mocking the write call,
        # matching this file's own no-mocking convention.
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=3_723_456)
        validation = ValidationReport(issues=())

        report_path = write_filter_report(output_path, result, validation, "128k")

        leftovers = [
            p for p in tmp_path.iterdir() if p != output_path and p != report_path
        ]
        assert leftovers == []

    def test_writes_real_issues_on_a_failed_validation(self, tmp_path: Path) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_000)
        validation = ValidationReport(
            issues=(
                ValidationIssue(
                    check="duration",
                    severity=Severity.ERROR,
                    message="Output duration differs from source by 500ms.",
                ),
                ValidationIssue(
                    check="metadata",
                    severity=Severity.WARNING,
                    message="Best-effort field 'series' not preserved.",
                ),
            )
        )

        report_path = write_filter_report(output_path, result, validation, "96k")

        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["validation"]["passed"] is False
        assert data["validation"]["issues"] == [
            {
                "check": "duration",
                "severity": "error",
                "message": "Output duration differs from source by 500ms.",
            },
            {
                "check": "metadata",
                "severity": "warning",
                "message": "Best-effort field 'series' not preserved.",
            },
        ]

    def test_overwrites_a_stale_report_from_an_earlier_render(
        self, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        stale_result = RenderResult(output_path=output_path, duration_ms=1)
        stale_validation = ValidationReport(
            issues=(
                ValidationIssue(
                    check="duration", severity=Severity.ERROR, message="stale"
                ),
            )
        )
        write_filter_report(output_path, stale_result, stale_validation, "64k")

        fresh_result = RenderResult(output_path=output_path, duration_ms=2)
        fresh_validation = ValidationReport(issues=())
        report_path = write_filter_report(
            output_path, fresh_result, fresh_validation, "128k"
        )

        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["validation"]["passed"] is True
        assert data["output"]["bitrate"] == "128k"


class TestWidenedReportSections:
    """ADR-0029: paths to every real artifact, high-level filter stats
    (never raw matched terms — see this module's own docstring), and
    per-stage timings."""

    def test_source_path_included_when_given(self, tmp_path: Path) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_000)
        report_path = write_filter_report(
            output_path,
            result,
            ValidationReport(issues=()),
            "128k",
            source_path=tmp_path / "Book.m4b",
        )
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["source"]["path"] == str(tmp_path / "Book.m4b")

    def test_transcript_paths_and_model_included(self, tmp_path: Path) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_000)
        transcript_path = tmp_path / "book.m4bt.json"
        transcript_path.write_text("{}", encoding="utf-8")
        transcript = _transcript([_word("hello", 0, 300)], path=transcript_path)

        report_path = write_filter_report(
            output_path,
            result,
            ValidationReport(issues=()),
            "128k",
            transcript=transcript,
        )

        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["transcript"]["jsonPath"] == str(transcript_path)
        assert data["transcript"]["textPath"] == str(
            transcript_path.with_suffix(".txt")
        )
        assert Path(data["transcript"]["textPath"]).exists()
        assert data["transcript"]["model"] == "base.en"

    def test_transcript_without_a_path_omits_text_path_but_not_model(
        self, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_000)
        transcript = _transcript([_word("hello", 0, 300)], path=None)

        report_path = write_filter_report(
            output_path,
            result,
            ValidationReport(issues=()),
            "128k",
            transcript=transcript,
        )

        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["transcript"]["jsonPath"] is None
        assert data["transcript"]["textPath"] is None
        assert data["transcript"]["model"] == "base.en"

    def test_filtering_summary_with_resolved_category_names(
        self, tmp_path: Path
    ) -> None:
        service = CatalogService()
        cat = service.create_category("Profanity")
        darn, _ = service.create_entry(cat.id, "darn")
        heck, _ = service.create_entry(cat.id, "heck")
        profile = service.create_profile("Test", entry_ids=[darn.id, heck.id])
        snapshot = service.create_snapshot(profile.id)
        words = [
            _word("darn", 0, 300),
            _word("heck", 1_000, 1_300),
            _word("darn", 2_000, 2_300),
        ]
        transcript = _transcript(words)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        scan.decide(scan.hits[0].id, ReviewStatus.EXCLUDED)
        plan = build_render_plan(scan.included_hits(), 60_000, snapshot.attenuation)

        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=60_000)
        report_path = write_filter_report(
            output_path,
            result,
            ValidationReport(issues=()),
            "128k",
            scan=scan,
            render_plan=plan,
            catalog=service,
        )

        data = json.loads(report_path.read_text(encoding="utf-8"))
        filtering = data["filtering"]
        assert filtering["profileName"] == "Test"
        assert filtering["totalHits"] == 3
        assert filtering["includedHits"] == 2
        assert filtering["excludedHits"] == 1
        assert filtering["uniqueTermsHit"] == 2
        assert filtering["categoryCounts"] == {"Profanity": 3}
        assert filtering["attenuatedDurationMs"] > 0

    def test_no_filtering_section_without_a_scan(self, tmp_path: Path) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_000)
        report_path = write_filter_report(
            output_path, result, ValidationReport(issues=()), "128k"
        )
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert "filtering" not in data

    def test_timings_included_and_totalled(self, tmp_path: Path) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_000)
        report_path = write_filter_report(
            output_path,
            result,
            ValidationReport(issues=()),
            "128k",
            transcribe_elapsed_seconds=120.0,
            scan_elapsed_seconds=1.5,
            render_elapsed_seconds=30.0,
            validation_elapsed_seconds=0.5,
        )
        data = json.loads(report_path.read_text(encoding="utf-8"))
        timings = data["timings"]
        assert timings["transcriptionMs"] == 120_000
        assert timings["scanMs"] == 1_500
        assert timings["renderMs"] == 30_000
        assert timings["validationMs"] == 500
        assert timings["totalMs"] == 152_000

    def test_no_timings_section_when_nothing_given(self, tmp_path: Path) -> None:
        output_path = tmp_path / "Book (filtered).m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_000)
        report_path = write_filter_report(
            output_path, result, ValidationReport(issues=()), "128k"
        )
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert "timings" not in data
