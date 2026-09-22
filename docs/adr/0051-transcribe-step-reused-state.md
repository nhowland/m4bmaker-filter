# ADR-0051: Transcribe step gets a real "reused" state

**Status:** Implemented and verified.

## Context

The User reported: choosing "Use existing →" on Transcript step correctly
skips Transcribe and jumps to Profile — but clicking Back from Profile
lands on Transcribe showing "Choose a transcript path first." with
Continue permanently disabled, a dead end with no way forward short of
leaving the step entirely.

Reading the code confirmed why: `TranscribeStep` starts in
`_STATE_NOT_READY` and only ever leaves it via `set_transcript_choice()`,
called by the wizard shell only on the forward path
(`_push_transcript_to_transcribe()`, inside `_on_continue()`'s dispatch
table). The reuse path (`TranscriptStep.reuse_requested` ->
`WizardWindow._on_transcript_reuse()`) skips straight to Profile and
never calls it at all — so for a reused source, `TranscribeStep` simply
never learns anything happened. `can_advance()` was hardcoded to only
return true in `_STATE_COMPLETED`, so the not-ready placeholder's
Continue stayed disabled forever for this source.

Also confirmed: Back isn't the only way to land there. The stepper's
own step circles (`WizardWindow._go_to_step`) let a User jump directly
to any previously-visited step, and doing so after a reuse hits the
identical stuck screen — a fix scoped only to the Back button would
have left that second path broken.

Two shapes were considered and weighed with the User before building:

1. **Detect it reactively on Back** — leave `TranscribeStep` as-is and
   have `_on_back()`/`_go_to_step()` special-case "landing on a skipped
   step" with some patch-in-place behavior.
2. **Give `TranscribeStep` a real state for this**, populated at the
   moment of skipping (inside `_on_transcript_reuse()` itself), so the
   step's own state is correct regardless of how it's later reached.

(2) was chosen: it fixes both entry paths (Back and direct stepper
click) with one change, and matches this wizard's one consistent rule —
every step owns and renders its own real state; the shell only ever
pushes data into a step, never patches around a gap in one from the
outside.

## Decision

New `_STATE_REUSED` in `TranscribeStep`, and a new entry point
`set_reused(manifest, transcript)` — the counterpart to
`set_transcript_choice()`, called by the shell instead of it. `can_advance()`
now returns true for `_STATE_COMPLETED` **or** `_STATE_REUSED`.

`WizardWindow._on_transcript_reuse()` calls `transcribe_step.set_reused(
transcript_step.manifest, transcript_step.compatible_transcript)`
*before* skipping to Profile — at skip-time, not reactively when the
User later navigates back. This is what closes both entry paths at
once: whether the User gets there via Back or a stepper click, the
step's own state already reflects reality.

The new panel: "✓ Using existing transcript", plain-language
explanation of why (reused a saved transcript, nothing to transcribe
here), and a "View Transcript" button — reusing the same
`_on_view_transcript()` Completed's panel already has, since
`set_reused()` stores the transcript the same way `_on_result_ready()`
does.

Choosing "Transcribe again instead" back on Transcript step and
continuing forward still works exactly as before: `_on_continue()`'s
existing `next_index == _TRANSCRIBE_INDEX: self._skipped.discard(...)`
special case calls `set_transcript_choice()`, which fully resets state
out of `_STATE_REUSED` (not treated as "already in flight" — that guard
only checks `_STATE_RUNNING`/`_STATE_COMPLETED`).

## Verification

**Unit tests:** `TestReused` (6 new, `test_transcribe_step.py`) covers
`can_advance()`, transcript storage, the panel's own text, "View
Transcript", the `can_advance_changed` signal, and the round-trip back
into real transcription. `TestTranscriptReuseSkipsTranscribe` in
`test_wizard_window.py` gained 3 new tests: `TranscribeStep` is told at
skip-time (not just the shell's own `_skipped` bookkeeping), Back into
a skipped Transcribe enables Continue, and a direct stepper click does
too — all three confirmed to actually fail against the code without
this fix (reproducing the exact reported bug) before confirming they
pass with it.

Full suite 1983 passed, 2 skipped; `black`/`flake8`/`mypy` clean on
every file touched (pre-existing, unrelated debt in `gui/window.py`/
`test_window.py` untouched).

**Real app**, both themes: loaded a real source with a real matching
saved transcript, chose "Use existing →", confirmed the "»" skip glyph
on Transcribe, clicked Back — the new panel rendered correctly with
Continue enabled, clicking it advanced back to Profile correctly, and
the skip glyph was preserved. Verified in both light and dark mode.
