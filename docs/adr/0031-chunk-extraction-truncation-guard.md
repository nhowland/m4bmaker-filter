# ADR-0031: Detect truncated/corrupt chunk extraction before handing it to whisper.cpp

**Status:** Implemented and verified.

## Context

A real transcription job (the User's own 26.8-hour book, chapter-aligned
chunking, 79 chunks) crashed at chunk 50 with a native `SIGABRT` from
`whisper-cli` — no Python exception, no error message, just the process
dying inside miniaudio's WAV decode (`read_audio_data: trying to decode
with miniaudio`), before transcription or DTW ever started.

An initial reproduction attempt using a manual re-extraction of the exact
same `[64538509, 64719904]` byte range appeared to work fine — a false
signal, since it omitted the `-dtw -nfa` flags the real job always runs
with. Re-running with the correct flags still didn't reproduce the crash,
and turned up an unrelated whisper.cpp DTW anomaly (deterministic segment
duplication) that doesn't explain a decode-time abort either.

Rather than keep guessing, a live filesystem watcher was set up to capture
the *actual* chunk file the real job wrote to disk at the moment of a real
Retry-triggered crash. Direct inspection of that captured file against a
clean manual re-extraction of the identical range was conclusive:

| | Manual re-extraction | The actual crashing file |
|---|---|---|
| Size | 5,805,224 bytes | 3,407,872 bytes (~59%) |
| Audio duration | 181.395s (correct) | 106.478s (missing ~75s) |
| RIFF declared size | `0x5894a0` (correct) | `0xFFFFFFFF` (placeholder) |

ffmpeg writes a placeholder size into the RIFF header when it starts
encoding to a seekable file, then seeks back and patches it with the real
final size once encoding completes. A file left with the placeholder,
combined with a byte count far short of what was requested, means the
real extraction's `ffmpeg` process was cut short before it could finish —
yet still returned exit code 0, since `_extract_chunk_wav`'s only success
check was the return code. The truncated, mislabeled file was then handed
straight to whisper.cpp, whose decoder had no graceful way to handle a WAV
lying about its own size and aborted natively instead.

This is not a whisper.cpp, DTW, or model bug — it's a silent extraction
failure one layer upstream that nothing was checking for.

## Decision

**Verify actual audio duration on disk after every extraction, not just
ffmpeg's exit code — and never trust the WAV header's declared size to do
it**, since the header is exactly what's unreliable in the failure mode
being guarded against. `_actual_wav_duration_ms()` parses the RIFF chunk
structure directly: chunks written before any audio streams in (`fmt `,
`LIST`/`INFO`) are complete and trustworthy by the time ffmpeg starts
writing them, so their declared sizes are used to skip past them, but the
`data` chunk's own declared size is ignored entirely — everything from its
payload start to the real end-of-file is counted instead, exactly as
`ffprobe` was independently observed to do when it correctly reported the
real crashing file's true (truncated) 106.478s duration despite its
corrupt header.

**Retry once, then fail loudly, instead of silently accepting a bad
file.** The manual re-extraction of the identical byte range succeeded
cleanly, which is evidence this is a transient condition (most plausibly
some form of resource contention with the long-running, GPU-bound
whisper-cli process from earlier chunks in the same job) rather than
something specific to that time range. A single retry costs little and
plausibly clears it; a second failure raises a clear `RuntimeError`
describing exactly the shortfall, which surfaces through the job's
existing `NEEDS_ATTENTION` → Retry flow rather than an opaque native
crash. `_CHUNK_DURATION_SHORTFALL_TOLERANCE_MS` (3000ms) stays generous
relative to `_extract_chunk_wav`'s own documented input-seek imprecision
(tens of ms at most) while unambiguously catching a 75-second shortfall.

## What changed

- `transcription_orchestrator.py`: `_actual_wav_duration_ms()` (new,
  pure/no I/O side effects beyond reading the file), wired into
  `_extract_chunk_wav()` behind a small retry loop
  (`_CHUNK_EXTRACTION_MAX_ATTEMPTS = 2`). ffmpeg's own non-zero-exit
  failure path is unchanged — it still raises immediately, since a retry
  there was already known-unhelpful before this ADR.

## What this ADR does not resolve

*Why* the real extraction's `ffmpeg` process gets cut short in the first
place is not identified — only that it happens, rarely, and that a clean
re-extraction of the same range does not reproduce it. If this guard ever
exhausts its retry in practice, that occurrence is itself a useful new
data point for investigating the underlying cause further.

## Verification

**Unit tests** (`TestActualWavDurationMs`, `TestExtractChunkWavValidation`,
`test_transcription_orchestrator.py`): correct-duration reporting on a
well-formed WAV; a placeholder (`0xFFFFFFFF`) header still yields the true
byte-based duration; a `LIST`/`INFO` chunk before `data` is parsed through
correctly; a non-WAV file returns `None`; a genuinely truncated file
reports its true short duration. For extraction itself: a complete
extraction succeeds without retry; a minor shortfall within tolerance is
accepted; a truncated first attempt recovers via retry; repeated
truncation raises a clear error after the retry budget; ffmpeg's own
non-zero exit still raises immediately without retry. 10 new tests; full
suite 1757 passed, 2 skipped; `black`/`flake8`/`mypy` clean.

**Real-data verification, the actual bug's own evidence:** ran
`_actual_wav_duration_ms()` directly against both artifacts recovered
during the live investigation — the real captured crashing file and the
clean manual re-extraction of the identical `[64538509, 64719904]` range.
Result: the real crashing file is correctly computed at 106,478ms actual
against 181,395ms expected (74,917ms shortfall — well past the tolerance,
correctly rejected); the clean re-extraction computes at exactly
181,395ms (zero shortfall, correctly accepted) — an exact match to the
independently-obtained `ffprobe` durations from the original
investigation, using the real files the bug actually produced rather than
a synthetic stand-in.
