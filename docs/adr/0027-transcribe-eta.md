# ADR-0027: Estimated remaining time on the Transcribe step

**Status:** Implemented.

## Context

The Transcribe step already shows elapsed time while a transcription job
runs, but nothing about how much longer it will take. The User asked for
a rough estimate next to it — explicitly not expecting exact prediction,
since transcription speed depends heavily on the machine (GPU vs. CPU
alone is a multi-x swing, per ADR-0001's own benchmark), just something
reasonable to start with that gets more accurate once the job itself
produces real timing data.

Progress in this pipeline only updates at *chunk* boundaries
(`transcription_orchestrator.py`'s `progress_callback`), never partway
through one — whisper.cpp gives no intermediate signal. Chunks are also
chapter-aligned and not uniform length (a 3-minute chapter and a
45-minute chapter can each be "one chunk"), so any estimate has to weigh
by each chunk's own audio duration, not by chunk count the way the
existing progress bar does.

## Decision

**Two-phase estimate, both computed entirely from data the step already
has — no worker or orchestrator changes:**

1. **Cold start** (no chunk has completed yet in this run): seed the
   estimate from a small hardcoded per-model table
   (`_DEFAULT_REALTIME_MULTIPLIER`) — a rough, explicitly-not-calibrated
   guess, not a measurement.
2. **Live refinement**: once any chunk in *this run* completes, replace
   the guess with a real rate — audio-ms actually transcribed so far
   this run, divided by wall-clock-ms it took — and use that rate against
   the remaining audio (weighted by each remaining chunk's own duration,
   from the `ChunkPlan`s the step already builds for the progress
   label).

**"This run," not "this job," matches `_start_time`'s own existing
convention** — elapsed already resets on every resume rather than
tracking the job's full lifetime, so the ETA's rate measurement resets
the same way: a rate measured before a pause (possibly on a differently
loaded machine, or just stale) never carries into a fresh resume's
estimate. What *does* carry over regardless of resume is how much actual
audio work remains — that's a property of the job, not the run.

**A countdown that only re-anchors on real data, not a value recomputed
every second.** The 1-second timer that already ticks Elapsed down
(er, up) just decrements a previously-computed remaining-seconds value;
the underlying estimate is only recalculated when a chunk actually
finishes. Recomputing `processed / elapsed` every second instead would
make the estimate visibly *worsen* while waiting on the current
in-flight chunk (elapsed keeps growing against audio not yet credited),
then jump back up the instant that chunk completes — a real behavior,
but a confusing one to watch, versus a plain countdown that only
corrects when new information actually arrives.

## What changed

- `transcribe_step.py`: `_DEFAULT_REALTIME_MULTIPLIER` (base.en: 15x,
  small.en: 6x, both explicitly rough) plus a fallback constant;
  `_chunk_audio_durations_ms()` (each `ChunkPlan.end_ms - start_ms`,
  the actual span whisper.cpp runs over, including its overlap padding);
  `_recompute_eta()`/`_est_remaining_display_text()`; new
  `_segment_start_chunk_count`/`_segment_processed_audio_ms` bookkeeping
  set in `_start_worker` and updated in `_on_progress`. A new label sits
  next to Elapsed in the running panel, refreshed on the same tick and
  on every progress event.

## What this ADR does not do

No cross-job/cross-machine learning — the default multipliers are fixed
constants, not calibrated from this user's own prior runs. That would
make cold-start estimates more accurate over time, but is more machinery
than a "rough estimate, refined once real data exists" feature calls
for; the User explicitly chose the simpler version when offered both.

## Verification

New `TestEstimatedRemaining` class in `test_transcribe_step.py`: cold
start reflects the chosen model's own default multiplier (and differs
between base.en/small.en on identical audio); a completed chunk's real
measured rate overrides the default (verified with a controlled
elapsed-time and chunk-duration setup, not real wall-clock timing); a
resumed job seeds `_segment_start_chunk_count` from prior progress
without letting pre-pause progress count toward this segment's own rate;
no estimate is shown before a run exists. Full suite: 1722 passed, 2
skipped (5 new tests); `black`/`flake8`/`mypy` clean (two pre-existing
mypy findings in this test file, both in untouched helper functions,
unrelated to this change).
