# ADR-0018 (G5, PRD §7.2 stage 5, §9.4): Scan step implementation

**Status:** Implemented and verified. Ports the wireframe worked out
interactively for this step — Ready/Running/Needs attention/Complete, a
plain indeterminate busy state instead of job-orchestrator-backed
pause/resume, real `ScanReport` fields — to real, tested code, following
ADR-0012/0013/0014/0015/0017's established pattern. The wireframe pass
itself made no new decisions this round; this ADR is the implementation
that puts it into real code for the first time.

**Related PRD items:** §7.2 stage 5 ("create a persisted scan result
against an immutable profile snapshot"); §9.4 (scan/review requirements).

## Scope

One new wizard step (`ScanStep`), replacing the placeholder — the
wizard's sixth fully-built step, and the first one with *two* real
predecessors that both matter (Transcript/Transcribe for the completed
`Transcript`, Profile for the chosen `profile_id`). One new worker
(`ScanWorker`). No backend changes at all — `scan.py`/`matcher.py`
(`run_scan()`, `Scan`, `ScanReport`, `build_report()`) already existed
from G2/G3 and needed nothing added; this round is UI-only.

## `ScanWorker`: the simple shape, not Transcribe's

Mirrors `MediaInspectWorker` (one call, `result_ready`/`error`, no job
store) rather than `TranscribeWorker` (chunked, pausable, durable via the
SQLite Job Orchestrator) — the wireframe pass already reasoned through
why: `matcher.scan_transcript()` has no chunk boundary the way
Transcribe's chunks do, and its own docstring already flags it as
unbenchmarked against a real long transcript. Building pause/resume
durability for a job type with no evidence it needs it would repeat the
exact over-engineering mistake the original 30s/5s pause-latency chunking
default turned out to be. If a real benchmark someday says otherwise,
that's a real re-scoping of this worker, not a small tweak.

## `ScanStep`: three states, two real predecessors, one re-entry guard

`set_inputs(transcript, catalog, profile_id)` — called by the shell with
whichever `Transcript` actually exists (Transcribe's own output, or the
one Transcript step found and reused; `WizardWindow._skipped` is already
the source of truth for which, so `_push_profile_to_scan()` reads the
same thing the stepper's own "»" glyph does) and Profile's
`selected_profile_id`, plus the shared `CatalogService`.

- **Ready to scan** — nothing runs until confirmed. `CatalogService
  .create_snapshot(profile_id)` happens on click, not before, so a
  last-second Profile edit is still captured in the frozen snapshot.
- **Running** — a plain indeterminate `QProgressBar` (range `(0, 0)`),
  not a real fraction — there's nothing chunked to compute one from.
- **Needs attention** — an unexpected `create_snapshot()`/`run_scan()`
  failure (real path: an invalid `profile_id`, e.g. one that was archived
  and then genuinely deleted between selection and Scan). Retry re-runs
  from the same stored inputs; nothing durable was lost since nothing
  durable was ever created for a scan in the first place.
- **Complete** — real `ScanReport` fields (`build_report()`): hit count
  with a live category-name breakdown, unique terms hit, the snapshot's
  own name/revision, and the planned attenuated duration. Re-scan is its
  own explicit button (PRD §9.4: a re-scan is always a new revision,
  never copied decisions).

**Re-entry guard**, same shape and same reason as
`TranscribeStep.set_transcript_choice`'s: revisiting Scan with the exact
same transcript fingerprint and the exact same `profile_id` while already
running or complete is a no-op, so simply going Back to an earlier step
and Continuing again doesn't silently clobber a real result. Critically,
this one also has to handle the *positive* case Transcribe's never
needed: if the transcript or the chosen profile genuinely changed (the
User picked a different profile, or went back and re-transcribed), the
old `Scan` is stale by construction — it matched different inputs — so
`set_inputs` resets to "ready to scan" rather than keeping a result that
no longer corresponds to what's shown.

## Wiring: Scan is now sandwiched between two other real steps

`WizardWindow._push_profile_to_scan()` (new) feeds Scan's inputs when
continuing past Profile. `WizardWindow._push_scan_to_review()` (new)
feeds Scan's completed `Scan` straight to `ReviewStep.set_scan()` when
continuing past Scan — `ReviewStep` already defined that exact hand-off
in its own docstring, written before Scan existed to receive it ("the
same situation `TranscriptStep.chosen_model` was in before Transcribe
existed"), so this is the first predecessor Review was actually waiting
on. Review's own placeholder-vs-real status is unchanged by this ADR —
it was already real; it just had nothing real pushed into it until now.

## Verification

- **15 new tests** (13 for `ScanStep` — not-ready/ready/running/needs
  attention/complete states, the snapshot-happens-on-click behavior, the
  invalid-profile-id failure path, both re-entry-guard cases —, 2 for
  `ScanWorker` covering its success and error signals), plus
  `test_wizard_window.py` updates (a new `_make_scan_ready` helper
  mirroring the established pattern, inserted after every
  `_make_profile_ready` call site; a `test_scan_step_is_the_real_widget`
  test; the placeholder-index set updated) — all against real
  `CatalogService`/`Scan`/`ScanReport`/`Transcript` objects, no mocking of
  the domain layer itself. `black`/`flake8`/`mypy` clean.
- Scoped test runs relevant to this feature (this step's own tests, the
  worker tests, `test_wizard_window.py`, `test_review_step.py`, all
  together) and this project's actual CI command
  (`pytest tests/ --ignore=tests/gui`) are both clean, repeatedly. The
  disclosed, investigated, environment-level segfault ADR-0017 already
  documented in detail (a full local `pytest` run spanning every GUI test
  file together can crash on this machine, root-caused to a PySide6
  6.11.2/shiboken6 interaction unrelated to any specific feature's code,
  and confirmed not to affect CI, which already excludes `tests/gui/`)
  still applies here for the same pre-existing reason — nothing new to
  add about it this round.
- Not live-verified against a real GPU/whisper.cpp-produced transcript in
  this round (no environment access changed since ADR-0015's own
  disclosed limitation) — verified via real domain objects under
  `QT_QPA_PLATFORM=offscreen` instead, same substitution ADR-0008
  established and every G5 UI ADR since has used.

## Not yet decided

Whether/how `ScanStep`'s completed `Scan` needs anything beyond what
`ReviewStep.set_scan()` already consumes — nothing surfaced during this
round that Review doesn't already handle. Render and Complete remain
placeholders, each needing their own wireframe pass before
implementation, in that order.
