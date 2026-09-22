"""The Transcript wizard step (PRD §7.2 stage 2; ADR-0014).

Real, not a placeholder — the wizard's third fully-built step (after
Source and Review). Ports ``docs/design/wizard-shell-wireframe.html``'s
Transcript design: a "compatible transcript found" panel (backed by
``transcript.find_compatible_transcript()``, ADR-0013) with an explicit
choice between reusing it or transcribing anyway, or — when no
compatible transcript exists — a model chooser showing both real
``model_manager.KNOWN_MODELS`` catalog entries with their real install
state, downloading a not-yet-installed one inline via the same
``ModelDownloadWorker`` ``ModelManagerWindow`` already uses (ADR-0009),
guarded by the shared :data:`~m4bmaker.gui.filter.workers.download_coordinator`
(ADR-0014) so the two windows can't race to write the same model file.

Unlike Review (which sits behind two still-placeholder steps) but like
Source, this step's real data comes from its immediate predecessor:
``wizard_window.py`` calls :meth:`set_source` with Source's own
``MediaManifest`` when the User actually continues past it — see that
module for the exact wiring and the "reuse skips Transcribe" navigation
this step's :attr:`reuse_requested` signal drives.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.model_manager import KNOWN_MODELS, ModelSpec, is_installed
from m4bmaker.filter.models import MediaManifest
from m4bmaker.filter.settings import get as get_setting
from m4bmaker.filter.storage import models_dir
from m4bmaker.filter.transcript import Transcript, find_compatible_transcript

from ..model_manager_window import ModelManagerWindow
from ..workers import ModelDownloadWorker, download_coordinator
from .source_step import _format_duration
from .step_base import WizardStep

_ACCENT = "#c45a2d"

_MODE_FOUND = "found"
_MODE_CHOOSE = "choose"


def _format_model_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.0f} MB"


def _info_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("statusLabel")
    label.setWordWrap(True)
    return label


class TranscriptStep(WizardStep):
    """PRD §7.2 stage 2: "load a compatible native transcript or choose
    local transcription model/settings."

    Two mutually exclusive display modes, neither of which is a
    generic Continue-driven choice — both require an explicit action:

    - ``"found"``: a compatible saved transcript exists. Continue stays
      disabled here on purpose (:meth:`can_advance` returns ``False``)
      so the User can't drift forward on an unstated choice — "Use
      existing →" (emits :attr:`reuse_requested`, which the shell uses
      to skip Transcribe entirely) and "Transcribe again instead"
      (switches to ``"choose"`` mode) are the only two ways out.
    - ``"choose"``: pick one of the two real catalog models. Continue
      enables once the selected one is actually installed and nothing
      is mid-download.
    """

    reuse_requested = Signal()

    def __init__(
        self, dest_dir: Path | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.step_title = "Choose Transcript Path"
        self.step_subtitle = (
            "Reuse a saved transcript for this file, or choose a model to "
            "transcribe it locally."
        )
        self._dest_dir = dest_dir or models_dir()
        self._manifest: MediaManifest | None = None
        self._compatible_transcript: Transcript | None = None
        self._mode = _MODE_FOUND
        self._selected_model: ModelSpec = KNOWN_MODELS[0]
        self._download_worker: ModelDownloadWorker | None = None
        self._downloading_spec: ModelSpec | None = None
        self._model_manager_window: ModelManagerWindow | None = None
        self._build_ui()
        self._render_body()

    # ── public state (read by the shell / a future Transcribe step) ────────

    @property
    def manifest(self) -> MediaManifest | None:
        return self._manifest

    @property
    def compatible_transcript(self) -> Transcript | None:
        return self._compatible_transcript

    @property
    def chosen_model(self) -> ModelSpec | None:
        """The model to transcribe with, or ``None`` when the choice was
        "reuse a saved transcript" instead — nothing calls this yet, since
        Transcribe is still a placeholder, same situation ReviewStep's
        ``set_scan()`` was in before Scan existed."""
        return self._selected_model if self._mode == _MODE_CHOOSE else None

    def can_advance(self) -> bool:
        if self._manifest is None:
            return False
        if self._mode == _MODE_FOUND:
            return False
        return self._download_worker is None and is_installed(
            self._selected_model, self._dest_dir
        )

    def set_source(self, manifest: MediaManifest) -> None:
        """Entry point, called by the wizard shell with Source's own
        eligible ``MediaManifest`` when the User continues past it.
        Looks up a compatible saved transcript for this exact source and
        resets to the "found" panel if one exists, else the model
        chooser — safe to call again (e.g. the User went Back to Source
        and picked a different file), fully re-deriving state rather
        than merging with whatever was there before."""
        self._manifest = manifest
        self._compatible_transcript = find_compatible_transcript(manifest.fingerprint)
        self._mode = (
            _MODE_FOUND if self._compatible_transcript is not None else _MODE_CHOOSE
        )
        self._pick_default_model()
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _pick_default_model(self) -> None:
        """Prefer the User's configured default (Settings), if it's
        actually installed — an uninstalled preference isn't usable yet,
        so falls through to the same "first installed, else the first
        catalog entry" logic as when no preference is set at all."""
        preferred_name = get_setting("preferred_model")
        if preferred_name:
            for spec in KNOWN_MODELS:
                if spec.name == preferred_name and is_installed(spec, self._dest_dir):
                    self._selected_model = spec
                    return
        for spec in KNOWN_MODELS:
            if is_installed(spec, self._dest_dir):
                self._selected_model = spec
                return
        self._selected_model = KNOWN_MODELS[0]

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
        if self._manifest is None:
            self._body_layout.addWidget(_info_label("Select a source first."))
            return
        if self._mode == _MODE_FOUND:
            self._body_layout.addWidget(self._build_found_panel())
        else:
            self._body_layout.addWidget(self._build_choose_panel())

    # ── "found" panel ────────────────────────────────────────────────────

    def _build_found_panel(self) -> QFrame:
        transcript = self._compatible_transcript
        assert transcript is not None
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("✓ Compatible transcript found")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        detail = QLabel(
            f"{transcript.engine.name} {transcript.engine.version} · "
            f"{transcript.engine.model} · "
            f"{_format_duration(transcript.source.duration_ms)}"
        )
        layout.addWidget(detail)
        btn_row = QHBoxLayout()
        use_btn = QPushButton("Use existing →")
        use_btn.clicked.connect(self._on_use_existing)
        btn_row.addWidget(use_btn)
        again_btn = QPushButton("Transcribe again instead")
        again_btn.clicked.connect(self._on_transcribe_again)
        btn_row.addWidget(again_btn)
        layout.addLayout(btn_row)
        return panel

    def _on_use_existing(self) -> None:
        self.reuse_requested.emit()

    def _on_transcribe_again(self) -> None:
        self._mode = _MODE_CHOOSE
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    # ── "choose a model" panel ──────────────────────────────────────────

    def _build_choose_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Choose a model")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        self._radio_group = QButtonGroup(panel)
        for spec in KNOWN_MODELS:
            row = QHBoxLayout()
            radio = QRadioButton(spec.name)
            radio.setChecked(spec.name == self._selected_model.name)
            radio.toggled.connect(
                lambda checked, s=spec: self._on_model_selected(s, checked)
            )
            self._radio_group.addButton(radio)
            row.addWidget(radio)

            installed = is_installed(spec, self._dest_dir)
            state_label = QLabel("✓ installed" if installed else "not installed")
            row.addWidget(state_label)

            meta_label = QLabel(f"{spec.label} · {_format_model_size(spec.size_bytes)}")
            row.addWidget(meta_label, stretch=1)

            already_downloading_this = (
                self._downloading_spec is not None
                and self._downloading_spec.name == spec.name
            )
            if not installed and not already_downloading_this:
                download_btn = QPushButton("Download")
                download_btn.clicked.connect(
                    lambda _checked=False, s=spec: self._on_download_clicked(s)
                )
                row.addWidget(download_btn)

            layout.addLayout(row)

        if self._downloading_spec is not None:
            layout.addWidget(self._build_progress_row())

        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        manage_btn = QPushButton("Manage Transcription Models…")
        manage_btn.clicked.connect(self._on_manage_models)
        bottom_row.addWidget(manage_btn)
        layout.addLayout(bottom_row)

        return panel

    def _on_model_selected(self, spec: ModelSpec, checked: bool) -> None:
        if not checked:
            return
        self._selected_model = spec
        self.can_advance_changed.emit(self.can_advance())

    def _on_manage_models(self) -> None:
        if self._model_manager_window is None:
            self._model_manager_window = ModelManagerWindow(
                dest_dir=self._dest_dir, parent=self
            )
            self._model_manager_window.closed.connect(self._on_model_manager_closed)
        self._model_manager_window.show()
        self._model_manager_window.raise_()
        self._model_manager_window.activateWindow()

    def _on_model_manager_closed(self) -> None:
        """A download/removal made in the Model Manager window can change
        which model is installed — re-render so the list's install-state
        and ``can_advance()`` (which itself checks ``is_installed()``)
        reflect that immediately, without needing the User to leave and
        re-enter this step."""
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    # ── inline download ──────────────────────────────────────────────────

    def _build_progress_row(self) -> QWidget:
        spec = self._downloading_spec
        assert spec is not None
        w = QWidget()
        layout = QVBoxLayout(w)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)
        self._progress_label = QLabel(f"Downloading {spec.name}…")
        layout.addWidget(self._progress_label)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._on_cancel_download)
        layout.addWidget(cancel_btn)
        return w

    def _on_download_clicked(self, spec: ModelSpec) -> None:
        if self._download_worker is not None:
            return
        if not download_coordinator.try_acquire(spec.name):
            QMessageBox.information(
                self,
                "Download In Progress",
                f"“{download_coordinator.active_name}” is downloading "
                "elsewhere (Model Manager). Wait for it to finish before "
                "starting another download.",
            )
            return
        self._downloading_spec = spec
        self._download_worker = ModelDownloadWorker(spec, self._dest_dir)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.result_ready.connect(self._on_download_finished)
        self._download_worker.cancelled.connect(self._on_download_cancelled)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_cancel_download(self) -> None:
        if self._download_worker is not None:
            self._download_worker.request_cancel()

    def _on_download_progress(self, message: str, fraction: float) -> None:
        self._progress_bar.setValue(int(fraction * 100))
        self._progress_label.setText(message)

    def _teardown_download(self) -> None:
        self._download_worker = None
        self._downloading_spec = None
        download_coordinator.release()

    def _on_download_finished(self, path: object) -> None:
        self._teardown_download()
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_download_cancelled(self) -> None:
        self._teardown_download()
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_download_error(self, message: str) -> None:
        self._teardown_download()
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())
        if self.isVisible():
            QMessageBox.critical(self, "Download Failed", message)
