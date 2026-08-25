"""The Source wizard step (PRD §7.2 stage 1; ADR-0010, ADR-0013).

Real, not a placeholder — the wizard's second fully-built step (after
Review). Ports ``docs/design/wizard-shell-wireframe.html``'s Source
design: a file picker, then an eligibility panel showing every real
``MediaManifest`` field (``media_inspector.py``) — audio track,
duration, chapters, metadata, cover art, storage estimate
(``renderer.estimate_storage_bytes``), and compatible saved transcript
availability (``transcript.find_compatible_transcript``) — or, if the
source isn't eligible, every ineligibility reason listed, not just the
first.

Nothing downstream consumes this step's selected ``MediaManifest`` yet —
the Transcript step it would feed is still a placeholder — so, same as
``ReviewStep.set_scan()``, this step is self-contained and independently
testable rather than wired into a real end-to-end flow.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.models import MediaManifest
from m4bmaker.filter.renderer import estimate_storage_bytes
from m4bmaker.filter.transcript import find_compatible_transcript
from m4bmaker.gui.filter.workers import MediaInspectWorker

from .step_base import WizardStep

_ACCENT = "#c45a2d"


def _format_bytes(n: int) -> str:
    gb = n / (1024**3)
    if gb >= 1:
        return f"~{gb:.1f} GB"
    mb = n / (1024**2)
    return f"~{mb:.0f} MB"


def _format_duration(ms: int) -> str:
    total_s = ms // 1000
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class _M4bPicker(QFrame):
    """Single-file ``.m4b`` picker: path field, Browse, drag-and-drop.

    Scoped-down cousin of ``gui/widgets.py``'s ``FolderDropZone`` — that
    widget's Build/Edit dual mode (build a new M4B from a folder, or edit
    chapters on an existing one) doesn't apply here; the wizard only ever
    selects one existing ``.m4b`` to filter. The native-macOS-panel
    pattern (``osascript`` via async ``QProcess``, falling back to
    ``QFileDialog`` elsewhere or on error) is deliberately the same as
    ``FolderDropZone._browse_m4b_macos()`` for UI consistency.
    """

    file_selected = Signal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._picker_proc: QProcess | None = None
        self.setAcceptDrops(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Drag a .m4b file here, or Browse…")
        self._edit.setReadOnly(True)
        layout.addWidget(self._edit, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

    def path(self) -> Path | None:
        text = self._edit.text().strip()
        return Path(text) if text else None

    def set_path(self, path: Path) -> None:
        self._edit.setText(str(path))
        self.file_selected.emit(path)

    def _browse(self) -> None:
        import sys

        if sys.platform == "darwin":
            self._browse_macos()
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audiobook",
            "",
            "M4B Audiobooks (*.m4b);;All Files (*)",
            "",
            QFileDialog.Option.DontUseNativeDialog,
        )
        self._handle_picked(path)

    def _browse_macos(self) -> None:
        script = (
            'set theFile to choose file with prompt "Select an M4B audiobook"\n'
            "return POSIX path of theFile"
        )
        proc = QProcess(self)
        self._picker_proc = proc
        proc.finished.connect(self._on_macos_picker_finished)
        proc.errorOccurred.connect(self._on_macos_picker_error)
        proc.start("osascript", ["-e", script])

    def _on_macos_picker_finished(
        self, exit_code: int, exit_status: QProcess.ExitStatus
    ) -> None:
        proc = self._picker_proc
        self._picker_proc = None
        if proc is None:
            return
        path = ""
        if exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0:
            raw: bytes = bytes(proc.readAllStandardOutput().data())
            path = raw.decode("utf-8").strip()
        self._handle_picked(path)

    def _on_macos_picker_error(self, _error: QProcess.ProcessError) -> None:
        self._picker_proc = None
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audiobook",
            "",
            "M4B Audiobooks (*.m4b);;All Files (*)",
            "",
            QFileDialog.Option.DontUseNativeDialog,
        )
        self._handle_picked(path)

    def _handle_picked(self, path: str) -> None:
        if not path:
            return
        if path.lower().endswith(".m4b"):
            self.set_path(Path(path))
        else:
            QMessageBox.warning(
                self, "Not an M4B", f"'{Path(path).name}' is not an .m4b file."
            )

    def _is_accepted(self, p: Path) -> bool:
        return p.suffix.lower() == ".m4b"

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and self._is_accepted(Path(urls[0].toLocalFile())):
            self._edit.setStyleSheet(f"QLineEdit {{ border-color: {_ACCENT}; }}")
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._edit.setStyleSheet("")

    def dropEvent(self, event: QDropEvent) -> None:
        self._edit.setStyleSheet("")
        urls = event.mimeData().urls()
        if urls:
            p = Path(urls[0].toLocalFile())
            if self._is_accepted(p):
                self.set_path(p)
        event.acceptProposedAction()


class SourceStep(WizardStep):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_title = "Select Source"
        self.step_subtitle = (
            "Inspect eligibility, selected audio track, duration, chapters, "
            "required metadata, and storage estimate before anything else runs."
        )

        self._manifest: MediaManifest | None = None
        self._inspect_worker: MediaInspectWorker | None = None

        self._build_ui()

    @property
    def manifest(self) -> MediaManifest | None:
        return self._manifest

    def can_advance(self) -> bool:
        return self._manifest is not None and self._manifest.eligible

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._picker = _M4bPicker()
        self._picker.file_selected.connect(self._on_file_selected)
        root.addWidget(self._picker)

        self._status_label = QLabel("")
        self._status_label.setObjectName("statusLabel")
        root.addWidget(self._status_label)

        self._result_area = QWidget()
        self._result_layout = QVBoxLayout(self._result_area)
        self._result_layout.setContentsMargins(0, 12, 0, 0)
        root.addWidget(self._result_area)

        root.addStretch(1)

    def _clear_result(self) -> None:
        while self._result_layout.count():
            child = self._result_layout.takeAt(0)
            if child is None:
                continue
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

    # ── inspection ───────────────────────────────────────────────────────

    def _on_file_selected(self, path: Path) -> None:
        self._manifest = None
        self._clear_result()
        self._status_label.setText(f"Inspecting {path.name}…")
        self.can_advance_changed.emit(False)

        self._inspect_worker = MediaInspectWorker(path)
        self._inspect_worker.result_ready.connect(self._on_inspect_finished)
        self._inspect_worker.error.connect(self._on_inspect_error)
        self._inspect_worker.start()

    def _on_inspect_error(self, message: str) -> None:
        self._status_label.setText("Inspection failed.")
        self._clear_result()
        self._result_layout.addWidget(
            _error_label(f"Couldn't inspect this file: {message}")
        )
        self.can_advance_changed.emit(False)

    def _on_inspect_finished(self, manifest: MediaManifest) -> None:
        self._manifest = manifest
        self._status_label.setText("")
        self._clear_result()
        if manifest.eligible:
            self._result_layout.addWidget(_EligiblePanel(manifest))
        else:
            self._result_layout.addWidget(_IneligiblePanel(manifest))
        self.can_advance_changed.emit(self.can_advance())


def _error_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {_ACCENT};")
    return label


class _EligiblePanel(QFrame):
    def __init__(self, manifest: MediaManifest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        heading = QLabel("✓ Eligible")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        track = next(
            (t for t in manifest.tracks if t.index == manifest.selected_track_index),
            None,
        )
        rows: list[tuple[str, str]] = []
        if track is not None:
            parts = []
            if track.channels == 1:
                parts.append("Mono")
            elif track.channels == 2:
                parts.append("Stereo")
            elif track.channels:
                parts.append(f"{track.channels}ch")
            if track.codec_name:
                parts.append(track.codec_name.upper())
            if track.sample_rate:
                parts.append(f"{track.sample_rate / 1000:.1f} kHz")
            if track.bit_rate:
                parts.append(f"~{round(track.bit_rate / 1000)} kbps")
            track_desc = " · ".join(parts)
            fallback = (
                " (fallback selection)" if manifest.selected_track_is_fallback else ""
            )
            rows.append(("Audio track", track_desc + fallback))

        rows.append(("Duration", _format_duration(manifest.duration_ms)))
        rows.append(("Chapters", str(len(manifest.chapters))))

        if manifest.required_metadata:
            rows.append(
                ("Metadata", ", ".join(sorted(manifest.required_metadata.keys())))
            )
        else:
            rows.append(("Metadata", "None found"))

        rows.append(
            ("Cover art", "Present" if manifest.cover_present else "Not present")
        )

        estimate = estimate_storage_bytes(manifest)
        rows.append(
            (
                "Est. storage needed",
                _format_bytes(estimate) if estimate else "Unknown",
            )
        )

        transcript = find_compatible_transcript(manifest.fingerprint)
        if transcript is not None:
            rows.append(
                (
                    "Saved transcript",
                    f"Found — {transcript.engine.name} {transcript.engine.version} "
                    f"· {transcript.engine.model}",
                )
            )
        else:
            rows.append(("Saved transcript", "None found for this source"))

        for label_text, value_text in rows:
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setObjectName("statusLabel")
            label.setFixedWidth(140)
            row.addWidget(label)
            value = QLabel(value_text)
            value.setWordWrap(True)
            row.addWidget(value, stretch=1)
            layout.addLayout(row)


class _IneligiblePanel(QFrame):
    def __init__(self, manifest: MediaManifest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        heading = QLabel("✗ Not eligible")
        heading.setStyleSheet(f"font-weight: 600; color: {_ACCENT};")
        layout.addWidget(heading)

        intro = QLabel("This source can't be used as-is:")
        intro.setObjectName("statusLabel")
        layout.addWidget(intro)

        for reason in manifest.ineligibility_reasons:
            reason_label = QLabel(f"•  {reason}")
            reason_label.setWordWrap(True)
            layout.addWidget(reason_label)
