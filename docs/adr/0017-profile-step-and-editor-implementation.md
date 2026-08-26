# ADR-0017 (G5, PRD §7.2 stage 4, §9.1, §9.4): Profile step and editor implementation

**Status:** Implemented and verified — with one disclosed, investigated,
unresolved local-test-environment limitation (see below). Ports
ADR-0016's approved design (a modal `ProfileEditorDialog` owned by the
wizard, not `CatalogWindow`) to real, tested code, following
ADR-0012/0013/0014/0015's established pattern.

**Related PRD items:** §7.2 stage 4 ("select existing basic profile or
manage categories/terms"); §9.1 (filter profile data model); §9.4 ("each
scan uses an immutable snapshot").

## Scope

Two new production files. `ProfileEditorDialog`
(`gui/filter/profile_editor_dialog.py`) — create/edit a `FilterProfile`:
name, a checkable category/entry `QTreeWidget`, and an
`AttenuationSettings` form. `ProfileStep`
(`gui/filter/wizard/profile_step.py`) — the wizard's fourth real step,
replacing the placeholder: lists real saved profiles, radio-selects one,
and opens the editor for create/edit/archive. One small addition to
already-shipped code: `CatalogWindow` gains a `closed` signal (a
`closeEvent` override) so `ProfileStep` can refresh its counts after a
User edits the catalog from "Manage Word Catalog…" without polling.

`WizardWindow` gains a `catalog_service` constructor parameter
(`CatalogService | None = None`, defaulting to `load_catalog()` —
mirroring `MainWindow._show_catalog_window`'s own lazy-load), wires
`ProfileStep` in at `_PROFILE_INDEX`, and removes Profile from
`_PLACEHOLDER_SUBTITLES`.

## What was built against ADR-0016's design

- **No `QButtonGroup`.** ADR-0016 didn't specify this either way; while
  implementing it became clear it's unnecessary — `QRadioButton`'s
  `autoExclusive` default already makes siblings under the same parent
  widget mutually exclusive without one. One fewer moving part.
- **Entry tree**: exactly as designed — `QTreeWidgetItem` category rows
  with checkable entry children, tri-state propagation handled manually
  via `itemChanged` (checking a category checks all its children;
  unchecking one child partially-checks its category), archived
  categories/entries that a profile being edited still references shown
  and checked but visually suffixed "(archived)", exactly matching
  `create_snapshot()`'s own "historical snapshots remain readable"
  stance.
- **Attenuation form**: six widgets, min/max pinned to the exact bounds
  `models.py`'s `_check_range` already enforces (0–250/0–300/0–100/5–50/
  5–50/-96.0–-60.0), so an out-of-range value is unreachable through this
  UI, not just rejected after the fact.
- **Archive, not delete**: `CatalogService` has no `delete_profile()` (a
  profile is never referenced *by* anything else the way a category/entry
  can be), so the step's destructive action is Archive only, with the
  same confirm-dialog pattern `CatalogWindow._delete_category` uses. No
  backend change needed.
- **First-run empty state**: `list_profiles()` returning `[]` (a fresh
  install, PRD §9.2's no-starter-catalog default) shows an inline "No
  filter profiles yet" state and blocks Continue — the realistic default,
  not an edge case, matching the wireframe's own framing.

## Verification

- **54 new tests** — 16 for `ProfileStep` (empty state, list rendering
  with live-computed counts, new/edit/archive flows against mocked
  `ProfileEditorDialog`/`CatalogWindow`, the defensive skip for a
  dangling `entry_id`), 13 for `ProfileEditorDialog` (create/edit mode,
  tree propagation, archived-but-referenced visibility, save/cancel,
  blank-name rejection), plus updates to `test_wizard_window.py` (a new
  `_make_profile_ready` helper mirroring the pattern every prior step's
  wiring test already established, inserted after every
  `_make_transcribe_ready` call site; a `test_profile_step_is_the_real_widget`
  test; the placeholder-index set updated). All against real
  `CatalogService`/`FilterProfile`/`AttenuationSettings` objects — no
  mocking of the domain layer itself. `black`/`flake8`/`mypy` clean.

## A real, disclosed, investigated test-environment limitation

Running this machine's *entire* local test suite as one `pytest`
invocation (`pytest` with no path — 1600+ tests across the whole
project, GUI and non-GUI) segfaults, close to reproducibly, once
`test_profile_step.py` is added — even though every scoped run relevant
to actually shipping this feature is clean: `test_profile_step.py` and
`test_profile_editor_dialog.py` alone or together (10/10 clean),
alongside `test_catalog_window.py`/`test_model_manager_window.py`/
`test_wizard_window.py` (100/100 clean, repeated), and — the one that
actually matters — **this project's real CI command,
`pytest tests/ --ignore=tests/gui`, is completely unaffected (clean,
3/3, ~0.7s)**, because CI already excludes `tests/gui/` entirely (its own
comment: "GUI tests require a real display; skip them in CI"). The gap
is real but narrow: a developer running the *entire* local suite,
GUI included, in one process, on this machine.

This was investigated far past what a "probably flaky" shrug would
justify, specifically because a segfault is a serious class of finding
to leave unexplained. What was ruled out, each confirmed by repeated
(4–10 run) full-suite trials, not a single sample: `QButtonGroup`
(removed outright — a real simplification, kept); a Python reference
cycle through the per-radio `toggled` lambda (tested via
`weakref`-based and fully closure-free replacements — no change);
disconnecting signals before `deleteLater()`; `deleteLater()` at all
(replacing it with `setParent(None)`, and with no teardown whatsoever)
— none of it moved the outcome. It was eventually reduced to a genuinely
minimal case: a **trivial, empty `QWidget` subclass, or even a bare
`QWidget()`, constructed roughly 9–16 times across a new test module**
reproduces it just as reliably as the real `ProfileStep` — conclusively
ruling out anything about this feature's own design, business logic, or
signal/slot patterns (all of which are otherwise identical to
`TranscriptStep`'s and `CatalogWindow`'s own already-shipped, already
heavily-exercised code). Reproduction is sensitive to obscure structural
factors that didn't yield to further bisection (module boundaries,
fixture-vs-direct construction, and instance count all showed
inconsistent effects across trials), pointing to a PySide6 6.11.2/
shiboken6 (already the latest release; not a stale-version issue) /
CPython interaction rather than anything reachable from this fork's own
code. An attempted `lldb` attach to get a native backtrace stalled on
this machine's debugserver security posture rather than producing one.

**Disclosed, not resolved — matching ADR-0008's own precedent** (that
ADR shipped without live visual verification for a comparable
environment-permission reason). Not fixed here because there is no
fix available at the application-code level: every mitigation attempted
at that level failed to change the outcome, and the actual trigger
appears to sit inside PySide6/shiboken itself.

## Not yet decided

Nothing downstream consumes `ProfileStep.selected_profile_id` yet — Scan,
which will (via `CatalogService.create_snapshot(profile_id)`), is still a
placeholder. The cross-window `CatalogService` staleness gap ADR-0016
already flagged (a separately-opened `CatalogWindow`, e.g. from
`MainWindow`'s Tools menu, holds its own instance) remains open,
disclosed, not solved.
