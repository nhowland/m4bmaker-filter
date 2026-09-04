# ADR-0043: Word List — move a word between categories

**Status:** Implemented and verified.

## Context

The User asked for the ability to move a selected word to a different
category from the Word List window (`CatalogWindow`). Previously the only
way to change a word's category was delete-and-recreate, losing its
`enabled`/`mask`/`notes` state and its `id` (breaking any profile that
referenced it by `entry_id`).

## Decision

`CatalogService.update_entry(entry_id, **changes)` already accepts an
arbitrary field change and is already used for `enabled`/`mask`/`notes`
edits in this same window — `category_id` is just one more field it
accepts unmodified. No service-layer change was needed at all; this is
a GUI-only addition.

New "Move to Category…" button next to the existing "Delete" button
below the entries table. Clicking it, with a word selected:

1. Builds the list of *other*, *non-archived* categories as move
   targets — archived categories are excluded for the same reason
   `_add_entry()` never offers one as a destination for a new word:
   moving an active word into one would silently drop it out of any
   live filtering.
2. If there are none, shows an info dialog rather than a picker with
   nothing useful in it.
3. Otherwise shows a `QInputDialog.getItem` picker (the same dialog
   pattern already used for "Rename Category"), naming the word being
   moved.
4. Checks for a duplicate in the target category via the same
   `find_duplicate_entry` call and outright-reject behavior "+ Word"
   already uses (ADR-0040) — a move that would create a duplicate is
   blocked with the same style of status message, not silently allowed.
5. Calls `update_entry(entry_id, category_id=target.id)`, saves, and
   refreshes — the word disappears from the currently-viewed (source)
   category's list, which is the correct, expected result of having
   just moved out of it.

The button carries no enabled/disabled state tied to selection, matching
the existing "Delete" button's own behavior in this same row — both are
always clickable and silently no-op if nothing is selected.

## Verification

**Unit tests** (`test_catalog_window.py`, `TestMoveEntry`, 7 new): no
selection is a no-op (dialog never invoked); no other categories shows
an info dialog and changes nothing; a real move updates the service and
the status label; cancelling the picker changes nothing; an archived
category is excluded from the offered list (while a second real,
non-archived category confirms the "no targets" path isn't what's
actually being exercised); moving into a category with an existing
duplicate is rejected, not moved; the entry disappears from the
source category's own table view after a successful move.

One real bug found and fixed while writing these tests, not shipped:
the first version of the archived-category-exclusion test only had one
active category (the entry's own) plus the archived one, so
`other_categories` came out empty and the code took the "nothing to
move to" branch instead — calling `QMessageBox.information`, which
wasn't mocked in that specific test and blocked indefinitely under the
offscreen Qt test platform. Fixed by adding a second real category to
the fixture so the intended code path (the offered-names list itself)
is what's actually under test.

`black`/`flake8`/`mypy` clean. `tests/gui/filter/test_catalog_window.py`
(39 passed, 7 new), full `tests/gui/filter/` (373 passed).

## Addendum: default entries-table sort was descending, not ascending

Real use surfaced a second, unrelated defect in the same table: opening
the Word List showed the Phrase column sorted Z-A by default, not the
A-Z a User expects on first open. `setSortingEnabled(True)` alone (the
only sort-related call before this fix) leaves Qt's own default sort
indicator on a freshly-sortable header — which is descending, not
ascending — so nothing in this code had ever actually chosen an order;
it was Qt's default winning by accident.

Fixed with one explicit `sortByColumn(_COL_ENTRY_PHRASE, Qt.SortOrder.
AscendingOrder)` call right after `setSortingEnabled(True)`, at
construction time only (not per-refresh) — a later click on any header
still freely controls the order from then on, same as before.

**Verification:** new regression test
(`test_default_sort_is_ascending_not_descending`) populates three
out-of-order entries and asserts the table's own row order without ever
calling `sortByColumn` itself, so it actually exercises the default
rather than a test-imposed one. `tests/gui/filter/test_catalog_window.py`
now 40 passed (1 more); full `tests/gui/filter/` 374 passed;
`black`/`flake8`/`mypy` clean.
