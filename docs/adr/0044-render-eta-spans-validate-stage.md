# ADR-0044: Render step's ETA spans the validate stage, not just encode+mux

**Status:** Implemented and verified.

## Context

The User reported the "Est. remaining" label on the Render step
disappears once the "Validating output…" phase starts, even though that
phase can itself cost minutes — the real filter-report from an earlier
investigation this session recorded `validationMs: 108652` (~1.8 min)
against `renderMs: 503449` (~8.4 min) for a real ~11.5-hour book, roughly
18% of the combined render+validate time, not a negligible tail.

Root cause: `render()`'s own progress fraction (0.0-1.0, ADR-0028)
covers only extract→envelope→attenuate→encode/mux. `validate()` runs
*after* `render()` returns and, until this change, had no progress
callback of its own — `RenderStep._on_validating()` explicitly cleared
the ETA and switched the progress bar to indeterminate, with a comment
saying there was "nothing left to estimate against." That was an
accurate description of the code at the time, not a bug in the display
logic — the estimator genuinely had no data for that phase.

Investigating *why* validation costs what it does found the real driver:
`validate_attenuation()` spawns one real `ffmpeg` subprocess per render
interval to measure RMS dB (293 intervals for the book above). It uses
fast input-side seeking, not a linear scan, so cost is roughly uniform
per interval regardless of position in the file: 108652ms / 293 ≈
371ms/interval, plausibly dominated by ffmpeg process-startup overhead.
Validation's cost is therefore knowable in advance — `len(render_plan.
intervals)` is known the moment Render starts — unlike, say, a stage
whose cost depends on data not yet seen.

## Decision

Give `validate_attenuation()`/`validate()` a real progress callback,
mirroring `renderer.render()`'s own `Callable[[str, float], None]`
shape exactly, and wire it all the way through to the Render step's ETA
— the same "hardcoded default, then corrected by this run's own real
data" pattern already used for Transcribe (ADR-0027) and encode+mux
(ADR-0028), extended to cover this phase too instead of stopping short
of it.

**`validator.py`**: `validate_attenuation()` gains `progress_callback:
Callable[[str, float], None] | None = None`, called once per interval
in its loop — including a skipped one (too short for a sustain region),
so the fraction always reaches a full 1.0 and a caller can plan against
`len(render_plan.intervals)` up front. `validate()` accepts the same
parameter and passes it straight through; the other three checks it
runs (duration/chapters/metadata) are in-memory comparisons against
already-known manifest fields, over before a caller could usefully
observe partial progress, so they don't participate.

**`workers.py`**: `RenderWorker` gains a `validating_progress = Signal(str,
float)`, emitted from the same cross-thread pattern its existing
`progress` signal already uses for `render()`'s own callback (Qt queues
delivery to the receiving object's thread automatically for a
cross-thread connection — no new thread-safety concern here).

**`render_step.py`**: `_on_validating()` now checks
`len(render_plan.intervals)` — with real intervals, it seeds an upfront
estimate (`interval_count * _DEFAULT_VALIDATE_SECONDS_PER_INTERVAL`,
0.4s, derived from the real 371ms/interval figure above with a touch of
headroom) and switches the progress bar determinate instead of
indeterminate; with zero intervals (e.g. every hit excluded), the old
"nothing to estimate, stay indeterminate" behavior is unchanged — there
is genuinely nothing to plan against in that case. A new
`_on_validating_progress(message, fraction)` handler, connected to the
worker's new signal, updates the progress label (now showing e.g.
"Validating output… (142 of 293)"), the progress bar, and re-anchors the
ETA from this run's own real elapsed-time-vs-fraction-done rate — the
same re-anchor-the-countdown pattern `_recompute_encode_eta` already
uses, just computed directly against interval-fraction rather than an
audio-duration/rate split, since validate's real cost scales with
interval count, not audio length.

`_est_remaining_seconds`/`_est_anchor_time` (already shared, generic
fields `_est_remaining_display_text()` reads regardless of which stage
set them) and `_validating_at` (already tracked, for the filter report's
own stage timings) are reused as-is — no new state fields were needed
beyond the one new default constant.

## Verification

**`validator.py`** (`test_validator.py`, 3 new): the callback fires once
per interval with the fraction reaching exactly 1.0, including for an
interval skipped for having no sustain region (must not stall short of
1.0 for a caller planning against total interval count); `validate()`
threads the callback through to the attenuation check correctly. All 24
pre-existing tests pass unmodified — the new parameter defaults to
`None` and is purely additive.

**`workers.py`** (`test_workers.py`, 1 new): `RenderWorker` passes a real
callback to `validate()` (not just accepts the kwarg silently) that, when
invoked, emits `validating_progress` as a real Qt signal a UI-thread
listener receives correctly across the thread boundary. All 7
pre-existing `TestRenderWorker` tests pass unmodified.

**`render_step.py`** (`test_render_step.py`, 3 new, 2 renamed for
accuracy): with real intervals, `_on_validating()` seeds a determinate
bar and a real upfront ETA (`pytest.approx(4.0)` for 10 intervals at
0.4s each); `_on_validating_progress()` updates label/bar/ETA from a
simulated real elapsed-time-vs-fraction scenario (2s elapsed at 50%
done → ~2s remaining, matching the expected re-anchored rate); a
guard test confirms progress arriving after the step has moved off
`_STATE_RUNNING` is ignored, not applied. The two pre-existing tests
that asserted the *old* "always indeterminate, always clears the ETA"
behavior (`test_validating_switches_to_indeterminate`,
`test_validating_clears_the_eta`) both used the zero-interval
`_empty_plan()` fixture — confirmed, not assumed, that this is genuinely
the zero-interval edge case (which is unchanged) rather than a
regression slipping past unnoticed; renamed to make that scope explicit
rather than reading as the general behavior.

`black`/`flake8`/`mypy` clean on every file touched. `tests/gui/filter/`
(378 passed) and the project's real CI command, `pytest tests/
--ignore=tests/gui` (1011 passed, 2 skipped), both clean.

## Addendum: "Calculating…" placeholder instead of a blank label

This whole investigation started from a separate, earlier User request:
whenever no estimate exists yet, the "Est. remaining" label should show
a placeholder rather than going blank — blank read as the feature being
silently absent, not still working on an answer. Deliberately simple:
`_est_remaining_display_text()` in both `transcribe_step.py` and
`render_step.py` now returns `"Est. remaining: Calculating…"` instead of
`""` whenever `_est_remaining_seconds`/`_est_anchor_time` are `None`, no
per-stage-aware logic distinguishing "estimate coming soon" from "no
estimate ever coming for this specific stage" (e.g. Render's fast
pre-encode stages, or a zero-interval validate phase) — both of those
windows are short in practice (the pre-encode stages are fast by design,
ADR-0007; a zero-interval `validate()` returns almost immediately with
no ffmpeg calls to make), so a brief, non-resolving placeholder there is
an acceptable, honest-enough tradeoff against the complexity of
threading that distinction through. Since `_eta_label` only exists
inside each step's own `_build_running_panel()` (rebuilt fresh on every
state transition), this can never leak into the Ready/Completed/Needs
Attention states where no "remaining time" concept applies at all.

As a side effect, this also resolves a small pre-existing visual glitch
neither report named directly: the row's "·" separator between Elapsed
and ETA was always visible, so a blank ETA read as a dangling separator
with nothing after it ("Elapsed: 0:05 ·"). With the label never blank
during a run, that glitch can't occur.

**Verification:** 3 existing tests that asserted the *old* blank-string
behavior updated (not just patched) after confirming what each was
actually covering — one direct function-level check (before any run
exists), one covering Render's fast pre-encode stages, one covering
Render's zero-interval validate reset — all now assert the placeholder
text while keeping their original assertions on the underlying `None`
value, so both "the number is still absent" and "the display says so
honestly" are checked. `tests/gui/filter/` 378 passed;
`black`/`flake8`/`mypy` clean.
