"""Tests for m4bmaker.gui.filter.workers.ModelDownloadWorker.

Mirrors tests/gui/test_worker.py's established pattern for QThread
workers: patch the blocking call the worker wraps, connect signals to a
plain list, ``start()`` the real thread, ``wait()`` (bounded) for it to
finish, then ``qapp.processEvents()`` to flush the queued cross-thread
signal delivery before asserting.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QApplication

from unittest.mock import patch

from m4bmaker.filter.model_manager import (
    ModelChecksumMismatchError,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelSpec,
)
from m4bmaker.filter.job_store import JobStore, connect
from m4bmaker.filter.jobs import JobState, JobType
from m4bmaker.filter.models import MediaManifest
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.filter.transcription_orchestrator import TranscriptionPaused
from m4bmaker.gui.filter.workers import (
    DownloadCoordinator,
    MediaInspectWorker,
    ModelDownloadWorker,
    TranscribeWorker,
)

_SPEC = ModelSpec(
    name="tiny.en",
    label="Test",
    url="https://example.com/ggml-tiny.en.bin",
    sha256="deadbeef",
    size_bytes=1000,
)

_ProgressCallback = Callable[[int, int], None]


class TestModelDownloadWorker:
    def test_success_emits_result_with_progress(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        installed_path = tmp_path / _SPEC.filename()

        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            assert progress_callback is not None
            progress_callback(500, 1000)
            progress_callback(1000, 1000)
            return installed_path

        results: list[Path] = []
        progresses: list[tuple[str, float]] = []

        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            worker = ModelDownloadWorker(_SPEC, tmp_path)
            worker.result_ready.connect(results.append)
            worker.progress.connect(lambda msg, frac: progresses.append((msg, frac)))
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert results == [installed_path]
        assert progresses[-1][1] == 1.0
        assert "tiny.en" in progresses[0][0]

    def test_cancelled_emits_cancelled_signal(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            raise ModelDownloadCancelled("cancelled")

        cancelled: list[None] = []

        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            worker = ModelDownloadWorker(_SPEC, tmp_path)
            worker.cancelled.connect(lambda: cancelled.append(None))
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert len(cancelled) == 1

    def test_download_error_emits_error_signal(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.workers.download_model",
            side_effect=ModelDownloadError("network failure"),
        ):
            errors: list[str] = []
            worker = ModelDownloadWorker(_SPEC, tmp_path)
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert errors == ["network failure"]

    def test_checksum_mismatch_emits_error_signal(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.workers.download_model",
            side_effect=ModelChecksumMismatchError(_SPEC, "badhash"),
        ):
            errors: list[str] = []
            worker = ModelDownloadWorker(_SPEC, tmp_path)
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert len(errors) == 1
        assert "tiny.en" in errors[0]

    def test_request_cancel_sets_cancel_event(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        seen_cancel_events: list[threading.Event] = []

        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            assert cancel_event is not None
            seen_cancel_events.append(cancel_event)
            cancel_event.wait(2)
            raise ModelDownloadCancelled("cancelled")

        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            worker = ModelDownloadWorker(_SPEC, tmp_path)
            worker.start()
            worker.wait(200)  # let run() reach cancel_event.wait(2)
            worker.request_cancel()
            worker.wait(3000)

        assert len(seen_cancel_events) == 1
        assert seen_cancel_events[0].is_set()


def _manifest() -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path="/books/a.m4b",
        fingerprint="sha256:x",
        duration_ms=10_000,
        tracks=(),
        selected_track_index=None,
        selected_track_is_fallback=False,
        chapters=(),
        required_metadata={},
        cover_present=False,
        eligible=True,
    )


class TestMediaInspectWorker:
    def test_success_emits_manifest(self, qapp: QApplication, tmp_path: Path) -> None:
        manifest = _manifest()
        results: list[MediaManifest] = []

        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                return_value="/usr/bin/ffprobe",
            ),
            patch("m4bmaker.gui.filter.workers.inspect", return_value=manifest),
        ):
            worker = MediaInspectWorker(tmp_path / "book.m4b")
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert results == [manifest]

    def test_missing_ffprobe_emits_recoverable_error_not_sys_exit(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        errors: list[str] = []
        with patch("m4bmaker.gui.filter.workers.find_binary", return_value=None):
            worker = MediaInspectWorker(tmp_path / "book.m4b")
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert len(errors) == 1
        assert "ffprobe" in errors[0]

    def test_unexpected_exception_emits_error(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        errors: list[str] = []
        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "m4bmaker.gui.filter.workers.inspect",
                side_effect=RuntimeError("boom"),
            ),
        ):
            worker = MediaInspectWorker(tmp_path / "book.m4b")
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert errors == ["boom"]


class TestDownloadCoordinator:
    def test_starts_unclaimed(self) -> None:
        coordinator = DownloadCoordinator()
        assert coordinator.active_name is None

    def test_first_acquire_succeeds(self) -> None:
        coordinator = DownloadCoordinator()
        assert coordinator.try_acquire("base.en") is True
        assert coordinator.active_name == "base.en"

    def test_second_acquire_fails_while_first_held(self) -> None:
        coordinator = DownloadCoordinator()
        coordinator.try_acquire("base.en")
        assert coordinator.try_acquire("small.en") is False
        assert coordinator.active_name == "base.en"

    def test_release_frees_the_slot(self) -> None:
        coordinator = DownloadCoordinator()
        coordinator.try_acquire("base.en")
        coordinator.release()
        assert coordinator.active_name is None
        assert coordinator.try_acquire("small.en") is True

    def test_release_is_idempotent(self) -> None:
        coordinator = DownloadCoordinator()
        coordinator.release()
        coordinator.release()
        assert coordinator.active_name is None


def _transcribe_source() -> TranscriptSource:
    return TranscriptSource(
        fingerprint="sha256:x", duration_ms=10_000, selected_audio_stream=0
    )


def _make_transcript() -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=_transcribe_source(),
        engine=TranscriptEngine(
            name="whisper.cpp",
            version="1.9.2",
            model="base.en",
            model_checksum="abc",
        ),
        segments=(),
    )


class TestTranscribeWorker:
    def _worker(
        self, tmp_path: Path, mode: str, job_id: str = "job-1"
    ) -> TranscribeWorker:
        return TranscribeWorker(
            job_id,
            tmp_path / "filter.db",
            tmp_path / "source.m4b",
            tmp_path / "model.bin",
            "sha256:model",
            _transcribe_source(),
            tmp_path / "book.m4bt.json",
            900_000,
            10_000,
            None,
            mode,  # type: ignore[arg-type]
        )

    def _patched_binaries(self):
        return (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "m4bmaker.gui.filter.workers.find_whisper_cli",
                return_value="/usr/bin/whisper-cli",
            ),
        )

    def test_fresh_mode_creates_job_and_emits_result(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        p1, p2 = self._patched_binaries()
        with (
            p1,
            p2,
            patch(
                "m4bmaker.gui.filter.workers.run_transcription_job",
                return_value=_make_transcript(),
            ) as mock_run,
        ):
            worker = self._worker(tmp_path, "fresh")
            results: list[Transcript] = []
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert len(results) == 1
        assert results[0].status == TranscriptStatus.COMPLETE
        mock_run.assert_called_once()

        store = JobStore(connect(tmp_path / "filter.db"))
        job = store.get_job("job-1")
        assert job is not None
        assert job.job_type == JobType.TRANSCRIPTION
        assert job.resource == {"fingerprint": "sha256:x", "model": "model"}

    def test_progress_signal_relayed_from_orchestrator_callback(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        def fake_run(*args, **kwargs):
            kwargs["progress_callback"]("Transcribed chunk 1/2", 0.5)
            return _make_transcript()

        p1, p2 = self._patched_binaries()
        with (
            p1,
            p2,
            patch(
                "m4bmaker.gui.filter.workers.run_transcription_job",
                side_effect=fake_run,
            ),
        ):
            worker = self._worker(tmp_path, "fresh")
            progresses: list[tuple[str, float]] = []
            worker.progress.connect(lambda msg, frac: progresses.append((msg, frac)))
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert progresses == [("Transcribed chunk 1/2", 0.5)]

    def test_resume_mode_transitions_paused_job_to_resuming(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        store = JobStore(connect(tmp_path / "filter.db"))
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        store.transition("job-1", JobState.RUNNING)
        store.transition("job-1", JobState.PAUSING)
        store.transition("job-1", JobState.PAUSED)

        p1, p2 = self._patched_binaries()
        with (
            p1,
            p2,
            patch(
                "m4bmaker.gui.filter.workers.run_transcription_job",
                return_value=_make_transcript(),
            ),
        ):
            worker = self._worker(tmp_path, "resume")
            worker.start()
            worker.wait(3000)
        qapp.processEvents()

        job = store.get_job("job-1")
        assert job is not None
        assert job.state == JobState.RESUMING

    def test_retry_mode_transitions_needs_attention_through_queued_to_preparing(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        store = JobStore(connect(tmp_path / "filter.db"))
        store.create_job("job-1", JobType.TRANSCRIPTION)
        store.transition("job-1", JobState.PREPARING)
        store.transition("job-1", JobState.RUNNING)
        store.transition("job-1", JobState.NEEDS_ATTENTION)

        p1, p2 = self._patched_binaries()
        with (
            p1,
            p2,
            patch(
                "m4bmaker.gui.filter.workers.run_transcription_job",
                return_value=_make_transcript(),
            ),
        ):
            worker = self._worker(tmp_path, "retry")
            worker.start()
            worker.wait(3000)
        qapp.processEvents()

        job = store.get_job("job-1")
        assert job is not None
        assert job.state == JobState.PREPARING

    def test_paused_via_transcription_paused_emits_paused_signal(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        p1, p2 = self._patched_binaries()
        with (
            p1,
            p2,
            patch(
                "m4bmaker.gui.filter.workers.run_transcription_job",
                side_effect=TranscriptionPaused("paused"),
            ),
        ):
            worker = self._worker(tmp_path, "fresh")
            paused: list[None] = []
            cancelled: list[None] = []
            worker.paused.connect(lambda: paused.append(None))
            worker.cancelled.connect(lambda: cancelled.append(None))
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert len(paused) == 1
        assert len(cancelled) == 0

    def test_cancel_requested_then_paused_transitions_to_cancelled(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        p1, p2 = self._patched_binaries()
        with (
            p1,
            p2,
            patch(
                "m4bmaker.gui.filter.workers.run_transcription_job",
                side_effect=TranscriptionPaused("paused"),
            ),
        ):
            worker = self._worker(tmp_path, "fresh")
            cancelled: list[None] = []
            worker.cancelled.connect(lambda: cancelled.append(None))
            worker.request_cancel()
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert len(cancelled) == 1

        store = JobStore(connect(tmp_path / "filter.db"))
        job = store.get_job("job-1")
        assert job is not None
        assert job.state == JobState.CANCELLED

    def test_unexpected_exception_transitions_to_needs_attention(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        p1, p2 = self._patched_binaries()
        with (
            p1,
            p2,
            patch(
                "m4bmaker.gui.filter.workers.run_transcription_job",
                side_effect=RuntimeError("whisper-cli crashed"),
            ),
        ):
            worker = self._worker(tmp_path, "fresh")
            messages: list[str] = []
            worker.needs_attention.connect(messages.append)
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert messages == ["whisper-cli crashed"]

        store = JobStore(connect(tmp_path / "filter.db"))
        job = store.get_job("job-1")
        assert job is not None
        assert job.state == JobState.NEEDS_ATTENTION
        assert job.error_message == "whisper-cli crashed"

    def test_missing_ffmpeg_emits_error_without_touching_job_store(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        with patch("m4bmaker.gui.filter.workers.find_binary", return_value=None):
            worker = self._worker(tmp_path, "fresh")
            errors: list[str] = []
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert len(errors) == 1
        assert "ffmpeg" in errors[0]

        store = JobStore(connect(tmp_path / "filter.db"))
        assert store.get_job("job-1") is None

    def test_missing_whisper_cli_emits_error_without_touching_job_store(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("m4bmaker.gui.filter.workers.find_whisper_cli", return_value=None),
        ):
            worker = self._worker(tmp_path, "fresh")
            errors: list[str] = []
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert len(errors) == 1
        assert "whisper-cli" in errors[0]

        store = JobStore(connect(tmp_path / "filter.db"))
        assert store.get_job("job-1") is None
