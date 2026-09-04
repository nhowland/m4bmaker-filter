# ADR-0035: Settings window — storage-location overrides and transcription defaults

**Status:** Implemented and verified.

## Context

Following the Tools-menu naming pass (ADR-0034), the User asked whether
Word List and Model Manager should really live inside a single
"Settings" window rather than as two separate ones. Working through it:
those two are full data-management workspaces (tables, CRUD, live
download progress) that people leave open while actively working, not
small values set once and left alone — the wrong shape for a
Preferences-style window (macOS HIG: `Cmd+,` is for lightweight
scalars, not busy content editors). The better move was to keep them
separate and instead add a genuine, small Settings window for actual
low-frequency configuration, with the User naming the first two real
candidates: a folder-defaults setting for transcription-related storage,
and (once discussed further) a default output folder for filtered
audiobooks.

The User was explicit that dark mode / check-for-updates — the base
app's own two existing preferences, in ``gui/prefs.py`` — should stay
separately owned, not folded into this new window. That directly shaped
where the new settings had to live: not in ``gui/prefs.py`` (shared,
base-app-owned), but in a new module scoped entirely to this feature.

## Decision

**New `filter/settings.py`**, deliberately the same small
load/save/get/set-with-merged-defaults shape as `gui/prefs.py`, but a
separate file living under this feature's own `storage.data_root()` —
consistent with `storage.py`'s own already-stated boundary between
durable feature data and lightweight scalar preferences, and keeping the
User's requested separation from the base app's preferences real at the
storage layer, not just the UI layer.

**Four settings for v1** (explicitly a starting set, not a closed list —
the User's own framing for this window from the start):
- `models_dir` / `transcripts_dir` — override `storage.py`'s
  `models_dir()`/`transcripts_dir()`, which now check the setting before
  falling back to their existing fixed default. `settings.py` importing
  `storage.data_root()` and `storage.py` needing `settings.get()` is a
  natural two-way dependency; broken by a local (function-body) import
  inside `models_dir()`/`transcripts_dir()`/`output_dir_override()`
  rather than restructuring either module.
- `output_dir` — the User's own follow-up addition once storage
  locations were on the table: `renderer.default_output_path()` now
  checks `storage.output_dir_override()` before its existing "same
  folder as source" fallback. Filename pattern (`<name> (filtered).m4b`)
  is unchanged either way; only which folder it lands in changes.
- `preferred_model` — `TranscriptStep._pick_default_model()` now prefers
  this setting's model when it's actually installed, falling through to
  the pre-existing "first installed, else the catalog's first entry"
  logic unchanged when the preference is unset or not yet downloaded (an
  uninstalled preference isn't a usable default).

**New `gui/filter/settings_window.py`** (`SettingsWindow`), added to
`MainWindow`'s Tools menu as "Settings…" via the same
lazy-create-and-reuse / `closed` signal / `apply_stylesheet` pattern
`CatalogWindow`/`ModelManagerWindow` already use. Two sections: Storage
Locations (three folder rows — current effective path, Browse…, Reset to
Default, the latter only enabled when an override is actually set) and
Transcription Defaults (a Preferred Model dropdown, "No preference" plus
each catalog entry). Every field persists immediately on change, the
same "no separate Save button" choice `CatalogWindow`/`gui/prefs.py`
both already make.

## What this ADR does not do

Does not touch `gui/prefs.py`, `dark_mode`, or `check_for_updates` — the
User's own explicit instruction. Does not merge Word List or Model
Manager into this window — the whole point of the discussion that led
here was keeping those separate.

## Verification

**Unit tests**: `settings.py` — load/save/get/set round-trips, corrupt-
JSON and missing-file fallback to defaults, one key's `set()` doesn't
disturb another (`test_settings.py`, 12 tests). `storage.py` — each
override function returns the setting when present and the original
fixed default when absent, mocked at the `settings.get()` boundary so a
real override on the machine running the tests can't make them flaky
(`test_storage.py`, 6 tests). `renderer.default_output_path()` — no-
override behavior explicitly pinned against a mocked
`output_dir_override()` for the same flakiness reason, plus the new
override-present case (`test_renderer.py`, 3 tests). `TranscriptStep` —
an installed preference wins, an uninstalled one falls back correctly,
no preference behaves exactly as before this ADR (`test_transcript_step.py`,
4 tests). `SettingsWindow` — every folder row's Browse/Reset/default-
display behavior, a cancelled Browse leaves nothing changed, the model
combo's default/preselect/persist/clear behavior, `closed` fires on
close (`test_settings_window.py`, 19 tests). 41 new tests total; full
suite 1809 passed, 2 skipped; `black`/`flake8`/`mypy` clean on every
changed/new file (pre-existing, unrelated issues elsewhere confirmed
unchanged against the pre-edit baseline via `git stash`, same discipline
as every prior ADR this session).
