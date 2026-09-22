"""Media eligibility inspection for an existing M4B (PRD §6.1, D-09).

This is new code, not an extension of ``m4bmaker.pipeline.load_audiobook``
(which scans a *folder of source files* to be converted) or
``m4bmaker.m4b_editor`` (which edits chapters/metadata on an existing M4B
but never inspects multiple audio tracks or decides eligibility). See
``docs/adr/0000-g0-repository-discovery.md`` §8 for why this is a new
module rather than an extension of either.

G1 scope: eligibility + primary-track selection + chapter/metadata read
only (PRD §17.4 G1: "Implement media eligibility inspection for AAC M4B and
canonical source manifest"). Fingerprinting here is a deliberately cheap
placeholder pending ADR-0001/O-02's final algorithm decision — see
:func:`compute_fingerprint`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from m4bmaker.utils import subprocess_flags

from .models import (
    SCHEMA_VERSION,
    AudioTrack,
    ChapterInfo,
    MediaManifest,
)

#: PRD §6.2/§6.4: only AAC is an eligible primary-track codec in v1.
_ELIGIBLE_CODEC = "aac"

#: ffprobe's generic MP4-family container name; M4B is not a distinct
#: container, it's an MP4 file with an ``M4B `` ftyp brand and audiobook
#: metadata conventions (the existing ``encoder.py`` writes this same brand
#: on output). Accepting on the general MP4-family name is intentional and
#: matches how ``ffprobe``/the existing codebase treat these files.
_MP4_FAMILY_FORMAT_NAMES = {"mov,mp4,m4a,3gp,3g2,mj2"}

#: Common tag-key spellings ffprobe surfaces for each required field
#: (PRD §6.3), tried in order. This mirrors the defensive multi-key lookup
#: already used in ``metadata.py:_first_tag``.
_METADATA_TAG_CANDIDATES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "author": ("artist", "album_artist"),
    "album": ("album",),
    "narrator": ("narrator", "composer", "\xa9nrt", "\xa9wrt"),
    "genre": ("genre",),
    "description": ("description", "comment"),
}


def _run_ffprobe(path: Path, ffprobe: str) -> dict[str, Any] | None:
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_chapters",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", **subprocess_flags()
    )
    if result.returncode != 0:
        return None
    try:
        data: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data


def compute_fingerprint(
    path: Path, duration_ms: int, selected_codec: str | None
) -> str:
    """Return a cheap source-identity fingerprint.

    **Placeholder pending ADR-0001/O-02.** PRD §10.3/§10.4 requires a
    fingerprint that can detect "source has moved but content matches" to
    support the relink workflow, but hashing the full audio content of a
    file up to 20 hours long is expensive enough (PRD §13.1 performance
    budget) that the exact algorithm — full hash, partial/sampled hash, or
    something else — is an open ADR decision, not something to lock in
    silently here. This placeholder combines file size, duration, and
    selected-track codec, which is enough to keep G1/G2 tests deterministic
    and to catch gross mismatches (wrong file entirely) but is **not**
    guaranteed to detect a subtly different re-encode of the same content.
    Do not treat this as the final fingerprint contract.
    """
    size = path.stat().st_size if path.exists() else 0
    basis = f"{size}:{duration_ms}:{selected_codec or ''}"
    return "sha256:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _select_primary_track(
    audio_streams: list[dict[str, Any]],
) -> tuple[int | None, bool]:
    """Return ``(selected_stream_position, is_fallback)``.

    *selected_stream_position* indexes into *audio_streams* (not the
    ffprobe global stream index). Per D-09: prefer the disposition-flagged
    default track; if none is flagged, fall back to the first audio track
    and report the fallback so the caller can require User acknowledgement
    before transcription (PRD §6.4).
    """
    if not audio_streams:
        return None, False
    for i, s in enumerate(audio_streams):
        if s.get("disposition", {}).get("default") == 1:
            return i, False
    return 0, True


def inspect(path: Path, ffprobe: str) -> MediaManifest:
    """Probe *path* and return a :class:`MediaManifest`.

    Never raises for an ineligible/unreadable source — ineligibility is
    reported via ``MediaManifest.eligible`` / ``.ineligibility_reasons`` so
    callers can preflight before starting any transcription/render work
    (PRD §6.1: "The app must preflight eligibility before model work or
    transcription. If ineligible, it must state the specific unsupported
    condition"). Only truly unexpected conditions (path is a directory,
    etc.) are allowed to raise, from the underlying ``pathlib`` calls.
    """
    reasons: list[str] = []

    if not path.exists() or not path.is_file():
        return MediaManifest(
            schema_version=SCHEMA_VERSION,
            source_path=str(path),
            fingerprint="",
            duration_ms=0,
            tracks=(),
            selected_track_index=None,
            selected_track_is_fallback=False,
            chapters=(),
            required_metadata={},
            cover_present=False,
            eligible=False,
            ineligibility_reasons=("File does not exist or is not a regular file.",),
        )

    data = _run_ffprobe(path, ffprobe)
    if data is None:
        return MediaManifest(
            schema_version=SCHEMA_VERSION,
            source_path=str(path),
            fingerprint="",
            duration_ms=0,
            tracks=(),
            selected_track_index=None,
            selected_track_is_fallback=False,
            chapters=(),
            required_metadata={},
            cover_present=False,
            eligible=False,
            ineligibility_reasons=(
                "File could not be read by ffprobe (unreadable, corrupt, or "
                "not locally accessible).",
            ),
        )

    fmt = data.get("format", {})
    format_name = fmt.get("format_name", "")
    if not any(name in format_name for name in _MP4_FAMILY_FORMAT_NAMES):
        reasons.append(
            f"Container is not MP4/M4B-family (ffprobe format_name={format_name!r})."
        )

    try:
        duration_ms = int(round(float(fmt.get("duration", 0.0)) * 1000))
    except (TypeError, ValueError):
        duration_ms = 0

    streams = data.get("streams", [])
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]

    tracks: list[AudioTrack] = []
    for s in audio_streams:
        tracks.append(
            AudioTrack(
                index=s.get("index", -1),
                codec_name=s.get("codec_name"),
                is_default=s.get("disposition", {}).get("default") == 1,
                channels=s.get("channels"),
                sample_rate=(
                    int(s["sample_rate"])
                    if str(s.get("sample_rate", "")).isdigit()
                    else None
                ),
                bit_rate=(
                    int(s["bit_rate"]) if str(s.get("bit_rate", "")).isdigit() else None
                ),
            )
        )

    if not audio_streams:
        reasons.append("No audio track found.")
        selected_pos: int | None = None
        is_fallback = False
    else:
        selected_pos, is_fallback = _select_primary_track(audio_streams)

    selected_codec: str | None = None
    if selected_pos is not None:
        selected_codec = audio_streams[selected_pos].get("codec_name")
        if selected_codec != _ELIGIBLE_CODEC:
            reasons.append(
                f"Primary/default audio track is {selected_codec!r}, not AAC "
                "— non-AAC primary audio is unsupported in v1 (PRD D-05)."
            )

    chapters: list[ChapterInfo] = []
    for i, ch in enumerate(data.get("chapters", []), start=1):
        try:
            start_ms = int(round(float(ch.get("start_time", 0.0)) * 1000))
        except (TypeError, ValueError):
            start_ms = 0
        title = ch.get("tags", {}).get("title", f"Chapter {i}")
        chapters.append(ChapterInfo(index=i, title=title, start_ms=start_ms))

    tags = fmt.get("tags", {})
    required_metadata: dict[str, str] = {}
    for field_name, candidate_keys in _METADATA_TAG_CANDIDATES.items():
        for key in candidate_keys:
            val = tags.get(key)
            if val:
                required_metadata[field_name] = str(val).strip()
                break

    cover_present = any(
        v.get("disposition", {}).get("attached_pic") == 1 for v in video_streams
    )

    fingerprint = compute_fingerprint(path, duration_ms, selected_codec)

    global_selected_index = (
        audio_streams[selected_pos].get("index") if selected_pos is not None else None
    )

    return MediaManifest(
        schema_version=SCHEMA_VERSION,
        source_path=str(path),
        fingerprint=fingerprint,
        duration_ms=duration_ms,
        tracks=tuple(tracks),
        selected_track_index=global_selected_index,
        selected_track_is_fallback=is_fallback,
        chapters=tuple(chapters),
        required_metadata=required_metadata,
        cover_present=cover_present,
        eligible=(len(reasons) == 0),
        ineligibility_reasons=tuple(reasons),
    )
