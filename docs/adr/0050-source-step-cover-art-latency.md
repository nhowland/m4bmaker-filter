# ADR-0050: Source step — cover art no longer waits behind inspect + transcript scan

**Status:** Implemented and verified.

## Context

The main app's own Source field shows cover art almost instantly when an
M4B is picked — chapters, metadata, and cover are all extracted together
in one fast mutagen-only pass (`gui/worker.py`'s `LoadM4bWorker`, no
subprocess). The filter wizard's own Source step, picking the same kind
of file, visibly took several seconds longer. The User asked why.

Reading the actual code path found the cause was ordering, not the
cover extraction itself: `_on_inspect_finished()` ran the eligibility
panel's `show_eligible()` — which called `find_compatible_transcript()`
synchronously on the UI thread, a saved-transcripts directory scan its
own docstring documents at 1.5-5s on a real transcripts directory — and
only *after* that returned did it construct and start `CoverArtWorker`
at all. So the cover worker wasn't even launched until the ffprobe
inspect subprocess had finished *and* a multi-second disk scan had run
on the main thread. `CoverArtWorker` itself (an ffmpeg subprocess with
a mutagen fallback) was a secondary, smaller factor.

## Decision

Two independent changes, both narrowing the gap between "User picks a
file" and "cover art appears":

1. **Start `CoverArtWorker` from `_on_file_selected()`, not
   `_on_inspect_finished()`.** Cover extraction never depended on
   anything ffprobe reports — it only needs the picked path. It now
   runs on its own `QThread` concurrently with `MediaInspectWorker`'s
   ffprobe subprocess, rather than strictly after it. Whichever
   finishes first paints first; `_InfoPanel.update_cover()` (new) lets
   a cover that finishes before inspect does get applied once the
   panel is up, without needing to re-run the rest of `show_eligible()`.

2. **Move the saved-transcript lookup off the UI thread.** New
   `TranscriptLookupWorker` (mirrors `CoverArtWorker`'s shape: one
   `result_ready` signal, no error signal — "nothing found" is a normal
   outcome, not a failure) runs `find_compatible_transcript()` on a
   background thread, started from `_on_inspect_finished()` once
   eligibility is known. The "Saved transcript" row shows "Checking…"
   until it resolves, matching the six-row placeholder pattern
   `_InfoPanel` already uses elsewhere rather than leaving the row
   blank.

`_InfoPanel.update_cover()` and the new `set_saved_transcript()` are
deliberately separate, narrow setters rather than both routing back
through `show_eligible()` — the two backgrounded lookups (cover,
transcript) now finish in whichever order the machine happens to
schedule them, and calling the full `show_eligible()` a second time
would reset whichever row hadn't finished yet back to its own
placeholder text, clobbering a real answer that had already arrived.

## Verification

**Unit tests:** `TestTranscriptLookupWorker` (2 new, `test_workers.py`)
covers both outcomes off-thread, matching `TestCoverArtWorker`'s own
shape. `test_source_step.py` gained
`test_saved_transcript_shows_checking_before_lookup_resolves` and
`test_stale_transcript_workers_result_is_ignored` (mirroring the
existing stale-cover-worker guard), and its existing transcript-lookup
tests were rewritten to deliver a result via `_on_transcript_ready()`
instead of asserting on a synchronous call. Full suite 1971 passed, 2
skipped; `black`/`flake8`/`mypy` clean on every file touched (pre-
existing, unrelated debt in `gui/window.py` untouched).

**Real app:** verified against the real running app with the real,
user-wide transcripts directory on this machine (19 files, 320MB, the
same real scale the lookup's own docstring cites). Selected a fresh
source file via the actual Browse dialog; the panel resolved with
cover art, chapters, metadata, and the matched saved transcript all
showing correctly. Directly measured `find_compatible_transcript()` on
this machine's real transcripts directory at 0.84s for a non-matching
fingerprint — confirmed real, non-trivial cost that the old code paid
*before* even starting cover extraction, and that the new code no
longer gates cover art behind.

## Addendum: mutagen tried before ffmpeg (2026-09-07)

Following up on the question of what latency remained after the
ordering fix above: `CoverArtWorker` calls
`m4bmaker.cover.extract_cover_from_audio()`, which tried an ffmpeg
subprocess first and fell back to mutagen. Benchmarked directly on a
real `.m4b` on this machine: the ffmpeg-first path took ~0.065-0.071s
per file (ffmpeg actually succeeds on the first try for these files —
the cost is pure subprocess-spawn overhead, not a failed attempt);
reading the same file's cover via mutagen alone took ~0.001s — the
same no-subprocess approach the main app's own `LoadM4bWorker` already
uses. A real, measured ~65-70x difference, if a small one in absolute
terms compared to the ordering fix above.

Reordered `extract_cover_from_audio()` to try mutagen first (MP4 covr
atom, then ID3 APIC frame) and fall back to ffmpeg only when neither
mutagen path finds anything — not a new function, since the existing
fallback chain already tries every format mutagen can't read; only the
order changed. This is a strict improvement for every caller of the
shared function (`pipeline.py`'s CLI build, the filter renderer's own
cover step, and this wizard's `CoverArtWorker`) — mutagen's attempts
fail fast (an exception, not a hang) on a file they don't understand,
so ffmpeg is still reached for anything genuinely outside mutagen's
coverage; the change only skips a subprocess spawn for the common case
where mutagen already has the answer.

**Verification:** 2 new tests in `test_cover.py`
(`TestExtractCoverFromAudioOrdering`) assert ffmpeg is never invoked
when mutagen finds a cover, and that ffmpeg is still reached and used
when mutagen finds nothing. Full suite 1973 passed, 2 skipped;
`black`/`flake8`/`mypy` clean. Verified in the real running app — a
freshly selected source's cover art rendered correctly with the new
ordering in effect.

## Addendum: persistent file card stuck on "No file selected yet" (2026-09-07)

Real-app regression from the first addendum above, caught by the User
via screenshot: the small persistent file card below the stepper (every
step from Source through Render, `file_card.py`'s `FileCard`) stopped
updating at all after a source was picked — stuck reading "No file
selected yet" even though the Source step's own larger info panel right
below it had already fully populated with cover art, chapters, and a
matched transcript.

Root cause: `FileCard` is refreshed only via `WizardWindow.
_refresh_file_card()`, wired to `SourceStep.cover_ready`, and that
method reads `manifest` and `cover_path` *together* in one call. Before
this ADR's first change, `CoverArtWorker` always started only after
`_on_inspect_finished()`, so by the time `_on_cover_ready()` emitted
`cover_ready`, `self._manifest` was already set — the file card always
saw both at once. Once cover extraction started running concurrently
with inspection instead, cover routinely finishes *first* (mutagen-first
extraction, the addendum above, is faster than the ffprobe inspect
subprocess) — so `_on_cover_ready()`'s `cover_ready.emit()` fired while
`self._manifest` was still `None`, and nothing ever emitted the signal
again once inspection caught up and actually set it. The file card was
told about a cover with no manifest, then never told about the manifest
at all.

Fixed by also emitting `cover_ready` from `_on_inspect_finished()`,
right after `self._manifest = manifest` — the signal's docstring was
broadened to match (it was never really "cover art changed", it's
"something the file card reads just changed, re-read both").
`_refresh_file_card()` itself needed no change — it already re-reads
both values fresh on every call, so telling it "something changed" more
often than strictly necessary is harmless.

**Verification:** new regression test in `test_wizard_window.py`
(`TestFileCardRefresh`) sets `SourceStep._cover_path` directly (as
`_on_cover_ready()` would, simulating cover finishing first) *before*
calling `_on_inspect_finished()`, then asserts the file card shows the
new filename — confirmed this test fails against the code without this
addendum's one-line fix (reproducing the exact "No file selected yet"
bug) and passes with it. Full suite 1974 passed, 2 skipped;
`black`/`flake8`/`mypy` clean. Verified in the real running app — the
file card now shows the cover thumbnail, filename, duration, and
"✓ Verified" badge immediately, matching the info panel below it.
