"""Native transcript artifact schema and (de)serialization (PRD §10.3).

G2 scope: schema plus JSON round-trip only. Nothing here calls an STT
engine — every :class:`Transcript` used by G2 code (matcher tests, fixture
files) is either hand-built or loaded from a synthetic/native ``.m4bt.json``
fixture, per PRD §17.4 G2's explicit instruction to use "deterministic
synthetic/native transcript fixtures, not live STT." Real transcript
*generation* is G3, gated on ADR-0001's remaining open items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .models import SchemaValidationError
from .storage import read_json, write_json_atomic


class TranscriptStatus(Enum):
    """PRD §10.4: "Transcript statuses are draft, partial, complete, failed,
    and incompatible." A normal scan requires COMPLETE."""

    DRAFT = "draft"
    PARTIAL = "partial"
    COMPLETE = "complete"
    FAILED = "failed"
    INCOMPATIBLE = "incompatible"


class SegmentStatus(Enum):
    """Per-chunk durability status (PRD §11.3): a segment is either not yet
    transcribed, atomically committed, or failed. There is no "in progress"
    value on purpose — PRD §11.3 requires a chunk to be persisted atomically
    as a whole, so partial-segment state is never observable."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class TranscriptWord:
    """One recognized, timestamped word (PRD §10.3 ``segments[].words[]``)."""

    text: str
    normalized: str
    start_ms: int
    end_ms: int
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            raise SchemaValidationError("TranscriptWord.start_ms must not be negative.")
        if self.end_ms <= self.start_ms:
            raise SchemaValidationError(
                f"TranscriptWord.end_ms ({self.end_ms}) must be greater than "
                f"start_ms ({self.start_ms})."
            )


@dataclass(frozen=True)
class TranscriptSegment:
    """One durability chunk (PRD §11.3, §10.3 ``segments[]``)."""

    id: str
    start_ms: int
    end_ms: int
    status: SegmentStatus
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True)
class TranscriptSource:
    fingerprint: str
    duration_ms: int
    selected_audio_stream: int


@dataclass(frozen=True)
class TranscriptEngine:
    name: str
    version: str
    model: str
    model_checksum: str
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Transcript:
    """The full native artifact (PRD §10.3), extension ``.m4bt.json``."""

    schema_version: int
    status: TranscriptStatus
    source: TranscriptSource
    engine: TranscriptEngine
    segments: tuple[TranscriptSegment, ...] = ()

    def words(self) -> list[TranscriptWord]:
        """All words across all segments, in timeline order.

        Segments are assumed already ordered by ``start_ms`` (the artifact
        writer's responsibility, not re-sorted here — silently re-sorting
        would hide a producer bug rather than surface it).
        """
        result: list[TranscriptWord] = []
        for segment in self.segments:
            result.extend(segment.words)
        return result


def shift_segment(segment: TranscriptSegment, offset_ms: int) -> TranscriptSegment:
    """Return a copy of *segment* with every word's timestamps shifted by
    *offset_ms*.

    Needed because the STT engine only ever sees one chunk's audio slice at
    a time and reports timestamps relative to that slice's own start (0) —
    not the source file's global timeline. This maps a chunk's raw output
    back onto the source timeline before it's persisted or matched against.
    The segment's own ``start_ms``/``end_ms`` are left untouched, since
    callers set those directly from the chunk plan already, in global
    terms.
    """
    shifted_words = tuple(
        TranscriptWord(
            text=w.text,
            normalized=w.normalized,
            start_ms=w.start_ms + offset_ms,
            end_ms=w.end_ms + offset_ms,
            confidence=w.confidence,
        )
        for w in segment.words
    )
    return TranscriptSegment(
        id=segment.id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        status=segment.status,
        words=shifted_words,
    )


# ── (de)serialization ──────────────────────────────────────────────────────


def transcript_to_dict(transcript: Transcript) -> dict[str, Any]:
    """Serialize *transcript* to the exact JSON shape in PRD §10.3
    (camelCase keys, integer millisecond timestamps)."""
    return {
        "schemaVersion": transcript.schema_version,
        "status": transcript.status.value,
        "source": {
            "fingerprint": transcript.source.fingerprint,
            "durationMs": transcript.source.duration_ms,
            "selectedAudioStream": transcript.source.selected_audio_stream,
        },
        "engine": {
            "name": transcript.engine.name,
            "version": transcript.engine.version,
            "model": transcript.engine.model,
            "modelChecksum": transcript.engine.model_checksum,
            "parameters": dict(transcript.engine.parameters),
        },
        "segments": [
            {
                "id": seg.id,
                "startMs": seg.start_ms,
                "endMs": seg.end_ms,
                "status": seg.status.value,
                "words": [
                    {
                        "text": w.text,
                        "normalized": w.normalized,
                        "startMs": w.start_ms,
                        "endMs": w.end_ms,
                        "confidence": w.confidence,
                    }
                    for w in seg.words
                ],
            }
            for seg in transcript.segments
        ],
    }


def transcript_from_dict(data: dict[str, Any]) -> Transcript:
    """Parse the PRD §10.3 JSON shape into a :class:`Transcript`.

    Raises :class:`SchemaValidationError` (via the nested dataclasses'
    ``__post_init__``) or :class:`KeyError`/:class:`ValueError` on malformed
    input — the caller (a future Transcript Store) is responsible for
    turning that into a ``NEEDS_ATTENTION``/``incompatible`` job outcome
    rather than crashing (PRD §11.2), not this parsing function.
    """
    source = data["source"]
    engine = data["engine"]
    segments = tuple(
        TranscriptSegment(
            id=seg["id"],
            start_ms=seg["startMs"],
            end_ms=seg["endMs"],
            status=SegmentStatus(seg["status"]),
            words=tuple(
                TranscriptWord(
                    text=w["text"],
                    normalized=w["normalized"],
                    start_ms=w["startMs"],
                    end_ms=w["endMs"],
                    confidence=w.get("confidence"),
                )
                for w in seg.get("words", [])
            ),
        )
        for seg in data.get("segments", [])
    )
    return Transcript(
        schema_version=data["schemaVersion"],
        status=TranscriptStatus(data["status"]),
        source=TranscriptSource(
            fingerprint=source["fingerprint"],
            duration_ms=source["durationMs"],
            selected_audio_stream=source["selectedAudioStream"],
        ),
        engine=TranscriptEngine(
            name=engine["name"],
            version=engine["version"],
            model=engine["model"],
            model_checksum=engine["modelChecksum"],
            parameters=dict(engine.get("parameters", {})),
        ),
        segments=segments,
    )


def write_transcript(path: Path, transcript: Transcript) -> None:
    """Write *transcript* to *path* (conventionally ``*.m4bt.json``) atomically."""
    write_json_atomic(path, transcript_to_dict(transcript))


def read_transcript(path: Path) -> Transcript:
    """Read and parse a transcript artifact from *path*."""
    return transcript_from_dict(read_json(path))
