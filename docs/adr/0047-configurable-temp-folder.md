# ADR-0047: Configurable scratch/temp folder, with a shortcut from Source

**Status:** Implemented and verified.

## Context

Rendering and transcribing a long audiobook writes large scratch files —
full-length uncompressed PCM copies of the source (source/envelope/
filtered, each roughly the size of the decoded book) during render, and
per-chunk WAV files during transcription. A real audiobook can require
50+ GB of scratch space at once. Both pipelines wrote this via a bare
`tempfile.TemporaryDirectory()`, using whatever the OS default temp
location happens to be, with no way for a User to redirect it — a real,
already-experienced problem (an earlier session in this same engagement
found and cleaned up 24GB of orphaned temp directories from this exact
mechanism).

The User asked to discuss this before building anything: a new Settings
option to choose where this scratch space goes, and possibly a shortcut
from the Source step's own "Temp storage needed" figure — the moment a
User would actually realize they need to redirect it. After discussion,
built directly (no mockup) — this one's UI-contained enough not to need
one.

## Investigation

Before proposing anything, traced the real call sites rather than
assuming: `renderer.py`'s `_run_with_progress()` also calls
`tempfile.TemporaryDirectory()`, but only for a short-lived
`stderr.log` capture file — not a real storage concern, left untouched.
The actual big consumer is `render()`'s own `TemporaryDirectory()`
(`source.pcm`/`envelope.pcm`/`filtered.pcm`), plus
`transcription_orchestrator.py`'s per-chunk WAV extraction. Also found
`storage.py` already had an unused `cache_root()` — its own docstring
already describes it as being for exactly this ("recreatable
intermediates only... decode scratch space, render staging") but nothing
called it. And `models_dir()`/`transcripts_dir()` already established
the override pattern this needed: read a `settings.py` key directly via
a local import, no parameter threading through call signatures at all —
simpler than the parameter-plumbing this was expected to need going in.

## Decision

New `settings.py` key `"temp_dir"` (default `None`, same "no override"
convention as the other three). New `storage.temp_root()`, mirroring
`models_dir()`/`transcripts_dir()`'s override pattern exactly, but
falling back to the pre-existing `cache_root()` instead of a
`data_root()` subdir — this content already fit that root's own stated
purpose. `render()` and `transcription_orchestrator.py`'s job-running
function now call `temp_root()` directly (local import, same as
`models_dir()`/`transcripts_dir()`'s own callers) and pass it as
`tempfile.TemporaryDirectory(dir=...)`, mkdir'd defensively right before
use rather than relying on `ensure_dirs()` (which, found along the way,
nothing in the app actually calls yet — real but pre-existing and out of
scope here). `ensure_dirs()` itself gained `temp_root()` in its own
directory tuple for when that does get wired up.

Settings window gained a fourth folder row ("Temp Files"), reusing
`_build_folder_row()` unchanged in shape — `_build_folder_row()` gained
an optional `tooltip` param (applied to the row's own label) so the row
can explain, in place, that this is the largest disk consumer in the
feature and can be pointed at a roomier drive.

Source step's `_InfoPanel` gained a "Change temp storage location…" link
on the "Temp storage needed" row itself
(`_InfoPanel.open_settings_requested` signal), built once at
construction alongside the row rather than toggled per-state — present
in both placeholder and eligible states equally, matching the "never
hide a row element between states" lesson from ADR-0046's addendum (a
hidden widget silently drops out of Qt's own
height calculation). Not tied to a loaded file at all, since the temp
setting is global. Forwarded up through `SourceStep.open_settings_requested`
→ `WizardWindow.open_settings_requested` → `MainWindow._show_settings_window`
(connected once, in `window.py`'s `_show_wizard_window()`) — `WizardWindow`
has no reach into `MainWindow`'s own Settings-window lifecycle, so it
just re-emits rather than reaching for it directly.

Deliberately left alone: `cover.py`'s own `get_temp_root()` (a
completely different, base-app-level mechanism, used only for small,
short-lived cover-art preview extraction) does not read this new
setting. Raised and rejected during the discussion — the inconsistency
is real but low-stakes, and folding a base-app utility into this
feature's own settings would be scope creep for a few MB of preview
extraction.

## Testing

New tests: `test_settings.py` (defaults dict), `test_storage.py`
(`temp_root()` override + fallback), `test_settings_window.py` (new row
folded into the existing parametrized folder-row tests, plus its own
"shows a real path when unset" test), `test_source_step.py` (link
present in both states, click forwards the signal), `test_wizard_window.py`
(signal forwarded from `SourceStep` to `WizardWindow`). Full project
suite 1936 passed / 2 skipped.

Also driven in the real running app (`m4bmaker-gui`), not just unit
tests: clicked the Source step's link, confirmed it raised Settings,
set and reset a real Temp Files override, confirmed both against the
real `settings.json` on disk. That real pass caught two things the unit
tests, which only assert on individual widgets, couldn't have: the link
label read as vague ("Change location…") without the row it was next to
for context, renamed to "Change temp storage location…"; and the
Settings window's fourth row pushed total content past its old
hardcoded `resize(720, 380)`, so the last row's Browse/Reset buttons
were visibly clipped against the row below rather than the window
growing to fit. Fixed both: the window now sizes itself off its real
built content (`centralWidget().sizeHint()`) instead of a guessed
constant, and each `_build_folder_row()` gained a deliberate spacing
differential (4px within one setting's own label-and-path, 24px between
different settings) so the four rows read as clearly separate groups
rather than one dense block — the same problem the tight, uniform
spacing had been silently hiding at three rows.

## Addendum: real disk-space audit, and get_temp_root()'s own separate leak (2026-08-31)

Prompted by the User reporting ~25GB of unaccounted-for disk space —
real filesystem investigation (`du`/`df` against `$TMPDIR`), not
speculation. Nearly all of it (20GB) turned out to be stale
`pytest-of-<user>/pytest-<N>` session directories from earlier test runs
(the model-manager test suite's realistic-sized fake `ggml-*.bin`
fixtures, ~4GB per full-suite run, across five old sessions that
outlived pytest's own default retention) — unrelated to this feature,
deleted directly. A separate, much smaller amount (~7MB across ~40
directories) was `get_temp_root()`'s own leak (`m4bmaker/utils.py`,
used by `cover.py`'s preview extraction) — the *actual* mechanism
behind this whole engagement's earlier, real 24GB orphaned-temp
incident, still leaking at a small scale.

Root cause: `get_temp_root()`'s cleanup is registered via `atexit`
only, which never runs on anything but a normal interpreter exit — a
crash, a force-quit, or a `kill`/`pkill` (all three happened for real
this session, restarting the GUI to pick up code changes) skips it
entirely and leaves the directory behind for good. No signal handler
can fully close this gap (`SIGKILL` is uncatchable by design), so
fixed it the way this class of problem is normally handled: a
startup-time sweep instead of a shutdown-time promise. Directory names
now embed the owning PID (`m4bmaker_<pid>_<random>`, up from a bare
`m4bmaker_<random>`); each first `get_temp_root()` call in a process
now sweeps sibling directories first, removing any whose embedded PID
is confirmed dead (`os.kill(pid, 0)` raising `ProcessLookupError`) and
leaving everything else untouched — including directories in an
unrecognized (pre-fix) name shape, since there's no PID to check.
Deliberately conservative in both directions: `PermissionError` (exists,
just not signalable) and any name that doesn't parse are both treated
as "leave it alone," so the sweep can only ever under-clean, never
delete something still in use.

Windows carve-out, found while writing this: `os.kill(pid, 0)` on
Windows doesn't just probe for existence the way it does on POSIX — it
calls `TerminateProcess()` under the hood, which is capable of actually
killing a real, unrelated process that happens to have reused the pid.
`_process_is_alive()` always reports "alive" on `sys.platform ==
"win32"` instead, leaving Windows exactly on its pre-existing
atexit-only behavior rather than risk that — this project's real
testing has been macOS-only throughout, so improving POSIX cleanup
without touching Windows behavior at all was the safe trade.

Verified end-to-end with real OS processes, not just mocks: spawned a
real subprocess that calls `get_temp_root()`, `SIGKILL`'d it before it
could reach `atexit` (confirmed the directory survives the kill, as
expected), then spawned a second real process and confirmed its own
`get_temp_root()` call swept the first one's now-dead-pid directory
away. 9 new unit tests (`_parse_pid_from_temp_dir_name`,
`_process_is_alive` including the Windows carve-out, and
`_sweep_stale_temp_roots`, all filesystem-isolated via `tmp_path`) plus
the two existing `get_temp_root()` tests updated for the new prefix
shape. Full suite 1948 passed / 2 skipped, `black`/`flake8`/`mypy`
clean.
