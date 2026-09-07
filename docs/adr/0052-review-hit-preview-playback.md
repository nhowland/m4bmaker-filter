# ADR-0052 (G5, PRD §5.3, §9.4): Review hit preview playback

**Status:** Implemented and verified. Reached through an iterative
interactive mockup — `docs/design/review-hit-preview-wireframe.html`
(also published live for the review itself), same wireframe-before-code
discipline ADR-0010 established for the wizard shell.

**Related PRD items:** §5.3 explicitly defers "Built-in player deep-link/
preview from match results" out of MVP — this ADR is the decision to pick
that item up now that the MVP steps are stable and shipped. §9.4 (Scan/
Review — `ScanHit.start_ms`/`end_ms`, `AttenuationSettings.lead_padding_ms`/
`tail_padding_ms`).

## Context

The User asked for a way to hear a Review hit's own audio before deciding
to include or exclude it, rather than relying only on the transcript's
text — and specifically asked whether the base app's own
`AudioPlayerWidget` (`m4bmaker/gui/player.py`) was reusable for this.

It is, cleanly: its public surface (`load`, `load_paused`, `seek_chapter`,
`stop`, `release`, `current_position_ms`) takes only a `Path` and
millisecond offsets — no `Book`/`Chapter` coupling at all. It's already
used exactly this way in the base app's own Chapters tab: click a row,
it loads-paused at that timestamp.

## Decision

### What gets played, and why two modes

Not the raw matched word — the *padded* window
(`hit.start_ms − lead_padding_ms` to `hit.end_ms + tail_padding_ms`,
whichever `AttenuationSettings` the scan actually used) is the default,
labeled **"Filtered word"**. That's the same interval Render will
actually silence, so it answers "did this cut off too much / too
little" — a raw-word preview would sound fine even when the padding is
wrong.

A second, explicit mode, **"Word in context"**, plays a fixed ±2 seconds
either side instead, for a different question: "is this really the
flagged word, said the way I think it is." A first pass considered
sizing this to N real transcript words either side (exact, via
`TranscriptWordIndex`) — rejected: it needs new data exposed from a
module that doesn't currently return word-level timing, and audiobook
narration paces steadily enough that a fixed ±2s already reads as "a few
words" in practice; word-count boundaries don't avoid crossing a chapter
break any better than fixed seconds do either. Not enough upside for the
extra surface area.

Both windows are computed from the hit's own `start_ms`/`end_ms` with
plain arithmetic — no new data added to either mode.

**Labels** are named for what a User recognizes (this app's own name,
"filter") rather than the mechanism ("padded," "lead-in," "±2000ms").

### New capability: extend the shared widget, don't fork it

`AudioPlayerWidget` gains one new method, `play_clip(path, start_ms,
end_ms)` — loads/seeks and auto-pauses once playback reaches `end_ms`,
using the widget's own existing `positionChanged` handling. This lives
in the base app's own module, not duplicated into the filter package: a
"preview a bounded clip" capability is generically useful (not specific
to this one screen), and it keeps every bit of position-tracking state
in the one place that already owns it, rather than the filter package
re-deriving it against a widget it would otherwise have to treat as a
black box.

**No separate Stop control.** The base widget's own Stop is meaningfully
different from Pause because resuming a multi-hour book from exactly
where you left off is worth a dedicated action. These clips run one to a
few seconds — resuming mid-clip isn't worth much — so ▶/⏸ here always
means "toggle playback, and reset to the clip's own start whenever it
isn't already running." Finishing naturally already resets the same
way; there's only one way to "stop."

### Where it lives: a docked row, not a third tab

A slim (~44px) row is docked directly beneath the Hits table, inside the
existing "Hits" tab — not a new "Player" tab alongside "Hits" and
"Render Plan". Considered and rejected: a third tab keeps the Hits
table maximally uncluttered, but it splits the one thing this feature
is for — hearing the audio *while* looking at the Context column and
the Include checkbox for that same hit — across two screens, adds a
click every time, and does nothing for the Render Plan tab.

The docked row costs about one row of table height, whether idle or
loaded, and isn't a new convention: it mirrors the base app's own
Chapters tab, which already docks this exact widget directly below a
big table (`gui/window.py`) for the same reason. Also considered: a
wider master-detail split (table left, permanent detail/player pane
right) — a much bigger redesign of an already-built, tested tab for a
benefit the docked row gets more cheaply; and a row-level play icon
opening a floating popover — zero permanent footprint, but popovers
aren't a pattern used anywhere else in this app, and Qt popover
positioning/dismissal is real added complexity next to a row that just
sits there.

### Interaction

Clicking a hit row loads paused at the active mode's clip start —
mirrors the base app's own chapter-preview click behavior exactly, not
a new pattern. Pressing ▶ plays and auto-stops at the clip end.
Selecting a different row while one is playing swaps the clip
immediately, no explicit stop required first. Switching between
"Filtered word" and "Word in context" for the same hit resets playback
and reloads the other window. 0 or 2+ rows selected disables preview
entirely — existing bulk Include/Exclude behavior is untouched; preview
is single-hit only.

### Overflow safety: elide the label, not hope it fits

Caught during mockup review (screenshot from the real, current Review
screen): a long enough term (no length cap on a catalog phrase in the
data model) plus a verbose inline label could push the mode toggle,
progress bar, and time display off the edge of the row. Fixed with the
same convention `source_step.py`'s `_InfoPanel._set_row()` already
established for this exact class of problem: elide the visible text to
a fixed budget, put the full detail in the tooltip. Concretely, the
always-visible label shrank to just the term (`Previewing "heck"`); the
exact clock range and why (lead-in/tail, or before/after) moved into
that label's own tooltip, since the adjacent time readout already shows
duration. Verified in the mockup against an artificially long stress-
test term, and against the wizard's own real minimum window size
(`760×560`, `wizard_window.py`) — not just the mockup document's own
wider default layout.

## Data flow

`ReviewStep.set_scan()` gains one new argument, `source_path: Path`,
wired from `wizard_window.py`'s `_push_scan_to_review()` reading
`SourceStep.manifest.source_path` — the same manifest
`_push_review_to_render()` already reads for Render. Nothing else
downstream changes: `_current_transcript()`, the render-plan
computation, and the stat strip are all untouched.

## What this ADR does not decide

- Multi-select preview (playing more than one hit at once, or a
  merged Render Plan interval instead of a single hit) — out of scope;
  the Render Plan tab has no per-row selection today for this to attach
  to anyway.
- The scrub position, once `AudioPlayerWidget` is embedded for real,
  will still be real-file-absolute (spans the whole book), not clamped
  to the active clip's own bounds — accepted, not solved, same as the
  mockup discloses. Teaching the shared widget a bounded-range slider
  mode is a separate, later decision if this turns out to matter in
  practice.
- Whether to also stop at a chapter boundary for "Word in context" —
  not addressed; a fixed ±2s can still bleed into an adjacent chapter,
  same as a word-count boundary would.

## Implementation notes (found while building)

Two things the design above didn't fully resolve until actual Qt code
forced the question:

- **`AudioPlayerWidget` needed two more small additions** beyond
  `play_clip()`: a bare `pause()` (existing `load_paused()` only
  seeks — while genuinely playing, a plain `setPosition()` call doesn't
  stop the audio) and a `playback_state_changed` signal (the dock's own
  Play/Pause icon has to react to `play_clip()`'s own automatic pause at
  clip end, not just to clicks on itself — polling `is_playing` had no
  natural place to live). Both are small, generically useful additions
  to the shared widget, same reasoning as `play_clip()` itself.
- **Reusing `AudioPlayerWidget` wholesale meant hiding its own Play/Stop
  buttons**, not just skipping the mode toggle's original custom
  transport bar — its own Stop resets to position 0 of the *whole
  file*, which is simply wrong for a bounded clip. New `show_controls`
  constructor flag (default `True`, so every existing caller is
  unaffected) skips adding those two buttons to the row; the dock
  injects its own Play button, term label, and mode toggle into that
  same row layout instead, via the identical `player_row.insertWidget()`
  trick the Chapters tab already uses for its own prev/next buttons.

## Verification

**Unit tests:** 15 new tests on `AudioPlayerWidget` (`test_player.py`:
`show_controls`, `play_clip`, `pause`, the clip-auto-stop mechanism
including that a manual seek or an unrelated `load()` clears a stale
clip boundary, and the new signal) and 17 new tests on `ReviewStep`
(`test_review_step.py`: exact padded/context window math against a real
`AttenuationSettings`, mode toggling, play/pause including the
no-separate-Stop reset-to-start behavior, masked-term handling, the
label's elide-with-tooltip fallback against a deliberately long catalog
phrase, `hideEvent`, and that `set_scan()` resets preview state) — all
treating `AudioPlayerWidget` as a black box per its own already-dedicated
tests, same "test the shell, not the widget" split this file already
used for `CatalogService`/`Scan`. Full suite 2015 passed, 2 skipped;
`black`/`flake8`/`mypy` clean on every file touched.

**Real app**, driven end to end through the actual wizard (a real
source with a real saved transcript, a real profile, a real scan — 345
real hits): selected a hit, confirmed the padded window's exact
computed start seeked correctly (verified via the real slider's own
reported position, not just that a row highlighted), pressed Play and
polled the real slider position advancing in real time
(192012→192539ms), confirmed the clip auto-stopped at exactly the
padded window's own end (192957ms) and the button reset to ▶ on its
own, then toggled to "Word in context" and confirmed it reseeked to the
wider window's own start (190312ms, exactly 2000ms earlier). Verified
in dark mode; light mode uses the identical token/QSS pattern already
validated for the same card+pill styling in ADR-0048/0049 but wasn't
re-screenshotted this pass (an unrelated theme-toggle hiccup in this
session, not a code concern).

## Addendum: two real bugs the User caught in the real app (2026-09-08)

Both surfaced from a live screenshot at a real, much-larger-than-default
window size (the User had resized the wizard well beyond its 900×640
default) — neither reproduced in this ADR's own original verification
pass above, which never drove the app at that size.

**1. The Hits table had collapsed to about one and a half visible
rows**, with a large blank gap between the preview dock and the
Back/Continue footer. Root cause had nothing to do with this ADR's own
stretch-factor math (confirmed by isolating `ReviewStep` in a bare
`QMainWindow` at the same size — it laid out correctly there): `QTable
Widget.wordWrap` defaults to `True` in Qt, and a long enough Context
cell (`TranscriptWordIndex.context()`'s own up-to-5-words-either-side
window, easily 10+ words plus the term) can silently wrap onto 2-3
lines, ballooning that one row's height and cutting how many rows fit
in the same pixel space — worse at a wide window specifically, since a
`Stretch`-mode Context column has more room to hold long-enough text
that still doesn't fit one line. Same class of bug this codebase
already has a fix pattern for (`source_step.py`'s `_InfoPanel`, elide
rather than wrap): `self._table.setWordWrap(False)` plus a tooltip on
each Context cell carrying the untruncated text, so Qt's own default
item delegate elides overflow to one line automatically rather than
wrapping it. Pre-existing behavior, not something this ADR's own
changes introduced — just not visible until a real 800-hit scan with
long, natural-language context strings was reviewed at a large window
size.

**2. The preview dock's progress display barely moved during
playback.** This one *was* this ADR's own design: embedding
`AudioPlayerWidget` "for real" meant keeping its own slider/time label,
explicitly disclosed above as file-absolute, not clip-relative. Seeing
it live made the cost of that trade-off obvious in a way the design
pass hadn't weighed correctly — a clip is a few seconds out of a
multi-hour book, so its slider only crawls a fraction of a percent
during an entire clip's playback, reading as "stuck," not "playing."
Fixed by reversing that call: `AudioPlayerWidget` gained a
`position_changed` signal, `show_controls=False` now omits its slider
and time label too (previously just Play/Stop), and the dock builds its
own clip-relative `QProgressBar` + time label (`0.0s / 1.3s`, filling
visibly across the clip's real duration) — restoring the exact
behavior the original interactive mockup had all along, before the Qt
implementation pass traded it away for simplicity.

**Verification:** 8 new tests (2 on `AudioPlayerWidget`'s
`position_changed`, 6 on `ReviewStep`'s progress math, word-wrap, and
tooltip). Full suite 2023 passed, 2 skipped; `black`/`flake8`/`mypy`
clean. Verified in the real app at the same large window size the User
reported the bug at (1800×1040, a real 820-hit scan): the table now
shows 14 full single-line rows with no gap, and the progress bar
visibly filled to ~75% (`0.5s / 1.3s`) during a real clip playthrough.
