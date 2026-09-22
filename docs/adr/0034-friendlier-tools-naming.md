# ADR-0034: Friendlier Tools menu naming, and a Model Manager entry point from Transcript

**Status:** Implemented and verified.

## Context

The User asked for more user-friendly names for the three Tools-menu
entries — "Filter Audiobook", "Manage Word Catalog", "Manage Models" —
flagging that jargon like "catalog" and "models" doesn't read naturally
to someone not already familiar with this fork's own internal vocabulary.
Separately, the Transcript step already lets a User inline-download a
model while picking one, but had no way to open the full Model Manager
(rename/remove/inspect provenance) without leaving the wizard — the
Profile step already has exactly this kind of escape hatch to its own
Catalog window, so Transcript lacked the equivalent.

## Decision

**Renamed** (`MainWindow`'s Tools menu):
- "Filter Audiobook…" → **"Filter for Language…"** — names what gets
  filtered, not just that filtering happens.
- "Manage Word Catalog…" → **"Word List…"** — drops "catalog" for the
  plainer term a User would actually use.
- "Manage Models…" → **"Manage Transcription Models…"** — "models" alone
  is unexplained jargon; tying it to "Transcription" gives it a real
  referent.

The Profile step's own "Manage Word Catalog…" button (which opens the
same `CatalogWindow`) got the matching "Word List…" rename for
consistency between the two entry points to the same window.

**Added a "Manage Transcription Models…" button to the Transcript step's
model-choosing panel**, opening `ModelManagerWindow` directly — the same
lazy-create-and-reuse pattern Profile's Catalog button and `MainWindow`'s
own Tools-menu entries already use. `ModelManagerWindow` gained a
`closed` signal (mirroring `CatalogWindow.closed` exactly, including the
same "hide, don't destroy" semantics) so Transcript can re-render its
model list and re-derive `can_advance()` after the window closes — a
download or removal made there needs to be reflected immediately, not
just after the User leaves and re-enters the step, since `can_advance()`
itself depends on `is_installed()`.

## What changed

- `window.py`: three `QAction` label renames.
- `profile_step.py`: one `QPushButton` label rename.
- `model_manager_window.py`: `closed = Signal()`, emitted at the end of
  `closeEvent()` (after the existing in-progress-download confirmation,
  so a declined close correctly does not emit).
- `transcript_step.py`: new "Manage Transcription Models…" button in
  `_build_choose_panel()`, `_on_manage_models()`/
  `_on_model_manager_closed()` handlers, lazily-created/reused
  `ModelManagerWindow` instance.

## Verification

**Unit tests**: `ModelManagerWindow` — closing emits `closed`; a declined
close during an active download does not. `TranscriptStep` — the button
opens the window with the step's own `dest_dir`; a second call reuses the
same instance rather than creating another; closing the window after a
model gets installed elsewhere flips `can_advance()` from `False` to
`True` without any other action. 5 new tests; full suite 1766 passed, 2
skipped; `black`/`flake8`/`mypy` clean on every changed file (pre-existing,
unrelated issues in `window.py` and `test_transcript_step.py`, both at
line numbers far from anything touched here, confirmed unchanged against
the pre-edit baseline via `git stash`).
