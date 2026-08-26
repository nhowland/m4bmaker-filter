"""The Scan wizard step (PRD §7.2 stage 5, §9.4; wireframe pass 2026-08-25).

Real, not a placeholder — the wizard's fifth fully-built step. Wired to
its two predecessors that actually matter here: Transcript/Transcribe
(whichever produced the real, completed ``Transcript`` — reusing an
existing one skips Transcribe, but not this step's need for its output)
and Profile (the chosen ``profile_id``). ``wizard_window.py`` calls
:meth:`set_inputs` with both, plus the shared ``CatalogService``, when the
User continues past Profile.

Three states, not the four/five Transcribe needed — a scan has no chunk
boundary to checkpoint at (``matcher.scan_transcript()``'s own docstring
already flags it as a single, unbenchmarked pass over the whole
transcript, not a resumable/pausable job like transcription), so there is
no Paused state and no durable job-store-backed resume: ``ScanWorker``
mirrors ``MediaInspectWorker``'s simpler shape instead of
``TranscribeWorker``'s.

- **Ready to scan** — nothing runs until confirmed, same as every other
  commit-point in this wizard. ``CatalogService.create_snapshot(profile_id)``
  freezes the profile's current entries and revision the moment Scan
  starts, not before, so a last-second Profile edit is still captured.
- **Running** — a plain indeterminate busy state, not a progress bar with
  a real fraction — there is nothing chunked to report a fraction of.
- **Needs attention** — an unexpected ``run_scan()``/snapshot failure;
  Retry re-runs from the same inputs, nothing durable was lost since
  nothing durable was ever created.
- **Complete** — real ``ScanReport`` fields (``scan.build_report()``);
  Continue enables, and Re-scan is its own explicit action (PRD §9.4: a
  re-scan is always a new revision, never a silent copy of old decisions).
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
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

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.models import NORMALIZATION_VERSION
from m4bmaker.filter.scan import Scan, ScanReport, build_report
from m4bmaker.filter.transcript import Transcript
from m4bmaker.filter.transcript_text import ensure_transcript_text

from ..workers import ScanWorker
from .source_step import _format_duration
from .step_base import WizardStep

_STATE_NOT_READY = "not_ready"
_STATE_READY = "ready"
_STATE_RUNNING = "running"
_STATE_NEEDS_ATTENTION = "needs_attention"
_STATE_COMPLETED = "completed"


def _info_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("statusLabel")
    label.setWordWrap(True)
    return label


class ScanStep(WizardStep):
    """PRD §7.2 stage 5: "create a persisted scan result against an
    immutable profile snapshot"."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_title = "Scan"
        self.step_subtitle = (
            "Scanning checks the transcript against your selected "
            "profile's word list. Results are saved so you can review "
            "them even if you close the app."
        )
        self._transcript: Transcript | None = None
        self._catalog: CatalogService | None = None
        self._profile_id: str | None = None
        self._scan: Scan | None = None
        self._worker: ScanWorker | None = None
        self._state = _STATE_NOT_READY
        self._error_message: str | None = None

        self._build_ui()
        self._render_body()

    # ── public state (read by the shell / a future Review hand-off) ────────

    @property
    def scan(self) -> Scan | None:
        return self._scan

    @property
    def transcript(self) -> Transcript | None:
        return self._transcript

    def can_advance(self) -> bool:
        return self._state == _STATE_COMPLETED

    def set_inputs(
        self, transcript: Transcript, catalog: CatalogService, profile_id: str
    ) -> None:
        """Entry point, called by the wizard shell with the real completed
        ``Transcript`` and chosen ``profile_id`` every time the User
        continues past Profile — including re-entering after Back with
        nothing changed, while this step is already running or has
        already completed. A no-op in that case (same transcript, same
        profile, already mid-flight or done), matching
        ``TranscribeStep.set_transcript_choice``'s own re-entry guard and
        for the same reason: naively re-deriving here would clobber a
        completed scan just from revisiting an earlier step. If either the
        transcript or the chosen profile actually changed, though, the old
        scan is stale by construction (it matched different inputs) — this
        resets to "ready to scan" rather than silently keeping a result
        that no longer corresponds to what's shown.
        """
        already_in_flight = self._state in (_STATE_RUNNING, _STATE_COMPLETED)
        same_inputs = (
            self._transcript is not None
            and self._transcript.source.fingerprint == transcript.source.fingerprint
            and self._profile_id == profile_id
        )
        if already_in_flight and same_inputs:
            return

        self._transcript = transcript
        self._catalog = catalog
        self._profile_id = profile_id
        self._scan = None
        self._error_message = None
        self._state = _STATE_READY
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

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
            self._body_layout.addWidget(
                _info_label("Choose a transcript and a profile first.")
            )
        elif self._state == _STATE_READY:
            self._body_layout.addWidget(self._build_ready_panel())
        elif self._state == _STATE_RUNNING:
            self._body_layout.addWidget(self._build_running_panel())
        elif self._state == _STATE_NEEDS_ATTENTION:
            self._body_layout.addWidget(self._build_needs_attention_panel())
        elif self._state == _STATE_COMPLETED:
            self._body_layout.addWidget(self._build_completed_panel())

    # ── derived data ─────────────────────────────────────────────────────

    def _profile_name(self) -> str:
        if self._catalog is None or self._profile_id is None:
            return ""
        try:
            return self._catalog.get_profile(self._profile_id).name
        except KeyError:
            return self._profile_id

    def _category_name(self, category_id: str) -> str:
        if self._catalog is None:
            return category_id
        try:
            return self._catalog.get_category(category_id).name
        except KeyError:
            return category_id

    def _category_breakdown(self, report: ScanReport) -> str:
        parts = [
            f"{self._category_name(category_id)} ×{count}"
            for category_id, count in report.category_counts.items()
        ]
        return ", ".join(parts) if parts else "none"

    # ── "ready to scan" ──────────────────────────────────────────────────

    def _build_ready_panel(self) -> QFrame:
        assert self._transcript is not None
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Ready to scan")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(
            _info_label(
                "Nothing runs until this is confirmed — Start is its own "
                "explicit action."
            )
        )

        if self._error_message:
            error_label = QLabel(self._error_message)
            error_label.setWordWrap(True)
            error_label.setStyleSheet("color: #c45a2d;")
            layout.addWidget(error_label)

        engine = self._transcript.engine
        duration = _format_duration(self._transcript.source.duration_ms)
        layout.addWidget(
            _info_label(
                f"Transcript: {engine.name} {engine.version} · "
                f"{engine.model} · {duration}"
            )
        )
        layout.addWidget(
            _info_label(
                f"Profile: {self._profile_name()} — will snapshot its "
                "current entries and revision when Scan starts"
            )
        )

        start_btn = QPushButton("▶ Start Scan")
        start_btn.clicked.connect(self._on_start)
        row = QHBoxLayout()
        row.addWidget(start_btn)
        view_btn = QPushButton("View Transcript")
        view_btn.clicked.connect(self._on_view_transcript)
        row.addWidget(view_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    def _on_view_transcript(self) -> None:
        if self._transcript is None:
            return
        text_path = ensure_transcript_text(self._transcript)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(text_path)))

    def _on_start(self) -> None:
        assert self._transcript is not None
        assert self._catalog is not None
        assert self._profile_id is not None
        try:
            snapshot = self._catalog.create_snapshot(self._profile_id)
        except Exception as exc:  # noqa: BLE001
            self._error_message = str(exc)
            self._state = _STATE_NEEDS_ATTENTION
            self._render_body()
            return

        self._state = _STATE_RUNNING
        self._render_body()

        self._worker = ScanWorker(self._transcript, snapshot, NORMALIZATION_VERSION)
        self._worker.result_ready.connect(self._on_result_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── "running" ────────────────────────────────────────────────────────

    def _build_running_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Scanning…")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(
            _info_label(
                "Indeterminate — a scan is a single pass over the whole "
                "transcript, not chunked, so there is no fraction to "
                "report yet."
            )
        )
        bar = QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)
        return panel

    def _on_result_ready(self, scan: Scan) -> None:
        self._worker = None
        self._scan = scan
        self._state = _STATE_COMPLETED
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_error(self, message: str) -> None:
        self._worker = None
        self._error_message = message
        self._state = _STATE_NEEDS_ATTENTION
        self._render_body()

    # ── "needs attention" ────────────────────────────────────────────────

    def _build_needs_attention_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Needs attention")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        error_label = QLabel(self._error_message or "Scan failed.")
        error_label.setWordWrap(True)
        error_label.setStyleSheet("color: #c45a2d;")
        layout.addWidget(error_label)

        retry_btn = QPushButton("↻ Retry")
        retry_btn.clicked.connect(self._on_start)
        row = QHBoxLayout()
        row.addWidget(retry_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    # ── "complete" ───────────────────────────────────────────────────────

    def _build_completed_panel(self) -> QFrame:
        assert self._scan is not None
        assert self._transcript is not None
        report = build_report(self._scan, self._transcript.source.duration_ms)

        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Scan complete")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        layout.addWidget(
            _info_label(
                f"Hits found: {report.total_raw_hits} — "
                f"{self._category_breakdown(report)}"
            )
        )
        layout.addWidget(_info_label(f"Unique terms hit: {report.unique_terms_hit}"))
        layout.addWidget(
            _info_label(
                f"Profile snapshot: {self._scan.profile_snapshot.name} · "
                f"rev. {self._scan.profile_snapshot.profile_revision}"
            )
        )
        layout.addWidget(
            _info_label(
                "Planned attenuated duration: "
                f"{_format_duration(report.total_planned_attenuated_duration_ms)}"
            )
        )

        rescan_btn = QPushButton("↻ Re-scan")
        rescan_btn.clicked.connect(self._on_start)
        row = QHBoxLayout()
        row.addWidget(rescan_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel
