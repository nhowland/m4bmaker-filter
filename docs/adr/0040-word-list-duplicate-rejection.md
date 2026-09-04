# ADR-0040: Word List rejects duplicate words outright

**Status:** Implemented and verified.

## Context

`CatalogService.create_entry()` deliberately allows duplicates (PRD
§9.2: "warn about duplicates," not reject them — the caller decides
what to do with the warning), and `CatalogWindow`'s manual "+ Word" add
flow used that latitude to add the word anyway, just noting the
duplicate in the status label afterward. With the catalog now holding
dozens of words (ADR-0038's expanded seed plus real additions), the
User asked for this specific flow to actually prevent duplicates
outright rather than warn-and-add.

## Decision

`CatalogWindow._add_entry()` now checks `find_duplicate_entry()`
*before* creating anything — the same normalized-phrase matching the
real Matcher itself uses (so "Shit" and "shit" are correctly treated as
the same word), scoped to the currently-selected category exactly like
`find_duplicate_entry()` already does (the same word can legitimately
exist in more than one category with different intent — unchanged).
If a duplicate is found, nothing is created; the status label says so,
and the typed text stays in the input field rather than being cleared,
so the User can see exactly what triggered the rejection and adjust it
if they meant something slightly different.

`CatalogService.create_entry()` itself is untouched — still allows
duplicates when called directly, matching the PRD's own stated
contract. This is a UI-level policy change in the one flow the User
named (the Word List's manual add), not a service-level behavior
change that would also affect other callers (`WordVariationDialog`'s
"+ Add", `catalog_seed.py`'s first-run seed) — those already avoid
creating true duplicates by construction (the variation scanner
excludes anything the real Matcher would already catch; the seed list
has no repeated words), so they don't need this same guard.

## What changed

- `catalog_window.py`: `_add_entry()` checks `find_duplicate_entry()`
  first and returns early with a status message on a match, instead of
  creating the entry and only noting the duplicate afterward.
- `word_variation_dialog.py` (follow-up, same day): the User asked
  whether "Find More Words" had the same gap — it did, and worse: its
  own suggestions are only checked against the *selected profile's*
  entries (`variation_scan.py`'s documented scope), so a word already
  in the catalog under the same category, just not yet part of this
  profile, wasn't excluded by that scan-coverage check at all.
  `_populate_table()` now checks `find_duplicate_entry()` per
  suggestion and shows a plain "Already in catalog" label instead of an
  active "+ Add" button when one exists — decided at population time
  rather than reactively on click, since this dialog is a batch-review
  table the User scans through, where seeing which suggestions are
  already handled at a glance is more useful than discovering it one
  dead click at a time. The Action column's fixed width (ADR-0036) grew
  to accommodate this new, longer label.

## Verification

**Unit tests** (`test_catalog_window.py`): a duplicate add is rejected
(entry count stays at 1, not 2); case-insensitive matching ("DARN" vs.
"darn"); the typed text remains in the input field after rejection; the
same word in a *different* category is correctly not treated as a
duplicate (preserves `find_duplicate_entry()`'s existing per-category
scoping). One pre-existing test updated to match the new behavior (was
asserting duplicates get added anyway with a warning). Full suite 1857
passed, 2 skipped; `black`/`flake8`/`mypy` clean.

**Follow-up unit tests** (`test_word_variation_dialog.py`, 3 new
tests): a suggestion whose surface text already exists in the catalog
under the same category shows "Already in catalog" instead of an
active button; nothing gets created and `save_catalog` isn't called for
that row; the same word existing in a *different* category still shows
a normal, active "+ Add" button (preserves the same per-category
scoping as the primary fix). Full suite 1860 passed, 2 skipped, stable
across repeated runs (this dialog's tests previously caused a real
segfault under ADR-0036 — re-verified clean); `black`/`flake8`/`mypy`
clean.
