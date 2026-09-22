# ADR-0030: Encoder safety widening — AAC leaves short attenuated intervals audible

**Status:** Implemented and verified.

## Context

A real render (the User's own ~11.5-hour book) failed validation on one
interval: `Interval [38966581,38967211) not sufficiently attenuated:
measured -65.1dB, expected at or below -74.0dB`. Direct investigation
against the real audio (not guesswork) ruled out, in order: this
session's own ADR-0028 changes (an old-code-path vs. new-code-path A/B
test on the real audio produced byte-identical results), the scan/
matching/interval-planning stages (independently verified correct),
gain-envelope generation (the exact byte value at the target sample
was verified correct — the true floor gain), and the attenuation
multiply itself (reproducing the full real pipeline from source
extraction through the multiply produced a properly-silenced -94dB at
that exact position).

**The corruption appeared specifically during AAC encoding**, and
reproduced independent of every variable tested: ffmpeg's native `aac`
encoder vs. macOS's separate `aac_at` (AudioToolbox) encoder, 128k vs.
256k vs. 320k bitrate, default vs. `-aac_coder twoloop` (highest
quality mode), and fully synthetic test audio with no real speech
involved at all. It was not a container/muxing timestamp issue (packet
PTS/DTS were verified perfectly smooth and continuous through the
region) and not this session's `-progress` instrumentation (proven via
direct A/B test). Systematic testing (isolating fade duration, fade
curve shape — linear vs. raised-cosine, near-silence content type —
true zero vs. broadband noise vs. tone) converged on one clear,
repeatable finding: **an abrupt loud → near-silent → loud transition
audibly leaks the original audio for roughly the first 30-50ms into
the "silent" side, regardless of how gradually or smoothly the gain
ramps there** — genuinely widening the *total* interval (not just its
fade) was the only thing that reliably cleared it in testing.

This is why only one interval out of many failed: this book's other
attenuated hits were either merged with a nearby hit into a wider
combined interval, or simply weren't among the narrowest possible
case — a single, isolated, unmerged short word with default padding
(630ms total: a 280ms recognized word plus 150ms/200ms padding) is the
worst case for this failure mode.

## Decision

**Widen the interval `generate_envelope_pcm()` actually receives,
internally, inside `render()` — never the `RenderPlan` any caller
holds.** A new `_widen_for_encoder_safety()` extends each interval
symmetrically by `_ENCODER_SAFETY_HOLD_MS` (100ms — roughly 2x the
empirically-confirmed ~50ms clearing threshold, real headroom without
being maximal) beyond its own original edges, applied as a real
floor-gain hold the encoder gets genuine distance to settle into.
Review's display, the filter report's stats, and what `validate()`
checks against all keep using the original, unwidened `RenderPlan` —
this is purely an internal rendering detail, not a change to what the
User is told was silenced.

**Capped by neighboring `RenderInterval`s, not by individual
transcript words.** This is the key design choice that keeps this safe
in a way ADR-0026's abandoned word-boundary clamp wasn't: a
`RenderInterval` boundary is a hit the User already approved for
silencing — a solid, already-known-correct signal — whereas ADR-0026
found individual transcript-word gaps unreliable (DTW's contiguous
token boundaries read as near-zero gap almost everywhere, including
across real pauses). Widening never reaches past a neighboring
interval's own original boundary. Two widened regions from genuinely
close hits are allowed to touch or overlap each other — both sides
only apply floor gain there either way, so the result is identical
regardless.

**100ms, not the ~200-500ms first proven to work, is a deliberate
trade-off, not the point of maximum confidence.** This safety hold has
no signal telling it to stop before a directly-adjacent *non-hit* word
(the same class of risk ADR-0026's "damn"/"cat" case was about) — a
larger value would clear the encoder issue with more margin but also
reach further into any such neighbor. 100ms was chosen to sit
meaningfully above the confirmed-working threshold while limiting that
reach, rather than maximizing one risk at the other's expense.

## What changed

- `renderer.py`: `_ENCODER_SAFETY_HOLD_MS` constant,
  `_widen_for_encoder_safety()`, wired into `render()` immediately
  before `generate_envelope_pcm()` — the widened plan is used only for
  that one call; every other use of `render_plan` in `render()` (and
  everything the caller does with it afterward — validation, the
  filter report) is untouched.

## What this ADR does not resolve

The exact codec-internal mechanism (why ~30-50ms, why content-
independent, why neither fade duration nor fade curve shape helped)
remains only empirically characterized, not fully explained — pursuing
that further looked like real diminishing returns once a well-tested,
targeted mitigation was already in hand. If a future case needs more
than 100ms of headroom, the same investigation notes (this ADR, and
the real audio/test artifacts it was derived from) are the starting
point.

## Verification

**Unit tests** (`TestWidenForEncoderSafety`, `test_renderer.py`):
symmetric widening on an isolated interval; fade/hit-id fields
preserved; clamping to 0 and to a known total duration; the
`source_duration_ms == 0` ("unknown") convention correctly skips the
end clamp; a close neighbor (60ms gap) caps widening at the neighbor's
own original boundary in both directions; a distant neighbor doesn't
constrain widening at all; interval order/count preserved across
several intervals; and — mocking the render pipeline's other stages —
confirms `generate_envelope_pcm` receives the widened plan while the
caller's own `RenderPlan` object is provably untouched. Full suite:
1747 passed, 2 skipped (9 new tests); `black`/`flake8`/`mypy` clean.

**Real-pipeline verification, the actual bug's own reproduction case:**
extracted the real ~4.6s window of audio around the exact failing
interval from the User's real book. Before the fix: encoding the
correctly-attenuated raw PCM through the unmodified production
pipeline reproduced -16.6dB at the target position (essentially
unfiltered) — confirming the bug is real, not a validator artifact.
After the fix: re-ran the *actual, unmodified* top-level `render()`
and `validate()` functions (not a manual reimplementation of their
logic) against a small real M4B built from that same audio, with the
same real interval and the same real attenuation settings (150ms/
200ms padding, -80dB floor) that produced the original failure —
**validation passed.**
