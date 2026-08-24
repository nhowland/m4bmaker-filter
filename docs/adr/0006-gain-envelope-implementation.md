# ADR-0006 (O-04): Gain-envelope implementation and timing/ramp behavior

**Status:** Partially resolved by direct empirical spike (2026-08-24). The
gating/attenuation mechanism and its scaling behavior are proven with real
measurements. **Fade-ramp precision is explicitly unresolved** — this ADR
reports a real negative finding rather than a guessed solution, per PRD
§17.1 rule 9.
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
available in this environment or currently a project dependency) did
**not** show a smooth ramp in a fine-grained scan — it jumped from
partial attenuation to full floor within about 10ms of a 50ms intended
ramp, similar in shape (though not necessarily the same cause) to the
`enable`-expression frame-quantization problem. Root cause not yet
diagnosed — leading candidates, none confirmed: (a) `amultiply`'s two
inputs (the source and the separately-decoded envelope file) may not be
sample-aligned by default when read as two independent `-i` inputs with
independently negotiated frame boundaries, producing a timing offset
between the two streams rather than a smoothness defect in the envelope
itself; (b) a measurement-window artifact similar to the earlier
`astats` false starts in this same investigation, not yet ruled out.

**This is the most promising direction for solving both scaling and
smoothness together, but it is not proven and must not be assumed
working.** Recommended next step, not yet done: verify the two
`amultiply` inputs are frame-aligned (e.g. force identical
`-ar`/`-ac`/frame-size on both, or read the envelope through the *same*
input via `asplit`-style routing instead of a second `-i`), then re-run
the same fine-grained boundary scan used elsewhere in this ADR before
concluding either way.

## Recommendation given current evidence

- **Adopt the chained `volume=enable='between(t,...)':volume=FLOOR`
  approach as the gating mechanism**, with the following constraints
  made explicit rather than assumed:
  - Practical interval-count ceiling: comfortably fast to several hundred
    (600 in ~1 minute on reference hardware); needs either a batching
    strategy or a different mechanism if real-world `RenderPlan` interval
    counts (after PRD §8.3's merge step reduces raw hits to merged
    intervals) turn out to regularly exceed roughly 600-800. Realistic
    profanity density in a 20-hour book, after merging nearby hits,
    seems very likely to land well under this — but that assumption
    itself needs a real fixture to confirm, not just asserted.
  - Boundary timing precision is on the order of 100ms at default
    settings, tunable down via `asetnsamples` at an unmeasured
    performance cost. PRD §8.3's 0ms-minimum padding value and §8.4's
    5ms-minimum fade value cannot be honestly claimed as achievable
    until this is re-measured with a forced small frame size at
    real interval-count scale (not just the single-interval precision
    test done here).
- **Do not claim fade-ramp behavior is solved at production scale.**
  Finding #5 proves smooth fades are achievable in ffmpeg at all (real
  progress — the naive expression-based approach in Finding #4 is not
  the only option and was the wrong one to keep pursuing), but only via
  a structure already shown too slow past a few hundred intervals.
  Finding #6 is the live, most promising lead for solving both problems
  together, but is unverified. **A renderer must not be built yet
  claiming both fast, many-interval rendering and smooth fades
  simultaneously — that combination has not been demonstrated.**

## What remains before this ADR can be marked fully resolved

1. Diagnose and re-test Finding #6 (`amultiply` envelope alignment) —
   this is the current best lead and the most impactful next step, since
   it could resolve both the scaling ceiling and the fade-smoothness
   problem in one mechanism if the alignment issue is fixed.
2. If Finding #6 doesn't pan out, measure whether splicing only tiny
   fade-edge slices (Finding #5's approach, narrowed) via `atrim`/
   `concat` scales any better than whole-interval segments did in
   Finding #3 — expected not to (segment *count* was the driver, not
   segment duration), but not yet actually tested.
3. Re-test boundary precision and fade smoothness at PRD's actual declared
   range extremes (0ms padding, 5ms fade), not just the round numbers
   used in this spike.
4. Re-run the Finding #3 scaling table with whichever fade mechanism is
   ultimately chosen applied throughout, not just the plain gate — the
   scaling numbers above are for gating alone and may not hold once fade
   handling is added at every interval.
5. All of the above on a real AAC-encoded M4B source (this spike used
   raw PCM WAV throughout) — AAC decode/re-encode could plausibly shift
   these numbers in either direction and has not been tested at all yet.
6. If envelope generation (Finding #6) is the chosen path, its own
   performance at real scale needs measurement: the spike's envelope was
   generated with a pure-Python per-sample loop (no numpy currently
   available or in this project's dependencies) — untested whether that
   approach is fast enough for a 20-hour, ~3.2 billion-sample envelope,
   or whether a run-length/sparse-segment generation strategy (the
   envelope is constant 1.0 almost everywhere) is needed instead.
