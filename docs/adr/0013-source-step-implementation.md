# ADR-0013: Source step — PySide6 implementation

**Status:** Implemented and verified. Extends ADR-0012's pattern (real
code for a wizard step, grounded in the real backend) to the Source
step, which had no prior wireframe pass of its own — its design was
worked out directly against `media_inspector.py`'s real
`MediaManifest` shape rather than against an invented mockup.
**Related PRD items:** §7.2 stage 1 ("Select Source" — eligibility,
selected audio track, duration, chapters, required metadata, storage
estimate, compatible saved transcript availability); §6.2/§6.4 (AAC-only
eligibility); §10.4 (transcript compatibility/status semantics); D-05
(non-AAC primary audio unsupported in v1).

## Scope

One wizard step (`SourceStep`), plus two backend functions PRD §7.2
stage 1 needs that didn't exist yet:

- `renderer.estimate_storage_bytes(manifest) -> int`
- `transcript.find_compatible_transcript(fingerprint, transcripts_dir=None) -> Transcript | None`

`SourceStep` replaces the `PlaceholderStep` at index 0; the remaining
placeholders (Transcript, Transcribe, Profile, Scan, Render, Complete)
are unchanged.

## `estimate_storage_bytes`: three PCM stages, not one

`render()`'s own pipeline (`renderer.py`) writes three raw-PCM scratch
files that all exist on disk simultaneously at peak — `source.pcm`,
`envelope.pcm`, and `filtered.pcm`, the last written just before
`encode_and_mux()` consumes it. The estimate sums all three (each
`duration_s * sample_rate * channels * 2` bytes, 16-bit PCM) rather than
just one, plus the final AAC output sized from the **source's own** bit
rate — the real render bitrate isn't chosen until the not-yet-built
Render step, so the source's bit rate is the best proxy available at
this stage. Cross-checked against ADR-0007's real 13.5-hour production
fixture: ~8.6GB per PCM stage, ~26GB total — matches what that book
actually consumed on disk during its real end-to-end render.

Returns `0` if the selected track's sample rate or channel count is
unknown, mirroring `render()`'s own guard for that same condition —
no estimate is possible without them, and `0` reads as "unknown" in the
UI rather than a misleading number.

## `find_compatible_transcript`: status-gated, most-recent-wins

Scans `*.m4bt.json` files in the transcripts directory (or a caller-
supplied one, for testability), matching on `Transcript.source.fingerprint`.
Two decisions:

- **Only `TranscriptStatus.COMPLETE` counts as compatible.** A draft,
  partial, failed, or otherwise-incompatible transcript exists on disk
  but isn't something the wizard can actually hand off to Scan (PRD
  §10.4) — showing it as "found" would be a lie the next step would have
  to un-tell.
- **Most-recently-modified match wins**, not first-found or oldest —
  if a source was transcribed more than once (a re-run after a bad
  first pass, a different engine/model), the newest COMPLETE result is
  the one worth reusing.

A transcript file that fails to parse is skipped (`except Exception:
continue`) rather than raised — one corrupt file in the directory
shouldn't block finding a good one elsewhere in it.

## `SourceStep`: real backend, no invented UI

- `_M4bPicker` — single-file picker (drag-and-drop plus a native macOS
  file panel via `osascript`/`QProcess`), scoped down from
  `gui/widgets.py`'s existing `FolderDropZone` pattern rather than a
  new implementation. Rejects non-`.m4b` drops/picks with a message box
  instead of silently ignoring them.
- Inspection runs on a new `MediaInspectWorker(QThread)`
  (`gui/filter/workers.py`), wrapping `media_inspector.inspect()` (an
  `ffprobe` subprocess call) off the UI thread — mirroring the existing
  `ModelDownloadWorker` convention in the same module. Uses
  `utils.find_binary("ffprobe")`, not `utils.find_ffprobe()` — the
  latter calls `sys.exit()` on a missing binary, correct for the CLI
  entry point but fatal to the whole app if called from a background
  thread; `find_binary` returns `None` instead, handled here via a
  recoverable `error` signal.
- `_EligiblePanel` renders every real `MediaManifest` field: selected
  audio track (channels/codec/sample rate/bit rate, plus a "fallback
  selection" note when `selected_track_is_fallback`), duration,
  chapter count, required metadata keys found, cover-art presence, the
  storage estimate, and compatible-transcript lookup result (engine
  name/version/model when found).
- `_IneligiblePanel` lists **every** `ineligibility_reasons` entry, not
  just the first — a source can fail more than one check at once
  (confirmed live: the ineligible test file below failed both the
  container check and the AAC-codec check simultaneously), and only
  showing the first would mean a user fixes one problem, re-runs, and
  discovers a second one that was true all along.
- `can_advance()` requires a manifest that is both present and
  `eligible` — an ineligible or not-yet-inspected source cannot proceed
  to Transcript.

## A robustness gap this step's tests surfaced

`WizardWindow._on_continue()` only ever checked the Continue button's
`enabled` state — never `step.can_advance()` directly. Every previous
step (`PlaceholderStep`, `ReviewStep`) defaults `can_advance()` to
`True`, so nothing exercised the gap until Source became the first step
that can genuinely return `False`. Fixed with a defensive check inside
`_on_continue()` itself, so the step's `can_advance()` stays the single
source of truth regardless of what calls the transition (the button's
own click signal, a future keyboard shortcut, an automated end-to-end
caller). This broke six previously-passing `test_wizard_window.py`
navigation tests that had implicitly relied on step 0 never blocking
advancement; fixed by adding a `_make_source_eligible()` test helper
that injects a hand-built eligible `MediaManifest` directly, bypassing
the real file-picker/inspection flow those tests aren't about.

## A test-safety bug caught by running the new tests, not by inspection

Almost every `SourceStep` test that selects a file was, without
noticing, starting a **real** `MediaInspectWorker` background thread
pointed at a nonexistent `tmp_path` file — nothing patched the worker
class, only individual test bodies called `_on_inspect_finished()`
manually afterward. The real thread's own signal could still fire
later, asynchronously, and overwrite whatever state a test had just
asserted on, or leak into a subsequent test via a delayed
`qapp.processEvents()`. Fixed with a `_select_and_finish()` helper that
patches `MediaInspectWorker` at the class level for the selection call
itself, then delivers the result directly — the same pattern
`test_workers.py` already uses to test worker classes without a real
subprocess/network dependency underneath them.

## Verification

- 38 new tests (7 `estimate_storage_bytes`, 8 `find_compatible_transcript`,
  3 `MediaInspectWorker`, 19 `SourceStep`, 1 `wizard_window` construction
  check), all against real backend objects — real `MediaManifest`/
  `Transcript` instances, not hand-built display-only fakes.
- `black`/`flake8`/`mypy` clean on every file this ADR touches.
- **Visually verified against the live app**, end to end with real
  `ffprobe` inspection (not mocked) — launched the app, opened
  Tools → Filter Audiobook…, confirmed the Source step's empty state
  matches the design, then:
  - Synthesized a real AAC-in-M4B test file with `ffmpeg` (stereo, two
    chapters, embedded cover art, title/artist metadata) and selected
    it via the native Browse panel. The eligible panel rendered every
    field correctly — audio track, chapter count, metadata keys, cover
    art, storage estimate, "no saved transcript" — and Continue became
    enabled.
  - Synthesized a second file with an MP3 audio stream inside a
    mismatched container (renamed extension) and selected it the same
    way. The ineligible panel correctly listed **both** simultaneous
    failure reasons (container mismatch and non-AAC codec), and
    Continue stayed disabled.

Full suite: **1509 passing, 2 correctly skipped**, project-wide.

## Not yet decided

Whether/how `SourceStep`'s manifest feeds the not-yet-built Transcript
step (today nothing consumes it — `WizardWindow` holds each step
independently, same as `ReviewStep.set_scan()`'s situation in
ADR-0012); Transcript's own real design, which hasn't had a wireframe
pass yet.
