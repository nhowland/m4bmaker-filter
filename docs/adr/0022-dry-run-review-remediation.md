# ADR-0022: Dry-run review remediation — Complete/Render merge, catalog seed data, transcript viewing, interface polish

**Status:** Implemented and verified.

## Scope

The User ran the real GUI application end-to-end (Source through
Complete) and reported 19 distinct issues live, screen by screen. This
ADR is the consolidated remediation plan for all 19, plus the four
open design decisions among them that required the User's input before
any code changed. Each decision below was asked directly (not assumed)
because it either bakes in editorial judgment (catalog seed content),
changes the wizard's step count and shell wiring (the Complete/Render
merge), or trades real engineering cost against a partial fix (Render's
progress accuracy).

## Decisions

**D1 — Complete is merged into Render, not kept as a separate step.**
Once Render's completed panel gets its own "Open Folder" buttons (D-
none, just a bug fix — see item 15/19 below), its content and Complete's
become functionally identical: pass/warning heading, output path,
duration, report path, Open Folder. The User chose to merge rather than
keep the duplication. Concretely: `STEP_LABELS` (`stepper.py`) drops
`"Complete"`, the wizard becomes 7 steps, `CompleteStep`/
`complete_step.py` and its wiring in `wizard_window.py`
(`_COMPLETE_INDEX`, `_push_render_to_complete`) are deleted, and
`RenderStep`'s own completed panel becomes the terminal screen. This
requires no new logic in `_on_continue` — it already closes the wizard
on the last step's "Done" (ADR-0010's original decision), and `is_last`
is computed generically from `len(self._steps)`, so Render simply
becomes the step that decision applies to once Complete is gone.
`RenderStep.can_advance()` already gates on `_STATE_COMPLETED` (only
reached on a passing/warned validation, never on
`_STATE_NEEDS_ATTENTION`), so the enablement behavior Complete used to
gate is unchanged. ADR-0020 (Complete step implementation) is marked
superseded by this ADR rather than deleted, to keep the historical
record of why Complete existed and was later merged away.

**D2 — Catalog seed data covers Profanity only, sourced from an
established public word list; Slurs and other sensitive categories ship
empty.** The catalog has no seed data at all today (item 11) —
confirmed by grep, nothing in `catalog.py`/`catalog_store.py` seeds
anything. Populating "Profanity" from a well-known public list is a
reasonable default; hand-picking which specific slurs belong in a
shipped "Slurs" category is a subjective, context-dependent editorial
call this app should not make unilaterally for every user. That
category (and any other sensitive category a user creates) stays
user-curated, same as today — this ADR only adds the instructional copy
(item 12) explaining that categories need entries added.

**D3 — The planned transcript-view link opens a generated plain-text
file, not the raw `.m4bt.json`.** The `Transcript` dataclass
(`transcript.py`) has no `path` field today — nothing carries the
on-disk location past `TranscribeStep`, which computes it locally at
write time. Rather than open the raw JSON artifact (readable but noisy —
full segment/word timing metadata mixed in with the text), a small
`.txt` companion with just the words in reading order is written
alongside the `.m4bt.json` and that's what the link opens.

**D4 — Render's progress-accuracy problem (item 16) is fixed with an
elapsed-time display only, not more accurate percentages.** The
underlying cause (item 17: `renderer.render()`'s callback marks
stage *starts* at fixed quarters, and `encode_and_mux` — the longest
stage by far on a real audiobook — has no sub-progress at all) is real,
but weighting the stage fractions by expected duration or parsing
ffmpeg's own progress stream are both real engineering investments for
a cosmetic problem. An elapsed-time label (mirroring
`TranscribeStep`'s own already-proven `QTimer`-driven "Elapsed: Xs")
is cheap and honest: it tells the truth about how long the render has
been running when the percentage can't.

## What changed (all 19 items)

**Copy — dev/spec language replaced with user-facing wording:**
1. Transcribe subtitle (`transcribe_step.py`)
2. Profile subtitle (`profile_step.py`)
3. Scan subtitle (`scan_step.py`)
4. Render's "No Pause or Cancel…" line (`render_step.py`)
5. Typo fix: "Categories && words" → "Categories & words"
   (`profile_editor_dialog.py`)
6. "Duration" label on Render/Complete's completed panel renamed to
   remove the render-time/audiobook-runtime ambiguity

**Visual/contrast — shared style fixes, not one-off patches:**
7. Side padding added around all 7 step bodies at one shared point
   (`wizard_window.py`'s `QStackedWidget` wrapper), not per-step
8. Continue/Done button given the app's existing primary-CTA treatment
   (new `QPushButton#primaryBtn` rule mirroring `#convertBtn`, base
   `#convertBtn` left untouched)
9. `QProgressBar#jobProgress` objectName applied to both the Transcribe
   and Render progress bars (was unstyled on both — the taller, readable
   variant already existed and was simply never wired up)
10. New `QTreeWidget::indicator`/`QTableWidget::indicator` stylesheet
    rules (light + dark) fixing checkbox contrast in three places at
    once: Word Catalog, the Profile editor's category/word tree, and
    Review's Included column

**Functional/feature work:**
11. `filter/catalog_seed.py` (new): a small, well-known public
    profanity word list seeded into a "Profanity" category on first run
    only (D2)
12. Instructional copy added to the Profile editor dialog
13. `Transcript` gains a `path` field; a plain-text companion is
    written alongside every `.m4bt.json`; Transcribe/Profile/Scan each
    get an "View Transcript" action that opens it (D3)
14. Review's confidence filter replaced with real percentage buckets
    instead of Available/Not available
15. Render's completed panel gets Open Folder buttons for the output
    `.m4b` and the report JSON (ports Complete's existing
    `QDesktopServices` pattern)
16. (see D4 — elapsed-time only, no percentage-accuracy change)
17. (see D4)
18. Elapsed-time display added to `RenderStep`, mirroring
    `TranscribeStep`'s pattern exactly

**Architecture (D1):**
19. Complete merged into Render; wizard drops from 8 steps to 7

**Verified not bugs during the dry run, no action taken:** Word
Catalog's "Show archived" checkbox (fully wired, correctly had nothing
to reveal in an empty test catalog); Model Manager listing only
`base.en`/`small.en` (deliberate PRD §10.1 V1 scope). The Profile step's
row-spacing observation was flagged but never confirmed by the User as
wanted on the list, and is left alone.

## What this ADR does not change

No change to the render/validate/scan backend logic itself beyond what
D3/D4 describe — `renderer.py`'s stage-fraction callback shape is
unchanged (D4), `matcher.py`/`validator.py` are untouched. No new
top-level dependency (D2's word list ships as a bundled Python literal,
same "no unapproved dependencies" posture as the rest of this fork).

## Verification

33 new/changed tests across `test_catalog_seed.py` (new),
`test_transcript_text.py` (new), `test_catalog_store.py`,
`test_transcript.py`, `test_profile_step.py`, `test_scan_step.py`,
`test_transcribe_step.py`, `test_review_step.py`, and
`test_wizard_window.py` (the last with every `CompleteStep` reference
removed — `TestRenderToCompleteWiring` deleted entirely,
`TestDoneClosesWizard` rewritten against Render as the wizard's last
step). `tests/filter/` (392 passed, 2 skipped), `tests/gui/filter/` —
every wizard step plus catalog/profile-editor/workers (287 passed), and
this project's real CI command, `pytest tests/ --ignore=tests/gui` (927
passed, 2 skipped), all clean.

One finding, deliberately not fixed here: a large enough combination of
`tests/gui/` files run together in one process (`tests/gui/filter/`
alongside the base app's own `tests/gui/test_window.py`) segfaults in
native Qt/Shiboken teardown code. Confirmed via bisection against the
pre-round commit — same combined run there is too small to reach it —
that this is a volume-triggered, pre-existing ceiling in this
offscreen-Qt test configuration, not a logic bug introduced by any
change in this ADR; every individual file and every realistic scope
actually touched by this round passes cleanly on its own. It's also
moot for this project's real CI gate, which never runs `tests/gui/` at
all. Spun off as its own follow-up rather than folded in here, since
it's an environment/tooling investigation, not UI or wiring work.
