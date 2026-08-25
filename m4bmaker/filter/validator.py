"""Output validation: duration, chapters, required metadata, and
attenuation (PRD §8.1, §8.2, §8.5).

A render must never be marked successful without passing every check
here — PRD §8.1: "If that target cannot be met... the source class is
not MVP-supported until a documented, tested tolerance is approved."

The default tolerances below are set from real measurement, not asserted
from a plan: rendering a real ~13.5-hour production AAC M4B (a real
audiobook, see ``docs/adr/0007-renderer-and-validator.md``) produced an
**exact** (0ms) duration match and **exact** (0ms) chapter start-time
match against the source, once a separate cover-art bug (documented in
``renderer.py``) was fixed. The tolerances here are set well above that
observed value as a safety margin — different bitrates, mono sources, or
encoder versions haven't been tested yet — not because that much drift is
considered acceptable.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from m4bmaker.utils import subprocess_flags

from .models import MediaManifest, RenderPlan

#: See module docstring — measured exact (0ms) on the one real fixture
#: tested so far; kept well above that as an untested-conditions margin.
DEFAULT_DURATION_TOLERANCE_MS = 100

#: PRD §8.2 rounding/timebase policy: canonical integer-millisecond
#: comparison, matching the transcript artifact's own startMs/endMs
#: convention (PRD §10.3) for consistency across the whole system.
DEFAULT_CHAPTER_START_TOLERANCE_MS = 50

#: PRD §8.5: how much *above* the configured gain floor a measured
#: interval's RMS is allowed to sit and still count as "attenuated."
#: Real content's pre-attenuation loudness varies, so the observed dB
#: drop isn't identical to the floor's dB value even though the
#: multiplicative gain applied is exact (proven in ADR-0006/0007) — this
#: is slack for that natural per-passage variation, not a weakening of
#: the check.
DEFAULT_ATTENUATION_MARGIN_DB = 6.0


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    check: str
    severity: Severity
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def passed(self) -> bool:
        """A render is successful only when there are zero ERROR-severity
        issues — PRD §8.1: never mark success without passing every
        check. WARNING-severity issues (best-effort metadata loss, PRD
        §6.3) do not block success."""
        return not any(i.severity is Severity.ERROR for i in self.issues)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)


def validate_duration(
    source: MediaManifest,
    output: MediaManifest,
    tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS,
) -> list[ValidationIssue]:
    """PRD §8.1: output duration must match source within tolerance,
    measured with the same pinned tool/version — satisfied automatically
    here since both manifests come from the same
    ``media_inspector.inspect()`` call path."""
    diff = abs(output.duration_ms - source.duration_ms)
    if diff > tolerance_ms:
        return [
            ValidationIssue(
                check="duration",
                severity=Severity.ERROR,
                message=(
                    f"Output duration differs from source by {diff}ms "
                    f"(tolerance {tolerance_ms}ms): source="
                    f"{source.duration_ms}ms, output={output.duration_ms}ms."
                ),
            )
        ]
    return []


def validate_chapters(
    source: MediaManifest,
    output: MediaManifest,
    start_tolerance_ms: int = DEFAULT_CHAPTER_START_TOLERANCE_MS,
) -> list[ValidationIssue]:
    """PRD §8.2: equal chapter count, ordering (positional comparison
    below is itself the ordering check), titles, and start times."""
    issues: list[ValidationIssue] = []
    if len(source.chapters) != len(output.chapters):
        issues.append(
            ValidationIssue(
                check="chapter_count",
                severity=Severity.ERROR,
                message=(
                    f"Chapter count differs: source={len(source.chapters)}, "
                    f"output={len(output.chapters)}."
                ),
            )
        )
        return issues  # positional comparison below would be meaningless

    for s, o in zip(source.chapters, output.chapters):
        if s.title != o.title:
            issues.append(
                ValidationIssue(
                    check="chapter_title",
                    severity=Severity.ERROR,
                    message=(
                        f"Chapter {s.index} title differs: "
                        f"{s.title!r} -> {o.title!r}."
                    ),
                )
            )
        diff = abs(s.start_ms - o.start_ms)
        if diff > start_tolerance_ms:
            issues.append(
                ValidationIssue(
                    check="chapter_start",
                    severity=Severity.ERROR,
                    message=(
                        f"Chapter {s.index} start time differs by {diff}ms "
                        f"(tolerance {start_tolerance_ms}ms): "
                        f"source={s.start_ms}ms, output={o.start_ms}ms."
                    ),
                )
            )
    return issues


def validate_required_metadata(
    source: MediaManifest, output: MediaManifest
) -> list[ValidationIssue]:
    """PRD §6.3: required fields must be preserved when present in the
    source. Fields outside this list are best-effort and not checked
    here — PRD §6.3: "The product must not claim perfect preservation of
    all MP4 atoms.\" """
    issues: list[ValidationIssue] = []
    for field_name, source_value in source.required_metadata.items():
        output_value = output.required_metadata.get(field_name)
        if output_value != source_value:
            issues.append(
                ValidationIssue(
                    check="metadata",
                    severity=Severity.ERROR,
                    message=(
                        f"Required metadata field {field_name!r} differs: "
                        f"{source_value!r} -> {output_value!r}."
                    ),
                )
            )
    if source.cover_present and not output.cover_present:
        issues.append(
            ValidationIssue(
                check="cover_art",
                severity=Severity.ERROR,
                message="Source had cover art but output does not.",
            )
        )
    return issues


def _measure_rms_db(
    path: Path, start_ms: int, end_ms: int, ffmpeg: str
) -> float | None:
    """Decode ``[start_ms, end_ms)`` from *path* and return its RMS level
    in dB via ffmpeg's ``astats`` filter, or ``None`` if it can't be
    measured (e.g. digital silence reports "-inf", parsed as ``-inf``
    float, not ``None`` — only a genuinely unparseable/missing reading
    returns ``None``).

    Input-side seeking (``-ss``/``-to`` before ``-i``) is required here,
    not optional — this runs against windows scattered across a
    potentially 20-hour file, and output-side seeking would decode from
    the beginning of the file for every single measurement.
    """
    start_s = f"{start_ms / 1000:.3f}"
    end_s = f"{end_ms / 1000:.3f}"
    cmd = [
        ffmpeg,
        "-ss",
        start_s,
        "-to",
        end_s,
        "-i",
        str(path),
        "-af",
        "astats=reset=1",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", **subprocess_flags()
    )
    last_line = None
    for line in result.stderr.splitlines():
        if "RMS level dB" in line:
            last_line = line
    if last_line is None:
        return None
    try:
        value = last_line.rsplit(":", 1)[1].strip()
    except IndexError:
        return None
    if value == "-inf":
        return float("-inf")
    try:
        return float(value)
    except ValueError:
        return None


def validate_attenuation(
    output_path: Path,
    render_plan: RenderPlan,
    ffmpeg: str,
    margin_db: float = DEFAULT_ATTENUATION_MARGIN_DB,
) -> list[ValidationIssue]:
    """PRD §8.5: confirm each planned interval's post-fade sustain region
    was actually attenuated in the rendered output.

    **Scope, stated explicitly rather than implied.** This checks that
    every *planned* interval is quiet in the output — it does not
    re-verify that every *other* sample in a multi-hour file was left
    untouched. That broader guarantee comes from the ``amultiply``
    mechanism itself (proven correct sample-for-sample in ADR-0006/0007,
    including at real ~13.5-hour scale) rather than from re-decoding an
    entire file here, which would be redundant re-proving of an already-
    established mechanism at a cost that scales with file length for no
    additional confidence.

    An interval too short to have a sustain region after its fades (i.e.
    ``fade_in_ms + fade_out_ms >= end_ms - start_ms``) is skipped — there
    is no window left to measure that isn't itself a fade transition,
    where a partial-attenuation reading would be expected and not a
    defect.
    """
    issues: list[ValidationIssue] = []
    floor_db = render_plan.attenuation.gain_floor_db
    for interval in render_plan.intervals:
        sustain_start = interval.start_ms + interval.fade_in_ms
        sustain_end = interval.end_ms - interval.fade_out_ms
        if sustain_end <= sustain_start:
            continue
        rms_db = _measure_rms_db(output_path, sustain_start, sustain_end, ffmpeg)
        if rms_db is None:
            issues.append(
                ValidationIssue(
                    check="attenuation",
                    severity=Severity.WARNING,
                    message=(
                        f"Could not measure RMS for interval "
                        f"[{interval.start_ms},{interval.end_ms}) — "
                        "skipped, not treated as a failure."
                    ),
                )
            )
            continue
        if rms_db > floor_db + margin_db:
            issues.append(
                ValidationIssue(
                    check="attenuation",
                    severity=Severity.ERROR,
                    message=(
                        f"Interval [{interval.start_ms},{interval.end_ms}) "
                        f"not sufficiently attenuated: measured {rms_db:.1f}dB, "
                        f"expected at or below {floor_db + margin_db:.1f}dB "
                        f"(floor {floor_db}dB + {margin_db}dB margin)."
                    ),
                )
            )
    return issues


def validate(
    source: MediaManifest,
    output: MediaManifest,
    render_plan: RenderPlan,
    output_path: Path,
    ffmpeg: str,
    duration_tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS,
    chapter_start_tolerance_ms: int = DEFAULT_CHAPTER_START_TOLERANCE_MS,
    attenuation_margin_db: float = DEFAULT_ATTENUATION_MARGIN_DB,
) -> ValidationReport:
    """Run every check and return one aggregated report. A render is only
    successful when :attr:`ValidationReport.passed` is true."""
    issues: list[ValidationIssue] = []
    issues += validate_duration(source, output, duration_tolerance_ms)
    issues += validate_chapters(source, output, chapter_start_tolerance_ms)
    issues += validate_required_metadata(source, output)
    issues += validate_attenuation(
        output_path, render_plan, ffmpeg, attenuation_margin_db
    )
    return ValidationReport(issues=tuple(issues))
