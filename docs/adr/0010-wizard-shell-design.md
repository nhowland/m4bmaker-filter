# ADR-0010 (G5, PRD §7.1, §7.2, §7.3): Wizard shell design

**Status:** Approved by the product owner after an iterative wireframe
review — see `docs/design/wizard-shell-wireframe.html` (also published
live for the review itself). This is the first G5 screen wireframed
before code, per the wireframing decision in ADR-0008.
**Related PRD items:** §7.1 (happy-path decision minimum), §7.2 (the
eight workflow stages), §7.3 (first-run/empty states).

## Decision

The wizard shell is a single window with three regions, stacked
vertically: a horizontal step stepper across the top, a content pane
for the current step's screen, and a footer with Back/Continue anchored
to the bottom. The eight PRD §7.2 stages map directly to eight steps:
Source, Transcript, Transcribe, Profile, Scan, Review, Render, Complete.

### Stepper

- **Horizontal, not vertical** — a row of eight equal-width, fixed-height
  cells, each a numbered circle connected to its neighbors by a solid
  line. Equal sizing regardless of label length or state was a deliberate
  requirement, not a default: it's what keeps the stepper reading as one
  coherent progress indicator rather than a list.
- **State is carried by color and weight alone**, not by an extra status
  word under each label (an earlier draft added "Viewing now" / "Complete"
  captions; removed — the circle fill and connecting-line color already
  say this, and the extra text was redundant clutter).
  - *Done*: filled green circle with a check mark; the connecting line to
    the next step fills green on both sides of the boundary (colored from
    both neighbors so the two flexed half-segments meet without a visible
    seam).
  - *Current*: accent-colored circle with a heavier border, bold
    accent-colored label. Nothing else — no glow, no filled row
    background; those were tried and read as noisier than useful.
  - *Locked* (not yet reachable): solid gray fill, muted label, not
    clickable.
- **Labels are one word each**, naming the step's function (Source,
  Transcript, Transcribe, Profile, Scan, Review, Render, Complete) —
  not PRD's longer stage names ("Choose transcript path"). The full stage
  name still appears as the content pane's eyebrow/title once you're on
  that step.
- **Steps lock in sequence; Back is always free.** A step past the
  furthest one actually reached is locked and not clickable. Back can
  always revisit or edit any earlier step.

### Content pane sizing

The content pane's height is set once, from the tallest step's actual
rendered content (measured directly in the browser at the pane's real
width — see the wireframe's `measureTallestStepHeight()`), not a guessed
pixel value. Every step then shares that one height: a short step (e.g.
Profile) shows its content top-aligned with genuine white space below it
rather than the window shrinking to hug it, and a long step (e.g.
Review's hit table) fits without needing to scroll. This was a real bug
in an earlier draft — the content pane wasn't set to fill the frame's
height, so the whole window resized per step and the footer visually
"floated" wherever the content happened to end.

### Auto-skip: reusing a transcript skips Transcribe entirely

If a saved transcript is compatible with the selected source (same
fingerprint, engine, and model), choosing "Use existing" on the
Transcript step jumps straight to Profile — Transcribe is not visited as
an empty formality. Its circle marks **done-but-skipped** (a "»" glyph
instead of a checkmark, still green, with a tooltip explaining why) so
the stepper doesn't claim a transcription run happened when it didn't.
Back from Profile skips over Transcribe too, since there is nothing there
to review.

This was decided over the alternative of merging Transcript+Transcribe
into one PRD-deviating step (discussed and rejected: it would blur two
different backend state machines — model download vs. the durable
transcription job, ADR-0005 — behind one label, and lose the stepper's
precision about which one is actually running). Auto-skip solves the
actual complaint (an empty, pointless step in the reuse-transcript
happy path) without either cost.

### Progress terminology: "Chapter" only when it's actually true

Transcribe's progress reads "Chapter *N* / *M*" using real chapter
numbers **only** when a chapter maps to exactly one processing chunk —
true whenever `chunking.py`'s chapter-aware planning didn't need to
subdivide a chapter or fall back to fixed-duration windowing. When a
chapter is subdivided (longer than the pause-latency budget) or the
source has sparse/no chapter markers, the label reads "Section *N* of
*M*" instead. This was chosen explicitly over always saying "Chapter"
(friendlier, but would misrepresent chunk counts as chapter counts for a
sparsely-chaptered or chapterless source — common among the self-produced,
non-DRM M4Bs this app targets) and over always saying "Section" (never
wrong, but loses the more meaningful label for the common well-chaptered
case).

**Not yet implemented**: the real Transcribe step needs to know, per
chunk, whether it corresponds 1:1 to a real chapter (and which one) —
this is a data-plumbing requirement on the eventual orchestrator/UI
wiring, not just a copy change, and is flagged here so it isn't lost by
the time that step is actually built.

### Other shell behaviors confirmed in review

- **Cancel confirms when it matters.** Closing the wizard mid-Transcribe
  or mid-Render prompts first, mirroring the confirm-before-close pattern
  `ModelManagerWindow` already uses (ADR-0009) — applied here at the
  wizard-window level, independent of `MainWindow`'s own shutdown
  confirmation.
- **Profile step reuses the Catalog window**, rather than duplicating
  category/word CRUD inside the wizard — "Manage Word Catalog…" opens the
  already-shipped `CatalogWindow` (ADR-0008).
- **Advanced settings stay behind a disclosure** on the Scan step
  (padding, merge adjacency, gain floor) — PRD §7.1 requires the happy
  path stay unblocked by them, so they're never shown open by default.
- **First-run/empty states render inline**, in the same shell, not as
  separate error screens — required by PRD §7.3. Demonstrated on the
  Transcript step (no model installed yet).

## What exists today

`docs/design/wizard-shell-wireframe.html` — a self-contained, interactive
HTML wireframe (no build step) implementing everything above, including
the live auto-skip demo. It uses its own deliberately low-fidelity
"blueprint" visual language (IBM Plex Sans/Mono, dashed schematic blocks),
distinct from the shipped app's real `gui/styles.py` palette, so it reads
as a structural draft under review rather than a finished screen.

No PySide6 code exists yet for the wizard — this ADR fixes the shell's
structure and behavior so that implementation isn't guessing at
decisions the wireframe review already settled.

## Not yet decided

Per-step screen content beyond what the wireframe sketches schematically:
the Scan-Review screen specifically is called out in ADR-0008 as needing
its own wireframe pass before code, same as this one. Model-selection UX
within the Transcript step, the Review step's hit-filtering interaction
detail, and the Render step's output-path picker are all still open.
