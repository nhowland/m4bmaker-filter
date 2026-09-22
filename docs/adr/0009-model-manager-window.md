# ADR-0009 (G5, PRD §10.1): Model Manager window design

**Status:** Decided for implementation purposes, same informal footing as
ADR-0004/0005/0006 — no product-owner sign-off was sought since this
follows directly from PRD §10.1's explicit, unambiguous UI requirements
and the UI-structure precedent ADR-0008 already set (separate window,
launched from `MainWindow`'s Tools menu).
**Related PRD items:** §10.1 (model UI requirements), §11.5 (progress
signal shape), §10.2 (network only for explicit User-initiated download).

## Context

`model_manager.py` (built during the G3 spike) already provides the full
backend: a known-model catalog (`KNOWN_MODELS`), checksum-verified atomic
download (`download_model`, with a `progress_callback`/`cancel_event`
pair), presence/corruption checks (`is_installed`/`verify_installed`),
and removal (`remove_model`). No UI existed yet. PRD §10.1 is explicit
about what that UI must show: "model name, engine/model version,
installed/download state, source/provenance, checksum, required disk
space, and removal action."

## Decisions

**1. Layout: a table for the scannable facts, a details panel for the
long ones.** Model name, size, and installed/download state fit a table
column each. Source URL and the full 64-character SHA-256 checksum do
not — cramming them into columns would force horizontal scrolling or
truncation on values a User might actually want to read/copy. These are
shown in a details panel below the table for the currently-selected row
instead, mirroring how `CatalogWindow` already separates a scannable
table from a detail/status area.

**2. Engine version is shown once, not per row.** PRD §10.1 groups
"engine/model version" as one requirement, but a model file has no
version of its own beyond its name — the version that exists is
whisper.cpp's own (`transcript_engine.get_whisper_version()`, already
built in G3). Repeating an engine-wide fact on every row would be
redundant and could drift if only one row were somehow refreshed
independently. It is shown once at the top of the window instead:
`"Engine: whisper.cpp 1.9.2"`, or a not-found message with an install
hint if `find_whisper_cli()` returns `None`.

**3. One download at a time.** The Download button is disabled for
every row while any row's download is in flight (tracked via
`_downloading_name`), not just the row being downloaded. This mirrors
how the base project already gates its own Convert/Split buttons during
their respective workers, and avoids the added complexity of managing
multiple concurrent `QThread`s and progress bars for a feature where
sequential downloads are in no way a hardship — these are one-time,
occasional, multi-hundred-megabyte operations, not something a User
would reasonably want running three at once.

**4. `ModelDownloadWorker` in a new `gui/filter/workers.py`, not inline
in the window file.** The base project already puts every `QThread`
worker for the main window in one shared `gui/worker.py` rather than
splitting one file per worker. `gui/filter/workers.py` starts that same
shared home for the filtering feature's own UI workers — this screen's
`ModelDownloadWorker` today, and a natural landing spot for a future
transcription-progress or render-progress worker when the wizard (next
G5 screen) is built, rather than inventing a new split-per-widget
convention that would diverge from the base project's own pattern.
Its shape mirrors `ConvertWorker` exactly: `progress` signal of
`(message, fraction)`, a `threading.Event`-backed `request_cancel()`,
and `result_ready`/`cancelled`/`error` signals so the worker never blocks
the UI thread.

**5. Closing the window during an active download asks for
confirmation**, then requests cancellation and waits (bounded, 5s) for
the thread — the same `closeEvent` pattern `MainWindow` already uses for
an in-progress conversion, applied here at the window level rather than
only at final app shutdown, since this window can be closed
independently while `MainWindow` stays open.

## What was built

- `m4bmaker/gui/filter/workers.py`: `ModelDownloadWorker(QThread)`.
- `m4bmaker/gui/filter/model_manager_window.py`: `ModelManagerWindow`,
  wired into `MainWindow`'s Tools menu as "Manage Models…", alongside the
  existing "Manage Word Catalog…" entry.
- `window.py`: state slot, dark-mode propagation, and `closeEvent`
  cleanup, mirroring `_catalog_window`'s wiring exactly. Confirmed via
  `git stash` comparison to introduce zero new mypy/flake8 issues beyond
  the file's pre-existing debt (32 mypy errors, 12 flake8 E501 lines,
  unchanged from before this change).
- 26 tests (5 for the worker, 21 for the window), `black`/`flake8`/`mypy`
  clean, using the same headless-behavioral approach ADR-0008 established
  (`QT_QPA_PLATFORM=offscreen`, real widget tree, real filesystem effects
  under `tmp_path` rather than mocked `is_installed` calls).

**Visually verified against the live app**, not just the offscreen test
suite: launched the real app, opened Tools → Manage Models…, and
confirmed the window renders exactly as designed — including a real,
unplanned proof point: this development machine has whisper.cpp actually
installed, and the window's "Engine: whisper.cpp 1.9.2" label correctly
reflects that live state rather than a stubbed one.
