# ADR-0008 (G5 start, PRD §9.2, §7, §2.1): Catalog persistence, UI structure, and start screen

**Status:** Decided by the product owner directly (not the informal
"decided for implementation purposes" footing of ADR-0004/0005) — asked
and answered explicitly before any G5 code was written.
**Related PRD items:** §9.2 (catalog CRUD), §7 (wizard flow), §14.3
(persistence contract, previously deferred by ADR-0005 for job/chunk
state only — catalog data was left open).

## Context

G2 (`catalog.py`) deliberately built `CatalogService` as an in-memory,
storage-agnostic business-logic layer, explicitly deferring where and how
catalog data (categories, entries, profiles) gets persisted, and how a
User reaches it in the UI at all — G1–G4 built no PySide6 screens. Four
decisions had to be made before any G5 UI code could be written:

1. How is the catalog persisted?
2. Is the new UI a new top-level window, or added to the existing
   `MainWindow`?
3. Of the several G5 screens the PRD implies (catalog management, model
   management, transcribe/scan/review/render wizard), which is built
   first?
4. Should any screen be wireframed before being coded?

## Decisions

**1. Persistence: a JSON file, matching `gui/prefs.py`'s pattern** — not
SQLite. Catalog data (categories/entries/profiles) is small and
low-concurrency compared to job/chunk state (ADR-0005's SQLite scope):
there is exactly one User editing it, interactively, in one window at a
time. This mirrors the project's own existing precedent
(`gui/prefs.py` already persists app preferences the same way) rather
than introducing a second SQLite database for data that doesn't need
transactional guarantees. `catalog_store.py` is the concrete repository:
`save_catalog()`/`load_catalog()` at a `platformdirs`-rooted
`catalog.json`, atomic writes via the existing `storage.write_json_atomic`
helper, and a `CatalogService.export_all()`/`from_records()` bulk-access
pair added to keep `catalog.py` itself storage-agnostic as originally
designed — the service still performs no I/O of its own.

**2. UI structure: a new separate window, launched from `MainWindow`** —
not screens embedded in the existing single-window layout. `CatalogWindow`
is a standalone `QMainWindow` opened via a new "Tools" menu item, following
the exact lazy-create/`show()`/`raise_()`/`activateWindow()` and
per-window `apply_stylesheet(dark)` pattern `QueueWindow` already
establishes. This keeps the filtering feature's UI additive to
`MainWindow` (consistent with `m4bmaker/filter/`'s additive-only module
boundary, ADR-0004) rather than requiring changes to the base
conversion tool's main screen layout.

**3. Starting screen: catalog management**, before model management or
the transcribe/scan/review/render wizard. A User needs a non-empty word
catalog before either of those screens is useful (the wizard's "select
profile" step and the Model Manager's install flow both assume at least
one category/entry exists to select or transcribe against), and catalog
management has no dependency on any other G5 screen — it can be built and
used standalone.

**4. Wireframes:** the product owner opted to wireframe the wizard shell
and scan-review screen specifically before they are coded (those are the
most novel/complex G5 screens — multi-step state, hit-review interaction).
Catalog management, being a conventional two-pane CRUD screen with a
close precedent already in the codebase (`QueueWindow`'s table-based
layout), was built directly without a wireframing pass. Wireframing
remains planned for when work reaches the wizard shell and scan-review
screen, not abandoned.

## What was built against these decisions

- `catalog.py`: `export_all()`/`from_records()` added (bulk read/write
  only, no new I/O, no change to existing CRUD behavior — all 24
  pre-existing tests pass unchanged).
- `catalog_store.py` (new): JSON persistence — round-trips
  categories/entries/profiles/attenuation/archived items/revisions;
  returns a fresh empty service (never raises) on a missing or corrupted
  file, logging a warning only in the corrupted case. 12 tests.
- `m4bmaker/gui/filter/` (new subpackage, mirrors `m4bmaker/filter/`'s
  additive boundary) → `catalog_window.py`: `CatalogWindow`, a two-pane
  category/word CRUD screen. Persists on every mutation (no separate Save
  button, matching `gui/prefs.py`). Archive-vs-hard-delete needs no UI
  branching — `CatalogService.delete_category`/`delete_entry` already
  decide internally; the Delete button always calls the same method.
  Duplicate-phrase warnings are shown via a non-blocking status label
  (PRD §9.2 says "warn," not "reject"). 20 tests.
- `window.py`: a new "Tools" menu with "Manage Word Catalog…", wired to a
  lazy-created `CatalogWindow` following `_queue_window`'s exact pattern
  (construction, dark-mode propagation, `closeEvent` cleanup). Verified
  via `git stash`/`git stash pop` comparison that this introduces zero
  new mypy or flake8 issues beyond the file's pre-existing debt (32
  mypy errors, 12 flake8 E501 lines, unrelated to this change).

## A disclosed verification limitation

This sandboxed environment cannot grant the macOS Screen Recording or
Accessibility permissions needed to screenshot the live app or drive it
via AppleScript/System Events — both were attempted and both failed
(`screencapture`: `could not create image from display`; AppleScript:
`AppleEvent timed out (-1712)`). Unlike the web and iOS surfaces used
elsewhere in this project, there is no way to visually self-verify this
desktop GUI here.

The mitigation: `tests/gui/filter/test_catalog_window.py` builds the real
widget tree under `QT_QPA_PLATFORM=offscreen` (the same headless mode
`tests/gui/conftest.py` already uses for every other GUI test in this
project) and drives it exactly as a User would — selecting table rows,
checking/unchecking the Enabled checkbox, editing Notes inline, typing
into the add-word field, clicking Add/Rename/Delete — asserting on both
the resulting widget state and the underlying `CatalogService`, with
`QInputDialog.getText`/`QMessageBox.question` patched to return
immediately rather than blocking on a real modal. This is real behavioral
verification (construct → act → assert), not a mock of the window itself;
it substitutes for pixel-level visual confirmation, which is not available
in this environment.
