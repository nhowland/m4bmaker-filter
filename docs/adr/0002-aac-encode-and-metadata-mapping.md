# ADR-0002 (O-03): AAC encode/mux strategy and M4B metadata/chapter preservation

**Status:** Proposed — direction only; exact tolerances/thresholds require
fixture measurement in G4, not asserted here.
**Decision needed by:** Milestone 1 implementation (schema/interfaces); full
close requires G4 render-spike evidence per PRD §17.4.
**Related PRD items:** D-06, D-07, D-08, §8.1, §8.2, §8.4, O-03

## Context

The renderer must decode the source's primary AAC stream to PCM, apply a
gain envelope only inside planned intervals (PRD §8.3/§8.4), re-encode to
AAC, and remux into a new M4B that preserves chapters and required metadata
exactly (PRD §6.3, §8.2), without ever touching the source file.

## Command/API flow (proposed)

This is a **new** pipeline stage, distinct from the existing
`encoder.py:encode()` (which muxes from a concat list of whole source files
and never touches sample data) and distinct from `m4b_editor.py`'s
copy-only chapter rewrite (which never re-encodes audio). Proposed flow:

1. **Extract** — `ffmpeg -i source.m4b -map 0:a:<selected_index> -f wav -` (or
   a temp file) to get PCM for the selected primary/default track only,
   at its native sample rate/channel count. Reuses `preflight.py`'s
   existing ffprobe stream-selection logic to identify `<selected_index>`
   per D-09.
2. **Apply gain envelope** — see ADR-0003 (O-04) for the exact envelope
   algorithm; this stage consumes a `RenderPlan` (PRD §14.4) of merged
   intervals and produces filtered PCM. Candidate mechanism: ffmpeg's
   `volume` filter with `enable='between(t,start,end)'` per interval,
   *or* an `asendcmd`-driven `volume@id` filter graph if interval count
   makes the naive `between()` chain too slow to build (flagged as a risk
   in the initial repository review — hundreds of hits on a 20-hour book is
   a realistic case). **This choice must be spiked and measured in G4, not
   assumed.**
3. **Re-encode** — `-c:a aac -b:a <bitrate>` targeting the source's detected
   nominal bitrate (D-07), reusing `encoder.py`'s existing bitrate-detection
   precedent from `preflight.py:FileInfo.bit_rate`.
4. **Remux with preserved chapters/metadata** — reuse `m4b_editor.py`'s
   `write_ffmetadata`/`-map_metadata`/`-map_chapters` approach, extended to
   carry forward the full required-field set in PRD §6.3 (including the
   `©nrt` narrator-atom special case `metadata.py`/`m4b_editor.py` already
   handle for the existing conversion path — this quirk is a **known trap**
   and must be covered by a fixture, since ffmpeg's own metadata mapping
   does not target `©nrt`).
5. **Stage atomically** — same `.partial` → `os.replace()` pattern as
   `encoder.py:encode()`, so a killed/failed render never leaves a
   corrupted output at the final path.

## Supported tag/chapter mapping

Direct reuse of the mapping already implemented and tested in
`m4b_editor.py:save_m4b_chapters()`: title/artist/composer/genre via
`-metadata`, narrator via a post-hoc `mutagen` `©nrt` write, `stik=2` +
`M4B ` brand flags for Apple Books compatibility. Cover art extraction
reuses `cover.py:extract_cover_from_audio()`. No new mapping logic is
believed necessary for the required-field list in PRD §6.3 — this is the
strongest piece of evidence from G0 that O-03 is largely "reuse and extend,"
not "invent."

## Encoder settings

- Bitrate: source nominal bitrate (D-07), read via the existing
  `preflight.py` stream probe; fallback to a documented fixed list if
  undetectable (exact fallback values are a G1 decision, not fixed here).
- Channels/sample rate: preserved from source (PRD §8.4) — reject the
  source class if the selected AAC pipeline can't preserve them, per
  PRD §8.4's explicit instruction not to silently downmix/resample.
- Container flags: same `stik=2`/`M4B ` brand as the existing pipeline for
  Apple Books/iOS recognition.

## Validation tools/versions

- `ffprobe` for both pre- and post-render canonical manifests (duration,
  chapter list, stream properties) — **same pinned ffprobe binary/version**
  used for both measurements, per PRD §8.1's explicit requirement ("measure
  source and output with the same pinned media-inspection tool/version").
- Decoded-PCM waveform comparison tool for §8.5 gain verification — to be
  selected in G4 (candidate: `ffmpeg`-decoded PCM analyzed with a small
  Python RMS/peak measurement script; no new heavy dependency expected).

## Fallback/failure rules

- Any codec/track/chapter/metadata condition outside PRD §6.4's supported
  list fails the preflight before transcription or render starts — no
  partial attempt.
- A render that fails post-encode validation (§8.1/§8.2 tolerance) is never
  exposed as a successful output; partial/staged files are cleaned up
  exactly as `encoder.py` already does for the existing conversion path.

## Open items requiring G4 fixture evidence before this ADR can close

1. **Duration tolerance** — PRD §8.1 proposes an initial 10ms target; must
   be confirmed or revised against real AAC encode/decode round-trip
   fixtures (lossy AAC frame boundaries may not hit 10ms exactly — this is
   an honest unknown, not a guess dressed as a number).
2. **Interval-count scaling** — whether the `volume`+`between()` filter
   chain remains performant at realistic hit counts (tens to low hundreds
   across a 20-hour book) or whether the `asendcmd` alternative is required.
   This directly affects render throughput and must be measured, not
   assumed, before G4 sign-off.
3. Exact rounding/timebase policy for chapter start-time equality (§8.2) —
   proposed: canonical millisecond integer comparison, matching the
   transcript artifact's own `startMs`/`endMs` integer-millisecond
   convention (PRD §10.3) for consistency across the whole system.
