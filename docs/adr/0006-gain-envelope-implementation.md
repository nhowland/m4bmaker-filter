# ADR-0006 (O-04): Gain-envelope implementation and timing/ramp behavior

**Status:** **Resolved by direct empirical spike (2026-08-24).**
Recommendation: precomputed sample-accurate gain envelope (generated
directly, not via ffmpeg expressions) applied via ffmpeg's `amultiply`
filter in a single pass. This solves gating, fade smoothness, and
interval-count scaling simultaneously, with real measurements at up to
~950 intervals on an hour of audio. Getting here required ruling out
four other approaches first, including two rounds of self-caught
measurement error — the full trail is kept below because the ruled-out
approaches and the mistakes are as instructive as the answer.
**Related PRD items:** §8.1, §8.3, §8.4, §8.5, O-04

## What was actually tested

All findings below come from running real `ffmpeg` 9.0.1 commands against
real audio (a generated 20-second 440Hz sine tone for precision testing,
and a real 51-minute 440Hz tone for scaling tests — a constant-amplitude
signal was used deliberately so RMS measurement via `ffmpeg`'s own
`astats` filter gives an unambiguous, reproducible pass/fail signal), not
reasoned about from documentation. Two false starts are recorded because
they produced genuinely misleading signals worth warning a future
implementer about.

### False start #1: `astats` without `reset=1` gives wrong readings

Early measurements used `ffmpeg -i out.wav -ss A -to B -af astats -f null -`
and grabbed the *first* "RMS level" line from the output. This is wrong:
without `reset=1`, `astats` accumulates across internal reporting periods,
and the first printed line is not necessarily the value for the requested
slice. This produced a false conclusion that `enable='between(t,...)'`
does not work at all. Correcting the measurement to
`astats=reset=1` and reading the **last** printed "RMS level" line
reversed that conclusion — the mechanism works. **Lesson for anyone
extending this work: always use `astats=reset=1` and read the final
line, never the first.**

### Finding #1 (confirmed working): `volume=enable='between(t,a,b)':volume=FLOOR`

A single `volume` filter instance with a timeline `enable` expression
correctly gates attenuation to the specified window. Verified against a
20s tone with two separate windows summed via `+` in one expression
(`between(t,5,6)+between(t,10,10.3)`) — RMS dropped to -81dB inside both
windows and stayed at baseline (-21dB) outside.

### Finding #2 (real precision limit): ~100-150ms frame-granularity smear

Scanning the boundary of an intended `[10.0, 10.3)` window at fine
resolution showed the actual attenuated region was closer to
`[9.9, 10.17)` — shifted and smeared by roughly 100-150ms relative to the
nominal boundary. This traces to the `volume` filter's default internal
frame size (~4096 samples ≈ 93ms at 44.1kHz): the timeline `enable`
expression is evaluated per-frame, not per-sample, so a frame is either
entirely gated or entirely passed based on where its start timestamp
falls. **This directly conflicts with PRD §8.3's padding range starting
at 0ms and §8.4's 5-50ms fade windows** — both assume finer-than-93ms
control. Forcing a smaller frame size via `asetnsamples=n=64` (~1.45ms
frames) tightened this to roughly 10-15ms in a follow-up test, still not
sample-accurate but much closer to what the PRD's ranges need. **Not yet
verified at the extremes of PRD's declared ranges (a 5ms fade, a 0ms
lead-padding boundary)** — flagged as open below.

### Finding #3 (critical, confirmed by measurement): interval-count scaling

This was the single biggest open risk flagged in the original repository
review, and it is now backed by real numbers, not speculation. Three
candidate approaches were built and run against a real 51-minute audio
file with realistically-spaced attenuation intervals:

| Approach | 300 intervals | 600 intervals | 1000 intervals |
|---|---|---|---|
| `atrim`+`volume`+`concat` (601 concat inputs at 300) | **Did not finish in 3 minutes** | not tested (already disqualified) | not tested |
| Single `volume` filter, one giant `between()`-sum expression | **Crashed**: "Error when evaluating the expression" / "Cannot allocate memory" | not tested (already disqualified) | not tested |
| **Chained `volume` filter instances** (one filter per interval, comma-separated) | **15.7s** (197x realtime) | **58.0s** (53.5x realtime) | **Did not finish in 2 minutes** |

The chained-filter approach is the clear winner of the three, and the
only one that survives past a few hundred intervals at all. Its own
scaling is **not linear** — 2x the intervals (300→600) took roughly 3.7x
the time, consistent with the low-hundreds-to-1000 timeout — so it has
its own practical ceiling somewhere between ~600 and ~1000 chained
filter instances on this reference hardware. **This is a hard finding,
not a soft one**: the `atrim`/`concat` approach that seems most obviously
"correct" (it produces an exact-boundary cut with no frame-quantization
smear, confirmed separately — silence landed at exactly the requested
sample in a small-scale test) is **operationally unusable** at realistic
interval counts. This reverses the naive intuition and is exactly the
kind of thing PRD §17.1 rule 9 exists to catch before it's built into
production code.

### Finding #4 (unresolved, real negative result): fade ramps did not render as smooth ramps

A piecewise-linear volume expression (ramp down over 50ms, floor, ramp
back up over 50ms) was tested inside a single chained `volume` filter
instance. At the default frame size, the entire 50ms fade collapsed into
a single abrupt jump (one frame boundary) rather than a perceptible ramp
— exactly the audible-click risk PRD §8.4 explicitly requires avoiding.
Forcing a much smaller frame size (`asetnsamples=n=64`) improved this to
a coarse 2-3 step staircase across the fade window, still not a smooth
continuous ramp. **This is not fixed as of this ADR.** Two follow-up
directions are recorded but neither has been tested:

1. Push the forced frame size even smaller (single-digit sample counts)
   and re-measure — may hit a genuine ffmpeg internal minimum, or may
   start meaningfully hurting the scaling numbers in Finding #3 (smaller
   frames mean proportionally more per-frame overhead across the whole
   file, not just the fade windows — untested how much).
2. Use ffmpeg's dedicated `afade`/`afade=t=out` filter (built for exactly
   this purpose, known to produce genuinely smooth curves) applied only
   to two short `atrim`-extracted slices per interval — the fade edges,
   not the whole interval — keeping the `atrim` cost small and bounded
   (2 tiny slices per interval, not one slice per interval as in the
   disqualified Finding #3 approach) rather than paying the full
   `atrim`+`concat` penalty already shown to be unusable.

## Follow-up round (same session): fade smoothness solved in isolation, but not yet at scale

Two more approaches were tested after the above was written.

### Finding #5 (confirmed working, in isolation): `asplit`+`afade`+`volume`+`amix` gives a genuinely smooth ramp to floor

Splitting one interval's audio into two parallel copies — one faded to
silence via the purpose-built `afade` filter, one held at a constant
floor level via `volume` — then recombining with `amix=normalize=0`
produces a real, gradual, sample-smooth ramp from full volume down to
the configured floor (not to absolute silence) and holds there:
measured `-24 → -27 → -31 → -38 → -60 → -81dB` descending smoothly across
a 0.5s fade-out, confirmed both in isolation and spliced back into a
20s file via the same `atrim`+`concat` structure as Finding #3's
disqualified approach. `afade` itself, tested alone, is unambiguously
sample-accurate (no frame-quantization staircase) — the frame-granularity
problem in Finding #4 was specific to the generic `enable`+expression
mechanism, not inherent to gain ramping in ffmpeg generally.

**The catch: this fix requires the same `atrim`+`concat`-per-segment
structure already disqualified by Finding #3's scaling data.** Smooth
fades and fast scaling to hundreds of intervals have not yet been
achieved by the same approach simultaneously. Splicing in only the tiny
fade edges (5-50ms) rather than the whole interval would reduce data
volume per segment but not segment *count* — and Finding #3's slowness
tracked with segment count, not segment duration, so this is not
expected to help without being tested (not yet tested).

### Finding #6 (promising, unresolved): precomputed sample-accurate envelope + `amultiply`

A structurally different approach was tried: generate the entire gain
envelope as its own audio signal (a second, separate PCM file the same
length as the source, valued ~1.0 at baseline and ramping to the floor
at each interval, computed sample-by-sample in Python — no ffmpeg
expression involved at all), then multiply it against the source with
ffmpeg's `amultiply` filter in a single pass. This is structurally
attractive because it sidesteps all three problems at once: no
expression for ffmpeg's parser to choke on (Finding #3), no per-interval
`atrim`/`concat` segment (Finding #3/#5's scaling cost), and the ramp
shape is exactly whatever was computed in Python, at full sample
accuracy (no frame quantization).

A first test of this (20s tone, 2 intervals, hand-rolled linear-ramp
envelope generated with the stdlib `wave`/`struct` modules — no numpy
available in this environment or currently a project dependency)
appeared **not** to show a smooth ramp in a windowed-RMS scan — it looked
like it jumped from partial attenuation to full floor within about 10ms
of a 50ms intended ramp.

**This turned out to be a third instance of the same measurement error
that produced this investigation's first false start.** Windowed RMS via
`astats` is a poor tool for judging a linear gain ramp applied to a pure
440Hz tone: a 5ms measurement window only spans about 2.2 cycles of the
tone, which is too short and non-integer a window for RMS to give a
reliable, smooth-looking reading regardless of how smooth the underlying
gain change actually is — an artifact of the *measurement*, not the
*signal*. Switching to direct raw-sample comparison (reading both input
files' actual integer sample values with Python's `wave`/`struct` and
checking `output[n] == round(source[n] * envelope[n] / 32768)` at every
sample near a boundary) showed **exact, sample-for-sample agreement with
no rounding surprises**, and the envelope values themselves descended
smoothly and monotonically exactly as generated (32767 → 32752 → 32737 →
32722 → 32708 → ... — a clean linear ramp, confirmed by inspection of
the actual numbers, not inferred from a derived metric). `amultiply`
was correct the entire time; the tool used to check it was not.
**Lesson reinforced a second time: verify audio-processing correctness
against raw sample data when precision below the tens-of-milliseconds
range matters, not windowed RMS.**

### Finding #7 (confirmed, decisive): the envelope approach scales independently of interval count

With the sample-level correctness question closed, the scaling question
was retested properly. Envelope generation itself needed to avoid a
naive per-sample Python loop over a multi-hour signal (a 20-hour book is
~3.2 billion samples) — solved by bulk-filling the (overwhelmingly
constant, gain=1.0) baseline via `array('h', [32767]) * n_samples`
(a single C-level operation) and only running an actual per-sample
Python computation across the small fraction of samples that fall
inside a fade window. Real measurements on the same 51-minute reference
file used throughout this ADR:

| Intervals | Envelope generation | `amultiply` render pass |
|---|---|---|
| 300 | 0.70s | **0.52s** |
| 951 | 2.03s | **0.51s** |

The render pass itself is effectively **constant time regardless of
interval count** — expected, since it is one linear pass multiplying two
equal-length sample streams, with no per-interval graph node, expression
term, or concat segment at all. This is roughly **30x faster than the
already-good chained-`volume`-filter approach at 300 intervals**, and
unlike every other approach tested in this ADR, it does not degrade or
fail at 951 intervals — it was the *only* approach where 1000-ish
intervals were not a problem at all.

## Recommendation (resolved)

**Use a precomputed sample-accurate gain envelope, generated directly in
Python (not via any ffmpeg expression), applied with `amultiply` in a
single pass.** This is the only approach tested that achieves all of:
correct gating, genuinely smooth configurable-floor fades, and scaling
that does not depend on interval count at all. It also sidesteps two
hard failure modes found elsewhere in this ADR outright: there is no
expression for ffmpeg's parser to choke on (Finding #3's crash), and no
per-interval filter graph node or concat segment to accumulate overhead
(Finding #3 and #5's scaling cost).

Concretely, for the Renderer (a later gate, not built yet):

1. Given a `RenderPlan` (`m4bmaker/filter/models.py`, from the already-
   built Interval Planner), generate an envelope array the length of the
   source's sample count: bulk-fill at "full gain," then for each
   `RenderInterval` compute only its fade-in ramp, floor hold, and
   fade-out ramp samples directly (the approach above, not an ffmpeg
   expression).
2. Extract the source's primary audio track to PCM (already a planned
   step per ADR-0002/O-03) and the envelope to a matching-format PCM/WAV.
3. `ffmpeg -i <source_pcm> -i <envelope> -filter_complex
   "[0:a][1:a]amultiply[out]" -map "[out]" ...` into the AAC encode step
   already designed in ADR-0002.

### Approaches ruled out, and why (kept for anyone tempted to retry them)

- **Single `volume` filter, one giant OR-combined `between()`
  expression**: crashes ffmpeg's expression parser past ~300 terms
  ("Cannot allocate memory"). Do not revisit without a specific ffmpeg
  version fix confirmed.
- **`atrim`+`volume`+`concat`, one segment per interval**: gives exact
  sample-accurate boundaries but does not finish in 3 minutes at just
  300 intervals (601 concat inputs). The scaling cost tracks segment
  *count*, not segment *duration* — narrowing segments to just the fade
  edges would not be expected to help (not separately retested, since
  Finding #6/#7 made it moot).
- **Chained `volume=enable='between(t,...)':volume=FLOOR` filter
  instances (one per interval)**: works, and was the best of the
  ffmpeg-expression-based options (600 intervals in 58s) — but has its
  own non-linear scaling ceiling around 600-1000, has ~100-150ms
  boundary smear from frame-quantization, and cannot produce a smooth
  fade at all (Finding #4) without additional machinery. Superseded by
  the envelope approach on every axis; no longer recommended now that
  Finding #6/#7 exist.
- **`asplit`+`afade`+`volume`+`amix` per interval, spliced via
  `atrim`+`concat`**: genuinely smooth, sample-accurate fades (Finding
  #5) — but inherits the disqualifying `atrim`+`concat` scaling cost.
  Superseded by the simpler envelope approach, which achieves the same
  smoothness without needing `atrim`/`concat`/`asplit`/`amix` at all.

## What remains before implementation

The core mechanism is settled with real evidence; what's left is
extending the same rigor from this controlled spike to production
conditions:

1. **Test on a real AAC-encoded M4B source, not raw PCM WAV.** Every
   measurement in this ADR used WAV throughout. AAC decode into PCM (for
   the envelope multiply) and the subsequent AAC re-encode are both
   planned steps (ADR-0002/O-03) that haven't been exercised together
   with this envelope mechanism yet — codec round-trip could plausibly
   affect sample-accuracy or introduce its own small timing offset
   (e.g. encoder priming samples) that this spike wouldn't have caught.
2. **Verify boundary/fade precision at PRD's actual declared range
   extremes** (0ms padding, 5ms fade) — this spike used 30-50ms fades
   and round interval numbers; since the envelope is generated directly
   sample-by-sample rather than through any frame-quantized mechanism,
   there's no structural reason to expect a problem at smaller values,
   but "no structural reason to expect a problem" is a hypothesis, not
   a measurement, until it's actually run.
3. **Confirm envelope generation scales to a real 20-hour, ~3.2
   billion-sample duration**, not just the 51-minute/136.7M-sample file
   used here. The bulk-fill strategy (Finding #7) should scale linearly
   with duration and be independent of interval count for the
   per-interval computation — both properties suggest this holds, but
   at ~23x the sample count actually tested, this needs a real run
   before being stated as proven rather than extrapolated.
4. **Confirm `amultiply`'s behavior when the two input streams differ
   in exact sample count** (source duration vs. envelope array length
   — should be identical by construction, but an off-by-one in envelope
   generation meeting a real AAC-decoded sample count that doesn't
   divide as cleanly as a synthetic sine source could surface an edge
   case not exercised here).
5. This ADR covers gain-envelope generation and application only. It
   does not cover the surrounding Renderer/Validator pipeline (staging,
   atomic output, duration/chapter/metadata validation per §8.1/§8.2) —
   those remain to be built and are a separate, more mechanical task
   now that this ADR removes the open question that was blocking it.
