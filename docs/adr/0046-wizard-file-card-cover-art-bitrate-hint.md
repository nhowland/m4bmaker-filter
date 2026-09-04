# ADR-0046: Persistent file card, Source step cover art, Render step bitrate hint

**Status:** Implemented and verified.

## Context

Four User-requested UI changes, mocked up and iterated on together before
any code (see the published mockup artifact from that discussion):

1. Keep the loaded book's filename (and metadata) visible across every
   wizard step, not just Source.
2. Show real cover art on the Source step, and make the eligibility
   checkmark more prominent and friendlier ("Eligible for filtering").
3. Note on the Render step when the auto-selected bitrate matches the
   source file's own bitrate.
4. Clarify the "Est. storage needed" label — the underlying number
   already blends temporary working files (freed after rendering) with
   the final output file (kept) into one peak figure; the label said
   neither.

Option B ("compact card with thumbnail") was chosen for the persistent
card from three weighted mockups.

## Decision

### Cover art infrastructure

`m4bmaker.cover.extract_cover_from_audio()` already existed (used at
render time to embed cover art in the output) but nothing used it for
*preview* purposes. New `CoverArtWorker(QThread)` in `workers.py` wraps
it off the UI thread, mirroring `MediaInspectWorker`'s own shape
exactly — except it has no `error` signal at all: a missing ffmpeg or a
failed extraction here is a preview inconvenience, not a recoverable
error worth surfacing (the wizard's other steps already report a
missing ffmpeg loudly where it actually matters, at render time).
`result_ready` always fires, with `None` on any failure, so a caller can
tell "still working" from "no art to show."

`SourceStep` kicks this worker off right after a successful inspection
(`_on_inspect_finished`), independent of the manifest's `eligible`
value, and exposes the result via a new `cover_path` property and a new
`cover_ready` signal (fires on every change, including to `None`, so a
listener always learns "extraction finished" rather than staying stuck
on a stale value from a previously-loaded file).

**Race condition, found and fixed before it shipped:** if the User picks
a second file while the first file's `CoverArtWorker` is still running,
that worker isn't cancelled (nothing to cancel a `QThread` mid-flight
here) and could otherwise apply its late result over the newer
selection's own state. Fixed with the same default-argument lambda-
capture pattern this package already uses elsewhere (e.g.
`word_variation_dialog.py`'s "+ Add" hookup): each worker's own instance
is captured at connection time and compared against `self._cover_worker`
when its result arrives, so a superseded worker's late result is
silently ignored rather than applied. Caught by a dedicated regression
test that actually completes two workers in sequence and confirms the
first (stale) one's result is discarded — an earlier version of this
test looked right but didn't actually exercise the guard, since it never
got far enough to create a second worker; fixed before it shipped.

### Persistent file card

New `m4bmaker/gui/filter/wizard/file_card.py`: `FileCard(QWidget)`, a
one-line strip (thumbnail, filename, duration + bitrate, an eligibility
badge) inserted into `WizardWindow` directly below the stepper. Hidden
entirely until a manifest exists — nothing to show before that, and an
empty card would just be chrome. Refreshed on every `_render()` call
(every step transition) by reading `SourceStep.manifest`/`cover_path`
directly — no independent state of its own, no new source of truth.
Also refreshed live via the new `cover_ready` signal, so the card's
thumbnail updates the moment extraction finishes even without a
navigation event.

`set_cover_pixmap()` (shared between the card and the Source step's own
panel, so both render a cover exactly the same way) scales and center-
crops to a square — real cover art is rarely already square — falling
back to plain text when there's no real image, the same
`QLabel(str(path))`-or-fallback-text shape `gui/widgets.py`'s own
existing cover-art widget already uses.

### Source step

`_EligiblePanel` gained a cover thumbnail (108px) to the left of its
existing info rows, and its "✓ Eligible" heading became a proper pill —
new `#eligibleBadge` QSS rule (both light and dark palettes, reusing the
existing "done"-state green / terracotta-adjacent red already
established for the stepper's own step badges, not new colors) reading
"✓ Eligible for filtering" / "✕ Not eligible." The redundant "Cover art:
Present/Not present" text row was removed — the thumbnail already shows
this directly (falling back to visible placeholder text when there's no
real art), so keeping both said the same thing twice.

### Render step: bitrate hint

New `_auto_bitrate` field, separate from `_bitrate` (which `_on_start()`
overwrites with whatever the combo currently shows) — the hint compares
the combo's live value against this fixed record of what
`pick_default_bitrate()` actually chose, connected to
`currentTextChanged` so it appears/disappears live as the User changes
the selection, not just at panel-build time.

**Real bug caught by writing the test for this, not shipped:** the hint
would have shown even when the source's bitrate was unknown and
`pick_default_bitrate()` fell back to its own hardcoded
`DEFAULT_BITRATE` — that's a guess, not a match to anything real, and
the hint text ("Matches your source file's bitrate") would have been
false. Fixed by only recording `_auto_bitrate` when the source's own
bitrate was actually known.

### Both steps: storage label

"Est. storage needed" → "Temp storage needed" (User's own chosen
wording) plus an explanation of what the number actually includes —
tooltip on the Source step's fixed-width row (matching the tooltip
pattern already used for Review's "Attenuated total," ADR-0045), a
direct inline sentence on the Render step's own free-form info line
since it has the room: "Temp storage needed: ~640 MB (temporary working
files + final output)."

## What this does not fix

**Test-isolation bug found, not fixed here** (flagged as a follow-up
task instead): `test_source_step.py`'s existing tests don't override
`find_compatible_transcript()`'s transcripts directory, so most tests in
that file were already scanning and JSON-parsing the real, ever-growing
user transcripts directory (321MB / ~20 real transcripts accumulated
over this project's own real-book testing) — a pre-existing gap, not
introduced here, just increasingly noticeable (~1.5-5s/test instead of
milliseconds) as that directory has grown. Left for a dedicated fix
since correcting it safely needs care not to break the two tests that
already deliberately mock that function with specific return values.

## Verification

**New test files/classes**: `test_file_card.py` (11 tests — visibility,
content formatting, badge state, `set_cover_pixmap`'s three paths: no
path, nonexistent path, and a real decodable image actually scaled to
the requested size); `TestCoverArtWorker` in `test_workers.py` (4 tests
— success, missing ffmpeg, no art found, unexpected exception, all
resolving to `None` rather than an error signal); `TestCoverArt` in
`test_source_step.py` (6 tests, including the stale-worker race-
condition guard); `TestBitrateHint` and `TestStorageLabel` in
`test_render_step.py` (5 tests, including the unknown-source-bitrate
false-hint guard).

**Existing tests updated, not broken**: two direct
`_on_inspect_finished()` call sites in `test_wizard_window.py`, and the
shared `_select_and_finish()` helper in `test_source_step.py`, now patch
`CoverArtWorker` the same way they already patched `MediaInspectWorker`
— without this, dozens of existing tests would each spin up a real
`QThread` (and, wherever ffmpeg is on `PATH`, a real subprocess) against
fake test paths.

`black`/`flake8`/`mypy` clean on every file touched. Full
`tests/gui/filter/` (408 passed) and the project's real CI command,
`pytest tests/ --ignore=tests/gui` (1011 passed, 2 skipped), both clean.
App confirmed to start without error; this environment's own documented
limitation (no Screen Recording/Accessibility access to screenshot or
drive the live macOS app, per every wizard test file's own docstring)
means the visual result itself needs the User's own confirmation.

## Addendum: persistent-card badge wording, Source pill layout

Real use surfaced that the persistent file card's badge — visible on
every step, unlike the Source step's own contextual pill right next to
the check that produced it — reused "✓ Eligible," which reads as stale
once you've moved well past Source. Discussed several alternatives
before picking one: "Ready" was rejected because it risks colliding
with each step's *own* notion of readiness (a render isn't "ready"
until it's done, a transcription isn't either) — a different flavor of
the same staleness problem, not a fix for it. Landed on "✓ Verified":
accurate regardless of which step is active, and scoped specifically to
the one check that already happened rather than implying anything about
current progress. The ineligible case (`"✕ Not eligible"`) is
unchanged — `can_advance()` already blocks leaving Source with an
ineligible file, so that badge is only ever seen on Source itself,
where "Not eligible" already stays fully accurate; no staleness problem
to solve there.

Separately, the Source step's own "✓ Eligible for filtering" pill moved
out of the info-panel's own column (previously indented to align with
the audio-track/duration/etc. rows beside it) into its own full-width
row above the whole cover-art-plus-info-panel block, left-aligned to
the frame itself — reads as this panel's headline rather than one more
detail buried inside it. Rendered larger via a new `#eligibleBadgeLarge`
QSS class (both palettes) — the existing `#eligibleBadge` class stays
as-is for the persistent card's own compact badge, which needs to stay
small in that thin strip; giving the Source step's pill a separate
object name kept the two independently sizeable without a compound
attribute selector this stylesheet doesn't otherwise use anywhere.

**Verification:** `test_eligible_badge` updated to assert `"✓ Verified"`
in place of `"✓ Eligible"`. `test_heading_reads_eligible_for_filtering`
(already existing, unmodified) still passes — the Source step's own
pill text didn't change, only its position and size. Full
`tests/gui/filter/` 408 passed; `black`/`flake8`/`mypy` clean.

## Addendum: cover thumbnail sized to the info panel's own real height

Real use found the Source step's cover thumbnail (fixed at 108px)
visibly shorter than the six rows of info text beside it. Rather than
pick a new fixed guess, `_EligiblePanel`'s info panel is now built
(rows populated) *before* the cover thumbnail, and the thumbnail is
sized to that panel's own measured `sizeHint().height()` — grows or
shrinks to match whatever that panel's real content actually needs
(e.g. a `Metadata` value long enough to wrap to two lines), the same
"measure the real widget, don't guess a pixel value" discipline already
established for this app's own action-column sizing (ADR-0036).

**Verification:** new regression test confirms the rendered thumbnail
is square and taller than the old fixed 108px against the same real
fixture used throughout this test file; a direct, real (non-test)
run against that fixture measured 150×150px — bigger, and a real,
computed value, not an assumption. Full `tests/gui/filter/` 409 passed;
`black`/`flake8`/`mypy` clean.

## Addendum: persistent card always visible, with real empty-state placeholders

The User pointed at the base m4bmaker app's own "Build" panel (a
screenshot of it before any file is loaded: a "Cover" placeholder box
and empty Title/Author/Narrator/Genre fields, filled in once a file
loads, never hidden outright) and asked for the wizard's own persistent
`FileCard` to follow the same pattern. It previously did the opposite —
`setVisible(False)` until a manifest existed, an outright-hidden state
rather than an empty one.

`FileCard` now calls `self.setVisible(True)` once at construction and
never hides itself again. A new `_show_placeholder()` (called from
`__init__` and from `set_manifest(None, ...)`) puts every part of the
card into an explicit empty state rather than leaving stale content
behind: the cover thumbnail falls back to `"Cover"` text (`set_cover_
pixmap()` already supported a `fallback_text` parameter for exactly
this; the card just hadn't been passing one), the filename label reads
"No file selected yet" styled muted (`#statusLabel`, not the bold
weight real content gets) so it reads as an empty slot rather than an
oddly-worded real filename, the meta line goes blank, and the
eligibility badge — which has no meaningful "empty" visual, unlike a
blank text field — is hidden rather than shown blank or gray.

**Real Qt quirk hit while updating the tests, not a code bug:** the
existing `TestVisibility` tests checked `isVisible()` on a bare,
unparented, never-`.show()`-called `FileCard()` — which reports `False`
by Qt's own default regardless of any `setVisible()` call, since an
unshown top-level widget is never actually "visible" in the literal
sense Qt means by that method. The *old* tests happened to pass anyway
purely by coincidence (asserting `False` before any `setVisible()` call
at all, and `True` right after an explicit `setVisible(True)` — which,
it turns out, *does* flip a top-level widget's own `isVisible()` even
unshown, just not the reverse "never called, still reports False"
starting state). Removing the constructor's `setVisible(False)` without
replacing it with an equivalent `setVisible(True)` left the flag never
set at all, which is what actually broke `test_shown_once_a_manifest_
is_set` — fixed by adding the explicit call, not by chasing the test
failure as if it were something else.

**Verification:** `TestVisibility` renamed to `TestPlaceholder` and
rewritten for the new contract — visible at every stage (construction,
populated, cleared again), the placeholder's exact text/state, and the
badge's own hidden/shown transitions. Full `tests/gui/filter/` 412
passed; `black`/`flake8`/`mypy` clean.

## Addendum: Source step's own info panel gets the same empty-state treatment

The persistent card's placeholder covered the top strip, but the Source
step's own main info panel — the larger cover-art-plus-detail-rows area
that becomes `_EligiblePanel` once a file is chosen — still showed
nothing at all before that, the same gap the previous addendum fixed
elsewhere. New `_PlaceholderPanel(QFrame)`, added to `_result_layout` at
construction (before `_on_file_selected` ever runs) and cleared the same
way any other stale result already is once a real selection begins:
mirrors `_EligiblePanel`'s exact six-row shape (`_INFO_ROW_LABELS`,
shared by both classes) with a `"—"` dash in place of each real value
and no eligibility pill (nothing meaningful to show yet, same reasoning
as the persistent card's own badge). The cover thumbnail is sized to
this placeholder panel's own measured height, the same "measure the
real widget" approach the populated panel already uses (this ADR's
earlier addendum) — since both panels share the identical row shape,
this also means choosing a file doesn't visibly jump in height when the
real panel replaces the placeholder one.

**Verification:** 2 new tests confirm the placeholder's six rows and
"Cover" fallback text are present before any file is chosen, and that
selecting a file clears it (leaving only the "Inspecting…" status text,
not a stale placeholder sitting behind it). Full `tests/gui/filter/`
414 passed; `black`/`flake8`/`mypy` clean.

## Addendum: one persistent info panel, not three swapped-in widgets

Real use surfaced that the previous addendum's fix was incomplete in
two ways: the placeholder was still torn down the instant a file was
picked (`_clear_result()` ran at the top of `_on_file_selected`),
leaving a real gap — nothing shown at all — for the several seconds a
large audiobook's real inspection takes; and the placeholder's height
still didn't reliably match the real panel's, because they were two
separate widget instances (`_PlaceholderPanel`, `_EligiblePanel`) each
independently arriving at "the same" measured height, which isn't
actually guaranteed just because both measurements were done the same
way.

Root-caused, then fixed at the root rather than patched: replaced both
classes with one `_InfoPanel`, built once in `_build_ui()` and updated
in place via `show_placeholder()`/`show_eligible()` as state changes,
never torn down and rebuilt. `SourceStep` no longer clears the whole
result area on file selection — it calls `show_placeholder()` on the
same, already-showing panel instead, so nothing ever disappears.
Ineligible/error results still use their own separate widgets (a
variable-length reasons list, or a single message — a fundamentally
different shape from six fixed rows and a cover, and not the reported
problem), swapped in via a new `_show_secondary()`/`_clear_secondary()`
pair that hides `_InfoPanel` (without destroying it) rather than
removing it from the layout.

**Real, measured bug caught by writing the regression test, not
shipped:** row values already word-wrapped, which meant a long real
value (e.g. a long `Metadata` key list) could grow a row to two lines
while the placeholder's own `"—"` never would — so row values are now
elided to a guaranteed single line (`_ROW_VALUE_ELIDE_WIDTH`, the same
`QFontMetrics.elidedText()` approach the persistent file card's own
filename already uses), with the full text always available as the
row's tooltip.

**A second, distinct height bug found by the same test, after the
first fix didn't fully close it:** the panel's own "✓ Eligible for
filtering" heading was hidden outright (`setVisible(False)`) in the
placeholder state — but a hidden widget is skipped entirely when Qt
computes a layout's height, so that row's own height vanished and
reappeared between states, independent of anything happening in the
rows below it. Fixed by keeping the heading always visible and toggling
its *text* (empty vs the real string) and QSS `state` property (cleared
vs `"eligible"`) instead — same padding and font-size either way, so
the row's own height never changes, and an empty, colorless label
(neither the eligible-green nor ineligible-red QSS rule matches with no
`state` set) reads as correctly blank rather than an odd empty green
pill.

**Verification:** a direct regression test constructs a manifest with a
long enough `Metadata` value that it would have word-wrapped under the
old design, and asserts the panel's own `sizeHint().height()` is
unchanged from the placeholder state's — this test caught both bugs
above in sequence (failed first on the wrapping issue, then again on
the hidden-heading issue after the first fix, before finally passing).
Existing tests updated for the new architecture: `_first_result_widget()`
now resolves to whichever of `_info_panel`/`_secondary_widget` is
actually being shown, rather than assuming layout position 0 is always
the relevant one; a test asserting the placeholder was cleared during
inspection was rewritten to assert the *opposite* (the intended fix);
a test asserting the panel was rebuilt on late cover art was rewritten
to assert the same instance is reused. Two more Qt visibility quirks
hit while writing these, both resolved with `isHidden()` rather than
`isVisible()` — same reasoning as the earlier `FileCard` addendum:
`isVisible()` on a widget whose root ancestor was never shown reports
`False` regardless of any `setVisible()` call, while `isHidden()`
correctly reflects the widget's own explicit flag. Full
`tests/gui/filter/` 416 passed; `black`/`flake8`/`mypy` clean.
