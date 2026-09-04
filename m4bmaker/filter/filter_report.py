"""Persists a User-facing ``filter-report.json`` alongside a render's
output (PRD §14.4, §7.2 stage 8's "report location").

ADR-0007 explicitly deferred this: "writing it to disk alongside output
is UI/orchestration-layer work (G5), not blocked by anything in this
ADR" — this module is that work, called once, right after a render
passes through :func:`validator.validate`. Nothing upstream (``renderer.py``,
``validator.py``) needed to change or know about this at all; it only
serializes their existing, already-real return values.

**ADR-0029 widened this from output-path/validation-only to the full
picture a User would actually want on disk next to their filtered
book**: where every artifact lives (source, transcript JSON/text,
output), what was actually filtered (category-level counts — never raw
matched terms; a slur category's *name* isn't sensitive, the actual
words matched under it are, and this file is plain text that might be
opened by someone other than the person who set up the profile), and
how long each real stage took. All of it comes from objects this
pipeline already produces — nothing here re-derives or estimates
anything.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .catalog import CatalogService
from .models import RenderPlan
from .renderer import RenderResult
from .scan import Scan
from .storage import write_json_atomic
from .transcript import Transcript
from .transcript_text import ensure_transcript_text
from .validator import ValidationReport

SCHEMA_VERSION = 2


def report_path_for(output_path: Path) -> Path:
    """Where a render's report lives — alongside the output, named from
    its own stem so multiple renders in the same folder never collide
    the way one fixed generic ``filter-report.json`` name would."""
    return output_path.with_name(f"{output_path.stem}.filter-report.json")


def _category_name(catalog: CatalogService, category_id: str) -> str:
    try:
        return catalog.get_category(category_id).name
    except KeyError:
        return category_id


def _filtering_summary(
    scan: Scan, render_plan: RenderPlan, catalog: CatalogService | None
) -> dict[str, object]:
    included = scan.included_hits()
    excluded = len(scan.hits) - len(included)
    unique_terms = len({hit.entry_id for hit in scan.hits})
    category_counts: dict[str, int] = {}
    for hit in scan.hits:
        name = (
            _category_name(catalog, hit.category_id)
            if catalog is not None
            else hit.category_id
        )
        category_counts[name] = category_counts.get(name, 0) + 1
    attenuated_ms = sum(iv.end_ms - iv.start_ms for iv in render_plan.intervals)
    return {
        "profileName": scan.profile_snapshot.name,
        "totalHits": len(scan.hits),
        "includedHits": len(included),
        "excludedHits": excluded,
        "uniqueTermsHit": unique_terms,
        "categoryCounts": category_counts,
        "attenuatedDurationMs": attenuated_ms,
    }


def _seconds_to_ms(seconds: float | None) -> int | None:
    return round(seconds * 1000) if seconds is not None else None


def write_filter_report(
    output_path: Path,
    result: RenderResult,
    validation: ValidationReport,
    bitrate: str,
    *,
    source_path: Path | None = None,
    transcript: Transcript | None = None,
    scan: Scan | None = None,
    render_plan: RenderPlan | None = None,
    catalog: CatalogService | None = None,
    transcribe_elapsed_seconds: float | None = None,
    scan_elapsed_seconds: float | None = None,
    render_elapsed_seconds: float | None = None,
    validation_elapsed_seconds: float | None = None,
) -> Path:
    """Write a real, persisted report next to *output_path* and return
    the path written to. Overwrites any existing report at that path —
    matching Scan's own "re-scan is always current," a re-render always
    reflects only its own latest result, not a merge with a stale one.

    Every keyword-only parameter is optional and independently omittable
    — a caller with only some of this context (a test, a future
    non-wizard entry point) still gets a valid report; the corresponding
    section is simply left out rather than populated with placeholder
    values, so a reader can tell "not available" apart from "zero."
    """
    data: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }

    if source_path is not None:
        data["source"] = {"path": str(source_path)}

    if transcript is not None:
        text_path: Path | None = None
        if transcript.path is not None:
            try:
                text_path = ensure_transcript_text(transcript)
            except ValueError:
                text_path = None
        data["transcript"] = {
            "jsonPath": str(transcript.path) if transcript.path else None,
            "textPath": str(text_path) if text_path else None,
            # engine.model is whisper.cpp's own model *filename* stem
            # (e.g. "ggml-base.en") -- strip the "ggml-" convention prefix
            # for a name a User would actually recognize as what they
            # picked in the Transcript step ("base.en").
            "model": transcript.engine.model.removeprefix("ggml-"),
        }

    data["output"] = {
        "path": str(result.output_path),
        "durationMs": result.duration_ms,
        "bitrate": bitrate,
    }

    if scan is not None and render_plan is not None:
        data["filtering"] = _filtering_summary(scan, render_plan, catalog)

    timings: dict[str, int | None] = {
        "transcriptionMs": _seconds_to_ms(transcribe_elapsed_seconds),
        "scanMs": _seconds_to_ms(scan_elapsed_seconds),
        "renderMs": _seconds_to_ms(render_elapsed_seconds),
        "validationMs": _seconds_to_ms(validation_elapsed_seconds),
    }
    if any(v is not None for v in timings.values()):
        known = [v for v in timings.values() if v is not None]
        timings["totalMs"] = sum(known)
        data["timings"] = timings

    data["validation"] = {
        "passed": validation.passed,
        "issues": [
            {
                "check": issue.check,
                "severity": issue.severity.value,
                "message": issue.message,
            }
            for issue in validation.issues
        ],
    }

    path = report_path_for(output_path)
    write_json_atomic(path, data)
    return path
