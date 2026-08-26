# ADR-0016 (G5, PRD §9.1, §9.2, §8.3): Profile editor design

**Status:** Decided (design only — not yet implemented). This is the
"later increment" `catalog_window.py`'s own module docstring already
forward-referenced when it was written for ADR-0008: "Profile management
(selecting entries into a named, reusable filter profile) is a distinct
PRD capability from catalog management and is deliberately not built
here; it belongs with the wizard's 'select profile' step." That sentence
already settled *where* profile authoring lives — this ADR is what it
looks like.

**Related PRD items:** §9.1 (filter profile data model), §9.2 (catalog
CRUD — profiles aren't explicitly in that section's bullets, but the same
create/read/update/archive shape applies), §8.3 (attenuation settings
table — defaults and ranges), §9.4 ("each scan uses an immutable
snapshot").

## Context

The Profile wireframe pass (`docs/design/wizard-shell-wireframe.html`,
Profile step) surfaced a real gap while grounding the step in
`CatalogService`: nothing in the app lets a User actually author a
`FilterProfile`. `CatalogWindow` (ADR-0008) ships exactly two panes —
Categories and Entries — and its own docstring already says profile
management doesn't belong there. `CatalogService.create_profile()` /
`update_profile()` exist and are fully tested, but no PySide6 code calls
them anywhere. The Profile step's "+ New Profile" button in the wireframe
was a dead end until this ADR.

The first draft of the wireframe's design note proposed a third
"Profiles" pane inside `CatalogWindow` — that was wrong, not just
undecided. It contradicted a decision ADR-0008 had already made in
`catalog_window.py`'s own docstring, just not yet acted on. This ADR
corrects course to the decision that was already there and fills in the
part that genuinely was open: the editor's actual design.

## Decision

**A modal `ProfileEditorDialog` (`QDialog`), owned by the wizard's
Profile step, not `CatalogWindow`.** Opened by "+ New Profile" (create
mode) or an "Edit" action on the currently-selected profile (edit mode).
Modal to the wizard window, matching the blocking, single-decisive-action
pattern `QInputDialog` already uses elsewhere in this fork
(`CatalogWindow._add_category`/`_rename_category`) — profile authoring is
a bigger form than a single text field, so it is its own `QDialog`
rather than a chain of `QInputDialog` prompts, but the "one modal, one
decision, then back to the list" shape is unchanged.

### Data source: the same `CatalogService`, not a second load

The Profile step loads `CatalogService` once via `load_catalog()` when
the wizard reaches it (mirroring `MainWindow._show_catalog_window`'s
lazy-load), and passes that *same instance* into every
`ProfileEditorDialog` it opens. The dialog never calls `load_catalog()`
itself. This matters because the dialog's category/entry tree and the
step's own profile list must agree on one in-memory state, and because a
save inside the dialog (`create_profile`/`update_profile`) has to be
visible to the step's list the moment the dialog closes, without a
reload.

**Known, disclosed limitation carried forward, not solved here:** if a
User has `CatalogWindow` open *and* the wizard's Profile step open at the
same time, each now holds its own loaded `CatalogService` and its own
save cycle — an edit in one is invisible to the other until it reloads.
This is the same shape of gap already flagged and accepted for Transcript
step's model downloads (ADR-0015's ADR notes: `ModelManagerWindow` and
the wizard can independently start downloading the same model). Worth a
shared-instance or file-watch fix eventually; not a v1 blocker, and not
silently ignored.

### Dialog contents

1. **Name** — a `QLineEdit`, required. Same validation path as
   categories/entries: `SchemaValidationError` from `FilterProfile`'s
   `__post_init__` (blank name) is caught and shown via `QMessageBox`,
   matching `_add_category`'s exact pattern.

2. **Entries — a checkable `QTreeWidget`, grouped by category.** Each
   category is a top-level, checkable, tristate item (`Qt.ItemIsAutoTristate`)
   whose children are that category's entries, each a checkable leaf.
   Checking a category checks all its entries; unchecking one entry
   drops the category checkbox to partially-checked. This is sourced from
   `service.list_categories()` / `service.list_entries(category_id=...)`
   — the exact same calls `CatalogWindow` already makes — so the tree
   always reflects the live catalog, never a copy of it.
   - Only non-archived categories/entries are offered for a *new*
     selection.
   - An entry already in `entry_ids` that has since been archived (in
     either its own record or its category's) still appears, checked, so
     editing an existing profile doesn't silently drop
     it — consistent with `create_snapshot()`'s own documented stance
     that archived-but-referenced entries stay in a profile for
     historical reproducibility (PRD §9.2). It renders visually muted
     (same "(archived)" suffix `CatalogWindow` already uses on its own
     rows) so the User can see why it's there and uncheck it
     deliberately if they want it gone.
   - Saving reads back the full set of checked leaf items into
     `entry_ids` — order doesn't matter (`FilterProfile.entry_ids` is
     compared/consumed as a set at snapshot time, per
     `create_snapshot()`).

3. **Attenuation — a form of six numeric fields**, one per
   `AttenuationSettings` field, each widget's min/max set to the *exact*
   bounds `models.py`'s `_check_range` already enforces, so an
   out-of-range value is unreachable through this UI rather than
   rejected after the fact:

   | Field | Widget | Range | Default |
   |---|---|---|---|
   | Lead padding | `QSpinBox` (ms) | 0–250 | 60 |
   | Tail padding | `QSpinBox` (ms) | 0–300 | 80 |
   | Merge adjacency | `QSpinBox` (ms) | 0–100 | 20 |
   | Fade in | `QSpinBox` (ms) | 5–50 | 15 |
   | Fade out | `QSpinBox` (ms) | 5–50 | 15 |
   | Gain floor | `QDoubleSpinBox` (dBFS) | -96.0–-60.0 | -80.0 |

   A new profile starts from `AttenuationSettings()`'s own defaults, not
   blank/zeroed fields — the dialog never has to define its own default
   table separately from the one `models.py` already owns.

4. **Save / Cancel.** Save calls `create_profile(name, entry_ids,
   attenuation)` (create mode) or `update_profile(profile_id, name=...,
   entry_ids=..., attenuation=...)` (edit mode), then `save_catalog(service)`
   — the same persistence call `CatalogWindow` already makes after every
   mutation, writing to the same `catalog.json`. Cancel discards the
   dialog's in-progress edits; nothing is written. There is no
   auto-save-per-keystroke here (unlike `CatalogWindow`'s table checkboxes)
   because a profile's fields are only meaningful together, as one
   decision, not independently toggleable facts.

### Archive, not delete

`CatalogService` has `archive_profile()` but no `delete_profile()` —
unlike categories/entries, a profile's deletion story was never built to
branch on whether it's referenced (nothing outside a profile references
a profile). The Profile step's list gets an "Archive" action (with the
same confirm-dialog pattern `CatalogWindow._delete_category` uses),
calling `archive_profile` and then hiding it from the default
(`include_archived=False`) list. No new backend method needed — this ADR
adds no `catalog.py` changes at all, UI-only.

## What this ADR does not decide

- Duplicate-profile ("Save as new") is out of scope — PRD doesn't
  require it, and it's easy to add later without touching this design.
- The cross-window staleness gap (`CatalogWindow` + wizard open
  simultaneously) noted above — disclosed, not solved.
- Whether the entry tree needs its own search/filter for a catalog large
  enough to make scrolling painful — not addressed; today's reference
  catalog (2 categories, ~53 words across the wireframe's demo data) is
  nowhere near that scale, and PRD doesn't set a minimum catalog size to
  design against.

## Verification plan (once implemented)

Same bar as every other wizard-step ADR this fork has shipped: real
`CatalogService`/`FilterProfile` objects in tests (no mocks), a
headless-Qt widget test building the real dialog tree and driving it
(matching `test_catalog_window.py`'s already-established pattern:
construct → check tree items → set spinbox values → click Save → assert
against the service), then live visual verification against the actual
app once environment access allows it.
