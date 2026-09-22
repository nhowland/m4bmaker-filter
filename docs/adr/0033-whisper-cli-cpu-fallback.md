# ADR-0033: CPU-only fallback when whisper-cli's GPU backend keeps crashing

**Status:** Implemented and verified.

## Context

ADR-0032 added a 3-attempt retry around `whisper-cli` after real testing
showed a native GPU-backend crash (`WHISPER_ASSERT: filter_width <
a->ne[2]`) clearing on an immediate rerun of the exact same input. The
User retried the real job after that fix shipped and hit the identical
crash on **all 3** attempts this time — plain retry alone isn't reliable
enough inside the app's real execution conditions, even though isolated
standalone testing never reproduced more than one failure in a row.

To find a more dependable mitigation, the real crashing chunk (captured
during ADR-0032's investigation) was run with `--no-gpu` — CPU-only —
three times in a row, still with `-dtw -nfa`. All three succeeded, each
taking about 46s versus roughly 19s on GPU (~2.4x). That gap is
consistent with the earlier finding that this is GPU/Metal-backend
specific rather than about the audio content or DTW itself: forcing CPU
execution sidesteps whatever is unstable in the GPU path entirely.

## Decision

**After `_WHISPER_MAX_ATTEMPTS` GPU attempts are exhausted, make one final
CPU-only (`--no-gpu`) attempt before raising.** The fallback keeps every
other flag unchanged, including `-dtw`/`-nfa` when DTW mode was
requested — `--no-gpu` only selects the compute backend and has no effect
on DTW's alignment math, so the fallback costs real wall-clock time on
whichever single chunk needs it (~2.4x, i.e. tens of seconds against a
book that's hours long) but zero timing-precision quality, which is the
right trade-off given how much this project has already invested in
DTW's accuracy (ADR-0024, ADR-0025).

This is a mitigation, not a diagnosis: *why* the GPU backend is
unstable specifically inside the running app (and not in isolated
CLI testing) was not identified, and pursuing that further is
whisper.cpp/ggml-internal territory past this fork's scope. If CPU-only
also fails, that's real signal the problem isn't GPU-specific after all,
and `WhisperTranscriptionError`'s message says explicitly that both the
GPU attempts and the CPU fallback were tried, distinguishing this from
either failure mode alone.

## What changed

- `transcript_engine.py`: `run_whisper()`'s retry loop, on exhausting GPU
  attempts, now runs one additional `subprocess.run` with `--no-gpu`
  appended before raising `WhisperTranscriptionError` — the error message
  reports both the GPU attempt count and the CPU fallback's own exit code
  when both fail.
- `transcribe_step.py` (unrelated fix, found while answering the User's
  question about why the progress bar showed "Chapter 1" / "1 of XX" on
  reload): `set_transcript_choice()` only seeded `_last_progress_fraction`
  from the existing job's real progress for a `PAUSED` job, never for
  `NEEDS_ATTENTION` — the far more common state a real crash leaves a job
  in. The underlying `TranscriptionJob` itself always resumed correctly
  (driven by the job store's committed-chunk state, not this display
  value); only the progress bar and "Chapter/Section N of M" label were
  wrong, showing 0%/chunk 1 until the first new progress callback arrived
  after clicking Retry. Now seeded for both states.

## Verification

**Unit tests** (`TestRunWhisper`, `test_transcript_engine.py`): GPU
attempts exhausted then a successful CPU-only fallback recovers
transparently (exact call count and flag presence — including `-dtw`/
`-nfa` surviving onto the CPU attempt — asserted); persistent failure
across GPU attempts and the CPU fallback raises with both outcomes in the
message after exactly 4 calls, not fewer or more. The `transcribe_step.py`
fix has its own new test (`TestSetTranscriptChoiceExistingJob`,
`test_transcribe_step.py`) building a real `NEEDS_ATTENTION` job via
`JobStore` with real committed progress, then confirming the progress bar
and chapter label are correct immediately after clicking Retry rather than
starting at 0%/chapter 1. Full suite 1761 passed, 2 skipped;
`black`/`flake8`/`mypy` clean.

**Real-data verification:** the CPU-only fallback's justification is
itself real-pipeline testing — the exact real chunk captured from the
live crash, run three times with `--no-gpu -dtw base.en -nfa` against the
real `whisper-cli` binary and real model, succeeding all three times.
