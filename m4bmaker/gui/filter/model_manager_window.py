"""Model Manager window: download/verify/remove STT models (PRD §10.1).

Second G5 screen, independent of the Catalog window — a User needs at
least one installed model before the (not yet built) transcription
wizard is useful, but installing a model has no dependency on the word
catalog, so this is its own top-level window rather than a wizard step.

PRD §10.1 is explicit about what the model UI must show: "model name,
engine/model version, installed/download state, source/provenance,
checksum, required disk space, and removal action." The table covers
name/state/size; source URL and full SHA-256 (64 hex characters — too
wide for a table cell) are shown in a details panel for the selected
row instead. Engine version is a property of the installed whisper.cpp
binary, not of any one model, so it is shown once at the top of the
window via ``transcript_engine.get_whisper_version()`` rather than
repeated per row.

Only one download runs at a time — the Download button is disabled for
every row while another row's download is in flight, matching how the
base project's Convert/Split buttons behave during their own workers.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.model_manager import (
    KNOWN_MODELS,
    ModelSpec,
    is_installed,
    remove_model,
)
from m4bmaker.filter.storage import models_dir
from m4bmaker.filter.transcript_engine import find_whisper_cli, get_whisper_version

from .workers import ModelDownloadWorker, download_coordinator

_COL_NAME = 0
_COL_LABEL = 1
_COL_SIZE = 2
_COL_STATUS = 3

_NAME_ROLE = Qt.ItemDataRole.UserRole


def _format_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.0f} MB"


class ModelManagerWindow(QMainWindow):
    """Secondary window for STT model download/removal."""

    def __init__(
        self, dest_dir: Path | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._dest_dir = dest_dir or models_dir()
        self._download_worker: ModelDownloadWorker | None = None
        self._downloading_name: str | None = None

        self.setWindowTitle("Model Manager")
        self.setMinimumSize(560, 380)
        self.resize(640, 460)

        self._build_ui()
        self._refresh_table()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._engine_label = QLabel()
        self._engine_label.setObjectName("modelEngineLabel")
        root.addWidget(self._engine_label)
        self._refresh_engine_label()

        self._status_label = QLabel("")
        self._status_label.setObjectName("modelStatusLabel")
        root.addWidget(self._status_label)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Model", "Description", "Size", "Status"]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_LABEL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            _COL_STATUS, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._table, stretch=1)

        self._details_label = QLabel("")
        self._details_label.setObjectName("modelDetailsLabel")
        self._details_label.setWordWrap(True)
        self._details_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self._details_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

        btn_row = QHBoxLayout()
        self._download_btn = QPushButton("Download")
        self._download_btn.clicked.connect(self._on_download_clicked)
        btn_row.addWidget(self._download_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        self._cancel_btn.setVisible(False)
        btn_row.addWidget(self._cancel_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(self._on_remove_clicked)
        btn_row.addWidget(self._remove_btn)

        btn_row.addStretch(1)
        root.addLayout(btn_row)

    # ── theme ────────────────────────────────────────────────────────────────

    def apply_stylesheet(self, dark: bool) -> None:
        from m4bmaker.gui.styles import get_stylesheet

        self.setStyleSheet(get_stylesheet(dark))

    # ── status ───────────────────────────────────────────────────────────────

    def _set_status(self, message: str) -> None:
        self._status_label.setText(message)

    def _refresh_engine_label(self) -> None:
        cli = find_whisper_cli()
        if cli is None:
            self._engine_label.setText(
                "Engine: whisper.cpp not found (install whisper-cli to transcribe)."
            )
            return
        version = get_whisper_version(cli)
        self._engine_label.setText(
            f"Engine: whisper.cpp {version or 'unknown version'}"
        )

    # ── table ────────────────────────────────────────────────────────────────

    def _refresh_table(self) -> None:
        selected = self._current_spec_name()
        self._table.setRowCount(0)
        for spec in KNOWN_MODELS:
            row = self._table.rowCount()
            self._table.insertRow(row)

            name_item = QTableWidgetItem(spec.name)
            name_item.setData(_NAME_ROLE, spec.name)
            self._table.setItem(row, _COL_NAME, name_item)
            self._table.setItem(row, _COL_LABEL, QTableWidgetItem(spec.label))
            self._table.setItem(
                row, _COL_SIZE, QTableWidgetItem(_format_size(spec.size_bytes))
            )
            self._table.setItem(
                row, _COL_STATUS, QTableWidgetItem(self._status_text(spec))
            )

        if selected is not None:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, _COL_NAME)
                if item is not None and item.data(_NAME_ROLE) == selected:
                    self._table.selectRow(row)
                    break

        self._update_button_states()

    def _status_text(self, spec: ModelSpec) -> str:
        if spec.name == self._downloading_name:
            return "Downloading…"
        return "Installed" if is_installed(spec, self._dest_dir) else "Not installed"

    def _current_spec_name(self) -> str | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._table.item(rows[0].row(), _COL_NAME)
        return item.data(_NAME_ROLE) if item is not None else None

    def _current_spec(self) -> ModelSpec | None:
        name = self._current_spec_name()
        if name is None:
            return None
        for spec in KNOWN_MODELS:
            if spec.name == name:
                return spec
        return None

    def _on_selection_changed(self) -> None:
        spec = self._current_spec()
        if spec is None:
            self._details_label.setText("")
        else:
            self._details_label.setText(f"Source: {spec.url}\nSHA-256: {spec.sha256}")
        self._update_button_states()

    def _update_button_states(self) -> None:
        spec = self._current_spec()
        downloading = self._download_worker is not None
        if spec is None:
            self._download_btn.setEnabled(False)
            self._remove_btn.setEnabled(False)
            return
        installed = is_installed(spec, self._dest_dir)
        self._download_btn.setEnabled(not installed and not downloading)
        self._remove_btn.setEnabled(installed and not downloading)

    # ── download ─────────────────────────────────────────────────────────────

    def _on_download_clicked(self) -> None:
        spec = self._current_spec()
        if spec is None or self._download_worker is not None:
            return
        if not download_coordinator.try_acquire(spec.name):
            QMessageBox.information(
                self,
                "Download In Progress",
                f"“{download_coordinator.active_name}” is downloading elsewhere "
                "(the Transcript wizard step). Wait for it to finish before "
                "starting another download.",
            )
            return
        self._downloading_name = spec.name
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._cancel_btn.setVisible(True)
        self._set_status(f"Downloading {spec.name}…")
        self._refresh_table()

        self._download_worker = ModelDownloadWorker(spec, self._dest_dir)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.result_ready.connect(self._on_download_finished)
        self._download_worker.cancelled.connect(self._on_download_cancelled)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    def _on_cancel_clicked(self) -> None:
        if self._download_worker is not None:
            self._download_worker.request_cancel()

    def _on_download_progress(self, message: str, fraction: float) -> None:
        self._set_status(message)
        self._progress_bar.setValue(int(fraction * 100))

    def _teardown_download_worker(self) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._download_worker = None
        self._downloading_name = None
        download_coordinator.release()

    def _on_download_finished(self, path: object) -> None:
        name = self._downloading_name
        self._teardown_download_worker()
        self._set_status(f"Installed {name}." if name else "Installed.")
        self._refresh_table()

    def _on_download_cancelled(self) -> None:
        self._teardown_download_worker()
        self._set_status("Download cancelled.")
        self._refresh_table()

    def _on_download_error(self, message: str) -> None:
        self._teardown_download_worker()
        self._set_status("Download failed.")
        self._refresh_table()
        if self.isVisible():
            QMessageBox.critical(self, "Download Failed", message)

    # ── remove ───────────────────────────────────────────────────────────────

    def _on_remove_clicked(self) -> None:
        spec = self._current_spec()
        if spec is None or not is_installed(spec, self._dest_dir):
            return
        confirm = QMessageBox.question(
            self,
            "Remove Model",
            f"Remove the downloaded “{spec.name}” model? "
            "You can download it again later.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        remove_model(spec, self._dest_dir)
        self._refresh_table()
        self._set_status(f"Removed {spec.name}.")

    # ── shutdown ─────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._download_worker is not None and self._download_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Cancel Download?",
                "A model download is in progress.\nAre you sure you want to close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            self._download_worker.request_cancel()
            self._download_worker.wait(5000)
        super().closeEvent(event)
