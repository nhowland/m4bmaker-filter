# ADR-0029: Widened filter-report.json — paths, filter stats, per-stage timings

**Status:** Implemented and verified.

## Context

The filter report written next to a render's output only ever held the
output path/duration/bitrate and the validation result — real, but not
what a User would actually want to know about a run: where the source,
transcript, and output all live; what was actually filtered; how long
each real stage took. The User asked for exactly this, explicitly
naming transcription and rendering timing as examples.

## Decision

**Widen `write_filter_report()` to a keyword-only, all-optional
signature** covering source path, transcript JSON/text paths and
model, filter stats, and per-stage timings — schema bumped to 2. Every
new section is independently omittable (present only when the caller
actually has that context) so a reader can tell "not available" apart
from "zero," and so existing callers with only partial context (a
test, a future non-wizard entry point) still get a valid report.

**Filter stats are category-level counts, never raw matched terms.** A
slur category's *name* isn't sensitive; the actual words matched under
it are, and this is a plain-text file that might be opened by someone
other than the person who set up the profile — the same reasoning this
app's own catalog masking feature already exists for. `totalHits`/
`includedHits`/`excludedHits`/`uniqueTermsHit` (all just numbers) plus
`categoryCounts` (category display name → count) and
`attenuatedDurationMs` give a genuinely useful "what happened" picture
without ever writing a matched word to disk.

**`write_filter_report()` moved from `RenderWorker` to
`RenderStep._on_result_ready`** (the UI thread, not the background
worker). The worker only ever held a bare `MediaManifest`/`RenderPlan`
— the `Transcript`/`Scan`/`CatalogService`/earlier-stage timings a
useful report needs all arrive at the *step* from the wizard shell
already (the same objects Scan/Review already hold), so writing the
report where that context already lives avoids threading it through
the worker's constructor just to hand it back out again.

**Per-stage timing, captured where the wall-clock boundary actually
is, not re-derived:**
- Transcription: `TranscribeStep` now accumulates real elapsed time
  across every run segment (resuming a paused job adds to the total
  rather than replacing it) — distinct from the live "Elapsed" label,
  which only ever shows the *current* segment.
- Scan: `ScanStep` gained simple start/finish timestamp capture — it
  had none before (no live elapsed display existed to reuse).
- Render/Validate: measured from `RenderStep`'s own existing
  `_start_time` and the `validating` signal, which already fires
  exactly at the render/validate boundary — no worker changes needed
  for this one at all.

## What changed

- `filter_report.py`: widened signature, `SCHEMA_VERSION = 2`,
  `_filtering_summary()`/`_category_name()`/`_seconds_to_ms()` helpers.
  Model name strips whisper.cpp's `ggml-` filename-stem convention
  (`ggml-base.en` → `base.en`) — a real, user-visible rough edge caught
  during this ADR's own real-pipeline verification, not anticipated in
  advance.
- `workers.py`: `RenderWorker` no longer calls `write_filter_report()`;
  `result_ready` now emits `(RenderResult, ValidationReport)`, not a
  third `report_path`.
- `render_step.py`: `set_inputs()` gained keyword-only
  `transcript`/`scan`/`catalog`/`transcribe_elapsed_seconds`/
  `scan_elapsed_seconds`; `_on_result_ready()` now computes render/
  validation elapsed time and calls `write_filter_report()` itself with
  everything gathered.
- `transcribe_step.py`: new `elapsed_seconds` property (cumulative
  across resumes).
- `scan_step.py`: new timing bookkeeping + `elapsed_seconds` property.
- `wizard_window.py`: `_push_review_to_render()` gathers the current
  transcript (via the existing `_current_transcript()` helper),
  `scan_step.scan`, the shared `CatalogService`, and both earlier
  steps' `elapsed_seconds`, passing all of it through.

## Verification

**Unit tests:** `test_filter_report.py` gained `TestWidenedReportSections`
covering each new section independently (present/absent, category-name
resolution via a real `CatalogService`, timing math, model-name
stripping). `test_workers.py`, `test_render_step.py`, and
`test_wizard_window.py` updated for the new 2-value signal and the
`_on_result_ready` signature no longer taking a `report_path` — several
of these tests now exercise the *real* `write_filter_report()` call
(writing to `tmp_path`), not a bypassed/mocked one, since that call now
lives in code these tests already drive directly. Full suite: 1738
passed, 2 skipped; `black`/`flake8`/`mypy` clean.

**Real-pipeline verification, not just mocks:** generated an actual
report from real chapter_01 data — real transcript, real scan against
the real TestProfile, real render, real validation. First attempt
surfaced two genuine things, not synthetic: a stale on-disk render
(the User's own live use of the running app had added catalog entries
since that chapter was last rendered, so a freshly-computed plan no
longer matched the stale output — real evidence the report reflects
actual state, not cached assumptions) and the `ggml-` model-name
prefix leaking into the JSON, both caught and fixed before shipping.
After re-rendering fresh, the real report showed 16 real hits across 1
category, 12.76s of real attenuated audio, real timings, and a real
passing validation — every field populated from what actually
happened, nothing synthesized.
