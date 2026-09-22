# ADR-0036: Word Variation Scanner — catalog-curation aid for missed variations

**Status:** Implemented and verified.

## Context

The User noticed words still getting through a filtered audiobook,
largely because whisper.cpp's own tokenization doesn't line up with a
catalog entry — a target word transcribed as a different surface form
(e.g. "shuck" heard as "shucking") or split across two adjacent tokens
("shuck" as "sh" + "uck"). This had already come up earlier in the
project (the "shuck"/"shucking"/"sh uck" example is the User's own
real-world one), where the explicit decision was to leave the Matcher
exact-match-only and instead grow the catalog by hand. This ADR is a
tool to make that hand-curation faster and more complete, not a
reopening of that decision.

Design was worked through with the User step by step: what kinds of
variation to detect (stemmed forms, whisper tokenization splits — not
generic fuzzy/edit-distance matching, too noisy for a moderation
catalog), where the feature should live (Profile step, since it already
has the real transcript and the shared `CatalogService` instance Word
List itself edits — Word List has no transcript in scope at all), the
UI flow (a table with a real context snippet per suggestion so an
addition is never blind), and finally the "Why" column's tooltip
wording (short jargon-free labels, fuller explanation on hover).

## Decision

**New `filter/variation_scan.py`** — pure detection logic, no Qt:
`find_word_variations(transcript, snapshot)` returns
`WordVariationSuggestion`s for two kinds of gap:

- **Stem match**: a transcript word that is a catalog entry's
  normalized form plus one of a small explicit suffix set (`-ing`,
  `-ed`, `-er`, `-ers`, `-es`, `-s`, with the common trailing-e-drop
  handled). A small, auditable whitelist rather than a general stemmer
  (e.g. Porter) — for a moderation catalog, predictable non-matches are
  worth more than broader recall; consonant doubling ("run" ->
  "running") is a known, deliberate gap, not an oversight.
- **Split token**: two adjacent transcript words, within the Matcher's
  own existing `MAX_PHRASE_GAP_MS` gap, whose concatenated normalized
  forms exactly equal a catalog entry. Adding a suggestion like this
  becomes an ordinary two-word catalog phrase — the Matcher's own
  already-existing phrase/gap logic catches it on the next real scan,
  no special case needed there at all.

Both checks skip anything the real Matcher (`matcher.scan_transcript`)
already catches — run once internally as a probe — so a suggestion is
always a genuine gap, never noise duplicating an existing hit. Only
single-word catalog entries are checked; a multi-word entry has no
meaningful stem or split-token relationship to test. Suggestions are
deduplicated per surface form across the whole transcript, carrying an
occurrence count and one representative context snippet, sorted by
count descending — a variation seen many times is stronger real signal
than a one-off, the exact prioritization discussed when this was
scoped.

**New `gui/filter/word_variation_dialog.py`** (`WordVariationDialog`) —
a modal opened fresh each time (unlike `CatalogWindow`/
`ModelManagerWindow`'s lazy-reuse pattern; nothing here needs to
persist between opens). A table: suggested word, related catalog entry,
a short "Why" label with the fuller explanation as a tooltip (the
User's own requested addition — jargon like "stem"/"token" stays out of
the visible label), occurrence count, a real context snippet, and a
per-row "+ Add" button that persists immediately (same "no batch
submit" convention `CatalogWindow` already uses) into the *same
category* as the related entry, then flips to "✓ Added".

**Wired into `ProfileStep`** as a new "Find More Words…" button next to
"View Transcript" — visible once a transcript exists, enabled once a
profile is selected (the scan runs against that profile's own entries
specifically, via `CatalogService.create_snapshot`, the same call the
real Scan step uses). Closing the dialog unconditionally refreshes
Profile's own list, since additions persist live rather than on an
accept/reject dialog result.

## A real test-infrastructure bug found along the way

The full suite segfaulted, deterministically, at the exact signature
ADR-0023 already diagnosed and fixed (`test_window.py`'s `win` fixture,
`QCoreApplication.sendPostedEvents(None, DeferredDelete)`) — but
`gc.disable()`, ADR-0023's fix, didn't prevent it this time. Bisecting
by excluding test files identified `test_word_variation_dialog.py`
specifically as the trigger. The mechanism is different from ADR-0023's
own: every "+Add" click replaces a table cell's `QPushButton` via
`setCellWidget`, which schedules the old widget for `deleteLater()` —
real Qt event-queue growth, not CPython cyclic garbage, so
`gc.disable()` doesn't touch it. Left unflushed across several
add-button tests in the same process, the backlog was large enough to
reproduce the same native-teardown reentrancy crash through a different
channel. Fixed by flushing immediately after each such click — the
exact `processEvents()` / `sendPostedEvents(None, DeferredDelete)` /
`processEvents()` sequence `test_window.py`'s own fixture already uses
— rather than touching production code, since the widget-replacement
itself is ordinary, correct Qt usage; only the test process's own
unflushed accumulation was the problem.

## Verification

**Unit tests** (`test_variation_scan.py`, 16 tests): stem-match
detection (gerund/past/agent/plural forms, e-drop, wrong-tail
rejection, multi-word entries excluded), split-token detection (within
gap, beyond gap, non-matching pair), already-covered exclusion (an
exact hit, and — the more meaningful case — a word covered by a
*different* entry's phrase hit), occurrence counting/dedup, sort order,
context-snippet content, empty-input handling.

**Dialog tests** (`test_word_variation_dialog.py`, 6 tests): table
population, tooltip content matches the shown label, add persists a
real catalog entry into the *related entry's* category and calls
`save_catalog`, the row flips to a confirmation label, the empty state
shows when nothing is found.

**ProfileStep wiring tests** (`test_profile_step.py`, 7 new tests in
`TestFindMoreWords`): visibility gated on transcript, enabled state
gated on profile selection (both independently), the dialog receives
the real service/transcript/snapshot and the snapshot really reflects
the selected profile's entries, both no-transcript and no-profile cases
are no-ops.

29 new tests total; full suite 1840 passed, 2 skipped, stable across
repeated runs (previously segfaulted); `black`/`flake8`/`mypy` clean on
every new/changed file.
