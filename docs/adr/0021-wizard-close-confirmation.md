# ADR-0021: Wizard close-confirmation, actually implemented

**Status:** Implemented and verified. Closes a real gap found while
implementing the Complete step (ADR-0020): `WizardWindow`'s own module
docstring, and ADR-0010's original wireframe review before it, both
claimed that closing the wizard mid-Transcribe or mid-Render prompts for
confirmation — but no `closeEvent` override existed anywhere in
`wizard_window.py`. This documented design intention was never actually
turned into code in any implementation round since ADR-0010. This ADR is
that code.

**Related:** ADR-0010 ("Cancel confirms when it matters. Closing the
wizard mid-Transcribe or mid-Render prompts first, mirroring the
confirm-before-close pattern `ModelManagerWindow` already uses.") —
that sentence described an intention; this ADR is what implements it.

## Decision

`WizardWindow.closeEvent()` checks both `TranscribeStep._worker` and
`RenderStep._worker` for a still-running job, in that order, and
confirms via `QMessageBox.question` before letting the close proceed —
the same real, already-proven pattern `ModelManagerWindow.closeEvent`
uses for a mid-flight download.

**The two cases are not symmetric, and the implementation doesn't
pretend they are:**

- **Mid-Transcribe**: `TranscribeWorker` has a real `request_pause()` —
  confirming pauses the job (real, resumable progress via the SQLite Job
  Orchestrator, ADR-0015) and bounded-waits (`wait(5000)`) for that to
  actually land before the window closes, exactly mirroring
  `ModelManagerWindow`'s own `request_cancel()` + `wait(5000)` sequence.
  The dialog says so plainly: "Closing now will pause it — your progress
  is saved and resumes next time."
- **Mid-Render**: `RenderWorker` has no `request_pause()` or
  `request_cancel()` at all — ADR-0019's own deliberate choice, since
  there is no handle back to the underlying ffmpeg subprocess to
  interrupt it. Confirming here does not, and cannot, ask the worker to
  do anything; the dialog says so plainly too: "cannot be safely
  stopped... Closing now will abandon it — you'll need to start over."
  The only thing this confirmation buys the User is not being surprised.

**Why it's safe to just let an abandoned `RenderWorker` keep running in
the background after the window closes:** `WizardWindow` is never
destroyed on close — no `Qt.WA_DeleteOnClose`, the same lazy-create-and-
reuse pattern `CatalogWindow`/`ModelManagerWindow`/`QueueWindow` already
use (`MainWindow._show_wizard_window` constructs it once, then just
`.show()`s the same instance on every later open). `close()` only hides
it. Since nothing destroys the `QThread` object out from under the
running render, there's no `QThread: Destroyed while thread is still
running` hazard — the render simply finishes (or fails) unobserved in a
hidden window, which is exactly what "abandoned" should mean, not a
crash.

## What this ADR does not change

No new backend capability — `RenderWorker` still has no stop mechanism,
and this ADR doesn't add one; ADR-0019's reasoning for that stands.
Scan and Profile/editor dialogs are not checked here — neither has a
worker whose loss is either destructive (`ScanWorker` is fast and
produces nothing durable to lose, by Scan's own design, ADR-0018) or
plausible to still be open when closing the *wizard* window specifically
(`ProfileEditorDialog` is modal to the wizard itself, so it must already
be dismissed before `WizardWindow.closeEvent` can even run).

## Verification

**6 new tests** in `test_wizard_window.py`, all shell-level (workers are
stand-in `MagicMock`s with `isRunning()`/`request_pause()`/`wait()` —
TranscribeWorker/RenderWorker have their own dedicated tests for their
own real behavior, same "test the shell, not the step" split every other
test in this file already uses): nothing running closes without any
prompt; declining either prompt keeps the window open (`close()` returns
`False`, verifying `event.ignore()` actually ran); confirming
mid-Transcribe calls `request_pause()` and `wait(5000)`; confirming
mid-Render calls neither (nothing to call); both running at once
confirms both prompts in order. Full wizard-step test suite (195 tests)
and this project's real CI command remain clean.
