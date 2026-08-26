"""Persists a User-facing ``filter-report.json`` alongside a render's
output (PRD §14.4, §7.2 stage 8's "report location").

ADR-0007 explicitly deferred this: "writing it to disk alongside output
is UI/orchestration-layer work (G5), not blocked by anything in this
ADR" — this module is that work, called once, right after a render
passes through :func:`validator.validate`. Nothing upstream (``renderer.py``,
``validator.py``) needed to change or know about this at all; it only
serializes their existing, already-real return values.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .renderer import RenderResult
from .validator import ValidationReport

SCHEMA_VERSION = 1


def report_path_for(output_path: Path) -> Path:
    """Where a render's report lives — alongside the output, named from
    its own stem so multiple renders in the same folder never collide
    the way one fixed generic ``filter-report.json`` name would."""
    return output_path.with_name(f"{output_path.stem}.filter-report.json")


def write_filter_report(
    output_path: Path,
    result: RenderResult,
    validation: ValidationReport,
    bitrate: str,
) -> Path:
    """Write a real, persisted report next to *output_path* and return
    the path written to. Overwrites any existing report at that path —
    matching Scan's own "re-scan is always current," a re-render always
    reflects only its own latest result, not a merge with a stale one.
    """
    data = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "output": {
            "path": str(result.output_path),
            "durationMs": result.duration_ms,
            "bitrate": bitrate,
        },
        "validation": {
            "passed": validation.passed,
            "issues": [
                {
                    "check": issue.check,
                    "severity": issue.severity.value,
                    "message": issue.message,
                }
                for issue in validation.issues
            ],
        },
    }
    path = report_path_for(output_path)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
