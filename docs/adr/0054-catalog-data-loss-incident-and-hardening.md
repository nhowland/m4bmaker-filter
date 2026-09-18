# ADR-0054: Catalog data-loss incident and hardening

**Status:** Implemented and verified.
**Related:** ADR-0022 (first-run catalog seeding), ADR-0038 (expanded
seed list), ADR-0053 (the work in progress when this happened)

## What happened

During real-app verification of ADR-0053's Transcript tab, the
Contributor's real, hand-curated catalog — two profiles ("Family
Friendly", "PG-13"), built up over weeks of manually reviewing real
transcripts and adding real missed words — was silently replaced with
an empty default. Nothing crashed, nothing logged anywhere the
Contributor could see, and by the time it was noticed there was no way
to tell from the app itself that anything had happened at all.

**Root cause, confirmed directly, not inferred:** a new test in this
same working session —
`TestAddToCatalogDialog::test_creates_entry_in_the_chosen_category_and_accepts`
(`tests/gui/filter/wizard/test_review_step.py`) — called
`_AddToCatalogDialog._on_add()` directly against a bare, in-memory
`CatalogService`. `_on_add()` calls the real `save_catalog(self._catalog)`
with no path argument, which — correctly, for production use — defaults
to the real per-user `catalog.json`. The test never patched
`save_catalog`, unlike every other test in this codebase that exercises
a catalog-mutating dialog
(`test_word_variation_dialog.py`, `test_profile_step.py`,
`test_profile_editor_dialog.py`, `test_catalog_window.py` all patch it
at their own module's import site — this test simply didn't follow
that established convention). Every time the test suite ran this
session, it quietly overwrote the real catalog with a throwaway
`"Mild"`/`"darnit"` test fixture.

No backup existed anywhere: no Time Machine, no APFS local snapshot,
and `catalog_store.save_catalog()`'s atomic write leaves no previous-
version trace by design. The two real profiles and everything in them
are not recoverable from disk. A partial reconstruction from data
surfaced in earlier, unrelated validation work in this same
conversation is the best available recovery (see FORK.md).

## Why "just fix that one test" isn't the whole fix

The immediate cause was one missing `patch()` call. But the Contributor's
own framing is the more important point: **the catalog is not disposable
test fixture data** — it accumulates real, human review effort across
every transcript ever manually checked, and nothing about
`catalog_store.py`'s actual behavior treats it that way. Two other real,
independent problems exist regardless of how *this* incident happened,
and would have caused the identical class of silent loss on their own:

1. `load_catalog()`'s "file exists but can't be parsed" branch returns
   a fresh, un-seeded service with nothing but a log line — the next
   `save_catalog()` call (from completely ordinary use: adding one
   word) permanently overwrites the original with no backup and no
   warning. This is a real, reachable path with no test bug required —
   any genuine on-disk corruption hits it exactly the same way.
2. The convention that protects the real file in tests — "remember to
   patch `save_catalog` at its import site" — depends entirely on every
   test file's author remembering it, forever. That's exactly the kind
   of guarantee that fails silently, once, at the worst possible time —
   which is what happened here.

## Decision: three independent layers, not one patch

**1. A test suite that cannot reach the real directory, structurally —
not by convention.** `tests/conftest.py` gained a session-wide autouse
fixture, `_isolated_filter_data_root`, that patches
`m4bmaker.filter.storage.user_data_dir`/`user_cache_dir` — the one
place `data_root()`/`cache_root()` import them — to a per-test `tmp_path`
for *every* test in the suite, unconditionally. `models_dir()`/
`transcripts_dir()`/`temp_root()` all fall back to a `data_root()`
subdirectory when unconfigured, so this one patch covers the catalog,
transcripts, models, and the job database, not just the specific file
that got hit this time. The existing per-file `patch("...save_catalog")`
convention stays — it verifies real *behavior* (was save called, with
what), which this doesn't — but the real directory is now unreachable
even if a test forgets it entirely, as one just did.

Two tests (`TestDefaultPathIsAlwaysIsolatedInTests`,
`tests/filter/test_catalog_store.py`) call `load_catalog()`/
`save_catalog()` with no path and *no local patch at all*, on purpose,
proving the global fixture alone is sufficient — the same shape of
mistake that caused this incident, deliberately reproduced against the
fix to confirm it's actually closed.

**2. `load_catalog()` never silently discards unreadable bytes again.**
When the file exists but fails to parse, its current bytes are now
copied to a timestamped `<name>.unreadable-<UTC timestamp>.bak` sibling
*before* the fallback service is returned — the original is also left
in place untouched, not moved. A later `save_catalog()` can still
overwrite the original (that's a separate, ordinary write), but a
recoverable copy now always exists somewhere on disk regardless of what
happens afterward. This alone would not have prevented this specific
incident (the real file parsed fine — the bug was writing over it with
different content entirely, not a corrupted read), but it closes the
other, independent hazard described above.

**3. The GUI tells the Contributor when this happens.** `load_catalog()`
gained an optional `on_recovery: Callable[[Path], None] | None`
parameter, called with the backup path in the one case it's ever
invoked. `gui/window.py`'s `_show_catalog_window()` passes
`self._warn_catalog_recovered`, which shows a real `QMessageBox.warning`
naming the backup location — turning a silent reset into something a
Contributor actually sees, the moment it happens, not weeks later by
accident. Existing/non-GUI callers are unaffected by leaving the new
parameter at its default.

## What this does not fix

Nothing here recovers the two lost profiles — there was no backup
mechanism in place *before* this ADR, so there is nothing for the new
one to have protected. The reconstruction in FORK.md is a best-effort
partial rebuild from incidental data, not a restoration.

Also open, not addressed here: `gui/prefs.py` has its own, separate
`platformdirs.user_config_dir()` call with its own already-existing
per-file test isolation (`tests/gui/test_prefs.py`'s `isolated_prefs`
fixture) — out of scope for this ADR, which is specifically about the
catalog and the shared `filter/storage.py` root, but worth the same
scrutiny if a similar incident ever touches it.

## Verification

**Unit tests**: 9 new in `tests/filter/test_catalog_store.py` (backup
creation and content, original file left in place, no backup on a
healthy load or first run, `on_recovery` called only in the one real
case, two tests with zero local patching proving the global fixture
alone suffices); 2 new in `tests/gui/test_window.py`
(`_warn_catalog_recovered`'s message contains the real backup path,
`_show_catalog_window` wires the callback correctly). Full suite: 2099
passed, 2 skipped (up from 2088 before this ADR) — zero regressions.
`black`/`flake8`/`mypy` clean on every file touched.

**Real verification, not just the test suite**: the real
`catalog.json`'s mtime/size were snapshotted before and after re-running
the full suite with the fix in place — unchanged, confirming the global
isolation fixture actually holds against the exact class of mistake
that caused this incident, not merely asserted to by the tests
themselves.
