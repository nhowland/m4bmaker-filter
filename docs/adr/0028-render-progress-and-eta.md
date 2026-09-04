# ADR-0028: Render stage reweighting, live encode progress, and estimated remaining time

**Status:** Implemented and verified.

## Context

The Render step's progress bar visibly sat at 75% for most of a real
render — `render()`'s four stages (extract, envelope, attenuate,
encode+mux) each report only their own *start* checkpoint (0%, 25%, 50%,
75%), regardless of real relative cost. ADR-0007's own measurement on a
full 13.5-hour audiobook already showed why: encode+mux alone is 587.7s
of 631.4s total render time — roughly 93%, not the 25% four equal steps
implied. The User asked for the same kind of estimate Transcribe already
has (ADR-0027), explicitly naming encode+mux as the stage that matters
most, and asked about the overhead of tapping ffmpeg's own real-time
progress before committing to it (answered: negligible — ffmpeg already
computes these stats every ~0.5s by default; the only change is which
stream they're written to).

## Decision

**Three changes, all confirmed by the same real-book relative-cost data:**

1. **Reweight the four stage checkpoints by real relative cost**
   instead of four equal 25% steps: extract 0%, envelope 5%, attenuate
   6%, encode+mux 8%→100% — derived directly from ADR-0007's own
   measurement (31.3s / 2.4s / 10.0s / 587.7s of 631.4s).
2. **Instrument encode+mux specifically with ffmpeg's own `-progress
   pipe:1`** output, replacing that one blocking `subprocess.run()` call
   with a streaming `Popen`, parsing `out_time=` lines (not
   `out_time_ms`/`out_time_us` — both are actually in *microseconds*
   despite the name, a known long-standing ffmpeg inconsistency; the
   human-readable `out_time=HH:MM:SS.ffffff` field has no such ambiguity)
   against the known total output duration. stderr is redirected to a
   temp file rather than captured live alongside stdout — draining two
   OS pipes at once needs a background thread or `select()`-based
   multiplexing, and getting that wrong risks the standard pitfall of an
   unread pipe filling its buffer and blocking the child; a file
   sidesteps this, only read back for the error message if the process
   actually fails. Every other stage stays a single blocking call — none
   are slow enough for sub-stage progress to matter.
3. **An "Est. remaining" label next to Elapsed, for encode+mux only** —
   same architecture as Transcribe's ADR-0027: a rough hardcoded default
   (80x realtime, derived from ADR-0007's own ~82.7x measurement, not a
   blind guess) seeds the estimate the instant encode+mux begins, then
   real streamed progress replaces it with this run's own measured rate
   (audio-ms encoded so far this run vs. wall-clock-ms it took). The
   countdown only re-anchors when new progress data arrives, not every
   second against a growing elapsed denominator — same reasoning as
   ADR-0027: recomputing continuously would make the estimate visibly
   *worsen* while waiting on ffmpeg's next progress line, then jump back
   down, which is more confusing than a plain countdown. Cleared
   entirely once `validate()` takes over — it has no progress signal of
   its own, so a frozen stale number would be worse than nothing.

**No estimate before Start is clicked** — unlike Transcribe's model
picker, Render has no equivalent "which of two known things" choice to
seed a pre-run guess from, and ADR-0007's own measurement is exactly one
fixture; this ADR doesn't change that.

## What changed

- `renderer.py`: `_run_with_progress()` (new, alongside the existing
  `_run()`), `_STAGE_EXTRACT_START`/`_STAGE_ENVELOPE_START`/
  `_STAGE_ATTENUATE_START`/`_STAGE_ENCODE_START` constants,
  `encode_and_mux()` gained `total_duration_ms`/`progress_callback`
  params wired to the new streaming runner, `render()` reweighted its
  checkpoints and remaps encode's own 0.0-1.0 sub-progress into its
  `[0.08, 1.0]` slice of the overall fraction.
- `render_step.py`: `_DEFAULT_ENCODE_REALTIME_MULTIPLIER`, encode-stage
  timing bookkeeping (`_encode_stage_started_at`), `_recompute_encode_eta()`/
  `_est_remaining_display_text()` (same anchor-and-countdown shape as
  Transcribe's), a new label next to Elapsed, and clearing it in
  `_on_validating()`. Updated the module docstring, which previously
  argued Render should *never* get a pre-computed estimate, citing
  Transcribe's (at the time nonexistent) restraint as precedent — that
  reasoning didn't survive ADR-0027 adding exactly this to Transcribe
  first.

## Verification

**Unit tests:** `test_renderer.py` — `encode_and_mux()`'s existing tests
now mock `subprocess.Popen` (its real call site) instead of
`subprocess.run()`; a new test confirms `out_time=` lines map correctly
to a 0.0-1.0 fraction against `total_duration_ms`. `TestRender` gained
tests confirming the exact new checkpoint values
(`[0.0, 0.05, 0.06, 0.08, 1.0]`) and that encode's own sub-progress
remaps correctly into `[0.08, 1.0]`. `test_render_step.py` — new
`TestEstimatedRemaining`: no estimate during the fast pre-encode stages;
cold start uses the default multiplier; a completed portion of real
encode progress overrides it with the measured rate; the estimate
clears during validation. Full suite: 1731 passed, 2 skipped (7 new
tests); `black`/`flake8`/`mypy` clean (pre-existing untyped-mock-closure
findings in both touched test files, unrelated to this change).

**Real-pipeline verification, not just mocks:** rendered a real book
chapter (chapter_04, 28.8 minutes of real narration) through the actual
production `render()` function end to end. Extract/envelope/attenuate
completed in 1.5s combined, exactly as the reweighting predicts; encode+
mux then reported 41 real progress events over the following ~19.2s,
climbing smoothly from 8% to 100% in ~0.5s increments (ffmpeg's own
default stats interval) rather than jumping straight from 75% to 100%;
real output validation passed. Measured real rate for this render
(~84x realtime) landed close to both the hardcoded default (80x) and
ADR-0007's own original measurement (82.7x) — real, independent
confirmation the default constant is a reasonable starting point on
real hardware, not just a number picked to look right.
