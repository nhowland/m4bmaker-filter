# ADR-0045: Review step explains overlapping-hit merging

**Status:** Implemented and verified.

## Context

The User asked what happens when two hits overlap — e.g. a single word
("ass") and a phrase containing it ("ass holes") both flagged at the
same spot. The real answer (confirmed against `interval_planner.
build_render_plan()` and `scan.build_report()`, not assumed): they're
separate `ScanHit` records that get merged into one padded
`RenderInterval` at render-plan time, and "Attenuated total" already
reflects that merged duration, not a naive sum of every hit's own
length — but nothing in the UI said so, so the "Total hits" count and
the actual number of silenced spots could look inconsistent with no
explanation.

## Decision

Two small, coordinated additions to the Review step, chosen after
iterating on wording directly with the User (kept deliberately simple —
no jargon like "provenance" or "padded intervals," and using a mild,
generic example instead of the real profanity pair that prompted the
question):

- The Render Plan tab's existing explainer note (previously: "What will
  actually run when you click Render — merged, padded intervals with
  provenance back to the hits that produced each one.") now says: "This
  is what actually gets silenced when you render. When hits are close
  together or overlap — like "darn" and "darn it" appearing at the same
  spot — they're combined into one silenced section instead of being
  treated separately. Most people won't need to check this before
  continuing."
- `_StatStrip._make_stat()` gained an optional `tooltip` parameter,
  applied to "Attenuated total" specifically (both its caption and
  value label, so hovering either shows it): "How much audio will
  actually go quiet. If two flagged hits overlap, that time is only
  counted once." — placed here rather than on "Total hits" since this
  is the number most likely to be second-guessed once someone notices
  hits and silenced spots don't match 1:1.

The "Merged intervals" `QGroupBox` heading directly below the note was
deliberately left as-is — a compact section label, not prose, where
"intervals" reads fine even though the note above it now says "section"
in plain language.

## Verification

**Unit tests** (`test_review_step.py`, 2 new): the Render Plan tab's
note text is found among the step's labels and contains both the "darn"
example and the "combined into one silenced section" phrasing; the
"Attenuated total" stat's tooltip contains "only counted once". All 35
pre-existing tests pass unmodified — this is additive UI text, no
behavior change.

`black`/`flake8`/`mypy` clean. `tests/gui/filter/wizard/test_review_step.py`
(37 passed), full `tests/gui/filter/` (380 passed).
