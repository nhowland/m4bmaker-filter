# ADR-0015: Transcribe step — PySide6 implementation

**Status:** Implemented and verified. Ports the wireframe worked out
interactively for this step — Ready/Running/Paused/Needs attention,
chapter-sized chunking, GPU acceleration, pause-latency deprioritized —
to real, tested, visually-verified code, following ADR-0012/0013/0014's
established pattern. The chunking and GPU decisions themselves were
already made and documented in ADR-0001's amendments (2026-08-25); this
ADR is the implementation that puts them into real code for the first
time.

**Related PRD items:** §7.2 stage 3 ("display durable progress and user
controls"); §11.2 (job state machine, recoverable states); §11.3
(transcription durability); D-15/D-16/D-17 (pause, pause latency —
revised, restart behavior).

## Scope

One wizard step (`TranscribeStep`), replacing the Transcribe placeholder
— the wizard's fourth fully-built step, wired directly to its immediate
predecessor (Source→Transcript→Transcribe are now three real, connected
steps in a row). One new worker (`TranscribeWorker`). Three small,
additive backend changes: `chunking.py` gains the production constants
and two pure helper functions ADR-0001 already specified; `transcript_engine.py`
stops always passing `--no-gpu` (ADR-0001's GPU reversal, implemented
here for the first time); `transcription_orchestrator.run_transcription_job()`
gains an optional `progress_callback` parameter so a GUI worker can relay
live progress without polling the job store from a second connection.

## `chunking.py` additions

- `PRODUCTION_CHAPTER_CHUNK_MS` (45 min), `PRODUCTION_CHAPTERLESS_CHUNK_MS`
  (15 min), `PRODUCTION_OVERLAP_MS` (10s) — the constants ADR-0001's
  "Chunking strategy" section already specified, given a real home.
- `default_chunk_params(chapters) -> (chunk_ms, overlap_ms)` — the one
  place that decision lives; `default_chunk_plan(duration_ms, chapters)`
  builds on it for the common case of wanting the actual plan.
- `chapter_for_chunk(chunk, chapters) -> ChapterInfo | None` — the
  correlation helper the Transcribe wireframe's "Chapter N of M" label
  needed. Unambiguous by construction: `_chapter_segments()` already
  scopes each chapter's own subdivision strictly to that chapter's span,
  so a chunk's owned range never straddles two chapters.

## `TranscribeWorker`: three start modes, one stop mechanism

`fresh` (create the job, `QUEUED`→`PREPARING`), `resume` (an existing
`PAUSED` job, →`RESUMING`), `retry` (an existing `NEEDS_ATTENTION` job,
→`QUEUED`→`PREPARING` — the only transitions PRD §11.2's matrix permits
out of that state). Pause and Cancel share one underlying mechanism (the
`should_pause` callback `run_transcription_job()` already checks at each
chunk boundary); Cancel just means "and transition to `CANCELLED`, not
`PAUSED`, once it actually stops." An exception the orchestrator doesn't
itself handle (anything but the expected `TranscriptionPaused`) gets the
job explicitly transitioned to `NEEDS_ATTENTION` by the worker before the
signal fires — PRD §11.2's "every recoverable state needs a User action"
requirement, not a dead end.

## `TranscribeStep`: four states, one entry point, real cross-restart resume

`set_transcript_choice(manifest, model_spec)` — called by the shell with
Transcript's own manifest and chosen model when the User continues past
it (never called on the "reuse existing" skip path, which has nothing to
transcribe). Checks the real job store for an existing incomplete job
matching this exact source's fingerprint *first* — if one is `PAUSED` or
`NEEDS_ATTENTION`, the step opens directly into that state instead of
offering to start fresh, the real substance behind "durable across app
restarts," not just within one session. Guarded against a real re-entry
bug this round's own tests caught: calling `set_transcript_choice` again
with the same fingerprint while already running or completed is a
no-op — without that guard, simply going Back and Continue again would
silently re-run the job-store lookup and clobber real in-flight progress.

- **Ready to start** — nothing runs until confirmed; Start is its own
  explicit action, matching every other commit-point in this wizard.
- **Running** — real `JobRecord.progress_message`/`.progress_fraction`,
  the friendly chapter label via `chapter_for_chunk()`, Pause and Cancel
  as distinct actions, a live elapsed-time tick.
- **Paused** — real `JobState.PAUSED`; Resume re-enters at the first
  uncommitted chunk.
- **Needs attention** — Retry or Cancel Job, not a dead end.
- **Completed** — `can_advance()` becomes `True`; nothing downstream
  consumes the produced `Transcript` yet (Profile is still a
  placeholder), the same situation `ReviewStep.set_scan()` and
  `TranscriptStep.chosen_model` were both in before their own
  predecessors existed.

"Est. remaining" — flagged as explicitly not built in the wireframe
review — is still not built. No throughput data exists to compute it
from without inventing an assumption, and that was the deliberate call
made at the time, not an oversight here.

## Verification

- 41 new tests this round (10 for the two new chunking helpers plus
  their shared params function, 1 for `progress_callback`, 9 for
  `TranscribeWorker` covering all three start modes and every terminal
  signal, 21 for `TranscribeStep` covering every state and the re-entry
  guard) — all against real backend objects (real `JobStore`, real
  `Transcript`/`TranscriptSource` construction), `black`/`flake8`/`mypy`
  clean. Full suite: **1587 passing, 2 correctly skipped**, project-wide.
- **Visually verified against the live app, with a real GPU-accelerated
  transcription run that actually completed** — advanced through Source
  and Transcript into the real Transcribe step and clicked Start against
  a real synthesized `.m4b` (this fork's pre-existing tiny eligibility
  fixture): a genuine, unexpected error surfaced immediately —
  `[Errno 2] No such file or directory: '.../result.json'`, whisper-cli
  silently producing no output against that fixture's near-empty encoded
  audio (a known artifact of this specific ffmpeg build's muxing
  behavior with lavfi-generated sources, already documented in ADR-0013).
  The **Needs Attention** state handled it exactly as designed: real
  error message, Retry and Cancel Job both present, no crash. Clicking
  Retry re-ran cleanly (a fresh temp directory, confirmed via the changed
  path in the error message) and failed again identically, as expected
  against the same fixture — proving the retry mechanism's own plumbing
  (`NEEDS_ATTENTION`→`QUEUED`→`PREPARING`) works independent of whether
  the underlying error resolves.

  Synthesized a second, real-speech `.m4b` (macOS `say`, the same
  "legally clean, no copyrighted audio" approach ADR-0001's own G3 spike
  used, with two real chapters) and ran it through the same live path:
  **Start → Running → Completed**, real chapter-aligned chunking (2
  chunks, one per chapter), real `ffmpeg` extraction, real GPU-accelerated
  `whisper-cli` inference, a real `.m4bt.json` written to this machine's
  actual transcripts directory. Read it back directly: `status: complete`,
  `engine: whisper.cpp 1.9.2 ggml-base.en`, and real transcribed text
  matching the spoken audio almost exactly — *"Chapter 1 the old
  lighthouse stood alone on the rocky point its light long since gone
  dark chapter 2 inside dust covered every surface und ist urbed for
  decades"* (the one mis-split, "und ist urbed" for "undisturbed," is an
  ordinary whisper.cpp tokenization artifact at a chunk boundary, not a
  pipeline defect). Continue enabled immediately on completion, exactly
  as designed.

  Both real audio fixtures and their generated artifacts were removed
  from scratch space after verification; the transcript file this
  produced remains at this machine's real transcripts directory as a
  genuine, correctly-produced artifact, not test pollution.

## Not yet decided

Whether/how `TranscribeStep.transcript` feeds the not-yet-built Profile
step; Profile's own real design, which hasn't had a wireframe pass. UI
surfacing of which backend (CPU/GPU) is active during a run — flagged as
a diagnostics nicety in ADR-0001, still not built. Verification of the
GPU-reversal and chapter-sized chunking decisions on non-Apple-Silicon
hardware, per ADR-0001's own disclosed limits.
