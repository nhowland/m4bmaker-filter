"""The Transcribe wizard step (PRD §7.2 stage 3; ADR-0015).

Real, not a placeholder — the wizard's fourth fully-built step. Wired
directly to its immediate predecessor, same as Source->Transcript
(ADR-0014): ``wizard_window.py`` calls :meth:`set_transcript_choice` with
Transcript's own ``MediaManifest`` and chosen ``ModelSpec`` when the User
continues past it.

When Transcript's "Use existing" path skips this step entirely instead,
``wizard_window.py`` calls :meth:`set_reused` at that same moment (not
merely when the User later navigates back here) — see ADR-0050's
follow-up: this step used to have no idea it had been skipped, so
re-entering it via Back (or a direct stepper click, which reaches any
previously-visited step the same way) landed on the plain "not ready"
placeholder with Continue permanently disabled, since nothing had ever
called either entry point for this source. Populating :meth:`set_reused`
at skip-time keeps this step's own state correct regardless of *how* a
User later reaches it, matching every other step's own rule of owning
and rendering its real state rather than the shell patching around a
gap in it.

Five states, four of them driven by the real ``JobState`` machine
(``jobs.py``) via :class:`~m4bmaker.gui.filter.workers.TranscribeWorker`:

- **Ready to start** — nothing runs until this is confirmed, matching
  every other commit-point in this wizard (Source's eligibility gate,
  Transcript's explicit reuse-or-transcribe choice).
- **Running** — real ``JobRecord.progress_message``/``.progress_fraction``,
  a friendly "Chapter N of M" / "Section X of Y" label derived from
  ``chunking.chapter_for_chunk()`` (trivial in the common case now that
  chunks are chapter-sized, ADR-0001), Pause and Cancel as distinct
  actions. Alongside Elapsed, an estimated-remaining-time label: a rough
  hardcoded per-model realtime multiplier (``_DEFAULT_REALTIME_MULTIPLIER``)
  seeds the very first guess, then every completed chunk in *this* run
  segment (audio-ms actually transcribed vs. wall-clock ms it took)
  replaces that guess with a real measured rate — chunks are
  chapter-aligned and not uniform length, so this is weighted by each
  chunk's own audio duration, not by chunk count the way the progress
  bar is. Deliberately not persisted across jobs/machines; see ADR-0027.
- **Paused** — real ``JobState.PAUSED``; Resume re-enters at the first
  uncommitted chunk, nothing already committed is redone.
- **Needs attention** — PRD §11.2's recoverable state: a User action
  (Retry or Cancel Job), not a dead end.

The fifth, **Reused**, isn't part of that job-state machine at all — it
means this step was skipped, so there's no job, running or otherwise.
Continue is enabled here same as Completed, since there's nothing left
to do; a "View Transcript" link works exactly like Completed's own,
reading the same saved transcript Transcript step already found.

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
_STATE_REUSED = "reused"

#: Rough, deliberately-approximate "audio-ms per wall-clock-ms" guesses used
#: only until this run has its own real measured rate (ADR-0027) — not
#: calibrated to any particular machine, just enough to show *something*
#: before the first chunk finishes. Real hardware varies far more than model
#: choice alone (GPU vs. CPU alone is a multi-x swing, ADR-0001), so this is
#: a starting point to correct away from, not a claim of accuracy.
_DEFAULT_REALTIME_MULTIPLIER = {
    "base.en": 15.0,
    "small.en": 6.0,
}
_FALLBACK_REALTIME_MULTIPLIER = 6.0


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

        # Estimated-remaining-time bookkeeping (ADR-0027). "Segment" means
        # "since this run started" — matches _start_time's own semantics,
        # which already reset on every resume rather than tracking the
        # job's full lifetime, so a stale pre-pause rate never pollutes a
        # fresh resume's estimate.
        self._segment_start_chunk_count = 0
        self._segment_processed_audio_ms = 0.0
        self._est_remaining_seconds: float | None = None
        self._est_anchor_time: float | None = None

        #: Cumulative real wall-clock transcription time across every run
        #: segment for the current source (ADR-0029) — see the
        #: ``elapsed_seconds`` property.
        self._elapsed_seconds = 0.0

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._build_ui()
        self._render_body()

    # ── public state ─────────────────────────────────────────────────────

    @property
    def transcript(self) -> Transcript | None:
        return self._transcript

    @property
    def elapsed_seconds(self) -> float | None:
        """Total real wall-clock time actually spent transcribing this
        source, summed across every run segment (ADR-0029) — resuming a
        paused job adds to this total rather than replacing it, unlike
        the live "Elapsed" label (which only ever shows the *current*
        segment, resetting to blank on Pause, matching ``_start_time``'s
        own semantics). ``None`` until this source has completed at
        least once."""
        return self._elapsed_seconds if self._elapsed_seconds > 0 else None

    def can_advance(self) -> bool:
        return self._state in (_STATE_COMPLETED, _STATE_REUSED)

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
        self._elapsed_seconds = 0.0

        existing = self._find_resumable_job(manifest.fingerprint)
        if existing is not None:
            self._job_id = existing.id
            self._last_progress_fraction = existing.progress_fraction or 0.0
            if existing.state == JobState.PAUSED:
                self._state = _STATE_PAUSED
            else:
                self._state = _STATE_NEEDS_ATTENTION
                self._error_message = existing.error_message or "Needs attention."
        else:
            self._job_id = None
            self._state = _STATE_READY

        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def set_reused(self, manifest: MediaManifest, transcript: Transcript) -> None:
        """Entry point, called by the wizard shell instead of
        :meth:`set_transcript_choice` the moment Transcript step's "Use
        existing" path skips this step entirely (ADR-0050 follow-up) —
        not lazily whenever the User later happens to navigate back
        here. Keeps this step's own state accurate no matter how it's
        later reached (Back, or a direct stepper click on an
        already-visited step), rather than leaving it stuck on the
        plain "not ready" placeholder with Continue disabled, which
        nothing had ever cleared for this source.

        Storing *transcript* here (not just leaving it to
        ``TranscriptStep.compatible_transcript``) is what makes "View
        Transcript" work the same way it does after a real
        transcription — this step's own copy, read the same way
        Completed's already is."""
        self._manifest = manifest
        self._model_spec = None
        self._job_id = None
        self._transcript = transcript
        self._error_message = None
        self._state = _STATE_REUSED
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
        elif self._state == _STATE_REUSED:
            self._body_layout.addWidget(self._build_reused_panel())

    def _chunk_plans_for_manifest(self) -> list[ChunkPlan]:
        assert self._manifest is not None
        if not self._chunk_plans:
            self._chunk_plans = default_chunk_plan(
                self._manifest.duration_ms, self._manifest.chapters
            )
        return self._chunk_plans

    def _chunk_audio_durations_ms(self) -> list[int]:
        """Each chunk's own audio-slice duration (``end_ms - start_ms``,
        including its overlap padding — the actual span whisper.cpp runs
        over, which is what compute time correlates with). Chapter-aligned
        chunks are not uniform length, so the ETA weighs by this, not by
        chunk count the way the progress bar does."""
        return [p.end_ms - p.start_ms for p in self._chunk_plans_for_manifest()]

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

        time_row = QHBoxLayout()
        time_row.setSpacing(12)
        self._elapsed_label = QLabel(self._elapsed_display_text())
        self._elapsed_label.setObjectName("statusLabel")
        time_row.addWidget(self._elapsed_label)
        separator = QLabel("·")
        separator.setObjectName("statusLabel")
        time_row.addWidget(separator)
        self._eta_label = QLabel(self._est_remaining_display_text())
        self._eta_label.setObjectName("statusLabel")
        time_row.addWidget(self._eta_label)
        time_row.addStretch(1)
        layout.addLayout(time_row)

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

    def _default_realtime_multiplier(self) -> float:
        if self._model_spec is None:
            return _FALLBACK_REALTIME_MULTIPLIER
        return _DEFAULT_REALTIME_MULTIPLIER.get(
            self._model_spec.name, _FALLBACK_REALTIME_MULTIPLIER
        )

    def _recompute_eta(self) -> None:
        """Re-anchor the countdown (ADR-0027): called once when this run
        starts (seeded from the hardcoded default) and again every time a
        chunk completes (seeded from this run's own real measured rate).
        Between calls, :meth:`_est_remaining_display_text` just counts the
        last anchored estimate down in real time rather than recomputing
        from a growing elapsed-time denominator every second — recomputing
        every tick against a not-yet-credited in-progress chunk would make
        the estimate visibly worsen while waiting on that chunk, then jump
        back up when it completes, which is more confusing than a plain
        countdown that only corrects when real data arrives."""
        durations = self._chunk_audio_durations_ms()
        total_audio_ms = sum(durations)
        if total_audio_ms == 0 or self._start_time is None:
            self._est_remaining_seconds = None
            self._est_anchor_time = None
            return

        completed_chunks = round(self._last_progress_fraction * len(durations))
        processed_overall_ms = sum(durations[:completed_chunks])
        remaining_audio_ms = max(0, total_audio_ms - processed_overall_ms)

        elapsed_segment_ms = (time.monotonic() - self._start_time) * 1000
        if self._segment_processed_audio_ms > 0 and elapsed_segment_ms > 0:
            rate = self._segment_processed_audio_ms / elapsed_segment_ms
        else:
            rate = self._default_realtime_multiplier()

        self._est_remaining_seconds = (
            (remaining_audio_ms / rate) / 1000 if rate > 0 else None
        )
        self._est_anchor_time = time.monotonic()

    def _est_remaining_display_text(self) -> str:
        if self._est_remaining_seconds is None or self._est_anchor_time is None:
            # Shown while running but before any estimate exists yet
            # (e.g. no chunk has completed and the very first seed value
            # hasn't landed) — a User-requested change from leaving the
            # label blank, which read as the feature being silently
            # absent rather than still working on an answer.
            return "Est. remaining: Calculating…"
        countdown = self._est_remaining_seconds - (
            time.monotonic() - self._est_anchor_time
        )
        return f"Est. remaining: ~{_format_elapsed(max(0.0, countdown))}"

    def _tick_elapsed(self) -> None:
        if hasattr(self, "_elapsed_label"):
            self._elapsed_label.setText(self._elapsed_display_text())
        if hasattr(self, "_eta_label"):
            self._eta_label.setText(self._est_remaining_display_text())

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

    # ── "reused" (Transcript step's saved transcript was reused) ────────

    def _build_reused_panel(self) -> QFrame:
        assert self._transcript is not None
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("✓ Using existing transcript")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(
            _info_label(
                "You chose to reuse a saved transcript for this source on "
                "the previous step, so there's nothing to transcribe here "
                "— Continue to keep going, or go back to Transcript if "
                "you'd rather transcribe it again instead."
            )
        )

        view_btn = QPushButton("View Transcript")
        view_btn.clicked.connect(self._on_view_transcript)
        row = QHBoxLayout()
        row.addWidget(view_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

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
        durations = self._chunk_audio_durations_ms()
        self._segment_start_chunk_count = round(
            self._last_progress_fraction * len(durations)
        )
        self._segment_processed_audio_ms = 0.0
        self._recompute_eta()
        self._elapsed_timer.start()
        self._state = _STATE_RUNNING
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _teardown_worker(self) -> None:
        self._worker = None
        self._elapsed_timer.stop()
        if self._start_time is not None:
            self._elapsed_seconds += time.monotonic() - self._start_time
        self._start_time = None
        self._est_remaining_seconds = None
        self._est_anchor_time = None

    def _on_progress(self, message: str, fraction: float) -> None:
        self._last_progress_fraction = fraction
        durations = self._chunk_audio_durations_ms()
        completed_chunks = round(fraction * len(durations))
        completed_in_segment = max(
            0, completed_chunks - self._segment_start_chunk_count
        )
        self._segment_processed_audio_ms = sum(
            durations[
                self._segment_start_chunk_count : self._segment_start_chunk_count
                + completed_in_segment
            ]
        )
        self._recompute_eta()
        if hasattr(self, "_progress_bar"):
            self._progress_bar.setValue(int(fraction * 100))
        if hasattr(self, "_progress_label"):
            self._progress_label.setText(self._progress_display_text())
        if hasattr(self, "_eta_label"):
            self._eta_label.setText(self._est_remaining_display_text())

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
