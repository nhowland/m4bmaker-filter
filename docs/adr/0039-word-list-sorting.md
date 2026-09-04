# ADR-0039: Click-to-sort on the Word List's entries table

**Status:** Implemented and verified.

## Context

Now that the catalog holds 31+ words in one category (ADR-0038's
expanded seed list plus the User's own additions), the entries table's
fixed insertion order made it hard to tell whether a word — or a close
variant of one — was already present before adding what turns out to be
a duplicate. The User asked for a sort function, specifically clicking
the "Phrase" column header.

## Decision

`QTableWidget.setSortingEnabled(True)` on the entries table — standard
Qt click-to-sort on any column header, toggling ascending/descending on
repeated clicks. Scoped to the entries table only, matching what was
asked; the categories table (a typically much shorter list) is
untouched.

**Population must suspend it.** `QTableWidget` re-sorts on every
`insertRow()`/`setItem()` call while sorting is enabled, which can
scatter a row's own cells (phrase/enabled/mask/notes) across the wrong
table rows mid-populate if left on during `_refresh_entries()`'s
per-row construction loop — the standard, well-known Qt pitfall for
this exact API. `_refresh_entries()` now disables sorting before
clearing/repopulating the table and re-enables it after, on every exit
path (including the early return when no category is selected).

## What changed

- `catalog_window.py`: `setSortingEnabled(True)` on `_entry_table` at
  construction; `_refresh_entries()` wraps its population with
  `setSortingEnabled(False)`/`setSortingEnabled(True)` around the
  existing `blockSignals` pair.

## Verification

**Unit tests** (`test_catalog_window.py`, `TestEntrySorting`, 4 new
tests): sorting is enabled by default; clicking (simulated via
`sortByColumn`) the Phrase column sorts ascending and descending
correctly; a regression guard specifically reproduces the population
pitfall this ADR guards against — sorts the table, then adds a new
entry and refreshes again, and confirms every row's phrase/notes still
belong to the same real catalog entry (would fail if sorting weren't
suspended during population). Full suite 1854 passed, 2 skipped;
`black`/`flake8`/`mypy` clean.
