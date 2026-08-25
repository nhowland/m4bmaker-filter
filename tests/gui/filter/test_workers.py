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
from m4bmaker.filter.models import MediaManifest
from m4bmaker.gui.filter.workers import (
    DownloadCoordinator,
    MediaInspectWorker,
    ModelDownloadWorker,
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
