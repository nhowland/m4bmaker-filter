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
- **Do not claim fade-ramp behavior is solved.** Finding #4 is a genuine
  open problem, not a detail to paper over. A renderer built on this
  ADR's gating mechanism today would produce a correct floor/gate
  (verified) but an audible click instead of a ramp at each interval's
  edges (not yet fixed) — that gap must stay visible, including in any
  UI copy, until resolved.

## What remains before this ADR can be marked fully resolved

1. Re-test boundary precision and fade smoothness at PRD's actual declared
   range extremes (0ms padding, 5ms fade), not just the round numbers
   used in this spike.
2. Prototype and measure the `afade`-on-short-`atrim`-slices hybrid
   (Finding #4, direction 2) for both correctness and its own scaling
   behavior at realistic interval counts.
3. Re-run the Finding #3 scaling table with a forced small frame size
   applied throughout (not just at the fade edges) to see whether fixing
   precision materially worsens the scaling ceiling.
4. All of the above on a real AAC-encoded M4B source (this spike used
   raw PCM WAV throughout) — AAC decode/re-encode could plausibly shift
   these numbers in either direction and has not been tested at all yet.
