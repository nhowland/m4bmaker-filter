# ADR-0011: Catalog term masking (category- and entry-level)

**Status:** Decided directly by the product owner, implemented and
shipped — same footing as ADR-0008/0009/0010's direct product-owner
decisions. Not in PRD §9.1's data model table; a fork-specific addition
layered on top of it, in the same spirit as ADR-0008/0009's UI-only
additions.
**Related PRD items:** §9.1 (Category/CatalogEntry data model, extended
here), §9.4 (review-screen hit display — masking affects how a hit's
"Canonical configured term" and "Recognized text" render, not whether
they're shown).
**Origin:** first prototyped as display-only logic in the wizard-shell
wireframe's Review step (`docs/design/wizard-shell-wireframe.html`,
ADR-0010) while designing how a redacted example term should look
(asterisk masking: first + last letter, middle asterisked). This ADR is
that concept made real: a User-configurable Catalog setting instead of
wireframe-only demo data.

## Decision

Two new fields, mirroring `enabled`'s existing per-entity-field pattern
rather than a nullable override/inheritance scheme:

- `Category.mask_all_terms: bool = False`
- `CatalogEntry.mask: bool = False`

**They compose by OR, not override.** `CatalogService.is_masked(entry_id)`
returns `category.mask_all_terms or entry.mask` — turning masking on for
a whole category masks every entry in it regardless of each entry's own
flag, and a User can still mask one specific entry in an otherwise-plain
category. Neither flag can turn the other off; there is no way to
"unmask" one entry within an otherwise-masked category. This was chosen
over a nullable-override design (`entry.mask: bool | None`, where `None`
means "inherit from category") because the natural reading of "select
whether a category *or* an individual word can be masked" is additive —
a category-wide switch for bulk convenience, plus an independent
per-word exception on top — not one field silently overriding the other.

**Where the flags live** is deliberately just booleans on the existing
`Category`/`CatalogEntry` dataclasses, not a separate masking-rules
table — masking is a display concern scoped to these two entities, and
the existing JSON persistence (`catalog_store.py`) round-trips new
dataclass fields automatically as long as they carry defaults, so this
needed no persistence-layer changes and no migration: an old
`catalog.json` saved before this field existed loads cleanly, with both
new fields defaulting to `False` (verified in
`test_catalog_store.py::TestLoadCorruptedFile::test_pre_masking_schema_loads_with_mask_defaults`).

## UI

`CatalogWindow` (ADR-0008) gets a "Mask" checkbox column in both tables,
mirroring "Enabled"'s exact pattern — same checkable-not-editable
`QTableWidgetItem` flags, same `itemChanged`-dispatched persist-on-toggle
behavior, no new dialog. The category table gained its first `itemChanged`
handler as part of this (it previously only had `itemSelectionChanged`,
since Name had no inline-editable state before now). A tooltip on each
"Mask" column header explains the OR-composition, since it isn't
obvious from the checkbox alone.

## What was built

- `models.py`: the two new fields, each documented with a pointer to
  this ADR since neither is in PRD §9.1's table.
- `catalog.py`: `create_category`/`create_entry` accept the new fields
  (defaulting to `False`); `update_category`/`update_entry` already
  handled them for free via `dataclasses.replace(**changes)`; new
  `CatalogService.is_masked(entry_id)` resolver.
- `catalog_window.py`: "Mask" columns on both tables, wired exactly like
  "Enabled".
- `docs/design/wizard-shell-wireframe.html`: the Review step's demo
  dataset now carries `Category.maskAllTerms`/`hit.mask` fields mirroring
  the real model instead of a hardcoded `categoryId === "cat-slurs"`
  check, and `displayTerm()` composes them by OR — the same logic the
  real `is_masked()` uses. One demo hit (`h13`, a Profanity-category
  "darn") carries an explicit per-word mask to demonstrate the entry-level
  override case concretely.
- Tests: `TestMasking` in `test_catalog.py` (6 tests — defaults, category-
  only, entry-only, both, neither, updates via `update_category`/
  `update_entry`); 2 new tests in `test_catalog_store.py` (round-trip,
  pre-existing-schema backward compatibility); 5 new tests in
  `test_catalog_window.py` (checkbox defaults and toggling for both
  tables, plus the OR-composition visible through the UI).
- **Visually verified against the live app**: launched it, opened
  Tools → Manage Word Catalog…, confirmed both "Mask" columns render
  correctly in the real dark-themed window.

Full suite: 1409 passing, 2 correctly skipped, `black`/`flake8`/`mypy`
clean (32 pre-existing `window.py` errors unchanged from baseline).

## Not yet decided

Whether masking should also affect anything **besides** review-screen
display — e.g., whether a masked term's canonical phrase should be
redacted in exported reports (PRD §9.4's persisted filter report) or in
logs. Out of scope here; this ADR covers only the Catalog data model and
its own management UI, not every downstream consumer of a term's text.
