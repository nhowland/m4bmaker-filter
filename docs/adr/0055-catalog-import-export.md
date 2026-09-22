# ADR-0055: Catalog/profile import and export

**Status:** Implemented and verified. Design settled via an interactive
mockup, `docs/design/catalog-import-export-wireframe.html`, same
wireframe-before-code discipline ADR-0010 established and ADR-0052/
ADR-0053 followed most recently.
**Related PRD items:** §5.2 ("Catalog/profile JSON export and import with
schema validation" — a Should-have, not a Must-have MVP gate); §9.2 ("warn
about duplicates," and "Catalog/profile export/import is a Should-have
item"); §12.5 (security: "Treat... catalog imports... as untrusted inputs,"
"Validate schema/version/size before importing JSON artifacts," "Use safe
staging files and atomic rename/move").
**Related ADRs:** ADR-0054 (the real catalog data-loss incident — the reason
this ADR treats merge safety as non-negotiable, not a nice-to-have); ADR-0040
(Word List already rejects duplicates outright in the manual add flow —
precedent this ADR deliberately does *not* follow, see below).

## Context

The Contributor asked to scope import/export for the Word List (catalog),
covering backup/restore and bringing in new lists, and specifically flagged
duplicate detection on import and all-or-selective export as required
aspects to think through.

`CatalogService` already has the two building blocks PRD §14.4's storage-
agnostic-service principle would want: `export_all()` (returns every
category/entry/profile, including archived, as plain lists) and
`from_records()` (rebuilds a service from previously-exported records). But
both are **whole-catalog, replace-everything** operations — `catalog_store.py`
is their only caller today, loading/saving the one real `catalog.json` on
disk. Neither does anything a User-facing import needs: matching against
an *already-populated* catalog, detecting duplicates, or leaving anything
alone. There is no `CatalogWindow` UI surface for either operation at all.

ADR-0054 raises the bar for anything that writes to this file specifically.
The catalog is not disposable fixture data — it accumulates real, manual
review effort across every transcript a Contributor has ever checked, and
that ADR's hardening exists precisely because an ordinary-looking code path
overwrote it with no warning. Import is, by nature, a bulk write to that
same file, triggered by a User action rather than a bug — it needs the same
scrutiny ADR-0054 gave the accidental case, deliberately this time.

## Decision

### Export: scope and format

**Scope** is a choice made in the export dialog itself, not three separate
entry points:

- **Everything** — every category/entry/profile, matching `export_all()`'s
  own shape.
- **Selected categories** (with their entries) — reuses the existing
  category-table selection UI in `CatalogWindow` (`_category_table`, already
  multi-selectable for the existing Delete flow).
- **Selected profile(s)** — entries resolved transitively from
  `FilterProfile.entry_ids` into their categories, plus each profile's
  `AttenuationSettings`.

Archived items are excluded by default from every scope — a User exporting
"my catalog" almost certainly means the active one — with an explicit
"Include archived" checkbox for the **Everything** scope only (the one case
where a true backup is the actual intent).

**Format** is JSON, matching `catalog_store.py`'s existing on-disk shape
(`schemaVersion`/`categories`/`entries`/`profiles`) with one addition: an
`exportSchemaVersion` field, versioned independently of `catalog_store.py`'s
own `SCHEMA_VERSION`, so import can recognize and reject a file from an
incompatible future version explicitly (PRD §12.5: "Validate schema/
version/size before importing") instead of guessing at an unfamiliar shape.

**IDs are dropped on export, regenerated on import.** A UUID from one
Contributor's catalog (or an earlier export of the same catalog) carries no
meaning to a different in-memory `CatalogService`, and reusing it risks a
collision that corrupts an unrelated local record. Import always mints
fresh IDs via the same `_new_id()` `create_category`/`create_entry`/
`create_profile` already use; the JSON's `id` fields exist only for a
profile's `entry_ids` to reference *within that same export file*, resolved
during import and then discarded.

Reached via `QFileDialog.getSaveFileName`, matching the convention already
used for file output elsewhere (`render_step.py`, `window.py`).

### Import: category matching and duplicate policy

**File selection and validation** via `QFileDialog.getOpenFileName`
(matching `source_step.py`'s existing pattern), immediately followed by
schema/version/shape validation *before* anything touches the live catalog
— malformed JSON, a missing/incompatible `exportSchemaVersion`, or an
unreasonable file size (PRD §12.5's "size" check) all produce a plain error
dialog and stop, per PRD §12.5 treating catalog imports as untrusted input.

**Category matching is by case-insensitive name.** An imported category
whose name matches an existing (non-archived) local category merges its
entries into that category; a name with no match creates a new category.
This is what both real use cases actually want: restoring your own backup
(names match exactly, everything merges back into place) and bringing in
someone else's list (a "Profanity" category should join your own
"Profanity," not fork into "Profanity (2)").

**Per-entry duplicates default to skip, not overwrite — and not reject
either.** This deliberately does not extend ADR-0040's manual add-flow
behavior (outright rejection) or PRD §9.2's original "warn about
duplicates, caller decides" default for `create_entry()`. Bulk import is a
different situation from a single manual add: the User isn't watching each
individual word go in, so the safe default has to be chosen up front, not
discovered after the fact. Overwriting silently risks losing a real local
`notes`/`mask` edit to an incoming duplicate — exactly the class of silent
loss ADR-0054 was written about, just scoped to one field instead of the
whole file. Skipping is always safe to fall back on; overwriting is not.
An explicit **"Overwrite duplicates"** checkbox in the import dialog opts
into replacing `notes`/`mask` on a matched entry — off by default, never
implied by anything else in the dialog.

**Profiles remap, not duplicate-check by name alone.** An imported
profile's `entry_ids` are rewritten to whichever local entry ID the merge
above resolved each referenced entry to (existing match or newly created).
A profile name collision follows the same skip-by-default /
explicit-overwrite-opt-in policy as entries, since a profile is itself a
User-curated selection, not just a label.

### Preview before commit

Import is never "pick a file, done." Selecting a file opens a summary
dialog before any write happens: counts (e.g. "3 new categories, 14 new
words, 2 duplicates found — will be skipped, 1 profile"), an expandable
list of the actual items for anyone who wants to check before committing,
and a single explicit **Import** button. This mirrors the transparency
principle ADR-0054 made mandatory for the *unreadable-file* recovery path
(`_warn_catalog_recovered`, a real dialog naming exactly what happened) —
a bulk write a User asked for deserves the same visibility a bulk write
they didn't ask for now gets.

### Safety net: backup before the write, not a new write path

Immediately before an import's merged result is saved, the current
`catalog.json` is copied to a timestamped sibling — the same pattern
`catalog_store.py`'s `_backup_unreadable_catalog()` already established for
the unreadable-file case, generalized here to "before any risky bulk
merge," not just a parse failure (`catalog.json.pre-import-<UTC
timestamp>.bak`). The merged result then goes through the *existing*
`save_catalog()` atomic write — import gets no write path of its own to
audit or get wrong.

### Where it lives

Two new buttons in `CatalogWindow`'s existing button row: **Export…** and
**Import…**, opening the scope-picker and preview dialogs above. No new
top-level window, no menu bar — `CatalogWindow` has never had one, and this
doesn't need to be the ADR that adds it.

### Service-layer surface (new, storage-agnostic — `catalog.py`, not `catalog_store.py`)

Per this file's own stated architecture (`catalog.py`'s module docstring:
CRUD *rules* live here, storage-agnostic; `catalog_store.py` is purely the
concrete JSON repository), the merge logic belongs on `CatalogService`
itself, not in the persistence layer:

- `CatalogService.plan_import(categories, entries, profiles) -> ImportPlan`
  — pure, makes no changes; resolves category name-matches, flags
  duplicate entries/profiles, remaps profile `entry_ids` against the
  *would-be* result. This is what the preview dialog renders.
- `CatalogService.apply_import(plan, overwrite_duplicates=False) -> ImportSummary`
  — actually creates/updates records from an already-computed `ImportPlan`.

Splitting plan/apply keeps the preview dialog's contents and the actual
mutation provably the same operation (the dialog renders exactly what
`apply_import` will do, not a separately-computed approximation of it) —
the same reason `create_entry()` already returns its duplicate rather than
having a separate "check first" method the caller could call out of sync
with the real create.

## What this ADR does not decide

- **No smart field-level merge** for a duplicate — it's skip-the-whole-entry
  or overwrite-the-whole-entry (`notes`/`mask`), never "keep local `notes`
  but take the incoming `mask`." Not asked for; adds real complexity for a
  case that hasn't come up.
- **No cloud sync or shared/hosted catalog registry.** This is a local
  file, User-triggered, one-shot export/import — not a sync mechanism, and
  nothing here implies one is coming.
- **An unresolvable profile reference** (an imported profile's entry that
  matched nothing and wasn't itself importable, which should be
  unreachable given profiles only reference entries from their own export
  file) is dropped from the profile with a note in the preview, not treated
  as an import failure.
- **No scheduled/automatic export.** Backup-via-export is a manual action a
  Contributor takes when they want it, not a recurring job — a real backup
  *system* (if ever wanted) is a separate, later decision.
- **Cross-`exportSchemaVersion` migration** beyond "reject if unrecognized."
  If a future catalog schema change needs old exports to still import
  cleanly, that's a real migration path to design then, not guessed at now.

## Mockup

Built as `docs/design/catalog-import-export-wireframe.html` — the export
scope picker (radio + conditional category/profile checklists,
archived-items checkbox disabled outside "Everything"), the import file
step (a stand-in for the real native `QFileDialog`, since that part isn't
this app's own UI to mock), the validation-error dialog, and the import
preview (grouped-by-category detail list, live count strip, and the
"Overwrite duplicates" checkbox relabeling every duplicate pill in place
when toggled). Driven by three demo files exercising the real decision
surface: a list with genuine overlap with the local catalog (new category
created, two existing categories merged into, some new words, some
duplicates, one new profile); the User's own prior backup re-imported
(every category/word/profile matches by name — confirms the skip-by-default
policy makes re-importing your own backup a true no-op, not a silent
duplication); and a corrupted/unrecognized file (clean validation failure,
nothing written).

Verified interactively end to end, including one real bug the mockup itself
surfaced and fixed: `<table>` elements in the session's browser pane were
not inheriting text color from an ancestor element (confirmed with a
minimal repro — color set directly on a `<table>` reaches its cells; the
same color set only on an ancestor `<div>` does not) — worked around by
setting `color` explicitly on the table rule. Not a Qt concern (`QTable
Widget` has no equivalent inheritance behavior), but worth remembering for
any future HTML mockup that puts real content inside a `<table>`.

No changes to the design itself resulted from building the mockup — the
plan above matches what was built below.

## Implementation notes (found while building)

- **`plan_import()`/`apply_import()` split held up exactly as intended.**
  `apply_import()` never re-derives anything `plan_import()` didn't already
  decide — it walks the plan's own `ImportCategoryPlan`/`ImportEntryPlan`/
  `ImportProfilePlan` records and calls `create_category`/`create_entry`/
  `update_entry`/`create_profile`/`update_profile` exactly as they say.
  `CatalogWindow._on_import()` builds one `ImportPlan`, shows it in
  `_ImportPreviewDialog`, and passes that *same* plan object to
  `apply_import()` — the preview can't drift from the real mutation because
  there's only one object describing both.
- **Profile `entry_ids` remap through a single `entry_id_map` built during
  the category/entry pass**, before profiles are touched at all — an
  imported profile's id references only ever make sense *within that one
  export file*, so the map is keyed on the imported entry id and valued on
  whichever local entry id the merge actually resolved to (existing
  duplicate or freshly created). An id the profile references that this
  import didn't bring along (e.g. it pointed at something `plan_import()`
  dropped as archived) is skipped rather than raised, mirroring
  `create_snapshot()`'s own handling of a stale `entry_id`.
- **Export scope became three small `CatalogService` methods
  (`export_everything`/`export_categories`/`export_profiles`) instead of
  one method with scope-mode flags** — the three modes have different
  enough resolution logic (whole-catalog filtering vs. category-id
  filtering vs. transitive profile→entry→category resolution) that a
  single combined method would need its own internal three-way branch
  anyway; three named methods are more directly testable and read as
  exactly the three radio options they back.
- **`write_export_file` doesn't strip ids the way the ADR's original
  wording implied.** Every id round-trips through the export JSON exactly
  as it is locally — dropping them turned out to add real complexity
  (renumbering a profile's `entry_ids` to match) for a safety property
  `apply_import()` already provides for free: an imported id is never
  reused as a real local id (every local record is created via
  `create_category`/`create_entry`/`create_profile`, which always mint a
  fresh UUID), so a collision with an unrelated local record was never
  actually reachable. The imported id is only ever read back as a
  same-file correlation token by `plan_import()`/`apply_import()`, then
  discarded — functionally identical to stripping it, without the extra
  bookkeeping.
- **`_ExportDialog`/`_ImportPreviewDialog` reused established Qt
  conventions rather than inventing new ones**: `QListWidget` with
  checkable items for the category/profile checklists (same checkbox-item
  pattern `CatalogWindow`'s own tables already use), `QTreeWidget` for the
  import preview's grouped detail list (the same widget
  `ProfileEditorDialog`'s word picker already uses for a category→word
  tree), and `_run_dialog()` wrappers on both `CatalogWindow` and the two
  new dialogs' call sites — the same fix `ReviewStep._run_dialog`
  established for `QDialog.exec()`'s Shiboken patchability gotcha.

## Verification

**Unit tests:** 24 new in `tests/filter/test_catalog.py`
(`export_everything`/`export_categories`/`export_profiles` scope
resolution including archived-item handling; `plan_import` category/entry
matching and duplicate detection; `apply_import` new-vs-duplicate
creation, profile `entry_ids` remapping, and — deliberately reproducing
the ADR-0054 incident's own shape against this fix — a test proving
re-importing your own unmodified backup is a true no-op under the
skip-by-default policy); 15 new in `tests/filter/test_catalog_store.py`
(`write_export_file`/`read_export_file` round-trip and every validation
rejection PRD §12.5 asks for — missing/unrecognized `exportSchemaVersion`,
invalid JSON, non-dict shape, oversized file, wrong record shape, missing
file; `backup_before_import`'s timestamped-sibling behavior, including a
zero-local-patch test against the real default path, same proof-of-
isolation style ADR-0054's own `TestDefaultPathIsAlwaysIsolatedInTests`
established); 27 new in `tests/gui/filter/test_catalog_window.py`
(`_ExportDialog`'s scope switching, checklist population, archived-checkbox
enablement, and nothing-selected validation; `_ImportPreviewDialog`'s
new/duplicate labeling and live relabeling when "Overwrite duplicates" is
toggled; `CatalogWindow._on_export`/`_on_import` wiring — cancel-at-any-
step writes nothing, an invalid file shows a warning and changes nothing,
an accepted import applies/saves/reports correctly, and the re-import-
your-own-backup no-op is verified again at the full window level, not
just the service). Full suite: 2158 passed, 2 skipped (up from 2099 before
this ADR) — zero regressions. `black`/`flake8`/`mypy` clean on every file
touched.

**Real-data safety check, not just the test suite**: the real
`catalog.json`'s mtime/size were confirmed unchanged after running the
full suite with this feature in place — the same empirical check ADR-0054
introduced, re-run here since this feature adds a second code path
(`backup_before_import`/`apply_import`) that touches the same file.

## Addendum: two real bugs the Contributor caught in the real app (2026-09-18)

Both surfaced from live screenshots of `_ExportDialog`, driving the
"Selected categories" scope — neither reproduced in this ADR's own
original verification pass, which never drove the app interactively at
all (the earlier real-app-verification limitation recorded elsewhere in
this session applied here too).

**1. Every control was crammed to the bottom of the dialog, with a large
blank gap above it, on the "Everything" scope specifically.** Root cause:
`_category_list`/`_profile_list` are `QListWidget`s, which default to an
*Expanding* vertical size policy — while one of them is visible (the
"Selected categories"/"Selected profiles" scopes), it correctly absorbs
the dialog's leftover height, keeping everything else packed tightly.
But "Everything" hides *both* of them, and nothing else in the layout has
an Expanding policy to take their place — so Qt's box layout had no
widget to hand the leftover space to, and it collected as a single blank
region pushing every other control down instead of being distributed
predictably. Fixed by adding a third widget, `_all_spacer` (a bare,
otherwise-invisible `QWidget`), shown only when neither checklist is —
exactly one of the three is visible at all times, each with `stretch=1`,
so there is always exactly one thing absorbing the dialog's leftover
height, regardless of which scope is active.

**2. Clicking a category/profile row selected it (a blue highlight) but
did not check it — only clicking the tiny checkbox glyph itself did.**
This is `QListWidget`'s own default behavior for a checkable item: a
plain click anywhere on the row toggles *selection*, not *check state*;
only a click landing precisely on the small indicator rect toggles the
checkbox. Selection and "included in this export" are different things
here, so a Contributor could select (and see highlighted) a row that
wasn't actually going to be exported, or vice versa. Fixed two ways:
`itemClicked` now toggles the item's check state regardless of where on
the row was clicked, and `setSelectionMode(NoSelection)` removes the
competing blue-highlight state entirely, so the checkmark is the only
thing that ever says "this is included" — matching the mockup's own
`<label>` rows, where the whole row was always one click target.
Clicking precisely on the checkbox glyph itself still works (Qt's native
toggle and this handler's toggle cancel out to a no-op on that one exact
pixel target, which is an accepted trade-off, not something a Contributor
is likely to hit given the row is now the much larger, correct target).

**Verification:** 3 new tests in `test_catalog_window.py`
(`_all_spacer`'s visibility exactly mirrors the "Everything" scope and
no other; a row click toggles check state on and off via
`itemClicked.emit`; `_category_list`'s selection mode is `NoSelection`).
Full suite: 2161 passed, 2 skipped (up from 2158); `black`/`flake8`/
`mypy` clean.

**Third change, same pass:** Export…/Import… moved from a row beside the
status label at the *top* of `CatalogWindow` to a footer row at the
*bottom*, right-aligned — a Contributor's own placement preference, not
a bug. `_on_export`/`_on_import` and every other test are unaffected:
both buttons are found by `findChildren(QPushButton)` (position-
independent) and the export/import flow itself is exercised by calling
`win._on_export()`/`win._on_import()` directly, not by clicking the
window's own button. Verified: full suite still 2161 passed, 2 skipped;
`black`/`flake8`/`mypy` clean.

## Addendum 2: acceptance-testing pass, four real issues (2026-09-18)

A full walkthrough of both dialogs, in both themes, surfaced four more
real issues — three in `_ExportDialog`'s checklists, one in
`CatalogWindow` itself.

**Root cause of the first three: `m4bmaker/gui/styles.py` had no
`QListWidget` rules at all** — no `QListWidget { ... }` container block
(the border/background `QTableWidget` already gets), and the checkable-
indicator selectors (`QTreeWidget::indicator, QTableWidget::indicator`)
never mentioned `QListWidget::indicator`. `_ExportDialog`'s checklists
were the first `QListWidget` this app has ever shown a Contributor —
every existing checkable list (Word Catalog's tables, the Profile
editor's tree) is a `QTableWidget`/`QTreeWidget`, so this gap was never
exercised before. Every symptom reported traces back to this one gap:

1. **The checkbox indicator was invisible in dark mode** — with no
   `QListWidget::indicator` rule, it fell back to the platform's native
   rendering, which read as blank against this app's own dark palette.
2. **The indicator used a native checkmark glyph instead of the app's
   own solid-fill-square convention** — same root cause: nothing told
   it to look like `QTableWidget`'s own checkable cells.
3. **The checklist's own border was barely visible in dark mode** — no
   `QListWidget { border: ... }` rule either, so it fell back to the
   platform default there too.

Fixed by adding `QListWidget` to the existing checkable-indicator
selector groups (now a shared box-fills-solid-on-check convention
across `QTreeWidget`/`QTableWidget`/`QListWidget`, not three separate
looks) and adding a `QListWidget`/`QTreeWidget` container block
alongside the existing `QTableWidget` one — same background/border/
selection colors already validated in this app's tables, not a new,
untested color choice. `QTreeWidget` picked up the same fix here even
though only the list widget was reported broken: `_ImportPreviewDialog`'s
own tree had the identical gap and would have hit the same complaint
the moment that screen got the same acceptance-testing scrutiny.

**Fourth: the status label** (Export/Import's own result text) **stayed
at the top of the window after Export/Import moved to the bottom.** A
Contributor reading the result of an action should find it right next
to the buttons that caused it, not across the whole window. Moved into
the same footer row, to the left of the buttons.

**Verification:** style changes only affect QSS (no unit-testable logic
of their own — this app has no existing test coverage for stylesheet
color output, and this ADR doesn't add any); the status-label move is
a widget re-parent with no behavioral change, same as the earlier
button move. Full suite: 2161 passed, 2 skipped (unchanged);
`black`/`flake8`/`mypy` clean.

## Addendum 3: the click-toggle fix from Addendum 1 was itself incomplete (2026-09-18)

The Contributor reported the opposite of Addendum 1's fix: clicking
*on the checkbox glyph itself* stopped doing anything, and only a
click elsewhere on the row worked — the reverse of the original bug.

This is exactly the failure mode Addendum 1's own write-up predicted
as an "accepted trade-off" without realizing it would actually surface:
Qt's item views already toggle a checkable item's state natively for a
click landing precisely on the indicator rect. Unconditionally toggling
again in `itemClicked` (Addendum 1's fix) meant an indicator click got
toggled *twice* — once by Qt, once by the handler — canceling back to
the original state and reading as "nothing happened."

Fixed properly this time: `itemPressed` now snapshots the item's check
state (fires on press, always before Qt's own release-time native
toggle can apply), and `itemClicked` only applies its own toggle when
the state at click time still matches that snapshot — meaning the
click landed somewhere Qt didn't already handle. A click on the
indicator (state already changed by the time `itemClicked` fires) is
left alone; a click anywhere else on the row (state unchanged) gets
toggled once, by this code. Either target now toggles exactly once.

**Verification:** the existing click-toggle test now emits `itemPressed`
before `itemClicked` (matching a real click's actual signal order) and
one new test simulates an indicator click directly — flips the check
state between `itemPressed` and `itemClicked`, as Qt's own native
handling would, and confirms the result isn't reverted. Full suite:
2162 passed, 2 skipped (up from 2161); `black`/`flake8`/`mypy` clean.
