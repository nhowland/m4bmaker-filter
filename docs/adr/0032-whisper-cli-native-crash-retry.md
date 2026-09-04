# ADR-0032: Retry whisper-cli on native crash — non-deterministic, not extraction truncation

**Status:** Implemented and verified.

## Context

ADR-0031 shipped a guard against truncated chunk extraction, on the theory
that a truncated/corrupt-header WAV was crashing whisper.cpp's decoder at
chunk 50 of a real 26.8-hour job. The User retried after that fix shipped
and hit the **identical** crash again, at the same chunk, unchanged.

That recurrence directly falsified the extraction-truncation theory. To
find out what was actually happening, a temporary, race-free capture was
added directly in `run_transcription_job` (an unconditional `shutil.copy2`
of each chunk's WAV to a stable path immediately after `_extract_chunk_wav`
returned — i.e. after ADR-0031's own completeness check had already
passed) rather than the earlier filesystem-watcher approach, which polled
every 50ms and — this is the real finding — had been racing ahead of
ffmpeg's write and capturing a still-in-progress file. That race is what
produced ADR-0031's "truncated, placeholder-header" artifact: not a
production bug, a side effect of the diagnostic tool itself.

The User retried once more with the new capture in place. Two things fell
out immediately:

1. **Chunk 50 is ~23.6 minutes (1,414,296 ms) of audio**, not the ~3-minute
   window ADR-0031's reproduction attempts had used. Chapter-aligned
   chunking makes one chunk per chapter, and this fork's own
   `PRODUCTION_CHAPTER_CHUNK_MS` ceiling (45 minutes) doesn't subdivide a
   chapter this short — so every earlier "clean reproduction" in this
   investigation had silently been testing the wrong audio the whole time.
2. Feeding this real, complete, correctly-sized capture straight to
   `whisper-cli` with the real production flags reproduced the crash on
   the first attempt — a **different** failure than the earlier "SIGABRT
   during WAV decode" theory: `WHISPER_ASSERT:
   .../whisper.cpp:8957: filter_width < a->ne[2]`, deep inside whisper.cpp's
   own DTW alignment code, well past decoding.

Empirical follow-up (binary-searching truncated prefixes of the same file,
then rerunning the untrimmed file repeatedly) showed this is **not** a
deterministic function of duration or content: 10/15/18/20/21/22/23-minute
prefixes all succeeded, several trims within seconds of the full length
succeeded, and — critically — 5 immediate reruns of the exact same
untrimmed file, same flags, all succeeded too. The very first run had
crashed; every rerun after did not. Isolated standalone testing produced
zero reproductions in roughly a dozen trials, while the real job had
failed at this exact chunk on three separate, real, in-app attempts. That
gap (0-in-many isolated vs. 3-for-3 in-app) suggests something about
running the same GPU-backed inference alongside the app's own Metal
usage — not this book's audio, not chunk duration, not extraction — but
its exact mechanism inside whisper.cpp/ggml's Metal backend was not
pursued further; that's upstream territory well past this fork's scope.

## Decision

**Retry the `whisper-cli` invocation itself** (not re-extraction — ADR-0031
already covers that separately and remains in place for the real,
different failure mode it targets) up to `_WHISPER_MAX_ATTEMPTS = 3` times
before raising `WhisperTranscriptionError`. This is a direct, evidence-based
mitigation: the same input that crashed on attempt 1 transcribed correctly
on 5/5 immediate reruns in real testing, so a same-process retry has a real
and high chance of clearing it, at the cost of a little latency only in the
failure case (~19s per attempt for this book's longest chunk).

No attempt is made to identify or work around the underlying Metal/ggml
mechanism — that's a whisper.cpp/ggml-level concern, and chasing an
apparent GPU-backend race or resource-contention issue inside a third-party
native dependency is past what this fork can reasonably own.

## What changed

- `transcript_engine.py`: `_WHISPER_MAX_ATTEMPTS = 3`, `run_whisper()`'s
  single `subprocess.run` call wrapped in a retry loop — success returns
  immediately on any attempt; the final attempt's failure raises
  `WhisperTranscriptionError` with the attempt count included, so a
  surfaced failure is now distinguishable from ADR-0031's extraction
  failure and reports that retries were already exhausted rather than
  looking like a first-try crash.
- `transcription_orchestrator.py`: the temporary race-free debug capture
  used for this investigation was removed after it had served its purpose.

## Verification

**Unit tests** (`TestRunWhisper`, `test_transcript_engine.py`): a
transient failure on the first two attempts followed by success on the
third is recovered transparently (exact call count asserted); persistent
failure across all attempts raises with the attempt count in the message
after exactly `_WHISPER_MAX_ATTEMPTS` calls, not fewer or more. The
existing non-zero-exit test continues to pass unmodified (it uses a fixed
`return_value`, so it now exercises all three retry attempts before
raising — still correct, since the assertion only checks the message
substring). 2 new tests; full suite 1759 passed, 2 skipped;
`black`/`flake8`/`mypy` clean.

**Real-data verification, this investigation's own evidence:** the retry
behavior's justification *is* the real-world reproduction data above —
the same real, complete, correctly-sized chunk 50 audio (captured
race-free from the actual production pipeline) crashing once and then
succeeding 5/5 times immediately after, using the real `whisper-cli`
binary and the real production DTW flags throughout, not a synthetic
stand-in.
