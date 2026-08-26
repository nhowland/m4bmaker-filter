"""The Transcribe wizard step (PRD §7.2 stage 3; ADR-0015).

Real, not a placeholder — the wizard's fourth fully-built step. Wired
directly to its immediate predecessor, same as Source->Transcript
(ADR-0014): ``wizard_window.py`` calls :meth:`set_transcript_choice` with
Transcript's own ``MediaManifest`` and chosen ``ModelSpec`` when the User
continues past it (never called when Transcript's "Use existing" path
skips this step entirely — that path has nothing to transcribe).

Four states, driven by the real ``JobState`` machine (``jobs.py``) via
:class:`~m4bmaker.gui.filter.workers.TranscribeWorker`:

- **Ready to start** — nothing runs until this is confirmed, matching
  every other commit-point in this wizard (Source's eligibility gate,
  Transcript's explicit reuse-or-transcribe choice).
- **Running** — real ``JobRecord.progress_message``/``.progress_fraction``,
  a friendly "Chapter N of M" / "Section X of Y" label derived from
  ``chunking.chapter_for_chunk()`` (trivial in the common case now that
  chunks are chapter-sized, ADR-0001), Pause and Cancel as distinct
  actions.
- **Paused** — real ``JobState.PAUSED``; Resume re-enters at the first
  uncommitted chunk, nothing already committed is redone.
- **Needs attention** — PRD §11.2's recoverable state: a User action
  (Retry or Cancel Job), not a dead end.

Durable across app restarts, not just within one session: entering this
step checks the real job store for an existing incomplete
``TranscriptionJob`` matching this exact source's fingerprint and resumes
showing that state (Paused / Needs attention) instead of starting fresh.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter import storage
from m4bmaker.filter.chunking import (
    ChunkPlan,
    chapter_for_chunk,
    default_chunk_params,
    default_chunk_plan,
)
from m4bmaker.filter.job_store import JobRecord, JobStore, connect
from m4bmaker.filter.jobs import JobState, JobType
from m4bmaker.filter.model_manager import ModelSpec, model_path
from m4bmaker.filter.models import MediaManifest
from m4bmaker.filter.transcript import Transcript, TranscriptSource
from m4bmaker.filter.transcript_text import ensure_transcript_text

from ..workers import TranscribeWorker
from .step_base import WizardStep

_STATE_NOT_READY = "not_ready"
_STATE_READY = "ready"
_STATE_RUNNING = "running"
_STATE_PAUSED = "paused"
_STATE_NEEDS_ATTENTION = "needs_attention"
_STATE_COMPLETED = "completed"


def _info_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("statusLabel")
    label.setWordWrap(True)
    return label


def _format_elapsed(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class TranscribeStep(WizardStep):
    """PRD §7.2 stage 3: "display durable progress and user controls"."""

    def __init__(
        self,
        models_dest_dir: Path | None = None,
        transcripts_dest_dir: Path | None = None,
        db_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.step_title = "Transcribe"
        self.step_subtitle = (
            "Converting your audio to text. This can take a while — if "
            "you close the app, it'll pick up where it left off next "
            "time."
        )
        self._models_dest_dir = models_dest_dir or storage.models_dir()
        self._transcripts_dest_dir = transcripts_dest_dir or storage.transcripts_dir()
        self._db_path = db_path or storage.database_path()

        self._manifest: MediaManifest | None = None
        self._model_spec: ModelSpec | None = None
        self._job_id: str | None = None
        self._state = _STATE_NOT_READY
        self._worker: TranscribeWorker | None = None
        self._transcript: Transcript | None = None
        self._error_message: str | None = None
        self._chunk_plans: list[ChunkPlan] = []
        self._start_time: float | None = None
        self._last_progress_fraction = 0.0

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._build_ui()
        self._render_body()

    # ── public state ─────────────────────────────────────────────────────

    @property
    def transcript(self) -> Transcript | None:
        return self._transcript

    def can_advance(self) -> bool:
        return self._state == _STATE_COMPLETED

    def set_transcript_choice(
        self, manifest: MediaManifest, model_spec: ModelSpec
    ) -> None:
        """Entry point, called by the wizard shell with Transcript's own
        manifest and chosen model *every* time the User continues past it
        — including re-entering after Back, with nothing changed, while
        this step is already running or has already completed. A no-op in
        that case (same fingerprint, already mid-flight or done) rather
        than re-deriving from the job store and clobbering real progress;
        otherwise checks for an existing incomplete job for this exact
        source, so a resumed session picks up where it left off instead of
        offering to start fresh."""
        already_in_flight = self._state in (_STATE_RUNNING, _STATE_COMPLETED)
        same_source = (
            self._manifest is not None
            and self._manifest.fingerprint == manifest.fingerprint
        )
        if already_in_flight and same_source:
            return

        self._manifest = manifest
        self._model_spec = model_spec
        self._chunk_plans = []
        self._transcript = None
        self._error_message = None
        self._last_progress_fraction = 0.0

        existing = self._find_resumable_job(manifest.fingerprint)
        if existing is not None:
            self._job_id = existing.id
            if existing.state == JobState.PAUSED:
                self._state = _STATE_PAUSED
                self._last_progress_fraction = existing.progress_fraction or 0.0
            else:
                self._state = _STATE_NEEDS_ATTENTION
                self._error_message = existing.error_message or "Needs attention."
        else:
            self._job_id = None
            self._state = _STATE_READY

        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _find_resumable_job(self, fingerprint: str) -> JobRecord | None:
        store = JobStore(connect(self._db_path))
        for job in store.list_jobs(JobType.TRANSCRIPTION):
            if job.resource.get("fingerprint") != fingerprint:
                continue
            if job.state in (JobState.PAUSED, JobState.NEEDS_ATTENTION):
                return job
        return None

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._body)
        root.addStretch(1)

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_body(self) -> None:
        self._clear_body()
        if self._state == _STATE_NOT_READY:
            self._body_layout.addWidget(_info_label("Choose a transcript path first."))
        elif self._state == _STATE_READY:
            self._body_layout.addWidget(self._build_ready_panel())
        elif self._state == _STATE_RUNNING:
            self._body_layout.addWidget(self._build_running_panel())
        elif self._state == _STATE_PAUSED:
            self._body_layout.addWidget(self._build_paused_panel())
        elif self._state == _STATE_NEEDS_ATTENTION:
            self._body_layout.addWidget(self._build_needs_attention_panel())
        elif self._state == _STATE_COMPLETED:
            self._body_layout.addWidget(self._build_completed_panel())

    def _chunk_plans_for_manifest(self) -> list[ChunkPlan]:
        assert self._manifest is not None
        if not self._chunk_plans:
            self._chunk_plans = default_chunk_plan(
                self._manifest.duration_ms, self._manifest.chapters
            )
        return self._chunk_plans

    # ── "ready to start" ─────────────────────────────────────────────────

    def _build_ready_panel(self) -> QFrame:
        assert self._manifest is not None and self._model_spec is not None
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Ready to start")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        info = _info_label(
            "Nothing runs until this is confirmed — Start is its own "
            "explicit action."
        )
        layout.addWidget(info)

        if self._error_message:
            error_label = QLabel(self._error_message)
            error_label.setWordWrap(True)
            error_label.setStyleSheet("color: #c45a2d;")
            layout.addWidget(error_label)

        plans = self._chunk_plans_for_manifest()
        chapter_count = len(self._manifest.chapters)
        if chapter_count:
            detail = (
                f"{len(plans)} chunks — one per chapter ({chapter_count} "
                "chapters), chapter-aligned by default"
            )
        else:
            detail = (
                f"{len(plans)} chunks — no chapter markers, fixed-duration fallback"
            )

        row = QHBoxLayout()
        model_label = QLabel(f"Model: {self._model_spec.name}")
        row.addWidget(model_label)
        plan_label = QLabel(detail)
        plan_label.setWordWrap(True)
        row.addWidget(plan_label, stretch=1)
        layout.addLayout(row)

        start_btn = QPushButton("▶ Start Transcription")
        start_btn.clicked.connect(self._on_start_clicked)
        layout.addWidget(start_btn)
        return panel

    def _on_start_clicked(self) -> None:
        self._error_message = None
        self._job_id = str(uuid.uuid4())
        self._start_worker(mode="fresh")

    # ── "running" ────────────────────────────────────────────────────────

    def _build_running_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Transcribing…")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("jobProgress")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(int(self._last_progress_fraction * 100))
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        self._progress_label = QLabel(self._progress_display_text())
        layout.addWidget(self._progress_label)

        self._elapsed_label = QLabel(self._elapsed_display_text())
        self._elapsed_label.setObjectName("statusLabel")
        layout.addWidget(self._elapsed_label)

        btn_row = QHBoxLayout()
        pause_btn = QPushButton("⏸ Pause")
        pause_btn.clicked.connect(self._on_pause_clicked)
        btn_row.addWidget(pause_btn)
        cancel_btn = QPushButton("✕ Cancel")
        cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        return panel

    def _progress_display_text(self) -> str:
        if self._manifest is None:
            return ""
        plans = self._chunk_plans_for_manifest()
        total = len(plans)
        if total == 0:
            return ""
        index = min(max(round(self._last_progress_fraction * total) - 1, 0), total - 1)
        chapters = self._manifest.chapters
        if chapters:
            chapter = chapter_for_chunk(plans[index], chapters)
            if chapter is not None:
                return f"Chapter {chapter.index} of {len(chapters)}"
        return f"Section {index + 1} of {total}"

    def _elapsed_display_text(self) -> str:
        if self._start_time is None:
            return ""
        return f"Elapsed: {_format_elapsed(time.monotonic() - self._start_time)}"

    def _tick_elapsed(self) -> None:
        if hasattr(self, "_elapsed_label"):
            self._elapsed_label.setText(self._elapsed_display_text())

    def _on_pause_clicked(self) -> None:
        if self._worker is not None:
            self._worker.request_pause()

    def _on_cancel_clicked(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()

    # ── "paused" ─────────────────────────────────────────────────────────

    def _build_paused_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Paused")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(int(self._last_progress_fraction * 100))
        layout.addWidget(progress_bar)

        info = _info_label(
            "Paused at a committed chunk boundary. Every chunk transcribed "
            "so far is durably written; nothing is redone on Resume."
        )
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        resume_btn = QPushButton("▶ Resume")
        resume_btn.clicked.connect(self._on_resume_clicked)
        btn_row.addWidget(resume_btn)
        cancel_btn = QPushButton("✕ Cancel")
        cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        return panel

    def _on_resume_clicked(self) -> None:
        self._start_worker(mode="resume")

    # ── "needs attention" ────────────────────────────────────────────────

    def _build_needs_attention_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Needs attention")
        heading.setStyleSheet("font-weight: 600; color: #c45a2d;")
        layout.addWidget(heading)
        layout.addWidget(_info_label(self._error_message or "Needs attention."))

        btn_row = QHBoxLayout()
        retry_btn = QPushButton("↻ Retry")
        retry_btn.clicked.connect(self._on_retry_clicked)
        btn_row.addWidget(retry_btn)
        cancel_btn = QPushButton("✕ Cancel Job")
        cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        return panel

    def _on_retry_clicked(self) -> None:
        self._start_worker(mode="retry")

    # ── "completed" ──────────────────────────────────────────────────────

    def _build_completed_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("✓ Transcription complete")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        view_btn = QPushButton("View Transcript")
        view_btn.clicked.connect(self._on_view_transcript)
        row = QHBoxLayout()
        row.addWidget(view_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    def _on_view_transcript(self) -> None:
        if self._transcript is None:
            return
        text_path = ensure_transcript_text(self._transcript)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(text_path)))

    # ── worker lifecycle ─────────────────────────────────────────────────

    def _start_worker(self, mode: str) -> None:
        assert self._manifest is not None
        assert self._model_spec is not None
        assert self._job_id is not None

        chunk_ms, overlap_ms = default_chunk_params(self._manifest.chapters)
        chapter_starts = (
            [c.start_ms for c in self._manifest.chapters]
            if self._manifest.chapters
            else None
        )
        source = TranscriptSource(
            fingerprint=self._manifest.fingerprint,
            duration_ms=self._manifest.duration_ms,
            selected_audio_stream=self._manifest.selected_track_index or 0,
        )
        transcript_filename = (
            self._manifest.fingerprint.split(":", 1)[-1] + ".m4bt.json"
        )
        transcript_path = self._transcripts_dest_dir / transcript_filename

        self._worker = TranscribeWorker(
            self._job_id,
            self._db_path,
            Path(self._manifest.source_path),
            model_path(self._model_spec, self._models_dest_dir),
            self._model_spec.sha256,
            source,
            transcript_path,
            chunk_ms,
            overlap_ms,
            chapter_starts,
            mode,  # type: ignore[arg-type]
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.paused.connect(self._on_paused)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.result_ready.connect(self._on_result_ready)
        self._worker.needs_attention.connect(self._on_needs_attention)
        self._worker.error.connect(self._on_error)
        self._worker.start()

        self._start_time = time.monotonic()
        self._elapsed_timer.start()
        self._state = _STATE_RUNNING
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _teardown_worker(self) -> None:
        self._worker = None
        self._elapsed_timer.stop()
        self._start_time = None

    def _on_progress(self, message: str, fraction: float) -> None:
        self._last_progress_fraction = fraction
        if hasattr(self, "_progress_bar"):
            self._progress_bar.setValue(int(fraction * 100))
        if hasattr(self, "_progress_label"):
            self._progress_label.setText(self._progress_display_text())

    def _on_paused(self) -> None:
        self._teardown_worker()
        self._state = _STATE_PAUSED
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_cancelled(self) -> None:
        self._teardown_worker()
        self._job_id = None
        self._state = _STATE_READY
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_result_ready(self, transcript: object) -> None:
        self._teardown_worker()
        assert isinstance(transcript, Transcript)
        self._transcript = transcript
        self._state = _STATE_COMPLETED
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_needs_attention(self, message: str) -> None:
        self._teardown_worker()
        self._error_message = message
        self._state = _STATE_NEEDS_ATTENTION
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_error(self, message: str) -> None:
        self._teardown_worker()
        self._error_message = message
        # No job state was touched (missing binary etc.) — back to ready,
        # not needs_attention, so Start can just be retried directly.
        self._state = _STATE_READY
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())
