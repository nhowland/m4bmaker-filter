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

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.model_manager import (
    ModelChecksumMismatchError,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelSpec,
)
from m4bmaker.filter.job_store import JobStore, connect
from m4bmaker.filter.jobs import JobState, JobType
from m4bmaker.filter.models import (
    NORMALIZATION_VERSION,
    AttenuationSettings,
    AudioTrack,
    FilterProfileSnapshot,
    MediaManifest,
    RenderPlan,
)
from m4bmaker.filter.renderer import RenderError, RenderResult
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.filter.transcript_engine import whisper_missing_message
from m4bmaker.filter.transcription_orchestrator import TranscriptionPaused
from m4bmaker.filter.validator import ValidationReport
from m4bmaker.gui.filter.workers import (
    CoverArtWorker,
    DownloadCoordinator,
    MediaInspectWorker,
    ModelDownloadWorker,
    RenderWorker,
    ScanWorker,
    TranscribeWorker,
    TranscriptLookupWorker,
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


class TestCoverArtWorker:
    def test_success_emits_the_extracted_path(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        cover = tmp_path / "cover.jpg"
        results: list[Path | None] = []

        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "m4bmaker.gui.filter.workers.extract_cover_from_audio",
                return_value=cover,
            ),
        ):
            worker = CoverArtWorker(tmp_path / "book.m4b")
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert results == [cover]

    def test_missing_ffmpeg_emits_none_not_an_error(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        # Purely a preview convenience (ADR-0046) -- unlike
        # MediaInspectWorker, a missing ffmpeg here isn't a recoverable-
        # error condition worth surfacing; the wizard's other steps
        # already report that loudly when it actually matters.
        results: list[Path | None] = []
        with patch("m4bmaker.gui.filter.workers.find_binary", return_value=None):
            worker = CoverArtWorker(tmp_path / "book.m4b")
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert results == [None]

    def test_no_cover_found_emits_none(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        results: list[Path | None] = []
        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "m4bmaker.gui.filter.workers.extract_cover_from_audio",
                return_value=None,
            ),
        ):
            worker = CoverArtWorker(tmp_path / "book.m4b")
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert results == [None]

    def test_unexpected_exception_emits_none(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        # No error signal at all on this worker, by design (see its own
        # docstring) -- an unexpected extraction failure just resolves
        # to "no art to show", same as any other failure mode here.
        results: list[Path | None] = []
        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "m4bmaker.gui.filter.workers.extract_cover_from_audio",
                side_effect=RuntimeError("boom"),
            ),
        ):
            worker = CoverArtWorker(tmp_path / "book.m4b")
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert results == [None]


class TestTranscriptLookupWorker:
    def test_match_found_emits_the_transcript(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        transcript = Transcript(
            schema_version=1,
            status=TranscriptStatus.COMPLETE,
            source=TranscriptSource(
                fingerprint="sha256:real",
                duration_ms=48_693_108,
                selected_audio_stream=0,
            ),
            engine=TranscriptEngine(
                name="whisper.cpp",
                version="1.9.2",
                model="base.en",
                model_checksum="sha256:c",
            ),
            segments=(),
        )
        results: list[Transcript | None] = []
        with patch(
            "m4bmaker.gui.filter.workers.find_compatible_transcript",
            return_value=transcript,
        ) as mock_lookup:
            worker = TranscriptLookupWorker("sha256:real")
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)
            mock_lookup.assert_called_once_with("sha256:real")

        qapp.processEvents()
        assert results == [transcript]

    def test_no_match_emits_none(self, qapp: QApplication, tmp_path: Path) -> None:
        results: list[Transcript | None] = []
        with patch(
            "m4bmaker.gui.filter.workers.find_compatible_transcript",
            return_value=None,
        ):
            worker = TranscriptLookupWorker("sha256:nomatch")
            worker.result_ready.connect(results.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert results == [None]


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
        assert errors[0] == whisper_missing_message()

        store = JobStore(connect(tmp_path / "filter.db"))
        assert store.get_job("job-1") is None


def _make_snapshot() -> FilterProfileSnapshot:
    service = CatalogService()
    category = service.create_category("Profanity")
    entry, _ = service.create_entry(category.id, "darn")
    profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
    return service.create_snapshot(profile.id)


class TestScanWorker:
    def test_success_emits_scan(self, qapp: QApplication) -> None:
        transcript = _make_transcript()
        snapshot = _make_snapshot()
        results: list[object] = []

        worker = ScanWorker(transcript, snapshot, NORMALIZATION_VERSION)
        worker.result_ready.connect(results.append)
        worker.start()
        worker.wait(3000)
        qapp.processEvents()

        assert len(results) == 1
        scan = results[0]
        assert scan.profile_snapshot is snapshot
        assert scan.transcript_schema_version == transcript.schema_version
        assert scan.source_fingerprint == transcript.source.fingerprint

    def test_unexpected_exception_emits_error(self, qapp: QApplication) -> None:
        transcript = _make_transcript()
        snapshot = _make_snapshot()
        errors: list[str] = []

        with patch(
            "m4bmaker.gui.filter.workers.run_scan", side_effect=RuntimeError("boom")
        ):
            worker = ScanWorker(transcript, snapshot, NORMALIZATION_VERSION)
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert errors == ["boom"]


def _manifest_with_track(
    bit_rate: int | None = 126_000,
    codec_name: str | None = "aac",
) -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path="/books/a.m4b",
        fingerprint="sha256:x",
        duration_ms=10_000,
        tracks=(
            AudioTrack(
                index=0,
                codec_name=codec_name,
                is_default=True,
                channels=2,
                sample_rate=44_100,
                bit_rate=bit_rate,
            ),
        ),
        selected_track_index=0,
        selected_track_is_fallback=False,
        chapters=(),
        required_metadata={},
        cover_present=False,
        eligible=True,
    )


def _empty_plan(duration_ms: int = 10_000) -> RenderPlan:
    return RenderPlan(
        intervals=(), attenuation=AttenuationSettings(), source_duration_ms=duration_ms
    )


class TestRenderWorker:
    def test_success_emits_result_and_validation(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        # ADR-0029: the worker no longer writes the filter report itself
        # (moved to RenderStep, which has the Transcript/Scan/timings
        # context a useful report needs and this worker never held) — it
        # only ever reports the bare RenderResult/ValidationReport.
        manifest = _manifest_with_track()
        plan = _empty_plan()
        output_path = tmp_path / "out.m4b"
        fake_result = RenderResult(output_path=output_path, duration_ms=10_000)
        fake_validation = ValidationReport(issues=())

        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            patch(
                "m4bmaker.gui.filter.workers.render", return_value=fake_result
            ) as mock_render,
            patch("m4bmaker.gui.filter.workers.inspect", return_value=manifest),
            patch("m4bmaker.gui.filter.workers.validate", return_value=fake_validation),
        ):
            worker = RenderWorker(
                Path("/books/a.m4b"), manifest, plan, output_path, "128k"
            )
            results: list[tuple] = []
            worker.result_ready.connect(lambda *args: results.append(args))
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert len(results) == 1
        result, validation = results[0]
        assert result is fake_result
        assert validation is fake_validation
        mock_render.assert_called_once()
        assert mock_render.call_args.args[0] == Path("/books/a.m4b")
        assert mock_render.call_args.args[6] == "128k"

    def test_failed_validation_still_emits_result_ready_not_error(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """The worker only reports what happened — deciding that a failed
        ValidationReport means "needs attention" is the step's job, not
        the worker's (mirrors ScanWorker's own "worker reports, step
        decides" split)."""
        manifest = _manifest_with_track()
        plan = _empty_plan()
        output_path = tmp_path / "out.m4b"
        fake_result = RenderResult(output_path=output_path, duration_ms=10_000)
        from m4bmaker.filter.validator import Severity, ValidationIssue

        failing_validation = ValidationReport(
            issues=(
                ValidationIssue(
                    check="duration", severity=Severity.ERROR, message="bad"
                ),
            )
        )

        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            patch("m4bmaker.gui.filter.workers.render", return_value=fake_result),
            patch("m4bmaker.gui.filter.workers.inspect", return_value=manifest),
            patch(
                "m4bmaker.gui.filter.workers.validate",
                return_value=failing_validation,
            ),
        ):
            worker = RenderWorker(
                Path("/books/a.m4b"), manifest, plan, output_path, "128k"
            )
            results: list[tuple] = []
            errors: list[str] = []
            worker.result_ready.connect(lambda *args: results.append(args))
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert errors == []
        assert len(results) == 1
        assert results[0][1].passed is False

    def test_missing_ffmpeg_emits_recoverable_error(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        errors: list[str] = []
        with patch("m4bmaker.gui.filter.workers.find_binary", return_value=None):
            worker = RenderWorker(
                Path("/books/a.m4b"),
                _manifest_with_track(),
                _empty_plan(),
                tmp_path / "out.m4b",
                "128k",
            )
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert len(errors) == 1
        assert "ffmpeg" in errors[0]

    def test_missing_ffprobe_emits_recoverable_error(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        errors: list[str] = []
        with patch(
            "m4bmaker.gui.filter.workers.find_binary",
            side_effect=lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
        ):
            worker = RenderWorker(
                Path("/books/a.m4b"),
                _manifest_with_track(),
                _empty_plan(),
                tmp_path / "out.m4b",
                "128k",
            )
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert len(errors) == 1
        assert "ffprobe" in errors[0]

    def test_render_error_emits_error_not_result_ready(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        errors: list[str] = []
        results: list[tuple] = []
        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            patch(
                "m4bmaker.gui.filter.workers.render",
                side_effect=RenderError("ffmpeg exploded"),
            ),
        ):
            worker = RenderWorker(
                Path("/books/a.m4b"),
                _manifest_with_track(),
                _empty_plan(),
                tmp_path / "out.m4b",
                "128k",
            )
            worker.error.connect(errors.append)
            worker.result_ready.connect(lambda *args: results.append(args))
            worker.start()
            worker.wait(3000)
        qapp.processEvents()
        assert errors == ["ffmpeg exploded"]
        assert results == []

    def test_validate_exception_emits_error(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        manifest = _manifest_with_track()
        output_path = tmp_path / "out.m4b"
        fake_result = RenderResult(output_path=output_path, duration_ms=10_000)
        errors: list[str] = []

        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            patch("m4bmaker.gui.filter.workers.render", return_value=fake_result),
            patch("m4bmaker.gui.filter.workers.inspect", return_value=manifest),
            patch(
                "m4bmaker.gui.filter.workers.validate",
                side_effect=RuntimeError("validate boom"),
            ),
        ):
            worker = RenderWorker(
                Path("/books/a.m4b"), manifest, _empty_plan(), output_path, "128k"
            )
            worker.error.connect(errors.append)
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert errors == ["validate boom"]

    def test_validating_signal_fires_between_render_and_result(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        manifest = _manifest_with_track()
        output_path = tmp_path / "out.m4b"
        fake_result = RenderResult(output_path=output_path, duration_ms=10_000)
        fake_validation = ValidationReport(issues=())
        events: list[str] = []

        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            patch("m4bmaker.gui.filter.workers.render", return_value=fake_result),
            patch("m4bmaker.gui.filter.workers.inspect", return_value=manifest),
            patch("m4bmaker.gui.filter.workers.validate", return_value=fake_validation),
        ):
            worker = RenderWorker(
                Path("/books/a.m4b"), manifest, _empty_plan(), output_path, "128k"
            )
            worker.validating.connect(lambda: events.append("validating"))
            worker.result_ready.connect(lambda *a: events.append("result_ready"))
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert events == ["validating", "result_ready"]

    def test_validate_receives_a_progress_callback_that_emits_the_signal(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        # ADR-0044: validate()'s own attenuation check reports per-
        # interval progress — the worker must pass a real callback
        # through, not just accept the kwarg silently, and that callback
        # must land as a real Qt signal a UI-thread listener can connect
        # to (the same cross-thread emit pattern `progress` already
        # uses for render()'s own callback).
        manifest = _manifest_with_track()
        output_path = tmp_path / "out.m4b"
        fake_result = RenderResult(output_path=output_path, duration_ms=10_000)
        fake_validation = ValidationReport(issues=())
        received: list[tuple[str, float]] = []

        def _fake_validate(*args: object, **kwargs: object) -> ValidationReport:
            callback = kwargs["progress_callback"]
            assert callback is not None
            callback("Validating output… (1 of 2)", 0.5)  # type: ignore[operator]
            callback("Validating output… (2 of 2)", 1.0)  # type: ignore[operator]
            return fake_validation

        with (
            patch(
                "m4bmaker.gui.filter.workers.find_binary",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            patch("m4bmaker.gui.filter.workers.render", return_value=fake_result),
            patch("m4bmaker.gui.filter.workers.inspect", return_value=manifest),
            patch("m4bmaker.gui.filter.workers.validate", side_effect=_fake_validate),
        ):
            worker = RenderWorker(
                Path("/books/a.m4b"), manifest, _empty_plan(), output_path, "128k"
            )
            worker.validating_progress.connect(
                lambda msg, frac: received.append((msg, frac))
            )
            worker.start()
            worker.wait(3000)

        qapp.processEvents()
        assert received == [
            ("Validating output… (1 of 2)", 0.5),
            ("Validating output… (2 of 2)", 1.0),
        ]
