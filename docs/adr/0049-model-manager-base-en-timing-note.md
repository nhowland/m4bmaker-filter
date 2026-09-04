# ADR-0049: Explain why base.en is recommended over small.en

**Status:** Implemented and verified.

## Context

The Model Manager table already labels base.en "Recommended" and
small.en "Higher recognition effort" — accurate, but silent on *why*,
which reads as an ordinary size/quality trade-off (smaller and faster
vs. bigger and more capable) rather than what this fork's own real
investigation actually found. The User asked for user-friendly content
explaining specifically why base.en works as well as small.en for word
timing.

The real answer is stronger than "just as good," and it already
exists in this project's own history (ADR-0024/0025's real-book DTW
comparison, closed out in the same investigation that set this fork's
current default attenuation padding): with DTW enabled, base.en and
small.en found the exact same real hits across every chapter tested —
zero hits either model found that the other missed. Timing was not
identical, though: small.en's own word timestamps landed systematically
*later* than base.en's on 96% of hits (mean +70ms start / +101ms end,
max +300ms end), never earlier. Late timing is this app's one
already-proven-fragile failure mode (a word's audio extending past its
own padded silence window) — so base.en isn't a smaller fallback, it's
the measurably safer choice for exactly what this app does with that
timing.

## Decision

A persistent info card in `ModelManagerWindow`, placed between the
model table and the per-row Source/SHA-256 details — visible
immediately, not a tooltip a User has to discover, matching the lesson
already learned in ADR-0037 (a tooltip requires already knowing where
to hover, and doesn't show up in a screenshot). Titled "Why base.en is
recommended," plain-language body naming both models by their real
table names but leaving out internal jargon (DTW, ADR numbers,
milliseconds) a User has no reason to know. Reuses the `aboutFeatureCard`
QSS styling ADR-0048 already added to both palettes, via a small
`_info_card()` helper duplicated locally rather than imported from
`about_dialog.py` — this codebase's own established convention for
small, private, per-module widget helpers.

Not tied to row selection (unlike the existing Source/SHA-256 details
label) — this explains the recommendation itself, which is true
regardless of which row a User has clicked.

## Verification

**Unit tests** (`test_model_manager_window.py`, 2 new): the card's
title and body text are both present among the window's labels before
any row is selected; the body names base.en explicitly. All 26
pre-existing tests pass unmodified.

Full suite 1967 passed, 2 skipped; `black`/`flake8`/`mypy` clean.

**Real-app verification, not just headless tests:** opened the window
in the real running app in both dark and light mode — the card renders
correctly in both, sits naturally between the table and the
Download/Remove buttons, and doesn't interfere with existing
selection-driven behavior. Screenshots taken of both themes.
