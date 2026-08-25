# ADR-0012: Wizard shell and Review step — PySide6 implementation

**Status:** Implemented and verified. Ports the approved design from
ADR-0010 (shell) and the Review-step work folded into ADR-0010/ADR-0011
(masking) from wireframe to real, tested, visually-verified PySide6 code.
**Related PRD items:** §7.2 (the eight workflow stages), §9.4 (scan/review
requirements), §8.3 (Interval Planner — reused directly, not
reimplemented).

## Scope

Real, working code for exactly two things: the wizard shell (stepper,
navigation, content pane) and the Review step. The other six steps
(Source, Transcript, Transcribe, Profile, Scan, Render, Complete) get a
`PlaceholderStep` stand-in — a plain "isn't built yet" message — because
only the shell and Review have actually been through ADR-0010's
wireframe review. This keeps the wizard fully navigable end-to-end today
(Back/Continue and the stepper's click-to-revisit all work against the
placeholders exactly as they will once each step is built for real)
without inventing screen designs nobody has reviewed.

## New module: `gui/filter/wizard/`

A subpackage of `gui/filter/`, one level deeper than
`catalog_window.py`/`model_manager_window.py` — the wizard is a
multi-screen feature (a shell plus one widget per step), unlike those
two single-window screens, so it gets its own additive-only boundary
consistent with ADR-0004's module-layout reasoning for `gui/filter/`
itself.

- `stepper.py` — `StepperWidget`, a direct port of the wireframe's
  connected-circle horizontal stepper (equal-width/height cells, a solid
  line through every circle, state carried by color/weight alone, no
  status captions). One wireframe edge case preserved exactly: the
  furthest-reached step, when not the currently active one, renders with
  the plain base badge style rather than "locked" — it's still
  clickable, just not focused.
- `step_base.py` — `WizardStep(QWidget)`, the minimal interface every
  step implements: a title/subtitle pair and a `can_advance()` +
  `can_advance_changed` signal pair so a step can gate Continue.
- `placeholder_step.py` — `PlaceholderStep`, the stand-in described above.
- `review_step.py` — `ReviewStep`, the real screen (below).
- `wizard_window.py` — `WizardWindow(QMainWindow)`, the shell: stepper on
  top, a `QStackedWidget` content pane, Back/Continue footer.

**One thing the wireframe needed that Qt doesn't**: the wireframe's
`measureTallestStepHeight()` JS probe (built to solve an earlier "window
resizes per step" bug) has no equivalent here — `QStackedWidget` already
sizes itself to its largest child natively, so every step shares one
window size for free.

## `ReviewStep`: real backend integration, not a demo dataset

Wired directly to the actual PRD §9.4 backend, reusing it rather than
reimplementing any of it:

- `set_scan(scan, catalog, transcript, source_duration_ms, attenuation=None)`
  is the entry point — nothing calls it yet, since the Scan step it
  would come from is still a placeholder, so the widget also has to
  render sensibly with **no** scan at all (its own tests exercise this
  directly, not only through a future end-to-end flow).
- Stats come from `scan.build_report()` verbatim.
- The Render Plan tab's merged intervals come from
  `interval_planner.build_render_plan()` verbatim — the exact function
  `build_report()` itself calls, not a second implementation of PRD
  §8.3's merge algorithm the way the HTML wireframe necessarily had to
  write in plain JS.
- Masking uses `CatalogService.is_masked()` (ADR-0011) plus a small
  `_mask_term()` display-only helper (first/last letter, asterisked
  middle) — the OR-composition logic itself lives in the backend, not
  duplicated here.
- Per-hit context (PRD §9.4: "up to five recognized words before/after")
  is new backend code this ADR adds: `scan.TranscriptWordIndex`, an
  O(1)-per-hit lookup built once per transcript (a linear scan per hit
  would be O(hits × words) against a transcript that can run to ~150k
  words). Real tests cover the exact-boundary hit, phrase-hit spanning
  multiple words, and a hit that doesn't belong to the given transcript
  at all (returns empty context rather than raising).

### Two deliberate departures from the wireframe

Both toward conventions already proven elsewhere in this codebase,
not arbitrary changes:

- **One checkable "Included" column**, not the wireframe's two separate
  Include/Exclude buttons per row — mirrors `CatalogWindow`'s
  already-proven checkable-`QTableWidgetItem` pattern (ADR-0008) instead
  of inventing a second interaction style for the same app.
- **Bulk actions read the table's native multi-row selection**
  (`QAbstractItemView.ExtendedSelection`) instead of the wireframe's
  separate per-row selection-checkbox column — Qt already provides real
  multi-row selection, so that wireframe column was working around a
  limitation plain HTML has that Qt doesn't.

## Styling

`gui/styles.py` gained one new palette token (Success — a muted green,
`#4b7a52` light / `#7cb88a` dark) and QSS for the stepper's badge/line/
label states, using Qt's dynamic-property selector pattern
(`QLabel#stepBadge[stepState="done"]`, toggled via
`setProperty()`+`style().polish()`) — new to this codebase, but the
idiomatic Qt way to do exactly what `styles.py` already does in spirit
(every color flows from `get_stylesheet(dark)`, never inline). Every
other widget in `ReviewStep` (`QTabWidget`, `QTableWidget`, `QComboBox`,
`QCheckBox`, `QProgressBar`) reused styling that already existed in
`styles.py` — none of it needed new rules.

## Verification

- 50 new tests (9 stepper, 15 wizard shell, 26 Review step), all using
  real backend objects — a real `CatalogService`, a real `Transcript`,
  and a real `Scan` produced by the actual matcher
  (`matcher.scan_transcript` via `scan.run_scan`), not hand-built fake
  `ScanHit` lists — so these tests exercise the real integration, not
  just this widget's own rendering logic in isolation.
- Two real bugs caught only by testing, not by inspection:
  - `QTableWidget.selectRow()` replaces rather than accumulates
    selection even in `ExtendedSelection` mode — confirmed empirically,
    not assumed; the bulk-action test now drives the selection model
    directly instead, matching what a real ctrl/shift-click produces.
  - Holding a `QTableWidgetItem` reference across a checkbox toggle
    fails (`RuntimeError: ... already deleted`) — every toggle rebuilds
    the whole table (same full-refresh pattern `CatalogWindow` already
    uses), so a second interaction on "the same" row needs a fresh
    `.item()` lookup, exactly as a second real click would hit a freshly
    rendered cell.
- `black`/`flake8`/`mypy` clean; confirmed via `git stash` comparison
  that `window.py`'s wiring introduces zero new lint/type issues beyond
  its pre-existing 32 mypy / 12 flake8 baseline.
- **Visually verified against the live app**: launched it, opened
  Tools → Filter Audiobook…, confirmed the shell renders exactly as
  designed on the first (placeholder) step, then clicked Continue five
  times and confirmed the stepper correctly shows five green checkmarks
  with filled connecting lines, Review's real title/subtitle/stat strip/
  tabs/filter row/table all render correctly, and the empty-scan state
  (all stats at zero, empty table) displays correctly since nothing has
  called `set_scan()` yet.

Full suite: 1470 passing, 2 correctly skipped, project-wide.

## Not yet decided

The other six steps' real designs (each needs its own wireframe pass,
same as Review got, before real code); how the eventual Scan step will
actually call `ReviewStep.set_scan()` (today nothing does — that's
real integration work once Source→Scan exist); whether the wizard needs
a more prominent entry point than a Tools-menu item, given it's the
fork's central new feature rather than a supporting tool like Catalog/
Model Manager.
