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
import re
import subprocess
import tempfile
from array import array
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from m4bmaker.utils import subprocess_flags

from .models import MediaManifest, RenderInterval, RenderPlan
from .storage import output_dir_override, temp_root

#: Frames per envelope-generation chunk. Bounds peak memory to roughly
#: this many samples * channels * 2 bytes regardless of source duration —
#: at stereo 44.1kHz that's ~38MB per chunk, independent of whether the
#: source is 5 minutes or 20 hours long.
_ENVELOPE_CHUNK_FRAMES = 10_000_000

_FULL_SCALE = 32767
_PCM_FORMAT = "s16le"

#: Simultaneous raw-PCM scratch stages at peak: source.pcm, envelope.pcm,
#: and filtered.pcm all exist on disk at once between
#: apply_gain_envelope() writing filtered.pcm and encode_and_mux()
#: consuming it (see render() below).
_PEAK_SIMULTANEOUS_PCM_STAGES = 3


class RenderError(Exception):
    """Raised when any render stage fails. The partial/staged output is
    always cleaned up before this is raised — never left as if it might be
    a complete file."""


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    duration_ms: int


def estimate_storage_bytes(manifest: MediaManifest) -> int:
    """Estimate total disk space a render of *manifest*'s source needs:
    the three simultaneous raw-PCM scratch stages at peak (source/
    envelope/filtered — see this module's docstring) plus the final AAC
    output, sized from the source's own bit rate since the actual render
    bitrate isn't chosen until the Render step (PRD §7.2 stage 1's
    "storage estimate").

    Returns 0 if the selected track's sample rate/channel count is
    unknown — mirrors :func:`render`'s own guard for that same case,
    since no estimate can be computed without them.
    """
    track = next(
        (t for t in manifest.tracks if t.index == manifest.selected_track_index), None
    )
    if track is None or track.sample_rate is None or track.channels is None:
        return 0

    duration_s = manifest.duration_ms / 1000
    pcm_bytes_per_stage = duration_s * track.sample_rate * track.channels * 2
    total_pcm_bytes = pcm_bytes_per_stage * _PEAK_SIMULTANEOUS_PCM_STAGES

    aac_bytes = duration_s * (track.bit_rate / 8) if track.bit_rate else 0

    return round(total_pcm_bytes + aac_bytes)


def default_output_path(source_path: Path) -> Path:
    """The Render step's default output location: ``"<stem>
    (filtered)<suffix>"`` — e.g. ``Book.m4b`` -> ``Book (filtered).m4b`` —
    in the User's configured default output folder (Settings) if one is
    set, else the same folder as the source. A starting point the
    wizard's own Browse action can always override, not a fixed policy."""
    filename = f"{source_path.stem} (filtered){source_path.suffix}"
    override = output_dir_override()
    if override is not None:
        return override / filename
    return source_path.with_name(filename)


#: Real AAC bitrates the Render step offers — the exact list
#: ``gui/window.py``'s own encoding-options combo already uses.
SUPPORTED_BITRATES: tuple[str, ...] = (
    "32k",
    "48k",
    "64k",
    "96k",
    "128k",
    "192k",
    "256k",
    "320k",
)

#: Matches ``gui/window.py``'s own ``_DEFAULT_BITRATE`` — used only when
#: the source's own bitrate can't be read at all.
DEFAULT_BITRATE = "96k"


def pick_default_bitrate(bit_rate_bps: int | None, codec_name: str | None) -> str:
    """Auto-select the best AAC bitrate for a source, porting
    ``gui/window.py``'s own real "snap to nearest of ``_BITRATES`` given
    the source's own bitrate" logic rather than reinventing it
    differently — same discount-then-snap algorithm: a lossy MP3/MP2
    source gets a 25% discount before snapping (AAC reaches equivalent
    perceptual quality at roughly 75% of the source bitrate there), every
    other codec (AAC, FLAC, WAV, ...) snaps without a discount. Ties snap
    to the higher step, the safer-quality choice. Falls back to
    :data:`DEFAULT_BITRATE` when the source's bitrate is unknown — no
    guess is better than a wrong one here.
    """
    if bit_rate_bps is None:
        return DEFAULT_BITRATE
    target_kbps = bit_rate_bps // 1000
    if codec_name in ("mp3", "mp2"):
        target_kbps = int(target_kbps * 0.75)
    available = [int(b.rstrip("k")) for b in SUPPORTED_BITRATES]
    closest = min(available, key=lambda x: (abs(x - target_kbps), -x))
    return f"{closest}k"


def _run(cmd: list[str], step: str) -> None:
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", **subprocess_flags()
    )
    if result.returncode != 0:
        stderr_tail = result.stderr.strip()[-2000:]
        raise RenderError(f"{step} failed (exit {result.returncode}): {stderr_tail}")


_OUT_TIME_RE = re.compile(r"^out_time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _run_with_progress(
    cmd: list[str],
    step: str,
    total_duration_ms: int,
    progress_callback: Callable[[float], None] | None,
) -> None:
    """Like :func:`_run`, but streams ffmpeg's own ``-progress`` output
    live and reports fractional completion (0.0-1.0, against
    *total_duration_ms*) as it arrives — for encode+mux specifically, the
    one render stage real measurement (ADR-0007) showed dominates total
    render time (~93% on a real 13.5-hour book: 587.7s of 631.4s). Every
    other stage in this pipeline stays a single blocking call — none of
    them are slow enough for sub-stage progress to matter (ADR-0028).

    stderr is redirected to a temp file rather than captured live
    alongside stdout's progress stream — draining two OS pipes at once
    needs a background thread or ``select()``-based multiplexing, and
    getting that wrong risks the standard pitfall of an unread pipe
    filling its OS buffer and blocking the child. A file sidesteps this
    entirely; it's only read back (for the error message) if the process
    actually fails, exactly like every other stage here already behaves
    on success.

    This directory stays on the OS default temp location rather than
    :func:`~m4bmaker.filter.storage.temp_root` (unlike :func:`render`'s own
    scratch PCM below) — it holds nothing but a short-lived stderr log, not
    a real storage concern the User-configurable temp location exists for.

    ``-progress`` itself adds no real work on ffmpeg's side — it's the
    same per-interval stats ffmpeg already computes and would otherwise
    print to stderr by default (``-nostats`` here just says "as
    machine-readable key=value lines instead of a human-readable line"),
    and reading a few dozen bytes off a pipe every ``-stats_period``
    (default 0.5s) is negligible next to the actual AAC encoding work
    driving this process's real runtime.
    """
    progress_cmd = cmd[:-1] + ["-nostats", "-progress", "pipe:1", cmd[-1]]
    with tempfile.TemporaryDirectory() as tmp:
        stderr_path = Path(tmp) / "stderr.log"
        with open(stderr_path, "w", encoding="utf-8") as stderr_file:
            proc = subprocess.Popen(
                progress_cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                encoding="utf-8",
                **subprocess_flags(),
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                match = _OUT_TIME_RE.match(line.strip())
                if match and progress_callback is not None and total_duration_ms > 0:
                    h, m, s = match.groups()
                    out_ms = (int(h) * 3600 + int(m) * 60 + float(s)) * 1000
                    progress_callback(min(1.0, out_ms / total_duration_ms))
            returncode = proc.wait()
        if returncode != 0:
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            raise RenderError(
                f"{step} failed (exit {returncode}): {stderr_text.strip()[-2000:]}"
            )


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


#: Minimum true floor-hold buffer, beyond an interval's own configured
#: fade edges, the renderer ensures exists on each side before AAC
#: encoding sees it (ADR-0030). Real testing against real narration
#: (not synthetic tones) found AAC encoding of an abrupt loud/near-silent
#: transition can leave an audible remnant of the *original* audio for
#: roughly the first 30-50ms into the "silent" side — independent of
#: bitrate, encoder quality settings, and encoder implementation (both
#: ffmpeg's native encoder and macOS's AudioToolbox encoder show it).
#: 100ms is ~2x that measured threshold — real headroom, chosen
#: deliberately smaller than the ~4x margin first tested: this value is
#: capped only by *other planned hits* (see
#: :func:`_widen_for_encoder_safety`), not by individual transcript
#: words, so a hit directly adjacent to a non-hit word (ADR-0026's
#: "damn"/"cat" case) has nothing else stopping this reach from
#: extending into it — smaller here trades some of this fix's own
#: margin for less of that residual risk, rather than maximizing one
#: at the other's expense.
_ENCODER_SAFETY_HOLD_MS = 100


def _widen_for_encoder_safety(
    intervals: tuple[RenderInterval, ...],
    source_duration_ms: int,
    safety_hold_ms: int = _ENCODER_SAFETY_HOLD_MS,
) -> tuple[RenderInterval, ...]:
    """Internal-only widening applied just before envelope generation —
    never reflected in the User-facing ``RenderPlan`` (Review's own
    display, the filter report's stats, or what ``validate()`` checks
    against, all of which keep using the original, narrower intervals).
    Real AAC encoding of a short, abruptly-attenuated interval can leave
    the original audio audible (see :data:`_ENCODER_SAFETY_HOLD_MS`) —
    this gives the encoder genuine extra distance to settle, without
    changing what the User is told was silenced.

    Extends each interval symmetrically until at least *safety_hold_ms*
    of pure floor gain exists beyond its own original edges, capped so
    it never reaches into a neighboring ``RenderInterval``'s own
    territory. Those boundaries are a safe, already-known signal (this
    module's own list of hits the User actually approved) — unlike
    individual transcript-word gaps, which real-audio testing during
    ADR-0026 found too unreliable a signal to clamp padding against.
    Widened regions from adjacent intervals are allowed to touch or
    overlap each other when the real gap between them is smaller than
    two safety holds — both sides only ever apply floor gain there
    either way, so the audio result is identical regardless.
    """
    widened = []
    n = len(intervals)
    for i, interval in enumerate(intervals):
        floor_left = intervals[i - 1].end_ms if i > 0 else 0
        if i + 1 < n:
            floor_right: int | None = intervals[i + 1].start_ms
        elif source_duration_ms > 0:
            floor_right = source_duration_ms
        else:
            floor_right = None  # unknown total duration -- no right-side cap

        new_start = max(interval.start_ms - safety_hold_ms, floor_left, 0)
        new_end = interval.end_ms + safety_hold_ms
        if floor_right is not None:
            new_end = min(new_end, floor_right)

        widened.append(replace(interval, start_ms=new_start, end_ms=new_end))
    return tuple(widened)


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
    total_duration_ms: int = 0,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    """Encode *filtered_pcm_path* (raw PCM) to AAC and mux with metadata/
    chapters copied from *original_source_path* (full-fidelity pass-through
    of every atom the source has, not just the fields this app's own
    schema knows about) and cover art from *cover_path*, if given,
    atomically staged to *dest_m4b_path*.

    *total_duration_ms*/*progress_callback*, when both given, report live
    encode progress via ffmpeg's own ``-progress`` output rather than a
    single blocking call (ADR-0028) — this is the one render stage real
    measurement showed dominates total render time.

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
        _run_with_progress(
            cmd, "AAC encode and mux", total_duration_ms, progress_callback
        )
    except RenderError:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, dest_m4b_path)


#: Each stage's *start* fraction, sized by real relative cost (ADR-0007's
#: measurement on a full 13.5-hour audiobook: extract 31.3s, envelope
#: 2.4s, attenuate 10.0s, encode+mux 587.7s of 631.4s total) rather than
#: four equal 25% steps — encode+mux is ~93% of real render time, so it
#: gets ~93% of the bar's range, not a quarter of it (ADR-0028).
_STAGE_EXTRACT_START = 0.0
_STAGE_ENVELOPE_START = 0.05
_STAGE_ATTENUATE_START = 0.06
_STAGE_ENCODE_START = 0.08


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

    scratch_root = temp_root()
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch_root) as tmp:
        tmp_path = Path(tmp)
        source_pcm = tmp_path / "source.pcm"
        envelope_pcm = tmp_path / "envelope.pcm"
        filtered_pcm = tmp_path / "filtered.pcm"

        _cb("Extracting source audio…", _STAGE_EXTRACT_START)
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

        _cb("Generating gain envelope…", _STAGE_ENVELOPE_START)
        actual_total_samples = source_pcm.stat().st_size // (2 * channels)
        # ADR-0030: the envelope actually generated silences a bit more
        # than render_plan itself reports, purely so AAC encoding has
        # real room to settle — the User-facing plan, validation target,
        # and filter report all keep using render_plan unmodified.
        safe_intervals = _widen_for_encoder_safety(
            render_plan.intervals, render_plan.source_duration_ms
        )
        generate_envelope_pcm(
            replace(render_plan, intervals=safe_intervals),
            sample_rate,
            channels,
            envelope_pcm,
            total_samples=actual_total_samples,
        )

        _cb("Applying attenuation…", _STAGE_ATTENUATE_START)
        apply_gain_envelope(
            source_pcm, envelope_pcm, filtered_pcm, sample_rate, channels, ffmpeg
        )

        cover_path = None
        if manifest.cover_present:
            from m4bmaker.cover import extract_cover_from_audio

            cover_path = extract_cover_from_audio(source_path, ffmpeg)

        _cb("Encoding and muxing output…", _STAGE_ENCODE_START)

        def _encode_progress(sub_fraction: float) -> None:
            overall = _STAGE_ENCODE_START + sub_fraction * (1.0 - _STAGE_ENCODE_START)
            _cb("Encoding and muxing output…", overall)

        encode_and_mux(
            filtered_pcm,
            source_path,
            output_path,
            bitrate,
            channels,
            sample_rate,
            ffmpeg,
            cover_path=cover_path,
            total_duration_ms=manifest.duration_ms,
            progress_callback=_encode_progress,
        )

    _cb("Done.", 1.0)
    return RenderResult(output_path=output_path, duration_ms=manifest.duration_ms)
