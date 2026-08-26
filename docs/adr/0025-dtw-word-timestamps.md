# ADR-0025: DTW-based word timestamps, replacing whisper.cpp's default heuristic

**Status:** Implemented and verified.

## Context

ADR-0024 fixed the render pipeline's attenuation math (verified correct)
and raised `lead_padding_ms`/`tail_padding_ms` based on a synthetic-TTS
characterization of whisper.cpp's word-timestamp accuracy. The User then
rendered real audiobook chapters with those new defaults and reported
the opposite-looking symptom from the original report: silence now
consistently landing *after* the word, not before it.

Direct verification (raw-waveform energy analysis, then a spectrogram/
waveform image with the actual rendered interval boundaries drawn on
it and visually inspected) confirmed this precisely: on real narration,
the rendered interval's start fell in the *middle* of the target word
(missing its first ~150-600ms) and its end fell well *after* the word's
waveform had already tapered off, extending into subsequent speech.
This is a **materially different bias direction** than the synthetic-TTS
characterization found (which was closer to evenly mixed early/late) —
meaning ADR-0024's larger `tail_padding_ms` made an already-late interval
drag even further past the word, which is exactly "silence after the
word."

Per the User's explicit framing — accurate, reliable silencing is this
feature's entire reason to exist, and re-examining core architecture
(including the transcription engine) was explicitly on the table if
needed — this was treated as warranting a real fix at the source, not a
further padding-value adjustment layered on top of a wrong assumption.

## Decision

**whisper.cpp exposes a materially more accurate word-timing mode
(`-dtw MODEL`, Dynamic Time Warping token-level alignment) that this
fork was not using**, and the fork's own default heuristic timestamps
are what ADR-0024 already showed misplacing word boundaries by hundreds
of milliseconds on real narration. Enabling DTW is a much smaller,
more targeted change than evaluating a different transcription engine
entirely, and directly addresses the root cause (the timestamp itself)
rather than compensating for it after the fact — so it was investigated
and implemented first, per the User's own stated preference to exhaust
smaller architectural levers before a bigger one.

**Two real, non-obvious findings from getting this working, both
confirmed directly against the installed binary, not assumed from
documentation:**

1. **DTW silently produces no data at all under flash attention in this
   build.** `whisper-cli` logs `dtw_token_timestamps is not supported
   with flash_attn - disabling` and every token's `t_dtw` field comes
   back `-1` — no error, no warning surfaced anywhere else. Flash
   attention must be disabled (`-nfa`) whenever DTW is requested; these
   are now always passed together, never as two independent settings a
   caller could mismatch.
2. **DTW gives each token a single aligned start point, not a start/end
   pair.** whisper.cpp's own `t_dtw` is in 10ms units and marks where
   that token's alignment begins; a token's *end* is the *next* token's
   DTW start (tokens are contiguous in a decode sequence), falling back
   to the existing heuristic `offsets` wherever a token lacks a valid
   DTW value — either because DTW wasn't requested, or because a
   handful of boundary tokens come back `-1` even when it was (both
   observed directly in real captured output).

**Speed cost, measured directly** (5 minutes of real narrated audio,
this fork's own reference audiobook, same hardware, GPU/Metal backend
in both runs): flash-attn default heuristic timestamps, 2.4s; DTW mode
(flash attention off), 3.6s — **~1.5x slower**, not the multi-x hit
originally worried about, since ADR-0001's earlier "4.1-5.6x speedup"
figure was GPU-vs-CPU overall, not flash attention's own isolated
contribution.

## What changed

- `transcript_engine.run_whisper()` gained `dtw_model_name: str | None`
  — when given (whisper.cpp's own bare model identifier, e.g.
  `"base.en"`, not a file path), appends `-dtw <name> -nfa` to the
  whisper-cli invocation.
- New `transcript_engine.dtw_model_name_for(model_path)` derives that
  bare identifier from a model file's own path, relying on
  `model_manager.ModelSpec.filename()`'s existing `f"ggml-{name}.bin"`
  convention (`.stem.removeprefix("ggml-")`) — no new parameter needed
  threaded through every caller that already has the path.
- `transcript_engine.whisper_result_to_segment()` now prefers each
  token's DTW-derived start/end over the heuristic `offsets` wherever a
  valid `t_dtw` value exists, per token, falling back token-by-token
  otherwise. A transcript produced without DTW parses exactly as
  before — this function doesn't need to know whether DTW was
  requested, only whether each token's own data has it.
- `transcription_orchestrator.run_transcription_job()` and
  `transcript_engine.transcribe_short_audio()` — the production chunked
  path and the single-shot spike/test-harness path — both now always
  pass `dtw_model_name_for(model_path)`, so DTW is the fork's actual
  default transcription behavior, not an opt-in flag sitting unused
  anywhere.

No change to `renderer.py`, `interval_planner.py`, or the
`AttenuationSettings` defaults ADR-0024 set — this ADR fixes the input
timestamps those defaults are computed against, not the padding
arithmetic itself.

## What this ADR does not change

No GUI-facing setting to toggle DTW on/off was added — it's now simply
how transcription works, matching the "revise core assumptions where
warranted" scope the User set for this investigation, not a
configurable knob nobody asked for. No forced-alignment tool was
evaluated (the second option discussed, below DTW and above swapping
engines in cost/disruption) — DTW's own measured accuracy improvement
made that unnecessary for now.

## Verification

**Unit tests** (`test_transcript_engine.py`): `run_whisper()` omits
`-dtw`/`-nfa` by default and adds both together when
`dtw_model_name` is given; `dtw_model_name_for()` correctly strips the
`ggml-` prefix; `whisper_result_to_segment()` prefers a token's DTW
start over its heuristic offset, uses the *next* token's DTW start as
this token's end, falls back to heuristic offsets when `t_dtw` is `-1`,
and behaves identically to the pre-DTW parser when `t_dtw` is absent
entirely (verified against the existing real-captured-output fixture
unchanged). `tests/filter/` (398 passed, 2 skipped) and this project's
real CI command (933 passed, 2 skipped) both clean; `black`/`flake8`/
`mypy` clean on every file touched.

**Real-pipeline verification, not just unit tests:** all 4 real
audiobook chapters (ADR-0024's own test chapters) were re-transcribed,
re-scanned, and re-rendered through the actual production code path
with DTW now built in. All 4 passed the app's own validator. Four
real hits, across three different chapters, were checked the same
rigorous way as the original bug report was confirmed: the actual
rendered interval boundaries drawn directly onto a waveform image and
visually inspected. Where the pre-DTW render on the identical hit
showed the start marker buried in the middle of the word and the end
marker well past it, the DTW-based render shows both markers landing
at the word's actual leading and trailing edges. An automated numeric
proxy (energy-derivative onset/offset detection) was also tried across
all 23 real hits from the 4 chapters, but produced a result that
directly contradicted the visual, ground-truth check on the one hit
both methods examined — that numeric method is disclosed as unreliable
for continuous narration, not used as evidence, and the visual check is
what this ADR's verification claim rests on. A fresh before/after audio
pack (original vs. filtered, same 4 hits) was sent to the User for the
final, most reliable confirmation this whole investigation has used
throughout — their own ears, the thing that caught both the original
bug and the regression ADR-0024's padding-only fix introduced.
