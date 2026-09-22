# ADR-0007: Renderer and Validator implementation (G4 completion)

**Status:** Implemented and proven against a real ~13.5-hour production
AAC M4B audiobook (2026-08-25). Resolves ADR-0002's/ADR-0006's remaining
open items: real AAC (not raw PCM), real ~20-hour-class scale, and the
actual duration/chapter/metadata tolerances (previously placeholders).
**Related PRD items:** §8 (all), O-03, O-04, O-05 (partially — see
Deferred below)

## What this closes out

ADR-0002 and ADR-0006 established the *mechanism* (precomputed envelope +
`amultiply`) and left several things explicitly unverified: real AAC
round-trip, real multi-hour scale, and evidence-based tolerances rather
than placeholder numbers. This ADR reports the results of actually doing
that verification, against a real book — not a synthetic fixture — and
implements `renderer.py`/`validator.py` on top of the findings.

**Test fixture:** a real, user-owned, non-DRM `.m4b` audiobook (~13.5
hours, 48,693.1s, stereo AAC LC 44.1kHz ~126kbps, 50 real chapter
markers, full metadata including cover art). Its container carries
leftover `AUDIBLE_DRM_TYPE`/`AUDIBLE_ACR` tags from a prior conversion —
**verified empirically, not assumed**, that the audio stream itself
decodes to real, varying content at multiple points deep in the file
(RMS levels from -65dB to -16dB, confirmed non-silent, non-garbage) using
plain `ffmpeg` with no activation bytes or special DRM handling. This
confirms the file is not currently protected, only that it once was
before a prior conversion stripped it — consistent with how this app is
meant to be used (a user's own already-owned, already-de-DRM'd files),
not a DRM-bypass path this app performs itself.

## Decisions

### D1 — Intermediate PCM format: raw headerless PCM, not WAV

**Found by testing, not anticipated.** The classic WAV/RIFF chunk-size
field is 32-bit, capping a WAV at ~4GiB. This real book's PCM is ~8.6GB
*per intermediate stage* — Python's stdlib `wave` module raised
`struct.error: 'L' format requires 0 <= number <= 4294967295` the first
time this was actually tried at real scale (ffmpeg's own WAV writer
silently extends past this via a de facto RF64-like behavior most tools
can still read, but that doesn't help code going through `wave`).
**Decision:** every intermediate file (`extract_primary_audio_pcm`,
`generate_envelope_pcm`, `apply_gain_envelope`'s output) is raw PCM via
`-f s16le`, with explicit `-ar`/`-ac` flags at every consumer — nothing
was reading these files as self-describing containers anyway, so the
header was never adding information. This removes the size ceiling
entirely rather than working around it (e.g. via RF64), and keeps the
implementation simple.

### D2 — Envelope generation: bounded-memory chunked writes, not one array

ADR-0006's spike built the whole envelope as one in-memory array — fine
at the ~51-minute scale tested there (low hundreds of MB), but a 20-hour
stereo 44.1kHz envelope is several gigabytes held in RAM at once, on
hardware that might have far less than the 48GB this was developed on.
`generate_envelope_pcm` writes in `_ENVELOPE_CHUNK_FRAMES`-sized chunks
(10M frames, ~38MB/chunk) instead: each chunk bulk-fills its
"no active interval" samples via one C-level `array` multiply (the same
trick that made ADR-0006's Finding #7 fast) and only runs a per-sample
Python loop over the small fraction of a chunk that actually falls
inside a fade/floor window. **Measured on the real book: 2.3-2.5 seconds
for the full 13.5-hour envelope, six real intervals** — confirms
ADR-0006's Finding #7 (constant-time-ish regardless of interval count)
holds at real scale, and confirms peak memory no longer scales with file
length.

### D3 — Envelope length: derived from the *actual* extracted PCM byte
count, not re-estimated from `duration_ms`

**Found by testing.** Deriving the envelope's total sample count
independently from `render_plan.source_duration_ms` (itself rounded from
ffprobe's float duration) produced an envelope **15 samples (0.34ms)
shorter** than the actually-extracted source PCM on the real book —
utterly inaudible, but a real risk: `amultiply` silently truncates to the
shorter of its two inputs, so any independent re-derivation of "how many
samples the source has" is a correctness hazard, not a rounding
curiosity to shrug off. **Decision:** `render()` computes
`source_pcm.stat().st_size // (2 * channels)` after extraction and passes
that exact count into `generate_envelope_pcm` — confirmed byte-exact
match on the real file after this fix.

### D4 — Cover art: standalone extracted image as a third input, never
mapped directly from the source container

**The most significant finding of this ADR, and a real, previously-latent
bug.** The obvious mux command — `-map 1:v?` (cover) alongside
`-map_metadata 1 -map_chapters 1` (also from input 1, the original
source) in one ffmpeg invocation — **silently corrupted chapter titles**
on the real test file: chapter *start times* remained exactly correct,
but roughly the first five *titles* were swapped for titles belonging to
unrelated later chapters (`"Chapter 1"` became `"Chapter 13"`,
`"Part I - Chapter 2"` became `"Chapter 16"`, etc.), while chapters 6
onward were unaffected. No error was raised — this would have shipped
silently if it hadn't been checked chapter-by-chapter against the real
source.

Diagnosed by isolation, not guessed: a minimal repro with two full M4B
inputs (`-i original -i original`) did **not** reproduce it; a repro
using a raw-PCM input for audio plus the real M4B for
metadata/chapters/cover **did**, and printed a real clue in stderr —
`"Application provided duration: <huge number> in stream 2 is invalid"`,
repeated dozens of times, correlating with the cover (`stream 2`)
mapping. Removing `-map 1:v?` alone fixed the chapter corruption
completely. Re-adding cover art via `m4bmaker.cover.extract_cover_from_audio`
(the base project's own already-proven pattern — also how
`encoder.py:encode()` supplies cover art, as three independent inputs
rather than mapping video out of an input that also supplies chapters)
fixed it while correctly preserving the cover. The exact ffmpeg-internal
root cause was not chased further, since a working, precedented
alternative was already available and confirmed correct on the real
file — recorded in `renderer.py`'s docstring so nobody reintroduces the
direct mapping without knowing why it silently corrupts chapter data.

### D5 — Duration/chapter tolerances: real measurement, not the
ADR-0002 placeholder

ADR-0002 proposed an unconfirmed "initial target" of 10ms duration
tolerance. Measured on the real book (once D4's cover bug was fixed):
**exact 0ms duration match** (`48693.108345s` to six decimal places,
identical) and **exact 0ms match on every one of 50 real chapter start
times**. `validator.py`'s defaults (`DEFAULT_DURATION_TOLERANCE_MS =
100`, `DEFAULT_CHAPTER_START_TOLERANCE_MS = 50`) are set well above this
observed value as a safety margin for untested conditions (different
bitrates, mono sources, other encoder versions) — not because that much
drift is considered acceptable. This is one real fixture, not a
statistical sample; the margin exists precisely because of that.

### D6 — Attenuation validation scope

`validate_attenuation` measures each planned interval's post-fade
sustain region and confirms it dropped to within `margin_db` (default
6dB) of the configured floor. It does **not** re-decode an entire
multi-hour file to confirm every other sample was left untouched — that
broader guarantee comes from `amultiply`'s proven sample-for-sample
correctness (ADR-0006, and D3 above at real scale), and re-verifying it
per-render would be redundant re-proving of an already-established
mechanism at a cost that scales with file length for no added
confidence. This scope boundary is stated in the function's docstring,
not left implicit.

## Real results on the test fixture

| Stage | Time | Notes |
|---|---:|---|
| Media inspection (source) | <1s | 50 chapters, full metadata, cover detected — all correct |
| Extract source PCM | 31.3s | 8.59GB raw PCM from 773MB AAC |
| Generate envelope | 2.3-2.5s | 6 real intervals across the full file |
| Apply gain envelope (`amultiply`) | 10.0s | byte-exact length match with D3 fix |
| Encode + mux | 587.7s | AAC encode at ~126kbps + full metadata/chapter/cover mux |
| **Total render** | **615.2s (~10.25 min)** | For the entire 13.5-hour book — roughly 79x realtime |
| Media inspection (output) | <1s | |
| Full validation | 2.7s | duration + 50 chapters + metadata + cover + 6 attenuation checks |
| **Validation result** | **PASSED, 0 errors, 0 warnings** | |

Concrete attenuation example (real narration content, not the synthetic
sine tone ADR-0006 used): immediately before the planned interval,
source and output both measured **-51.5/-51.6dB** (unchanged); inside the
interval's sustain region, source measured **-17.95dB** (normal speech)
and output measured **-97.6dB** — comfortably past the configured -80dB
floor; immediately after, **-17.5/-18.2dB** (unchanged, small AAC
re-encode variance, not attenuation-related).

## Deferred / explicitly out of scope

- **Render-stage recovery/resumability (PRD O-05)** is not addressed by
  this ADR. The current renderer is all-or-nothing within one `render()`
  call, staged atomically at the very end (no partial output ever
  exposed) — matching PRD §11.4's "mandatory fallback" (explicit restart,
  no partial-render claims), not yet the "preferred" segment-resumable
  design. Given the real 615s/10.25-minute total render time for a
  13.5-hour book, full-restart-on-failure is a reasonable MVP position;
  segment-resumable rendering would need its own spike if real-world
  failure rates justify the complexity.
- **Mono sources, other bitrates, other AAC encoder versions** — every
  number in this ADR comes from one real stereo 44.1kHz ~126kbps fixture.
  The tolerances are deliberately conservative for this reason.
- **A `filter-report.json` artifact** (PRD §14.4) tying a `ValidationReport`
  to a persisted, user-facing file is not implemented — `ValidationReport`
  exists as a return value only; writing it to disk alongside output is
  UI/orchestration-layer work (G5), not blocked by anything in this ADR.
