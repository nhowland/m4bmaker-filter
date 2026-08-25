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


class DownloadCoordinator:
    """Process-wide "one model download at a time" guard, shared across
    every window that can start one — ``ModelManagerWindow`` and the
    wizard's Transcript step (ADR-0014). ADR-0009's original guard
    (``ModelManagerWindow._downloading_name``) was scoped to that
    window's own state only, so nothing stopped both windows from
    starting a download of the *same* model file at once if a User had
    both open simultaneously — this closes that gap with one shared
    instance (:data:`download_coordinator` below) both windows check.

    Deliberately not thread-safe beyond what a single Qt UI thread
    already guarantees: every caller (``_on_download_clicked`` in both
    windows) runs on the UI thread, so a plain attribute is enough —
    no lock needed for what's really just cooperative UI-level state.
    """

    def __init__(self) -> None:
        self._active_name: str | None = None

    @property
    def active_name(self) -> str | None:
        return self._active_name

    def try_acquire(self, name: str) -> bool:
        """Claim the single download slot for *name*. Returns ``False``
        (claiming nothing) if another download is already in flight."""
        if self._active_name is not None:
            return False
        self._active_name = name
        return True

    def release(self) -> None:
        """Idempotent — safe to call even if nothing is held."""
        self._active_name = None


#: Shared across the whole process — import this instance, never
#: construct a second :class:`DownloadCoordinator`.
download_coordinator = DownloadCoordinator()


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
