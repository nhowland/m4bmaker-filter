# ADR-0020 (G5, PRD §7.2 stage 8): Complete step implementation

**Status:** Superseded by [ADR-0022](0022-dry-run-review-remediation.md).
Complete's content converged with Render's own completed panel once the
latter grew its own Open Folder button, so ADR-0022 merged Complete into
Render's Completed state and removed this step and its file. Kept here
as the historical record of why the step existed in the first place;
the "Done closes the window" decision this ADR settled still stands,
just owned by Render now. Original text below, unedited.

Ports the wireframe worked out
interactively for this step — a real, single "passed validation"
statement rather than the original sketch's invented per-check deltas,
plus the settled "Done closes the window" decision — to real, tested
code, following ADR-0012 through 0019's established pattern. This is the
last of the eight PRD §7.2 stages to move from placeholder to real code.

**Related PRD items:** §7.2 stage 8 ("show validation status, output
location, report location, and warnings").

## Scope

One new wizard step (`CompleteStep`), replacing the last remaining
placeholder. Two small additions to already-shipped code: `RenderStep`
gained `result`/`validation`/`report_path` properties (the "nothing
downstream consumes this yet" gap ADR-0019 itself flagged), and
`WizardWindow._on_continue()` gained a special case for the last step —
"Done" now actually does something. `placeholder_step.py` is deleted:
with all eight steps real, it had zero remaining callers or tests, and
this codebase doesn't keep dead code around "just in case."

## `CompleteStep`: one real state, no re-entry guard

Unlike every other real step, `CompleteStep` needs no re-entry guard.
Every earlier step's guard exists to protect *in-flight or completed*
work from being silently clobbered by a naive re-derivation — but
nothing here ever runs, and nothing here can go stale the way a `Scan`
or `RenderPlan` can when an upstream choice changes. Re-displaying
whatever `RenderStep` most recently produced is simply correct, every
time, including if the User goes back to Render, changes the bitrate,
and re-renders — the second `set_result()` call is expected to fully
replace the first, not merge with it.

**The wireframe pass's own factual correction, now in real code.** The
original sketch (drawn before Render existed) showed three separate
green checks with specific numbers: "duration match Δ 0 ms," "chapters
50/50," "metadata & cover preserved." Building Render made clear this
isn't achievable honestly — `validator.py`'s checks only ever produce a
structured `ValidationIssue` when something *fails*; a passing
`ValidationReport` has zero issues and no recorded "matched within Xms"
value for any check. `CompleteStep` shows one combined statement instead
— "✓ Passed validation" or "✓ Passed, with warnings" plus the real
warning text — the same "don't show a guess dressed as data" standard
already applied to Transcribe's "Est. remaining" and Render's "Est.
render time."

**"Open Folder"** is a real, first-time addition to this codebase —
confirmed via search that no reveal-in-Finder/Explorer helper existed
anywhere before this. `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`
against the output's parent folder; patched in its own test, since it's
a real OS-level call a headless test shouldn't actually trigger.

## The "Done" button: settled, not assumed

Asked directly rather than guessed: **Done closes the wizard window.**
No separate "filter another book" reset action is needed alongside it —
every step's own re-entry guard (Source through Render, already built
across ADR-0014 through ADR-0019) already makes "go back to Source, pick
a different file" work correctly on its own, so that path was already
the real "start another book" flow before Complete was ever built.
Implemented as one new branch in `WizardWindow._on_continue()`: when the
active step is the last one, call `self.close()` instead of the normal
advance-to-next-index logic. `CompleteStep.can_advance()` uses the base
`WizardStep` default (always `True`) — there is nothing left to gate.

Also fixed a small, real bug in `_build_steps()`'s own fallback branch,
found while removing `placeholder_step.py`'s last use: the `else` branch
that used to construct a `PlaceholderStep` for any unhandled index now
raises `ValueError` instead — with every `STEP_LABELS` index wired to a
real step class, that branch is genuinely unreachable, and raising
loudly if it's ever hit again (e.g. a future stage added to `STEP_LABELS`
without updating this function) is more honest than silently falling
back to a stand-in that no longer exists anywhere else in the codebase.

## A real gap found, not fixed here

While wiring the "Done closes the window" behavior, grep across
`wizard_window.py` confirmed it has **no `closeEvent` override at all**
— yet its own module docstring claims "closing the wizard mid-Transcribe
or mid-Render prompts first, mirroring the confirm-before-close pattern
`ModelManagerWindow` already uses." That was a documented design
intention from ADR-0010's original wireframe review that was never
actually turned into code in any implementation round since. Out of
scope for Complete specifically (Complete's own close behavior — nothing
running, no confirmation needed — is unaffected by this gap), so it's
flagged as a separate follow-up task rather than folded in here.

## Verification

- **9 new tests** for `CompleteStep` (not-ready state, passing/
  passing-with-warnings display, Open Folder's real target folder,
  re-visiting with a second result fully replacing the first), **2 new
  tests** for `RenderStep`'s new properties, **3 new tests** for
  `WizardWindow`'s Render→Complete hand-off and the Done-closes-the-
  window behavior (the latter verified by patching `close()` directly
  and asserting it was called — checking `isVisible()` would have proven
  nothing, since a never-`.show()`-shown test window already reports
  `False` before any close() call) — all against real
  `RenderResult`/`ValidationReport` objects. Also removed the now-
  meaningless `test_every_other_step_is_a_placeholder` test and fixed a
  stale, incorrect code comment in `test_wizard_window.py` that still
  said "flip a PlaceholderStep's advance-ability" about a test that
  actually monkeypatches `ReviewStep` (a leftover from before Review
  itself was built for real). `black`/`flake8`/`mypy` clean.
- Every test scope relevant to shipping this — this step's own tests,
  `RenderStep`'s, `WizardWindow`'s, and every wizard-step test file
  together (189 tests), plus a broader batch alongside
  `CatalogWindow`/`ModelManagerWindow`/`ProfileEditorDialog` (281
  tests) — is clean, repeatedly. This project's real CI command
  (`pytest tests/ --ignore=tests/gui`) remains clean too. The disclosed,
  investigated, environment-level segfault ADR-0017 documented still
  applies for the same pre-existing reason and remains unaffected by
  this round.

## Not yet decided

Nothing — this closes out the last of the eight PRD §7.2 stages. The
wizard is now fully real end-to-end, Source through Complete. The one
concrete gap surfaced this round (WizardWindow's missing mid-Transcribe/
mid-Render close confirmation) is flagged as its own follow-up task, not
decided or built here.
