"""Tests for m4bmaker.filter.transcription_orchestrator — durable, resumable
TranscriptionJob (PRD §11.3).

whisper.cpp calls and ffmpeg chunk extraction are mocked throughout except
for one opt-in real-binary/real-model test, matching the same
M4BMAKER_WHISPER_MODEL gating used in test_transcript_engine.py.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from m4bmaker.filter.job_store import JobStore, connect
from m4bmaker.filter.jobs import JobState, JobType
from m4bmaker.filter.transcript import (
    TranscriptSource,
    TranscriptStatus,
    read_transcript,
)
from m4bmaker.filter.transcription_orchestrator import (
    TranscriptionPaused,
    run_transcription_job,
)

_ORCH = "m4bmaker.filter.transcription_orchestrator"


def _raw_whisper_json(tokens: list[tuple[str, int, int, float]]) -> dict[str, Any]:
    return {
        "transcription": [
            {
                "tokens": [
                    {"text": text, "offsets": {"from": start, "to": end}, "p": p}
                    for text, start, end, p in tokens
                ]
            }
        ]
    }


# duration=50000, chunk_ms=30000, overlap_ms=5000 -> exactly 2 chunks:
# chunk0 [0,30000) owns [0,25000); chunk1 [25000,50000) owns [25000,50000).
CHUNK0_JSON = _raw_whisper_json(
    [
        (" a", 1000, 1400, 0.9),
        (" boundary", 26000, 26400, 0.8),  # local=global here; in overlap tail
    ]
)
CHUNK1_JSON = _raw_whisper_json(
    [
        (" boundary", 1000, 1400, 0.85),  # local 1000 + chunk1 start 25000 = 26000
        (" b", 15000, 15400, 0.95),  # local 15000 + 25000 = 40000
    ]
)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(connect(tmp_path / "filter.db"))


def _make_source(duration_ms: int = 50_000) -> TranscriptSource:
    return TranscriptSource(
        fingerprint="sha256:test", duration_ms=duration_ms, selected_audio_stream=0
    )


def _patched(responses: list[dict[str, Any]]):
    return (
        patch(f"{_ORCH}._extract_chunk_wav"),
        patch(f"{_ORCH}.run_whisper", side_effect=lambda *a, **k: responses.pop(0)),
        patch(f"{_ORCH}.get_whisper_version", return_value="1.9.2"),
    )


class TestHappyPath:
    def test_completes_and_persists_deduplicated_transcript(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        transcript_path = tmp_path / "book.m4bt.json"

        p1, p2, p3 = _patched([CHUNK0_JSON, CHUNK1_JSON])
        with p1, p2, p3:
            transcript = run_transcription_job(
                "job-1",
                store,
                tmp_path / "source.wav",
                tmp_path / "model.bin",
                "sha256:model",
                _make_source(),
                transcript_path,
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
            )

        assert transcript.status is TranscriptStatus.COMPLETE
        words = [w.text.strip() for w in transcript.words()]
        assert words == ["a", "boundary", "b"]  # deduped, boundary kept once

    def test_job_transitions_to_completed(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        p1, p2, p3 = _patched([CHUNK0_JSON, CHUNK1_JSON])
        with p1, p2, p3:
            run_transcription_job(
                "job-1",
                store,
                tmp_path / "source.wav",
                tmp_path / "model.bin",
                "sha256:model",
                _make_source(),
                tmp_path / "book.m4bt.json",
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
            )
        job = store.get_job("job-1")
        assert job is not None
        assert job.state is JobState.COMPLETED

    def test_both_chunks_marked_committed(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        p1, p2, p3 = _patched([CHUNK0_JSON, CHUNK1_JSON])
        with p1, p2, p3:
            run_transcription_job(
                "job-1",
                store,
                tmp_path / "source.wav",
                tmp_path / "model.bin",
                "sha256:model",
                _make_source(),
                tmp_path / "book.m4bt.json",
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
            )
        chunks = store.list_committed_chunks("job-1")
        assert [c.chunk_index for c in chunks] == [0, 1]

    def test_unknown_job_raises(self, store: JobStore, tmp_path: Path) -> None:
        with pytest.raises(KeyError):
            run_transcription_job(
                "nonexistent",
                store,
                tmp_path / "source.wav",
                tmp_path / "model.bin",
                "sha256:model",
                _make_source(),
                tmp_path / "book.m4bt.json",
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
            )


class TestChapterAwareChunking:
    def test_chapter_start_times_change_extracted_chunk_boundaries(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        # duration=50000, chunk_ms=30000, overlap_ms=5000: uniform planning
        # would produce exactly 2 chunks at [0,25000)/[25000,50000). A
        # chapter break at 10000 that isn't a uniform-planning boundary
        # forces a different split into 3 chunks instead, proving the
        # chapter data actually reached plan_chunks rather than being
        # silently ignored.
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)

        captured_boundaries: list[tuple[int, int]] = []

        def _fake_extract(source_path, start_ms, end_ms, ffmpeg, dest_path):
            captured_boundaries.append((start_ms, end_ms))
            dest_path.touch()

        responses = [
            _raw_whisper_json([]),
            _raw_whisper_json([]),
            _raw_whisper_json([]),
        ]
        with (
            patch(f"{_ORCH}._extract_chunk_wav", side_effect=_fake_extract),
            patch(f"{_ORCH}.run_whisper", side_effect=lambda *a, **k: responses.pop(0)),
            patch(f"{_ORCH}.get_whisper_version", return_value="1.9.2"),
        ):
            run_transcription_job(
                "job-1",
                store,
                tmp_path / "source.wav",
                tmp_path / "model.bin",
                "sha256:model",
                _make_source(50_000),
                tmp_path / "book.m4bt.json",
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
                chapter_start_times_ms=[0, 10_000],
            )

        # Chapter-aware plan (owned): (0,10000), (10000,35000), (35000,50000)
        # -- the 40s second chapter exceeds the 30000ms budget and splits at
        # step=25000. Each audio *slice* extends owned_end by overlap_ms
        # (clamped to duration), matching ChunkPlan's trailing-padding rule.
        assert captured_boundaries == [(0, 15_000), (10_000, 40_000), (35_000, 50_000)]

    def test_no_chapter_data_uses_uniform_boundaries(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)

        captured_boundaries: list[tuple[int, int]] = []

        def _fake_extract(source_path, start_ms, end_ms, ffmpeg, dest_path):
            captured_boundaries.append((start_ms, end_ms))
            dest_path.touch()

        _, p2, p3 = _patched([CHUNK0_JSON, CHUNK1_JSON])
        with (
            patch(f"{_ORCH}._extract_chunk_wav", side_effect=_fake_extract),
            p2,
            p3,
        ):
            run_transcription_job(
                "job-1",
                store,
                tmp_path / "source.wav",
                tmp_path / "model.bin",
                "sha256:model",
                _make_source(),
                tmp_path / "book.m4bt.json",
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
            )

        assert captured_boundaries == [(0, 30_000), (25_000, 50_000)]


class TestPauseAndResume:
    def test_pause_before_second_chunk_persists_first_and_stops(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        transcript_path = tmp_path / "book.m4bt.json"

        pause_after_first = iter(
            [False, True]
        )  # don't pause before chunk0, do before chunk1
        p1, p2, p3 = _patched([CHUNK0_JSON, CHUNK1_JSON])
        with p1, p2, p3:
            with pytest.raises(TranscriptionPaused):
                run_transcription_job(
                    "job-1",
                    store,
                    tmp_path / "source.wav",
                    tmp_path / "model.bin",
                    "sha256:model",
                    _make_source(),
                    transcript_path,
                    chunk_ms=30_000,
                    overlap_ms=5_000,
                    ffmpeg="ffmpeg",
                    should_pause=lambda: next(pause_after_first),
                )

        job = store.get_job("job-1")
        assert job is not None
        assert job.state is JobState.PAUSED
        assert [c.chunk_index for c in store.list_committed_chunks("job-1")] == [0]

        persisted = read_transcript(transcript_path)
        assert persisted.status is TranscriptStatus.PARTIAL
        assert [w.text.strip() for w in persisted.words()] == ["a"]

    def test_resume_skips_already_committed_chunks(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        transcript_path = tmp_path / "book.m4bt.json"
        source_path = tmp_path / "source.wav"
        model_path = tmp_path / "model.bin"
        source = _make_source()

        # First run: pause before chunk1.
        pause_flag = iter([False, True])
        p1, p2, p3 = _patched([CHUNK0_JSON, CHUNK1_JSON])
        with p1, p2, p3:
            with pytest.raises(TranscriptionPaused):
                run_transcription_job(
                    "job-1",
                    store,
                    source_path,
                    model_path,
                    "sha256:model",
                    source,
                    transcript_path,
                    chunk_ms=30_000,
                    overlap_ms=5_000,
                    ffmpeg="ffmpeg",
                    should_pause=lambda: next(pause_flag),
                )

        # Simulate the app restarting: brand-new JobStore backed by the same
        # (now-persisted) sqlite file and a fresh call into the orchestrator.
        store.transition("job-1", JobState.RESUMING)

        # Only CHUNK1_JSON is queued this time — if the resumed run tried to
        # re-transcribe chunk0, popping from an empty list would raise
        # IndexError and fail the test.
        p1, p2, p3 = _patched([CHUNK1_JSON])
        with p1, p2, p3:
            final = run_transcription_job(
                "job-1",
                store,
                source_path,
                model_path,
                "sha256:model",
                source,
                transcript_path,
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
            )

        assert final.status is TranscriptStatus.COMPLETE
        words = [w.text.strip() for w in final.words()]
        assert words == ["a", "boundary", "b"]
        job = store.get_job("job-1")
        assert job is not None
        assert job.state is JobState.COMPLETED
        assert [c.chunk_index for c in store.list_committed_chunks("job-1")] == [0, 1]

    def test_resume_across_a_genuinely_new_store_connection(
        self, tmp_path: Path
    ) -> None:
        """Stronger resumability proof: the second run uses a brand-new
        JobStore/connection over the same db file, not the same Python
        object — closer to what actually happens across an app restart."""
        db_path = tmp_path / "filter.db"
        transcript_path = tmp_path / "book.m4bt.json"
        source = _make_source()

        store1 = JobStore(connect(db_path))
        store1.create_job("job-1", JobType.TRANSCRIPTION)
        store1.transition("job-1", JobState.PREPARING)

        pause_flag = iter([False, True])
        p1, p2, p3 = _patched([CHUNK0_JSON, CHUNK1_JSON])
        with p1, p2, p3:
            with pytest.raises(TranscriptionPaused):
                run_transcription_job(
                    "job-1",
                    store1,
                    tmp_path / "source.wav",
                    tmp_path / "model.bin",
                    "sha256:model",
                    source,
                    transcript_path,
                    chunk_ms=30_000,
                    overlap_ms=5_000,
                    ffmpeg="ffmpeg",
                    should_pause=lambda: next(pause_flag),
                )

        store2 = JobStore(connect(db_path))  # fresh connection, new object
        store2.transition("job-1", JobState.RESUMING)
        p1, p2, p3 = _patched([CHUNK1_JSON])
        with p1, p2, p3:
            final = run_transcription_job(
                "job-1",
                store2,
                tmp_path / "source.wav",
                tmp_path / "model.bin",
                "sha256:model",
                source,
                transcript_path,
                chunk_ms=30_000,
                overlap_ms=5_000,
                ffmpeg="ffmpeg",
            )
        assert final.status is TranscriptStatus.COMPLETE
        assert [w.text.strip() for w in final.words()] == ["a", "boundary", "b"]


class TestFailurePropagation:
    def test_ffmpeg_failure_propagates_and_does_not_complete_job(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        with (
            patch(
                f"{_ORCH}._extract_chunk_wav", side_effect=RuntimeError("ffmpeg boom")
            ),
            patch(f"{_ORCH}.get_whisper_version", return_value="1.9.2"),
        ):
            with pytest.raises(RuntimeError, match="ffmpeg boom"):
                run_transcription_job(
                    "job-1",
                    store,
                    tmp_path / "source.wav",
                    tmp_path / "model.bin",
                    "sha256:model",
                    _make_source(),
                    tmp_path / "book.m4bt.json",
                    chunk_ms=30_000,
                    overlap_ms=5_000,
                    ffmpeg="ffmpeg",
                )
        job = store.get_job("job-1")
        assert job is not None
        assert job.state is JobState.RUNNING  # not silently marked complete/failed
        assert job.state is not JobState.COMPLETED


@pytest.mark.skipif(
    not os.environ.get("M4BMAKER_WHISPER_MODEL"),
    reason=(
        "Opt-in real-binary integration test. Set M4BMAKER_WHISPER_MODEL to "
        "a local ggml model path and have whisper-cli and ffmpeg on PATH. "
        "Skipped by default and in CI."
    ),
)
class TestRealPauseResume:
    def test_real_chunked_transcription_with_pause_and_resume(
        self, tmp_path: Path
    ) -> None:
        assert shutil.which("whisper-cli") is not None
        assert shutil.which("ffmpeg") is not None
        model_path = Path(os.environ["M4BMAKER_WHISPER_MODEL"])
        assert model_path.exists()

        source_wav = tmp_path / "speech.wav"
        aiff = tmp_path / "speech.aiff"
        os.system(
            f'say -o "{aiff}" '
            '"This is a test of the darn filtering system. '
            'Go to hell and back, my friend."'
        )
        os.system(
            f'ffmpeg -y -i "{aiff}" -ar 16000 -ac 1 -c:a pcm_s16le "{source_wav}"'
        )

        duration_ms = 4600  # slightly over the ~4.5s fixture
        source = TranscriptSource(
            fingerprint="sha256:real-orchestrator-test",
            duration_ms=duration_ms,
            selected_audio_stream=0,
        )
        db_path = tmp_path / "filter.db"
        transcript_path = tmp_path / "book.m4bt.json"

        store1 = JobStore(connect(db_path))
        store1.create_job("job-1", JobType.TRANSCRIPTION)
        store1.transition("job-1", JobState.PREPARING)

        # Two real overlapping chunks with a 1s overlap.
        pause_flag = iter([False, True])
        with pytest.raises(TranscriptionPaused):
            run_transcription_job(
                "job-1",
                store1,
                source_wav,
                model_path,
                model_checksum="unverified-in-this-test",
                source=source,
                transcript_path=transcript_path,
                chunk_ms=3000,
                overlap_ms=1000,
                ffmpeg="ffmpeg",
                should_pause=lambda: next(pause_flag),
            )

        paused_job = store1.get_job("job-1")
        assert paused_job is not None
        assert paused_job.state is JobState.PAUSED

        store2 = JobStore(connect(db_path))
        store2.transition("job-1", JobState.RESUMING)
        final = run_transcription_job(
            "job-1",
            store2,
            source_wav,
            model_path,
            model_checksum="unverified-in-this-test",
            source=source,
            transcript_path=transcript_path,
            chunk_ms=3000,
            overlap_ms=1000,
            ffmpeg="ffmpeg",
        )

        assert final.status is TranscriptStatus.COMPLETE
        words = [w.text.strip().lower() for w in final.words()]
        assert "darn" in words
        assert "hell" in words
        assert words.count("hell") == 1  # confirms real dedup, not just presence
