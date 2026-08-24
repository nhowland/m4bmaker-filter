"""Durable, resumable TranscriptionJob orchestration (PRD §11.3).

Ties together ``chunking.py`` (the pure planning/merge algorithm),
``transcript_engine.py`` (the whisper.cpp adapter), ``job_store.py``
(durable job/chunk state), and ``transcript.py``'s atomic JSON persistence
into the actual TranscriptionJob business logic. This is the first
genuinely durable, resumable path in the fork: a paused job's committed
chunks survive a process restart, and resuming continues from the first
uncommitted chunk rather than restarting from zero — proven in
``tests/filter/test_transcription_orchestrator.py`` by literally
instantiating two independent runs against the same job/transcript state.

**Word-level deduplication happens here, before anything is persisted.**
Each chunk's raw whisper.cpp output still contains the full overlap
region; :func:`run_transcription_job` trims every committed segment down
to its chunk's *owned* word range (via ``chunking.merge_segment_words``)
before writing it to the transcript JSON. This means
``Transcript.words()`` is always already deduplicated for any transcript
this function produces — the Matcher (``matcher.py``, G2) does not need
its own dedup pass.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from m4bmaker.utils import subprocess_flags

from .chunking import ChunkPlan, merge_segment_words, plan_chunks
from .job_store import JobStore
from .jobs import JobState
from .transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    read_transcript,
    shift_segment,
    write_transcript,
)
from .transcript_engine import (
    get_whisper_version,
    run_whisper,
    whisper_result_to_segment,
)


class TranscriptionPaused(Exception):
    """Raised as a control-flow signal (not an error) when a pause request
    lands mid-run. The job is left in state ``PAUSED`` with everything
    committed so far durably persisted before this is raised — callers
    should treat it as a normal, expected outcome, not a failure."""


def _chunk_segment_id(index: int) -> str:
    return f"chunk-{index:06d}"


def _extract_chunk_wav(
    source_path: Path, start_ms: int, end_ms: int, ffmpeg: str, dest_path: Path
) -> None:
    """Extract ``[start_ms, end_ms)`` from *source_path* as 16kHz mono PCM
    WAV — whisper.cpp's required input format.

    Uses input-side seeking (``-ss``/``-to`` placed *before* ``-i``) rather
    than output-side seeking: for a multi-hour source, output-side seeking
    would decode from the very start of the file for every single chunk,
    which does not scale. Input-side seeking is fast (keyframe/sample
    based) at the cost of slightly less frame-exact boundaries — acceptable
    here since chunk boundaries already have deliberate overlap padding to
    absorb exactly this kind of imprecision.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start_ms / 1000}",
        "-to",
        f"{end_ms / 1000}",
        "-i",
        str(source_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dest_path),
    ]
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", **subprocess_flags()
    )
    if result.returncode != 0:
        stderr_tail = result.stderr.strip()[-1000:]
        raise RuntimeError(
            f"ffmpeg failed to extract chunk [{start_ms},{end_ms}): {stderr_tail}"
        )


def _load_committed_segments(
    transcript_path: Path,
    plans: list[ChunkPlan],
    store: JobStore,
    job_id: str,
) -> list[tuple[TranscriptSegment, ChunkPlan]]:
    """Reconstruct already-committed ``(segment, plan)`` pairs from a prior
    run, if any. A chunk only counts as committed if **both** the JSON
    transcript has its segment **and** the job store's SQLite row says so
    (see ``docs/adr/0005-job-orchestrator-persistence.md``'s write-ordering
    rationale) — if a crash landed between the two writes, the SQLite side
    (written second) will be missing and this chunk is correctly treated
    as not-yet-committed, so the main loop redoes it and overwrites the
    stale JSON segment. This is what makes resume self-healing rather than
    trusting on-disk state blindly.
    """
    if not transcript_path.exists():
        return []
    existing = read_transcript(transcript_path)
    by_id = {seg.id: seg for seg in existing.segments}
    result: list[tuple[TranscriptSegment, ChunkPlan]] = []
    for plan in plans:
        seg_id = _chunk_segment_id(plan.index)
        if store.is_chunk_committed(job_id, plan.index) and seg_id in by_id:
            result.append((by_id[seg_id], plan))
    return result


def run_transcription_job(
    job_id: str,
    store: JobStore,
    source_audio_path: Path,
    model_path: Path,
    model_checksum: str,
    source: TranscriptSource,
    transcript_path: Path,
    chunk_ms: int,
    overlap_ms: int,
    ffmpeg: str,
    *,
    language: str = "en",
    whisper_cli: str | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> Transcript:
    """Run (or resume) TranscriptionJob *job_id* to completion or pause.

    Resumable: already-committed chunks are skipped and the existing
    transcript at *transcript_path* is loaded and extended rather than
    overwritten from scratch. Returns the final, ``COMPLETE`` transcript
    when every planned chunk is done; raises :class:`TranscriptionPaused`
    if *should_pause* signals a pause before that point.

    The job must already exist in *store*, in a state from which
    ``RUNNING`` is reachable (``PREPARING`` after initial creation, or
    ``RESUMING`` after an explicit User Resume action) — those two prior
    transitions, and any pre-resume compatibility verification (PRD
    §11.3's "Verify source fingerprint, selected track, engine
    version/model checksum... before resume"), are the caller's
    responsibility, not this function's.
    """
    job = store.get_job(job_id)
    if job is None:
        raise KeyError(f"Unknown job_id: {job_id!r}")
    if job.state in (JobState.PREPARING, JobState.RESUMING):
        store.transition(job_id, JobState.RUNNING)

    plans = plan_chunks(source.duration_ms, chunk_ms, overlap_ms)
    committed = _load_committed_segments(transcript_path, plans, store, job_id)
    committed_indices = {plan.index for _, plan in committed}
    engine_version = get_whisper_version(whisper_cli) or "unknown"

    def _engine() -> TranscriptEngine:
        return TranscriptEngine(
            name="whisper.cpp",
            version=engine_version,
            model=model_path.stem,
            model_checksum=model_checksum,
            parameters={"language": language, "gpu": "false"},
        )

    def _write(status: TranscriptStatus) -> Transcript:
        ordered = sorted(committed, key=lambda pair: pair[1].index)
        transcript = Transcript(
            schema_version=1,
            status=status,
            source=source,
            engine=_engine(),
            segments=tuple(seg for seg, _ in ordered),
        )
        write_transcript(transcript_path, transcript)
        return transcript

    with tempfile.TemporaryDirectory() as tmp:
        for plan in plans:
            if plan.index in committed_indices:
                continue

            if should_pause is not None and should_pause():
                store.transition(
                    job_id, JobState.PAUSING, "Pausing after current chunk..."
                )
                store.transition(
                    job_id, JobState.PAUSED, f"Paused before chunk {plan.index}."
                )
                raise TranscriptionPaused(
                    f"Job {job_id} paused before chunk {plan.index}."
                )

            chunk_wav = Path(tmp) / f"{_chunk_segment_id(plan.index)}.wav"
            _extract_chunk_wav(
                source_audio_path, plan.start_ms, plan.end_ms, ffmpeg, chunk_wav
            )

            raw = run_whisper(
                chunk_wav, model_path, language=language, whisper_cli=whisper_cli
            )
            raw_segment = whisper_result_to_segment(
                raw,
                segment_id=_chunk_segment_id(plan.index),
                segment_start_ms=plan.start_ms,
                segment_end_ms=plan.end_ms,
            )
            global_segment = shift_segment(raw_segment, offset_ms=plan.start_ms)

            owned_words = merge_segment_words([(global_segment, plan)])
            persisted_segment = TranscriptSegment(
                id=global_segment.id,
                start_ms=plan.start_ms,
                end_ms=plan.end_ms,
                status=global_segment.status,
                words=tuple(owned_words),
            )

            committed.append((persisted_segment, plan))
            committed_indices.add(plan.index)

            _write(TranscriptStatus.PARTIAL)
            store.commit_chunk(
                job_id,
                plan.index,
                plan.start_ms,
                plan.end_ms,
                plan.owned_start_ms,
                plan.owned_end_ms,
            )
            store.update_progress(
                job_id,
                f"Transcribed chunk {plan.index + 1}/{len(plans)}",
                (plan.index + 1) / len(plans) if plans else 1.0,
            )

    final_transcript = _write(TranscriptStatus.COMPLETE)
    store.transition(job_id, JobState.COMPLETED, "Transcription complete.")
    return final_transcript
