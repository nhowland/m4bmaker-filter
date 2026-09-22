# ADR-0041: Profile Editor word list always sorted alphabetically

**Status:** Implemented and verified.

## Context

The Edit Profile dialog's word tree showed each category's entries in
catalog insertion order, not alphabetically — with 31+ words in the
"Profanity" category (ADR-0038's expanded seed plus real additions),
finding a specific word to check meant scanning the whole list. The
User asked for automatic alphabetical display, explicitly not a manual
sort control — the Word List window already got click-to-sort
(ADR-0039), but this dialog's tree has no column headers to click in
the first place, and always-sorted is simpler than adding one.

## Decision

`_populate_tree()` sorts each category's visible entries by
`canonical_phrase.lower()` before building tree items — case-
insensitive, so "Zebra" and "apple" order the way a person expects
rather than by raw byte value (which would put every capital letter
before every lowercase one). Categories themselves are unchanged
(catalog order) — the User's request was specifically about words.

## What changed

- `profile_editor_dialog.py`: `_populate_tree()`'s existing archived-
  filter list comprehension became a `sorted(...)` call over the same
  filter, keyed on lowercased `canonical_phrase`.

## Verification

**Unit tests** (`test_profile_editor_dialog.py`, `TestWordsSortedAlphabetically`,
2 new tests): entries inserted in deliberately non-alphabetical order
display alphabetically; sorting is case-insensitive ("apple", "Mango",
"Zebra" in that order despite mixed case). All pre-existing tests
continue to pass unmodified. Full suite 1862 passed, 2 skipped;
`black`/`flake8`/`mypy` clean (2 pre-existing, unrelated issues in this
test file, unchanged from before this ADR).
