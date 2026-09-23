# ADR-0056: Guide the User to install `whisper-cli` before they reach Transcribe

**Status:** Decision approved by the Contributor (2026-09-23), pending
their review of the mockup,
`docs/design/whisper-cli-install-banner-wireframe.html` (same wireframe-
before-code discipline as ADR-0010/0052/0053/0055). Not implemented yet.
**Related PRD items:** §7.2 stage 2 (Transcript step: "load a compatible
native transcript or choose local transcription model/settings"); §17.1
rules 5 and 6 (no unapproved dependencies, no silent network behavior);
§17.1 rule 7 (do not weaken invariants).
**Related ADRs:** ADR-0001 (whisper.cpp as a subprocess binary); ADR-0003
(planned bundling of the whisper.cpp binary into the signed installers;
never built); ADR-0034 (Language Filter menu naming, Model Manager entry
point from the Transcript step); ADR-0051 (Transcribe step "reused" state).

## Context

`m4bmaker-filter` is distributed through PyPI (1.1.1 and 1.1.1.post1).
`pip install m4bmaker-filter` installs Python code only. The language
filter's transcription runs the whisper.cpp command-line program,
`whisper-cli`, as a subprocess (ADR-0001), and that program is neither a
Python dependency nor bundled: ADR-0003 planned to bundle it into the
PyInstaller/signed builds, and that never happened, and no such builds
exist for this fork.

The consequence, found while preparing the PyPI release: a User who follows
the README's `pip install` instructions gets a working Language Filter menu,
a working Word List, a working Source step, and a working model download,
then hits an error at Transcribe: "whisper-cli not found. Install whisper.cpp
and make sure it's on your PATH." (`gui/filter/workers.py`, the
transcription worker's backstop check). That is the last step of the setup,
after the User has picked a file, possibly downloaded a 148 MB model, and
clicked through the wizard.

What exists today:

- `find_whisper_cli()` (`filter/transcript_engine.py`) → `find_binary(
  "whisper-cli")` → `None` when missing. Discovery checks a PyInstaller bundle
  directory first, then PATH, then Homebrew locations.
- `ModelManagerWindow._refresh_engine_label()` shows "Engine: whisper.cpp not
  found (install whisper-cli to transcribe)." Only visible if the User opens
  that window, and it says *what* but not *how*.
- The transcription worker refuses to start with the error above, which is
  correct and stays as the backstop.
- The README, since commit `6e42fa6`, has a "Language filter: install
  whisper-cli" section (Homebrew on macOS, whisper.cpp releases or a source
  build on Linux/Windows). It helps only Users who read it before installing;
  it is invisible from inside the app.
- Only the Homebrew route (`brew install whisper-cpp`, whisper.cpp 1.9.x, DTW
  mode working) has been verified. Linux/Windows steps are unverified.

Two facts constrain the design:

1. **Not every path needs `whisper-cli`.** On the Transcript step, if a
   compatible saved transcript exists ("found" mode) and the User reuses it,
   Transcribe is skipped entirely (ADR-0051) and `whisper-cli` is never run.
   The Word List, Profile editor, and Model Manager also work without it. A
   warning shown at app start or wizard entry would nag Users who never
   transcribe.
2. **The check and the failure use the same function.** The transcription
   worker calls `find_whisper_cli()`. If the earlier check calls the same
   function, it cannot report "missing" for a machine where the worker would
   have found it, so acting on that result (including blocking Continue)
   cannot create a false block that the worker itself wouldn't have hit.

## Decision (proposed)

Add an **inline install banner to the Transcript step's "choose a model"
panel**, shown only when `find_whisper_cli()` returns `None`:

1. **Where and when.** In the "choose" panel (the mode where transcription
   will actually run), above the model list. Not shown in "found" mode. If
   the User picks "Transcribe again instead" from "found" mode, the choose
   panel and banner appear at that point. Checked when the panel renders
   (i.e. on `set_source`/mode change) and on Re-check. Nothing is shown when
   `whisper-cli` is found: no "all good" clutter.
2. **Content.** A short heading ("Transcription engine not installed"), one
   sentence on why ("The language filter transcribes with whisper.cpp's
   `whisper-cli`, which isn't installed by pip."), the platform-specific
   step, and two buttons:
   - **macOS:** the command `brew install whisper-cpp` in a read-only,
     selectable field, with **Copy**.
   - **Windows / Linux:** a plain-language line and an **Open releases
     page** button (`https://github.com/ggml-org/whisper.cpp/releases`),
     with an honest note that these platforms are untested with this
     project. No command is shown, because none has been verified.
   - **Re-check** (all platforms): re-runs `find_whisper_cli()`; if found,
     the banner disappears and Continue can enable.
3. **Continue is blocked while it's missing** (in "choose" mode).
   `TranscriptStep.can_advance()` additionally requires that
   `find_whisper_cli()` is not `None`. Justified by fact 2 above: this is not
   a new restriction, it moves the certain failure earlier. The banner is the
   explanation for why Continue is disabled. Re-check re-emits
   `can_advance_changed`.
4. **One source of truth for the wording.** A small pure function next to
   `find_whisper_cli()` (e.g. `whisper_install_hint(platform) ->
   InstallHint`) returns the platform's command/URL/note. The banner, the
   Model Manager's engine label (which gets the same one-line pointer), and
   the transcription worker's backstop error all use it, so the three
   surfaces can't drift. The function takes the platform as a parameter so it
   is testable without a GUI.
5. **Backstop unchanged.** The worker's own `find_whisper_cli()` check stays.
   Its message gains the hint text but its behavior (emit error, don't start)
   is untouched.

### Explicitly not part of this decision

- **No auto-install.** The app never runs `brew`, `winget`, a build, or any
  package manager. That would be a new process-execution path with
  privilege and supply-chain implications, and would run arbitrary installs
  on a User's machine from a wizard step (PRD §17.1 rule 6's intent).
- **No network access.** The app does not fetch or download `whisper-cli`.
  Opening the releases page is a User-initiated browser open, not an app
  request.
- **No bundling.** Shipping a pinned, checksummed binary per platform
  (ADR-0003's original plan) is the actual fix for Users who can't or don't
  want to install it by hand, but it needs PRD §17.1 rule 5 dependency
  approval, licence review, and signing work. It belongs with G6 packaging,
  not this ADR.
- **No minimum-version check.** The app relies on `-dtw`/`-nfa` support
  (ADR-0025) and was developed against whisper.cpp 1.9.x. A present but too
  old or DTW-less `whisper-cli` is not detected here. Flagged as a separate
  risk; it needs its own evidence (which versions lack the flags) before any
  gating.
- **No check at app start or wizard entry**, and no change to the Source
  step (see fact 1).
- **No `pyproject.toml`/PyPI-metadata change.** That is a separate, cheap
  documentation follow-up if the Contributor wants it.

## Alternatives considered

| Option | Verdict |
|---|---|
| Warn at app launch / wizard open | Rejected: nags Users who only reuse transcripts or use the Word List; the banner belongs where the requirement actually exists. |
| Warn but leave Continue enabled | Rejected: the worker fails 100% of the time in that state, so enabling Continue only defers a certain error to a later, more expensive point (after model download and setup). |
| Only improve the Model Manager label | Rejected as sufficient: Users don't visit that window before failing. Kept as a secondary surface. |
| In-app "Install for me" button | Rejected: process execution and privilege concerns above. |
| Bundle the binary now | Deferred to G6 packaging; not a small change. |
| Auto re-check on window focus | Deferred. A Re-check button is enough for a first version; focus-based rechecks add event plumbing for little gain. |

## Consequences

- A fresh pip user learns about `whisper-cli` at the step where it matters,
  with a copyable command on macOS, before choosing or downloading a model.
- macOS gets a concrete command; Windows/Linux get an honest pointer. The
  banner text must not overclaim: it should say those platforms are
  untested.
- `TranscriptStep.can_advance()` gains a dependency on an external-tool
  check. Existing tests that assume Continue enables once the model is
  installed will need `find_whisper_cli` patched to a fake path (they must
  never depend on the real machine's install, consistent with ADR-0054's
  isolation rule for anything environmental).
- Three user-facing strings converge on one helper.
- Linux/Windows guidance stays weak until someone verifies an install route
  there; recording that is part of this ADR's follow-ups.

## Verification plan

Automated (no real `whisper-cli` needed):

- `whisper_install_hint()` returns the expected command/URL/note for
  `darwin`, `win32`, and `linux`; unknown platforms fall back to the
  generic pointer.
- `TranscriptStep`: banner absent when `find_whisper_cli` is patched to a
  path; banner present and `can_advance()` False when patched to `None` with
  an installed model; banner absent in "found" mode; "Transcribe again
  instead" from "found" mode shows it; Re-check flips banner and
  `can_advance_changed` after the patch changes.
- Copy button places the command on the clipboard (offscreen Qt supports
  `QApplication.clipboard()`).
- Worker backstop error text contains the hint; behavior unchanged.
- Model Manager engine label includes the pointer when missing.
- Patching `QDesktopServices.openUrl` (a static method, patchable per the
  project's Qt-test conventions) to assert the releases URL.

Live (required, by the Contributor, because visual layout and real
missing-tool behavior can't be proven by property assertions; see the
ADR-0053 chapter-picker history):

- With `whisper-cli` genuinely absent from every discovery location, the
  banner appears at the right place and Continue is disabled.
- After installing it, Re-check clears the banner and enables Continue
  without restarting the app.
- Light and dark themes both legible.

Because `_which` also probes Homebrew locations, simulating "missing" on a
machine that has it installed needs either a temporary rename of the real
binary (only by the Contributor, on their own install) or a small local
harness that patches `find_whisper_cli`. The implementer must not rename or
remove a User's installed binary.

## Contributor answers

1. **Block Continue while `whisper-cli` is missing: yes** (decision item 3
   stands as written).
2. **Windows/Linux get a releases-page link with an "untested" note: yes**
   (decision item 2 stands as written).
3. **README cross-reference to the banner: not decided.** Defaults to no
   README change until the Contributor says otherwise; it is cheap and only
   reaches PyPI with a new release.

## Mockup

`docs/design/whisper-cli-install-banner-wireframe.html` shows four
scenarios (macOS missing, Windows/Linux missing, found, saved-transcript
mode) in light and dark, with a working Copy, Open releases page, and
Re-check ("Simulate installing it" lets Re-check succeed). It also shows
the disabled Continue with a short reason beside it in the footer, a
detail added during the mockup: the banner shouldn't be the only
explanation for a disabled button. Implementation follows only after the
Contributor approves the mockup.
