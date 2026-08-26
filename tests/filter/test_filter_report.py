"""Tests for m4bmaker.filter.filter_report (PRD §14.4; ADR-0007/0019).

Real file I/O against tmp_path — this module's whole job is writing a
real, persisted JSON file, so there's nothing meaningful to mock here.
"""

from __future__ import annotations

import json
from pathlib import Path

from m4bmaker.filter.filter_report import report_path_for, write_filter_report
from m4bmaker.filter.renderer import RenderResult
from m4bmaker.filter.validator import Severity, ValidationIssue, ValidationReport


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
        assert data["schemaVersion"] == 1
        assert data["output"]["path"] == str(output_path)
        assert data["output"]["durationMs"] == 3_723_456
        assert data["output"]["bitrate"] == "128k"
        assert data["validation"]["passed"] is True
        assert data["validation"]["issues"] == []
        assert "generatedAt" in data

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
