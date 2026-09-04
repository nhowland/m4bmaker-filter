# ADR-0048: "About Language Filter" menu item and dialog

**Status:** Implemented and verified.

## Context

The User asked for an "About" (or similarly worded) item in the
Language Filter menu that opens a user-friendly overview of what the
feature does and what capabilities it offers — informational only, no
controls. The base app already has its own "About m4Bookmaker" in the
Help menu (credits/version/support links, `MainWindow._show_about()`)
— that dialog is the wrong shape and the wrong scope for this request:
it hardcodes light-only colors (not theme-aware) and is about the
*application's* identity, not a feature *tour*. This needed its own,
separate dialog scoped to the Language Filter feature, matching this
whole engagement's established pattern of keeping the feature's own
additions inside the "Language Filter" menu rather than folding into
the base app's own UI.

## Decision

New `gui/filter/about_dialog.py`, `AboutLanguageFilterDialog(QDialog)`
— built fresh on every open (no state to preserve between opens,
matching `_show_about()`'s own shape) rather than the
lazy-create-and-reuse pattern this feature's other secondary windows
use. Content: a centered header (title, one-sentence tagline, and a
short "how it works" line naming the real wizard flow — pick a book,
transcribe, review, render), then a scrollable list of feature cards
(`QFrame#aboutFeatureCard`, a new small QSS addition reusing this
app's existing "warm card" tokens in both palettes) covering, in the
same order a User actually encounters them while using the feature:
Word List, Filter Profiles, Automatic Transcription, Smart Scanning,
Review Before You Commit, Filtered Render, and Models & Storage. Real,
theme-aware styling via the existing `get_stylesheet(dark)` — unlike
the base app's own About dialog, this one respects the current
light/dark mode.

New "About Language Filter" `QAction` in `MainWindow`'s Language
Filter menu, right after "Filter Audiobook Language…" and its own
separator. Given an explicit `MenuRole.NoRole`, the same defensive
pattern already used for "Settings…" in this same menu — Qt's macOS
integration auto-detects "About"-like action text and would otherwise
try to relocate it into the app's own top-level menu, where it would
collide with the base app's own "About m4Bookmaker" entry sitting
there already.

## Verification

**Unit tests** (`test_about_dialog.py`, 9 new): the dialog is a
`QDialog` (not a `QMainWindow`, matching the "no state, built fresh"
choice above), every real feature title and description text from
`_FEATURES` actually appears in the rendered widget tree, the core
workflow steps (transcribe/review/render) are named somewhere in the
overview, the Close button accepts the dialog, and `apply_stylesheet()`
doesn't raise for either theme. Full suite 1965 passed, 2 skipped;
`black`/`flake8`/`mypy` clean.

**Real-app verification, not just headless tests:** opened the dialog
in the actual running app in both dark and light mode, resized it to
confirm every one of the 7 feature cards renders correctly (not just
the ones visible without scrolling), and confirmed the Close button
and window-title wiring both work. Screenshots taken of both themes.

## Addendum: two-column layout, no scrolling required

That real-app pass confirmed the dialog's original single-column
layout (520px wide) needed real scrolling to reach the last few cards
— acceptable but not what "so it doesn't have to be scrolled" asks
for. Feature cards now lay out in a two-column `QGridLayout` (`_FEATURE_COLUMNS
= 2`) instead of a single stacked column, and the dialog's default
size grew from 520×640 to 860×600 to fit it. 7 cards in 2 columns is 4
short rows instead of 7 tall ones — confirmed against the real running
app that this fits entirely within the default size with no
scrollbar, at both the default size and the widened one tested for
the base implementation above. The `QScrollArea` stays in place as a
defensive fallback for an unusually small screen; it just isn't
exercised at any normal size anymore.

No test changes needed — the existing content-presence tests
(`test_about_dialog.py`) don't assert layout shape, only that each
feature's title and body text appears somewhere in the widget tree,
which holds regardless of column count. Full suite 1965 passed, 2
skipped; `black`/`flake8`/`mypy` clean.

## Addendum: real screen still clipped the last row; sized from real content instead

A screenshot from the User's own machine showed the addendum above
wasn't actually enough: at the fixed 860×600 default, the bottom
"Models & Storage" card was still cut off, no scrollbar visible to
reach it. Two compounding causes, both real Qt behavior rather than
one obvious bug: `860×600` was itself still a guessed constant, not
measured from anything; and even a *measured* `sizeHint()` on this
dialog's own header/body widgets would have kept undercounting, since
every description here is a word-wrapped `QLabel`, and a plain,
width-agnostic `sizeHint()` reports the *unwrapped*, single-line
height for those — only `heightForWidth()` at the width the dialog
actually opens at reflects how many lines a card's text really wraps
to. This is the same root lesson as the Settings window's own sizing
bug (ADR-0047 addendum), compounded by a second Qt gotcha specific to
wrapped text.

`_size_to_content()` (new) measures `heightForWidth(860)` on the
header's and body's own layouts directly (not the dialog's or the
scroll area's `sizeHint()`, neither of which reflects this correctly)
and sums them with the footer's plain `sizeHint()`, capped at 90% of
the primary screen's available height so this can't grow past what
actually fits — the `QScrollArea` remains as the fallback for whatever
that cap does clip. Separately, per the User's own request, the
combined "Models & Storage" card split into two — "Manage
Transcription Models" and "Settings" — each mapping 1:1 to a real,
distinct menu item/window, bringing the total to an even 8 cards (4
clean rows of 2, no orphan card alone in the last row).

Verified against the real running app in both themes: the dialog now
opens at 860×707 (computed, not asserted) with all 8 cards visible and
no scrollbar in either case. Full suite 1965 passed, 2 skipped;
`black`/`flake8`/`mypy` clean (one real `union-attr` finding from
`layout()` returning `QLayout | None` — fixed with an `assert`, the
same defensive pattern already used for `centralWidget()` in the
Settings window fix this mirrors).
