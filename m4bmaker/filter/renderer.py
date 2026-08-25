"""Renderer: apply a RenderPlan's gain envelope to a source M4B and produce
a filtered output, preserving chapters/metadata/cover (PRD §8, O-03, O-04,
ADR-0002, ADR-0006).

Implements the mechanism ADR-0006's G4 spike proved: generate the gain
envelope as its own sample-accurate PCM signal directly (never via an
ffmpeg expression), apply it with a single ``amultiply`` pass. See that
ADR for why every other approach tested was rejected.

**Memory note.** ADR-0006's spike generated the whole envelope as one
in-memory array — fine at the ~51-minute scale it was tested at (a few
hundred MB), but a genuinely bad idea at the ~20-hour scale this app
targets: a 20-hour stereo 44.1kHz envelope would be several gigabytes
held in RAM at once, on hardware that might have far less than the 48GB
this was developed on. :func:`generate_envelope_pcm` here writes in
bounded-size chunks instead — peak memory is a small constant regardless
of source duration. This is a real fix motivated by testing against a
real ~13.5-hour audiobook, not a hypothetical concern.

**Container format note — also found by testing against that real file,
not anticipated in advance.** Every intermediate PCM file in this
pipeline is **raw headerless PCM** (``-f s16le``), not WAV. The classic
WAV/RIFF format's chunk-size field is 32-bit, capping a file at ~4GiB.
The real 13.5-hour test book's PCM is ~8.6GB *per intermediate stage* —
ffmpeg's own WAV writer silently extends past this (a de facto RF64-style
behavior most tools including ffmpeg itself can still read back), but
Python's stdlib :mod:`wave` module cannot: it raised ``struct.error: 'L'
format requires 0 <= number <= 4294967295`` the first time this was
actually tried at real scale. Since every consumer of these intermediate
files (ffmpeg itself) is already told the exact sample rate/channel count
via explicit ``-ar``/``-ac``/``-f`` flags, the WAV header was never
adding information — dropping it removes the size ceiling entirely
rather than working around it.

**Pipeline (four ffmpeg/Python passes, each staged to a temp file):**

1. Extract the source's selected primary audio track to raw PCM,
   preserving native channel count and sample rate (PRD §8.4: "Preserve
   channel count and sample rate... otherwise reject the source class").
2. Generate the gain envelope as matching-format raw PCM.
3. ``amultiply`` the two together — the proven attenuation mechanism.
4. Encode the result to AAC and mux it back with the *original* source
   file's metadata, chapters, and cover art via ``-map_metadata``/
   ``-map_chapters`` (full-fidelity preservation of whatever the source
   has — including fields this app's own schema doesn't know about, like
   series/part tags — rather than reconstructing from the narrower
   ``MediaManifest.required_metadata`` dict), atomically staged.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from m4bmaker.utils import subprocess_flags

from .models import MediaManifest, RenderPlan

#: Frames per envelope-generation chunk. Bounds peak memory to roughly
#: this many samples * channels * 2 bytes regardless of source duration —
#: at stereo 44.1kHz that's ~38MB per chunk, independent of whether the
#: source is 5 minutes or 20 hours long.
_ENVELOPE_CHUNK_FRAMES = 10_000_000

_FULL_SCALE = 32767
_PCM_FORMAT = "s16le"


class RenderError(Exception):
    """Raised when any render stage fails. The partial/staged output is
    always cleaned up before this is raised — never left as if it might be
    a complete file."""


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    duration_ms: int


def _run(cmd: list[str], step: str) -> None:
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", **subprocess_flags()
    )
    if result.returncode != 0:
        stderr_tail = result.stderr.strip()[-2000:]
        raise RenderError(f"{step} failed (exit {result.returncode}): {stderr_tail}")


def extract_primary_audio_pcm(
    source_path: Path,
    track_index: int,
    sample_rate: int,
    channels: int,
    ffmpeg: str,
    dest_pcm_path: Path,
) -> None:
    """Decode *source_path*'s stream *track_index* to raw PCM at
    *dest_pcm_path*, preserving *sample_rate*/*channels* exactly (both
    must come from the source's own probed values — this function does
    not resample or downmix)."""
    dest_pcm_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source_path),
        "-map",
        f"0:{track_index}",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-f",
        _PCM_FORMAT,
        str(dest_pcm_path),
    ]
    _run(cmd, "Source audio extraction")


def _db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def _gain_at(
    local_sample: int, span_samples: int, fade_in_n: int, fade_out_n: int, floor: float
) -> float:
    """Gain at *local_sample* within one interval spanning *span_samples*,
    per PRD §8.4's ramp-in/hold-floor/ramp-out shape."""
    if local_sample < fade_in_n:
        frac = local_sample / fade_in_n
        return 1.0 - (1.0 - floor) * frac
    remaining = span_samples - local_sample
    if remaining <= fade_out_n:
        frac = remaining / fade_out_n
        return 1.0 - (1.0 - floor) * frac
    return floor


def generate_envelope_pcm(
    render_plan: RenderPlan,
    sample_rate: int,
    channels: int,
    dest_pcm_path: Path,
    total_samples: int | None = None,
) -> None:
    """Write a sample-accurate gain envelope (PRD §8.3/§8.4) as raw
    interleaved 16-bit PCM to *dest_pcm_path*, matching *sample_rate*/
    *channels* so it can be ``amultiply``-ed directly against the
    extracted source PCM.

    *total_samples*, when supplied, should be the *actual* decoded sample
    count of the extracted source PCM (its file size, not a duration
    estimate) — ``amultiply`` silently truncates to the shorter of its two
    inputs, so any independent re-derivation of "how many samples the
    source has" is a correctness risk, not just a cosmetic one. A real
    ~13.5-hour test file showed exactly this: ``source_duration_ms``
    (rounded from ffprobe's float duration) and the source PCM's real
    decoded sample count differed by 15 samples (0.34ms) — utterly
    inaudible, but still the kind of drift that must not be *assumed*
    away. Falls back to deriving the count from
    ``render_plan.source_duration_ms`` only when the caller has no better
    number available (e.g. in isolated tests of this function).

    Writes in bounded-size chunks (see module docstring) rather than
    building one array for the whole file — peak memory does not grow
    with source duration. Each chunk bulk-fills its "no active interval"
    samples at full gain (a single C-level array multiply, per
    ADR-0006's Finding #7) and only computes per-sample values for the
    portion of the chunk actually inside a fade/floor window.
    """
    if total_samples is None:
        total_samples = int(round(render_plan.source_duration_ms * sample_rate / 1000))
    floor = _db_to_linear(render_plan.attenuation.gain_floor_db)

    # Precompute each interval's sample-space bounds once.
    interval_bounds = []
    for interval in render_plan.intervals:
        s0 = int(round(interval.start_ms * sample_rate / 1000))
        s1 = int(round(interval.end_ms * sample_rate / 1000))
        fi_n = max(1, int(round(interval.fade_in_ms * sample_rate / 1000)))
        fo_n = max(1, int(round(interval.fade_out_ms * sample_rate / 1000)))
        interval_bounds.append((s0, s1, fi_n, fo_n))

    dest_pcm_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_pcm_path, "wb") as f:
        pos = 0
        next_interval_idx = 0
        while pos < total_samples:
            chunk_end = min(pos + _ENVELOPE_CHUNK_FRAMES, total_samples)
            chunk_len = chunk_end - pos

            # Advance past intervals that end before this chunk starts.
            while (
                next_interval_idx < len(interval_bounds)
                and interval_bounds[next_interval_idx][1] <= pos
            ):
                next_interval_idx += 1

            # Intervals starting before this chunk ends overlap it (any
            # already fully behind `pos` were skipped by the advance above).
            overlapping = []
            j = next_interval_idx
            while j < len(interval_bounds) and interval_bounds[j][0] < chunk_end:
                overlapping.append(interval_bounds[j])
                j += 1

            buf = array("h", [_FULL_SCALE]) * (chunk_len * channels)
            for s0, s1, fi_n, fo_n in overlapping:
                lo = max(s0, pos)
                hi = min(s1, chunk_end)
                span = s1 - s0
                for global_i in range(lo, hi):
                    g = _gain_at(global_i - s0, span, fi_n, fo_n, floor)
                    val = int(round(g * _FULL_SCALE))
                    base = (global_i - pos) * channels
                    for ch in range(channels):
                        buf[base + ch] = val

            f.write(buf.tobytes())
            pos = chunk_end


def apply_gain_envelope(
    source_pcm_path: Path,
    envelope_pcm_path: Path,
    dest_pcm_path: Path,
    sample_rate: int,
    channels: int,
    ffmpeg: str,
) -> None:
    """Multiply *source_pcm_path* by *envelope_pcm_path* sample-for-sample
    (the proven mechanism, ADR-0006 Finding #6/#7). Both inputs and the
    output are raw PCM — *sample_rate*/*channels* must be passed
    explicitly since raw PCM carries no self-describing header."""
    dest_pcm_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        _PCM_FORMAT,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        str(source_pcm_path),
        "-f",
        _PCM_FORMAT,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        str(envelope_pcm_path),
        "-filter_complex",
        "[0:a][1:a]amultiply[out]",
        "-map",
        "[out]",
        "-f",
        _PCM_FORMAT,
        str(dest_pcm_path),
    ]
    _run(cmd, "Gain envelope application")


def encode_and_mux(
    filtered_pcm_path: Path,
    original_source_path: Path,
    dest_m4b_path: Path,
    bitrate: str,
    channels: int,
    sample_rate: int,
    ffmpeg: str,
    cover_path: Path | None = None,
) -> None:
    """Encode *filtered_pcm_path* (raw PCM) to AAC and mux with metadata/
    chapters copied from *original_source_path* (full-fidelity pass-through
    of every atom the source has, not just the fields this app's own
    schema knows about) and cover art from *cover_path*, if given,
    atomically staged to *dest_m4b_path*.

    **Why *cover_path* is a separate standalone image, not mapped directly
    from *original_source_path*'s own video stream.** Doing the obvious
    thing — ``-map 1:v?`` alongside ``-map_metadata 1 -map_chapters 1``
    from the same MP4/MOV input — was tried first and **silently
    corrupted chapter titles** on a real test file (a real ~13.5-hour
    audiobook): chapter *start times* stayed exactly correct, but roughly
    the first five *titles* were swapped for titles from unrelated later
    chapters, while the rest of the file was fine. Isolated by testing:
    removing the direct cover mapping fixed it completely; re-adding the
    cover as an independent third input (extracted to a standalone image
    file first, via ``m4bmaker.cover.extract_cover_from_audio`` — the
    base project's own already-proven pattern, also how
    ``encoder.py:encode()`` supplies cover art) fixed it while still
    preserving the cover correctly. The precise ffmpeg-internal cause
    wasn't chased further since a working, precedented pattern was
    already available — recorded here so nobody reintroduces the direct
    mapping without knowing why it silently corrupts chapter data.
    """
    dest_m4b_path.parent.mkdir(parents=True, exist_ok=True)
    partial = dest_m4b_path.with_name(dest_m4b_path.name + ".partial")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        _PCM_FORMAT,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        str(filtered_pcm_path),
        "-i",
        str(original_source_path),
    ]
    if cover_path is not None:
        cmd += ["-i", str(cover_path)]
    cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
    if cover_path is not None:
        _ext = cover_path.suffix.lower()
        _copy_cover = _ext in {".jpg", ".jpeg", ".png"}
        cmd += [
            "-map",
            "2:v",
            "-c:v",
            "copy" if _copy_cover else "mjpeg",
            "-disposition:v",
            "attached_pic",
        ]
    cmd += [
        "-c:a",
        "aac",
        "-b:a",
        bitrate,
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-metadata",
        "stik=2",
        "-brand",
        "M4B ",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(partial),
    ]
    try:
        _run(cmd, "AAC encode and mux")
    except RenderError:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, dest_m4b_path)


def render(
    source_path: Path,
    manifest: MediaManifest,
    render_plan: RenderPlan,
    output_path: Path,
    ffmpeg: str,
    ffprobe: str,
    bitrate: str,
    progress_callback: Callable[[str, float], None] | None = None,
) -> RenderResult:
    """Run the full render pipeline: extract -> generate envelope ->
    attenuate -> encode+mux. Leaves *source_path* untouched. Raises
    :class:`RenderError` on any stage failure; no partial output is ever
    left at *output_path*.
    """

    def _cb(msg: str, frac: float) -> None:
        if progress_callback is not None:
            progress_callback(msg, frac)

    track = next(
        (t for t in manifest.tracks if t.index == manifest.selected_track_index), None
    )
    if track is None or track.sample_rate is None or track.channels is None:
        raise RenderError(
            "Selected audio track's sample rate/channel count could not be "
            "determined; cannot render (PRD §8.4: reject the source class "
            "rather than guess)."
        )
    sample_rate = track.sample_rate
    channels = track.channels

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source_pcm = tmp_path / "source.pcm"
        envelope_pcm = tmp_path / "envelope.pcm"
        filtered_pcm = tmp_path / "filtered.pcm"

        _cb("Extracting source audio…", 0.0)
        extract_primary_audio_pcm(
            source_path,
            (
                manifest.selected_track_index
                if manifest.selected_track_index is not None
                else 0
            ),
            sample_rate,
            channels,
            ffmpeg,
            source_pcm,
        )

        _cb("Generating gain envelope…", 0.25)
        actual_total_samples = source_pcm.stat().st_size // (2 * channels)
        generate_envelope_pcm(
            render_plan,
            sample_rate,
            channels,
            envelope_pcm,
            total_samples=actual_total_samples,
        )

        _cb("Applying attenuation…", 0.5)
        apply_gain_envelope(
            source_pcm, envelope_pcm, filtered_pcm, sample_rate, channels, ffmpeg
        )

        cover_path = None
        if manifest.cover_present:
            from m4bmaker.cover import extract_cover_from_audio

            cover_path = extract_cover_from_audio(source_path, ffmpeg)

        _cb("Encoding and muxing output…", 0.75)
        encode_and_mux(
            filtered_pcm,
            source_path,
            output_path,
            bitrate,
            channels,
            sample_rate,
            ffmpeg,
            cover_path=cover_path,
        )

    _cb("Done.", 1.0)
    return RenderResult(output_path=output_path, duration_ms=manifest.duration_ms)
