"""QThread workers for the filtering feature's UI (PRD §11.5).

Mirrors ``m4bmaker/gui/worker.py``'s existing convention exactly: one
``QThread`` subclass per long-running operation, a ``progress`` signal of
``(message, fraction)``, a ``threading.Event``-based ``request_cancel()``,
and ``result_ready``/``cancelled``/``error`` signals so the caller never
blocks the UI thread waiting on a return value.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from m4bmaker.filter.media_inspector import inspect
from m4bmaker.filter.model_manager import (
    ModelChecksumMismatchError,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelSpec,
    download_model,
)
from m4bmaker.utils import find_binary


class ModelDownloadWorker(QThread):
    """Run :func:`download_model` off the UI thread."""

    progress = Signal(str, float)  # message, 0.0-1.0
    result_ready = Signal(object)  # Path (installed file)
    cancelled = Signal()  # user cancellation (not an error)
    error = Signal(str)

    def __init__(self, spec: ModelSpec, dest_dir: Path) -> None:
        super().__init__()
        self._spec = spec
        self._dest_dir = dest_dir
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            path = download_model(
                self._spec,
                self._dest_dir,
                progress_callback=self._on_progress,
                cancel_event=self._cancel_event,
            )
            self.result_ready.emit(path)
        except ModelDownloadCancelled:
            self.cancelled.emit()
        except (ModelDownloadError, ModelChecksumMismatchError) as exc:
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            if self._cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.error.emit(str(exc))

    def _on_progress(self, downloaded: int, total: int) -> None:
        fraction = downloaded / total if total else 0.0
        mb_done = downloaded / (1024 * 1024)
        mb_total = total / (1024 * 1024)
        message = f"Downloading {self._spec.name}: {mb_done:.1f} / {mb_total:.1f} MB"
        self.progress.emit(message, fraction)


class MediaInspectWorker(QThread):
    """Run :func:`media_inspector.inspect` (an ffprobe subprocess call)
    off the UI thread.

    Uses :func:`m4bmaker.utils.find_binary`, not
    :func:`~m4bmaker.utils.find_ffprobe` — the latter calls ``sys.exit()``
    on a missing binary, correct for the CLI but not for a background
    thread inside a running GUI, which needs a recoverable ``error``
    signal instead (see ``find_binary``'s own docstring).
    """

    result_ready = Signal(object)  # MediaManifest
    error = Signal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        ffprobe = find_binary("ffprobe")
        if ffprobe is None:
            self.error.emit(
                "ffprobe not found. Install ffmpeg (which bundles ffprobe) "
                "and make sure it's on your PATH."
            )
            return
        try:
            manifest = inspect(self._path, ffprobe)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return
        self.result_ready.emit(manifest)
