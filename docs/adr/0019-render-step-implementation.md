# ADR-0019 (G5, PRD §7.2 stage 7, §8): Render step implementation

**Status:** Implemented and verified. Ports the wireframe worked out
interactively for this step — Ready/Running/Needs attention/Complete, a
real determinate progress bar (unlike Scan's), and an already-decided
durability position cited rather than re-litigated — to real, tested
code, following ADR-0012/0013/0014/0015/0017/0018's established pattern.

**Related PRD items:** §7.2 stage 7 ("confirm output plan, run
attenuate/encode/mux/validation"); §8 (timeline/chapter integrity,
attenuation behavior/verification); §14.4 (the persisted filter report).

## Scope

One new wizard step (`RenderStep`), replacing the placeholder — the
wizard's seventh fully-built step. One new worker (`RenderWorker`). Three
small, additive backend pieces the wireframe pass's own "open questions"
flagged as missing: `renderer.default_output_path()`,
`renderer.pick_default_bitrate()`/`SUPPORTED_BITRATES`, and a new module
`filter_report.py` (`write_filter_report()`). One small addition to
already-shipped code: `ReviewStep.current_render_plan()`, so Render has a
public way to read Review's *live* include/exclude decisions as a real
`RenderPlan` — nothing outside `ReviewStep` could reach that before.

`renderer.py`/`validator.py` themselves needed zero changes —
`render()`/`validate()` (ADR-0006/0007) were already complete, tested,
and benchmarked against a real ~13.5-hour production audiobook before
this round started.

## The three small backend additions, each closing a wireframe-flagged gap

- **`default_output_path(source_path) -> Path`** — `"<stem>
  (filtered)<suffix>"`, same folder as the source. The base app's own
  `_computed_output_path()` was confirmed, on inspection, to be shaped for
  a different flow (building a new M4B from a folder of files) and
  genuinely doesn't transfer — this is a fresh, small function, not a
  refactor of that one.
- **`pick_default_bitrate(bit_rate_bps, codec_name) -> str` /
  `SUPPORTED_BITRATES`** — ports (not reinvents) `gui/window.py`'s own
  real "snap to nearest of `_BITRATES`, with a 25% discount for lossy
  MP3/MP2 sources" auto-selection logic, verified against the same real
  reference book's own ~126kbps AAC track (snaps to 128k, matching what
  the wireframe pass already predicted by hand).
- **`filter_report.write_filter_report()`** — ADR-0007 explicitly
  deferred this: "writing it to disk alongside output is
  UI/orchestration-layer work (G5), not blocked by anything in this
  ADR." This is that work: a real, persisted `<output stem
  >.filter-report.json` (named from the output, not one fixed generic
  name, so multiple renders in one folder never collide), containing the
  real `ValidationReport`'s pass/fail status and every issue. Lives in
  its own module rather than `renderer.py` specifically to avoid giving
  `renderer.py` a new dependency on `validator.py` it never needed before
  — the two stay decoupled, `filter_report.py` is the only thing that
  needs both.

## `RenderWorker`: determinate progress, no cancellation it can't honor

Mirrors `MediaInspectWorker`'s no-job-store shape (same reasoning as
`ScanWorker`), but unlike Scan's indeterminate bar, `render()` already
has four real stages to report via its own `progress_callback` — this
worker's `progress` signal just relays them directly. `validating` fires
once, after `render()` succeeds and before `validate()` runs (which has
no progress callback of its own — a single blocking call, same
reasoning as Scan's indeterminate state). `result_ready` carries all
three real outputs: `RenderResult`, `ValidationReport`, and the real path
`write_filter_report()` just wrote.

**No `request_cancel()`.** Every other worker in this codebase that
offers Cancel has a real stop mechanism underneath it (a
`threading.Event` checked at a chunk boundary, a cancellable download).
`render()` has none — it's one synchronous call into ffmpeg subprocesses
with no handle exposed back to this worker to interrupt them. Rather than
add a button that can't do what it claims, `RenderStep`'s Running state
has no Cancel at all; the wizard window's own confirm-before-close
(already documented to apply "mid-Render") is the real, working escape
hatch — killing the app kills the child ffmpeg process too.

**A deliberate split between "the worker reports, the step decides":**
`RenderWorker` emits `result_ready` for *both* a passing and a failing
`ValidationReport` — it never itself decides that a failed validation
means "needs attention." `RenderStep._on_result_ready()` makes that call
(PRD §8.1: never present a failed validation as success), the same
separation `ScanWorker`/`ScanStep` already established.

## `RenderStep`: four states, two real predecessors

`set_inputs(manifest, render_plan)` — called with Source's own
`MediaManifest` and Review's *live* `current_render_plan()`. Re-entry
guard shaped like Scan's own: `RenderPlan` and everything it's built
from (`AttenuationSettings`, `RenderInterval`) are frozen dataclasses, so
structural `==` is exactly the right "did anything actually change"
check — same manifest fingerprint and an identical plan is a no-op while
running or complete; a genuinely different plan (the User went back to
Review and changed a decision) resets to "ready to render" with a
freshly recomputed default output path/bitrate, since an old confirmed
plan is stale by construction once what it would render has changed.

- **Ready to render** — real, *editable* defaults: output path (with a
  Change… browse action) and bitrate (a combo box, pre-selected by
  `pick_default_bitrate`), plus `estimate_storage_bytes()` (already
  proven at Source step) and the live interval/hit counts from the
  passed-in plan. Deliberately no "Est. render time" field — ADR-0007's
  real 615s/10.25min measurement is for exactly one fixture, and showing
  an extrapolated number for an arbitrary source would be a guess
  dressed as data, the same anti-pattern Transcribe's own "Est.
  remaining" already avoided.
- **Running** — determinate bar from `render()`'s four real stages, then
  indeterminate once `validating` fires.
- **Needs attention** — a `RenderError` or a failed `ValidationReport`,
  either way with real issue text. The output file is never auto-deleted
  — Retry safely overwrites it via `encode_and_mux()`'s existing atomic
  staging.
- **Complete** — real validation status (warnings listed if any, a plain
  "passed" message if none), the real output path, and the real,
  freshly-written report path. Deliberately does *not* show a numeric
  duration/chapter delta on success: `validator.py`'s own checks only
  ever produce a structured `ValidationIssue` when something *fails* —
  there is no recorded "matched within Xms" value on the passing path to
  display honestly, so the wireframe's more detailed sketch (`Δ 0 ms`)
  was intentionally simplified here rather than faked. A future round
  wanting that richer display would need `validator.py` itself to also
  report measured values on success, not just on failure — flagged, not
  built.

## Verification

- **51 new tests**: 15 for `RenderStep`, 12 for `RenderWorker`, 10 for
  `default_output_path`/`pick_default_bitrate`, 5 for
  `filter_report.write_filter_report`/`report_path_for`, 5 for
  `ReviewStep.current_render_plan`, plus `test_wizard_window.py` updates
  (a new `_make_render_ready` helper mirroring the established pattern,
  inserted after every `_make_scan_ready` call site; a
  `test_render_step_is_the_real_widget` test; the placeholder-index set
  updated) — all against real domain objects (`RenderResult`,
  `ValidationReport`, `RenderPlan`, `MediaManifest`), no mocking of the
  domain layer itself; only the ffmpeg-touching functions
  (`render`/`validate`/`inspect`) are patched at the worker boundary,
  matching every other worker test in this codebase. `black`/`flake8`/
  `mypy` clean.
- Every test scope relevant to shipping this — this step's own tests,
  the worker tests, the new backend function tests, `test_review_step.py`,
  `test_wizard_window.py`, and all wizard-step test files run together
  (175 tests) — is clean, repeatedly. This project's real CI command
  (`pytest tests/ --ignore=tests/gui`) remains clean too. The disclosed,
  investigated, environment-level segfault ADR-0017 documented (a full
  local `pytest` run spanning every GUI test file together can crash on
  this machine, root-caused to a PySide6 6.11.2/shiboken6 interaction
  unrelated to any specific feature's code, confirmed not to affect CI)
  still applies for the same pre-existing reason — nothing new about it
  this round.
- Not live-verified against a real ffmpeg render in this round — same
  disclosed limitation as ADR-0018, unchanged since ADR-0015. Verified
  via real domain objects under `QT_QPA_PLATFORM=offscreen` instead.

## Not yet decided

Only Complete remains as a placeholder — the last PRD §7.2 stage without
its own wireframe pass. Nothing yet consumes `RenderStep`'s completed
`RenderResult`/`ValidationReport`/report path — Complete, which will
(PRD §7.2 stage 8: "show validation status, output location, and report
location"), is that consumer, in the same "nothing downstream uses this
yet" position every prior step was in before its own successor existed.
Whether `validator.py` should also report structured measured values on
a passing validation (to support a richer Complete/Render summary than
"passed, no warnings") is flagged above, not decided.
