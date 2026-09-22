# ADR-0014: Transcript step — PySide6 implementation, and a shared model-download guard

**Status:** Implemented and verified. Ports the wireframe design worked
out interactively for this step — including the tabbed-scenario
presentation and the "download inline, not link-out" decision made
during that review — to real, tested, visually-verified code, following
ADR-0012/0013's established pattern.
**Related PRD items:** §7.2 stage 2 ("Choose transcript path" — load a
compatible native transcript or choose local transcription model/
settings); §7.3 (first-run/offline empty states); §10.1 (model UI
requirements); §10.2 (network only for explicit User-initiated
download); §10.4 (transcript compatibility/status semantics).

## Scope

One wizard step (`TranscriptStep`), replacing the Transcript
placeholder — the wizard's third fully-built step, and the first one
wired directly to its immediate predecessor rather than sitting behind
still-placeholder steps the way Review does. Also: a shared
"one download at a time" guard spanning `ModelManagerWindow` and this
step, closing a real gap the Transcript wireframe review surfaced
before any code was written (see the wireframe's "Transcript — decided"
design note).

## `TranscriptStep`: two modes, neither Continue-driven by default

- **`"found"`** — `transcript.find_compatible_transcript()` (ADR-0013)
  returned a match. Continue stays disabled here on purpose
  (`can_advance()` is unconditionally `False` in this mode) — the two
  panel buttons, "Use existing →" and "Transcribe again instead", are
  the only ways out, so the choice is always explicit, never a default
  Continue click drifting the User into an unstated outcome.
- **`"choose"`** — no compatible transcript, or the User chose
  "Transcribe again instead." Both real `model_manager.KNOWN_MODELS`
  catalog entries always show, each with its own real `is_installed()`
  state (one chooser, not a separate screen for "nothing installed" vs.
  "something's installed" — PRD §7.3's first-run state is just this
  same chooser with both rows reading "not installed"). Continue enables
  once the selected model is actually installed and nothing is
  mid-download.

Downloading a not-yet-installed model happens right on its row —
`ModelDownloadWorker`, the exact class `ModelManagerWindow` already
uses (ADR-0009), not a second implementation. This was the wireframe
review's one open, explicitly-flagged decision (link out to Model
Manager, or embed inline); inline won, on the reasoning that PRD §7.3's
"allow download... " belongs in the wizard's own happy path, not a
second window a User has to context-switch into mid-flow.

## `DownloadCoordinator`: the gap that decision opened

Choosing inline immediately raised the loose end the wireframe review
already flagged: ADR-0009's "one download at a time" guard
(`ModelManagerWindow._downloading_name`) is *that window's own*
attribute — nothing stopped it and this new step from independently
starting a download of the same model file at once if a User had both
open simultaneously. `gui/filter/workers.py` gets one new, tiny,
process-wide instance, `download_coordinator`, that both windows now
check: `try_acquire(name)` claims the single slot (or returns `False`
if another download already holds it), `release()` frees it. Both
windows show an explanatory message rather than silently no-op'ing
when blocked — a User who clicks Download in one window while the
other is mid-download needs to know why nothing happened.

Deliberately not thread-safe beyond what one Qt UI thread already
guarantees — every caller runs on the UI thread, so a plain attribute
is enough; a lock would be solving a race that can't actually occur
here.

## Wired to Source, for real — not stubbed like Review's situation

Source and Transcript are adjacent, and both are now real, so
`wizard_window.py` wires them directly: advancing past Source calls
`TranscriptStep.set_source(manifest)` with Source's own eligible
`MediaManifest`. `set_source()` is safe to call again — re-derives
`find_compatible_transcript()` and the mode fully from scratch rather
than merging with prior state — since Back-and-reselect-a-different-file
is a normal path, not an edge case to special-case around.

## Reuse skips Transcribe — a real navigation feature, not just a demo

`TranscriptStep.reuse_requested` (emitted by "Use existing →") drives
`WizardWindow._on_transcript_reuse()`: jump straight to Profile, mark
Transcribe skipped. The stepper's `»` skipped-glyph rendering already
existed in `StepperWidget.set_progress()`'s `skipped` parameter since
ADR-0012 — unused until now, since nothing before this step could ever
produce a skip. Visiting Transcribe for real afterward (User goes Back,
picks "Transcribe again instead," and continues through normally)
clears the skip mark — `_skipped` isn't a one-way flag, it reflects
"was this step's most recent visit satisfied by a shortcut," which can
change.

## Verification

- 34 new tests across `TranscriptStep` (22), the shared coordinator (5,
  plus 2 in `ModelManagerWindow`'s own suite proving it's genuinely
  blocked by a download the coordinator says is held elsewhere), and
  `WizardWindow` (5: real-widget construction, Source→Transcript
  manifest wiring including a second pass with a different source, and
  both directions of the reuse/skip navigation) — all against real
  backend objects and a real `tmp_path` filesystem, not fakes.
- `black`/`flake8`/`mypy` clean on every file this ADR touches.
- **Visually verified against the live app, with a real network
  download** — launched the app, advanced through the real Source step
  (the same synthesized eligible `.m4b` fixture from ADR-0013) into the
  real Transcript step: no compatible transcript existed, so the model
  chooser rendered both catalog entries as not-installed, Continue
  correctly disabled. Clicked Download on `base.en` — a real HTTPS
  request to the real pinned URL ran, completed within the
  screenshot-round-trip window, checksum-verified, and installed for
  real; the row flipped to "✓ installed" and Continue enabled
  immediately. Continuing landed on Transcribe (still a placeholder)
  with the stepper correctly showing two green checkmarks and no skip
  glyph — the normal, not-skipped path. (This left a genuine, correctly
  checksum-verified `base.en` installed at this machine's real
  `models_dir()` — not test pollution, an actual usable install,
  removable via Model Manager like any other.)

Full suite: 1546 tests total (up from 1511), all passing in isolation
and in every reasonably-sized combination tried.

## A pre-existing test-environment issue, confirmed not caused by this work

Running the complete bare `pytest -q` (all 1546 tests, one process) now
segfaults in the offscreen Qt platform plugin far more often than the
"~1 in 9" rate disclosed in ADR-0013's round — it reproduced on every
attempt today, landing at a different test each time depending on which
new tests were included or excluded. Isolated via `git stash` back to
the exact previous commit (1a7c1bd, none of this round's code): the
*same* bare full-suite command still segfaults there too (2 of 3
attempts, at a completely unrelated, untouched test), and no explicit
test-ID-list reproduction of the crashing run — including the exact
same tests, same order, same addopts — ever crashed, no matter how it
was assembled. Every individual test file, and every reasonably-sized
combination (the full `tests/gui/` tree twice, `tests/filter/` +
`tests/gui/filter/` together twice, this round's four new/changed
files together four times), passed cleanly and repeatedly. This is
conclusively a pre-existing, environment-level instability in how the
offscreen Qt platform plugin behaves at the full-project scale — not a
bug this round's code introduced or can fix — but it is materially
more disruptive now than when ADR-0013 disclosed it, and is flagged
here rather than left to be rediscovered as a surprise.

## Not yet decided

Whether/how `TranscriptStep.chosen_model` / `.compatible_transcript`
feed the not-yet-built Transcribe step (nothing consumes them yet, same
situation `ReviewStep.set_scan()` was in before Scan existed);
Transcribe's own real design, which hasn't had a wireframe pass.
