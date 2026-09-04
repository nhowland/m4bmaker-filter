"""Tests for m4bmaker.filter.validator (PRD §8.1, §8.2, §8.5; ADR-0007).

ffmpeg calls (_measure_rms_db's astats subprocess) are mocked throughout,
matching the project's existing convention. The real end-to-end proof
against a real ~13.5-hour production AAC M4B — including the concrete
before/during/after RMS numbers that informed DEFAULT_ATTENUATION_MARGIN_DB
— is documented in docs/adr/0007-renderer-and-validator.md.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m4bmaker.filter.models import (
    AttenuationSettings,
    ChapterInfo,
    MediaManifest,
    RenderInterval,
    RenderPlan,
)
from m4bmaker.filter.validator import (
    Severity,
    ValidationReport,
    validate,
    validate_attenuation,
    validate_chapters,
    validate_duration,
    validate_required_metadata,
)


def _manifest(
    duration_ms: int = 10_000,
    chapters: tuple[ChapterInfo, ...] = (),
    required_metadata: dict[str, str] | None = None,
    cover_present: bool = False,
) -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path="/books/a.m4b",
        fingerprint="sha256:x",
        duration_ms=duration_ms,
        tracks=(),
        selected_track_index=0,
        selected_track_is_fallback=False,
        chapters=chapters,
        required_metadata=required_metadata or {},
        cover_present=cover_present,
        eligible=True,
    )


class TestValidateDuration:
    def test_exact_match_no_issues(self) -> None:
        source = _manifest(duration_ms=10_000)
        output = _manifest(duration_ms=10_000)
        assert validate_duration(source, output) == []

    def test_within_tolerance_no_issues(self) -> None:
        source = _manifest(duration_ms=10_000)
        output = _manifest(duration_ms=10_050)
        assert validate_duration(source, output, tolerance_ms=100) == []

    def test_exceeds_tolerance_is_error(self) -> None:
        source = _manifest(duration_ms=10_000)
        output = _manifest(duration_ms=10_200)
        issues = validate_duration(source, output, tolerance_ms=100)
        assert len(issues) == 1
        assert issues[0].severity is Severity.ERROR
        assert issues[0].check == "duration"

    def test_boundary_value_is_not_an_error(self) -> None:
        source = _manifest(duration_ms=10_000)
        output = _manifest(duration_ms=10_100)
        assert validate_duration(source, output, tolerance_ms=100) == []


class TestValidateChapters:
    def _chapters(self, *titles_and_starts: tuple[str, int]) -> tuple[ChapterInfo, ...]:
        return tuple(
            ChapterInfo(index=i + 1, title=t, start_ms=s)
            for i, (t, s) in enumerate(titles_and_starts)
        )

    def test_identical_chapters_no_issues(self) -> None:
        ch = self._chapters(("Ch1", 0), ("Ch2", 5000))
        source = _manifest(chapters=ch)
        output = _manifest(chapters=ch)
        assert validate_chapters(source, output) == []

    def test_count_mismatch_is_error_and_skips_positional_checks(self) -> None:
        source = _manifest(chapters=self._chapters(("Ch1", 0), ("Ch2", 5000)))
        output = _manifest(chapters=self._chapters(("Ch1", 0)))
        issues = validate_chapters(source, output)
        assert len(issues) == 1
        assert issues[0].check == "chapter_count"
        assert issues[0].severity is Severity.ERROR

    def test_title_mismatch_is_error(self) -> None:
        source = _manifest(chapters=self._chapters(("Ch1", 0)))
        output = _manifest(chapters=self._chapters(("Chapter One", 0)))
        issues = validate_chapters(source, output)
        assert any(i.check == "chapter_title" for i in issues)

    def test_start_time_mismatch_beyond_tolerance_is_error(self) -> None:
        source = _manifest(chapters=self._chapters(("Ch1", 1000)))
        output = _manifest(chapters=self._chapters(("Ch1", 1200)))
        issues = validate_chapters(source, output, start_tolerance_ms=50)
        assert any(i.check == "chapter_start" for i in issues)

    def test_start_time_within_tolerance_no_issue(self) -> None:
        source = _manifest(chapters=self._chapters(("Ch1", 1000)))
        output = _manifest(chapters=self._chapters(("Ch1", 1030)))
        issues = validate_chapters(source, output, start_tolerance_ms=50)
        assert issues == []

    def test_the_real_bug_this_module_guards_against(self) -> None:
        """Regression test for the exact defect found on a real ~13.5-hour
        fixture (ADR-0007 D4): start times correct, titles silently
        swapped for unrelated chapters' titles."""
        source = self._chapters(("Chapter 1", 17_367), ("Chapter 2", 1_258_171))
        swapped = self._chapters(("Chapter 13", 17_367), ("Chapter 16", 1_258_171))
        issues = validate_chapters(
            _manifest(chapters=source), _manifest(chapters=swapped)
        )
        assert len(issues) == 2
        assert all(i.check == "chapter_title" for i in issues)


class TestValidateRequiredMetadata:
    def test_matching_fields_no_issues(self) -> None:
        meta = {"title": "Dune", "author": "Frank Herbert"}
        source = _manifest(required_metadata=meta)
        output = _manifest(required_metadata=dict(meta))
        assert validate_required_metadata(source, output) == []

    def test_differing_field_is_error(self) -> None:
        source = _manifest(required_metadata={"title": "Dune"})
        output = _manifest(required_metadata={"title": "Dun"})
        issues = validate_required_metadata(source, output)
        assert len(issues) == 1
        assert issues[0].check == "metadata"

    def test_missing_field_in_output_is_error(self) -> None:
        source = _manifest(required_metadata={"title": "Dune", "author": "FH"})
        output = _manifest(required_metadata={"title": "Dune"})
        issues = validate_required_metadata(source, output)
        assert len(issues) == 1
        assert "author" in issues[0].message

    def test_lost_cover_art_is_error(self) -> None:
        source = _manifest(cover_present=True)
        output = _manifest(cover_present=False)
        issues = validate_required_metadata(source, output)
        assert any(i.check == "cover_art" for i in issues)

    def test_cover_art_preserved_no_issue(self) -> None:
        source = _manifest(cover_present=True)
        output = _manifest(cover_present=True)
        assert validate_required_metadata(source, output) == []


def _astats_result(rms_db: str) -> MagicMock:
    r = MagicMock()
    r.stderr = f"[Parsed_astats_0 @ 0x0] RMS level dB: {rms_db}\n"
    return r


class TestValidateAttenuation:
    def _plan(self, floor_db: float = -80.0) -> tuple[RenderPlan, RenderInterval]:
        atten = AttenuationSettings(gain_floor_db=floor_db)
        interval = RenderInterval(
            start_ms=1000, end_ms=2000, fade_in_ms=15, fade_out_ms=15, hit_ids=("h-1",)
        )
        return (
            RenderPlan(
                intervals=(interval,), attenuation=atten, source_duration_ms=10_000
            ),
            interval,
        )

    def test_measured_at_or_below_floor_plus_margin_no_issue(self) -> None:
        plan, _ = self._plan(floor_db=-80.0)
        with patch("subprocess.run", return_value=_astats_result("-85.0")):
            issues = validate_attenuation(
                Path("/tmp/out.m4b"), plan, "ffmpeg", margin_db=6.0
            )
        assert issues == []

    def test_measured_above_floor_plus_margin_is_error(self) -> None:
        plan, _ = self._plan(floor_db=-80.0)
        with patch("subprocess.run", return_value=_astats_result("-20.0")):
            issues = validate_attenuation(
                Path("/tmp/out.m4b"), plan, "ffmpeg", margin_db=6.0
            )
        assert len(issues) == 1
        assert issues[0].severity is Severity.ERROR
        assert issues[0].check == "attenuation"

    def test_negative_infinity_reading_counts_as_attenuated(self) -> None:
        plan, _ = self._plan(floor_db=-80.0)
        with patch("subprocess.run", return_value=_astats_result("-inf")):
            issues = validate_attenuation(Path("/tmp/out.m4b"), plan, "ffmpeg")
        assert issues == []

    def test_unmeasurable_reading_is_warning_not_error(self) -> None:
        plan, _ = self._plan()
        r = MagicMock()
        r.stderr = "no astats output at all\n"
        with patch("subprocess.run", return_value=r):
            issues = validate_attenuation(Path("/tmp/out.m4b"), plan, "ffmpeg")
        assert len(issues) == 1
        assert issues[0].severity is Severity.WARNING

    def test_interval_too_short_for_sustain_region_is_skipped(self) -> None:
        atten = AttenuationSettings(fade_in_ms=15, fade_out_ms=15)
        interval = RenderInterval(
            start_ms=1000, end_ms=1020, fade_in_ms=15, fade_out_ms=15, hit_ids=("h-1",)
        )  # 20ms span, fades alone consume it -- no sustain region
        plan = RenderPlan(
            intervals=(interval,), attenuation=atten, source_duration_ms=10_000
        )
        with patch("subprocess.run") as m:
            issues = validate_attenuation(Path("/tmp/out.m4b"), plan, "ffmpeg")
        m.assert_not_called()
        assert issues == []

    def test_uses_input_side_seeking(self) -> None:
        plan, _ = self._plan()
        with patch("subprocess.run", return_value=_astats_result("-90.0")) as m:
            validate_attenuation(Path("/tmp/out.m4b"), plan, "ffmpeg")
        cmd = m.call_args[0][0]
        assert cmd.index("-ss") < cmd.index("-i")

    def test_progress_callback_called_once_per_interval(self) -> None:
        atten = AttenuationSettings()
        intervals = tuple(
            RenderInterval(
                start_ms=i * 1000,
                end_ms=i * 1000 + 500,
                fade_in_ms=15,
                fade_out_ms=15,
                hit_ids=(f"h-{i}",),
            )
            for i in range(3)
        )
        plan = RenderPlan(
            intervals=intervals, attenuation=atten, source_duration_ms=10_000
        )
        calls: list[tuple[str, float]] = []
        with patch("subprocess.run", return_value=_astats_result("-90.0")):
            validate_attenuation(
                Path("/tmp/out.m4b"),
                plan,
                "ffmpeg",
                progress_callback=lambda msg, frac: calls.append((msg, frac)),
            )
        assert len(calls) == 3
        assert [round(frac, 4) for _, frac in calls] == [
            round(1 / 3, 4),
            round(2 / 3, 4),
            1.0,
        ]
        assert "1 of 3" in calls[0][0]
        assert "3 of 3" in calls[2][0]

    def test_progress_callback_fires_for_a_skipped_interval_too(self) -> None:
        # A too-short interval never reaches _measure_rms_db (see the
        # skip test above) but progress must still advance for it, or a
        # caller planning against len(render_plan.intervals) would see
        # the fraction stall short of 1.0.
        atten = AttenuationSettings(fade_in_ms=15, fade_out_ms=15)
        short = RenderInterval(
            start_ms=1000, end_ms=1020, fade_in_ms=15, fade_out_ms=15, hit_ids=("h-1",)
        )
        plan = RenderPlan(
            intervals=(short,), attenuation=atten, source_duration_ms=10_000
        )
        calls: list[tuple[str, float]] = []
        with patch("subprocess.run") as m:
            validate_attenuation(
                Path("/tmp/out.m4b"),
                plan,
                "ffmpeg",
                progress_callback=lambda msg, frac: calls.append((msg, frac)),
            )
        m.assert_not_called()
        assert calls == [("Validating output… (1 of 1)", 1.0)]


class TestValidateAggregate:
    def test_all_clean_passes_with_no_issues(self) -> None:
        source = _manifest(duration_ms=5000, required_metadata={"title": "Dune"})
        output = _manifest(duration_ms=5000, required_metadata={"title": "Dune"})
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=5000
        )
        report = validate(source, output, plan, Path("/tmp/out.m4b"), "ffmpeg")
        assert isinstance(report, ValidationReport)
        assert report.passed is True
        assert report.issues == ()

    def test_any_error_fails_the_report(self) -> None:
        source = _manifest(duration_ms=5000)
        output = _manifest(duration_ms=999_999)  # wildly different
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=5000
        )
        report = validate(source, output, plan, Path("/tmp/out.m4b"), "ffmpeg")
        assert report.passed is False
        assert len(report.errors) >= 1

    def test_errors_and_warnings_partition_correctly(self) -> None:
        source = _manifest(duration_ms=5000)
        output = _manifest(duration_ms=999_999)
        interval = RenderInterval(
            start_ms=1000, end_ms=2000, fade_in_ms=15, fade_out_ms=15, hit_ids=("h-1",)
        )
        plan = RenderPlan(
            intervals=(interval,),
            attenuation=AttenuationSettings(),
            source_duration_ms=5000,
        )
        bad_result = MagicMock()
        bad_result.stderr = "unparseable\n"
        with patch("subprocess.run", return_value=bad_result):
            report = validate(source, output, plan, Path("/tmp/out.m4b"), "ffmpeg")
        assert len(report.errors) == 1  # duration
        assert len(report.warnings) == 1  # unmeasurable attenuation window
        assert report.passed is False

    def test_progress_callback_is_threaded_through_to_attenuation_check(self) -> None:
        source = _manifest(duration_ms=5000, required_metadata={"title": "Dune"})
        output = _manifest(duration_ms=5000, required_metadata={"title": "Dune"})
        interval = RenderInterval(
            start_ms=1000, end_ms=2000, fade_in_ms=15, fade_out_ms=15, hit_ids=("h-1",)
        )
        plan = RenderPlan(
            intervals=(interval,),
            attenuation=AttenuationSettings(),
            source_duration_ms=5000,
        )
        calls: list[tuple[str, float]] = []
        with patch("subprocess.run", return_value=_astats_result("-90.0")):
            validate(
                source,
                output,
                plan,
                Path("/tmp/out.m4b"),
                "ffmpeg",
                progress_callback=lambda msg, frac: calls.append((msg, frac)),
            )
        assert calls == [("Validating output… (1 of 1)", 1.0)]
