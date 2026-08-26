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
from typing import Literal

from PySide6.QtCore import QThread, Signal

from m4bmaker.filter.job_store import JobStore, connect
from m4bmaker.filter.jobs import JobState, JobType, is_terminal
from m4bmaker.filter.media_inspector import inspect
from m4bmaker.filter.model_manager import (
    ModelChecksumMismatchError,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelSpec,
    download_model,
)
from m4bmaker.filter.filter_report import write_filter_report
from m4bmaker.filter.models import FilterProfileSnapshot, MediaManifest, RenderPlan
from m4bmaker.filter.renderer import RenderError, RenderResult, render
from m4bmaker.filter.scan import Scan, run_scan
from m4bmaker.filter.transcript import Transcript, TranscriptSource
from m4bmaker.filter.transcript_engine import find_whisper_cli
from m4bmaker.filter.transcription_orchestrator import (
    TranscriptionPaused,
    run_transcription_job,
)
from m4bmaker.filter.validator import ValidationReport, validate
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


TranscribeStartMode = Literal["fresh", "resume", "retry"]


class TranscribeWorker(QThread):
    """Run (or resume, or retry) one TranscriptionJob off the UI thread,
    via :func:`transcription_orchestrator.run_transcription_job` (PRD
    §11.3; ADR-0015).

    Opens its own :class:`JobStore` connection in :meth:`run` rather than
    sharing one from the UI thread — SQLite connections aren't safe to
    use across threads, and this worker is the only place that touches
    the job's row while it runs.

    *mode* governs the state transition this worker performs before
    calling ``run_transcription_job`` (whose own docstring makes those
    transitions the caller's responsibility, not its own):

    - ``"fresh"``: creates the job, ``QUEUED`` -> ``PREPARING``.
    - ``"resume"``: an existing ``PAUSED`` job, -> ``RESUMING``.
    - ``"retry"``: an existing ``NEEDS_ATTENTION`` job, -> ``QUEUED`` ->
      ``PREPARING`` (the only transitions PRD §11.2's allowed-transition
      matrix permits out of ``NEEDS_ATTENTION``).

    Pause and Cancel share one underlying stop mechanism (the same
    ``should_pause`` callback `run_transcription_job` already checks at
    each chunk boundary) — Cancel just means "and transition to
    ``CANCELLED``, not ``PAUSED``, once it actually stops."
    """

    progress = Signal(str, float)  # message, 0.0-1.0
    paused = Signal()
    cancelled = Signal()
    result_ready = Signal(object)  # Transcript
    needs_attention = Signal(str)  # job exists, now NEEDS_ATTENTION
    error = Signal(str)  # failed before any job state was touched

    def __init__(
        self,
        job_id: str,
        db_path: Path,
        source_audio_path: Path,
        model_path: Path,
        model_checksum: str,
        source: TranscriptSource,
        transcript_path: Path,
        chunk_ms: int,
        overlap_ms: int,
        chapter_start_times_ms: list[int] | None,
        mode: TranscribeStartMode,
    ) -> None:
        super().__init__()
        self._job_id = job_id
        self._db_path = db_path
        self._source_audio_path = source_audio_path
        self._model_path = model_path
        self._model_checksum = model_checksum
        self._source = source
        self._transcript_path = transcript_path
        self._chunk_ms = chunk_ms
        self._overlap_ms = overlap_ms
        self._chapter_start_times_ms = chapter_start_times_ms
        self._mode = mode
        self._stop_requested = threading.Event()
        self._cancel_requested = False

    def request_pause(self) -> None:
        self._stop_requested.set()

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self._stop_requested.set()

    def run(self) -> None:
        ffmpeg = find_binary("ffmpeg")
        if ffmpeg is None:
            self.error.emit(
                "ffmpeg not found. Install ffmpeg and make sure it's on " "your PATH."
            )
            return
        whisper_cli = find_whisper_cli()
        if whisper_cli is None:
            self.error.emit(
                "whisper-cli not found. Install whisper.cpp and make sure "
                "it's on your PATH."
            )
            return

        store = JobStore(connect(self._db_path))
        try:
            if self._mode == "fresh":
                store.create_job(
                    self._job_id,
                    JobType.TRANSCRIPTION,
                    resource={
                        "fingerprint": self._source.fingerprint,
                        "model": self._model_path.stem,
                    },
                )
                store.transition(self._job_id, JobState.PREPARING, "Preparing…")
            elif self._mode == "resume":
                store.transition(self._job_id, JobState.RESUMING, "Resuming…")
            else:  # "retry"
                store.transition(self._job_id, JobState.QUEUED, "Retrying…")
                store.transition(self._job_id, JobState.PREPARING, "Preparing…")

            transcript: Transcript = run_transcription_job(
                self._job_id,
                store,
                self._source_audio_path,
                self._model_path,
                self._model_checksum,
                self._source,
                self._transcript_path,
                self._chunk_ms,
                self._overlap_ms,
                ffmpeg,
                chapter_start_times_ms=self._chapter_start_times_ms,
                whisper_cli=whisper_cli,
                should_pause=self._stop_requested.is_set,
                progress_callback=self.progress.emit,
            )
        except TranscriptionPaused:
            if self._cancel_requested:
                store.transition(self._job_id, JobState.CANCELLED, "Cancelled by User.")
                self.cancelled.emit()
            else:
                self.paused.emit()
            return
        except Exception as exc:  # noqa: BLE001
            job = store.get_job(self._job_id)
            if job is not None and not is_terminal(job.state):
                store.set_error(self._job_id, "transcription_error", str(exc))
                store.transition(self._job_id, JobState.NEEDS_ATTENTION, str(exc))
            self.needs_attention.emit(str(exc))
            return
        self.result_ready.emit(transcript)


class ScanWorker(QThread):
    """Run :func:`scan.run_scan` (the Matcher) off the UI thread (PRD §9.4).

    No ``JobStore``, no chunking, no pause/resume — unlike Transcription,
    a scan has no natural chunk boundary to checkpoint at (``matcher.
    scan_transcript()``'s own docstring already flags it as a single,
    unbenchmarked pass over the whole transcript), so this mirrors
    ``MediaInspectWorker``'s simpler shape instead of ``TranscribeWorker``'s:
    one call, a result or an error, nothing durable to resume if the wizard
    closes mid-scan. If a real long-book benchmark someday shows this is
    slow enough to need interruptibility, that's a real re-scoping of this
    worker, not a small tweak (see the Scan wireframe pass's own notes).
    """

    result_ready = Signal(object)  # Scan
    error = Signal(str)

    def __init__(
        self,
        transcript: Transcript,
        profile_snapshot: FilterProfileSnapshot,
        normalization_version: int,
    ) -> None:
        super().__init__()
        self._transcript = transcript
        self._profile_snapshot = profile_snapshot
        self._normalization_version = normalization_version

    def run(self) -> None:
        try:
            scan: Scan = run_scan(
                self._transcript, self._profile_snapshot, self._normalization_version
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return
        self.result_ready.emit(scan)


class RenderWorker(QThread):
    """Run :func:`renderer.render` then :func:`validator.validate` off the
    UI thread (PRD §7.2 stage 7, §8; ADR-0006/0007/0019).

    No pause/resume, no cancellation hook — ``render()`` is one
    synchronous, all-or-nothing call with no chunk boundary to checkpoint
    at, matching ADR-0007's own already-decided position ("given the real
    615s/10.25-minute total render time... full-restart-on-failure is a
    reasonable MVP position"). This worker doesn't invent a
    ``request_cancel()`` this class can't actually honor (there is no
    handle back to the underlying ffmpeg subprocess to stop) — the real
    escape hatch is the wizard window's own confirm-before-close, which
    already applies "mid-Render" per its own docstring.

    ``validating`` fires once, right after ``render()`` succeeds and
    before ``validate()`` runs — ``validate()`` has no progress callback
    of its own (a single blocking call, same reasoning as ``ScanWorker``'s
    indeterminate state), so the UI has nothing to show but "this next
    phase is running," not a fraction of it.
    """

    progress = Signal(str, float)  # message, 0.0-1.0 (render() phases only)
    validating = Signal()
    result_ready = Signal(
        object, object, object
    )  # RenderResult, ValidationReport, report_path
    error = Signal(str)

    def __init__(
        self,
        source_path: Path,
        manifest: MediaManifest,
        render_plan: RenderPlan,
        output_path: Path,
        bitrate: str,
    ) -> None:
        super().__init__()
        self._source_path = source_path
        self._manifest = manifest
        self._render_plan = render_plan
        self._output_path = output_path
        self._bitrate = bitrate

    def run(self) -> None:
        ffmpeg = find_binary("ffmpeg")
        if ffmpeg is None:
            self.error.emit(
                "ffmpeg not found. Install ffmpeg and make sure it's on " "your PATH."
            )
            return
        ffprobe = find_binary("ffprobe")
        if ffprobe is None:
            self.error.emit(
                "ffprobe not found. Install ffmpeg (which bundles ffprobe) "
                "and make sure it's on your PATH."
            )
            return

        try:
            result: RenderResult = render(
                self._source_path,
                self._manifest,
                self._render_plan,
                self._output_path,
                ffmpeg,
                ffprobe,
                self._bitrate,
                progress_callback=self.progress.emit,
            )
        except RenderError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return

        self.validating.emit()
        try:
            output_manifest = inspect(self._output_path, ffprobe)
            report: ValidationReport = validate(
                self._manifest,
                output_manifest,
                self._render_plan,
                self._output_path,
                ffmpeg,
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return

        report_path = write_filter_report(
            self._output_path, result, report, self._bitrate
        )
        self.result_ready.emit(result, report, report_path)
