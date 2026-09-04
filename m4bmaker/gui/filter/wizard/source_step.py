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

from PySide6.QtCore import QProcess, Qt, Signal
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
from m4bmaker.gui.filter.workers import CoverArtWorker, MediaInspectWorker

from .file_card import set_cover_pixmap
from .step_base import WizardStep

_ACCENT = "#c45a2d"


def _repolish(widget: QWidget) -> None:
    """Force Qt to re-evaluate attribute-selector QSS after setProperty()
    -- same helper stepper.py/file_card.py already use for their own
    dynamic-state styling, duplicated rather than imported since it's a
    private, per-module convention in this package."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


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
    #: Fires whenever cover_path changes -- including to None, so a
    #: listener (the persistent file card) always learns "extraction
    #: finished, here's the answer" rather than staying stuck on a stale
    #: value from a previously-loaded file while a new one is still
    #: being inspected/extracted.
    cover_ready = Signal()

    #: Fires when the User clicks the "Temp storage needed" row's "Change
    #: temp storage location…" link -- this is the moment they've just
    #: seen how much scratch space a specific book needs and would most
    #: plausibly want to redirect it, so the shortcut lives right next to
    #: that figure rather than requiring them to already know Settings
    #: has this.
    open_settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_title = "Select Source"
        self.step_subtitle = (
            "We'll check this file's audio format, duration, chapters, and "
            "metadata to confirm it's ready for filtering."
        )

        self._manifest: MediaManifest | None = None
        self._inspect_worker: MediaInspectWorker | None = None
        self._cover_path: Path | None = None
        self._cover_worker: CoverArtWorker | None = None

        self._build_ui()

    @property
    def manifest(self) -> MediaManifest | None:
        return self._manifest

    @property
    def cover_path(self) -> Path | None:
        return self._cover_path

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
        # Built once and kept for the widget's whole lifetime -- starts
        # in its own placeholder state (matching the base app's own
        # empty-state pattern: a placeholder cover box and blank fields,
        # not nothing) and is updated in place from then on, never torn
        # down and rebuilt (ADR-0046 addendum).
        self._info_panel = _InfoPanel()
        self._info_panel.open_settings_requested.connect(self.open_settings_requested)
        self._result_layout.addWidget(self._info_panel)
        # The ineligible/error case's own separate, simpler widget, when
        # one is currently shown in the info panel's place -- tracked
        # so it can be removed cleanly without touching _info_panel.
        self._secondary_widget: QWidget | None = None

        root.addStretch(1)

    def _clear_secondary(self) -> None:
        if self._secondary_widget is not None:
            self._result_layout.removeWidget(self._secondary_widget)
            self._secondary_widget.deleteLater()
            self._secondary_widget = None

    def _show_secondary(self, widget: QWidget) -> None:
        self._info_panel.setVisible(False)
        self._clear_secondary()
        self._result_layout.addWidget(widget)
        self._secondary_widget = widget

    # ── inspection ───────────────────────────────────────────────────────

    def _on_file_selected(self, path: Path) -> None:
        self._manifest = None
        self._cover_path = None
        self.cover_ready.emit()
        self._clear_secondary()
        self._info_panel.setVisible(True)
        self._info_panel.show_placeholder()
        self._status_label.setText(f"Inspecting {path.name}…")
        self.can_advance_changed.emit(False)

        self._inspect_worker = MediaInspectWorker(path)
        self._inspect_worker.result_ready.connect(self._on_inspect_finished)
        self._inspect_worker.error.connect(self._on_inspect_error)
        self._inspect_worker.start()

    def _on_inspect_error(self, message: str) -> None:
        self._status_label.setText("Inspection failed.")
        self._show_secondary(_error_label(f"Couldn't inspect this file: {message}"))
        self.can_advance_changed.emit(False)

    def _on_inspect_finished(self, manifest: MediaManifest) -> None:
        self._manifest = manifest
        self._status_label.setText("")
        if manifest.eligible:
            self._clear_secondary()
            self._info_panel.setVisible(True)
            self._info_panel.show_eligible(manifest, self._cover_path)
        else:
            self._show_secondary(_IneligiblePanel(manifest))
        self.can_advance_changed.emit(self.can_advance())

        worker = CoverArtWorker(Path(manifest.source_path))
        # Captures *worker* itself, not just its result, so a stale
        # worker's late-arriving signal (the User picked a different
        # file while this one was still extracting) can be told apart
        # from the current one -- same default-arg lambda-capture
        # pattern already used for per-row context elsewhere in this
        # package (e.g. word_variation_dialog.py's own "+ Add" hookup).
        worker.result_ready.connect(
            lambda cover_path, w=worker: self._on_cover_ready(cover_path, w)
        )
        worker.start()
        self._cover_worker = worker

    def _on_cover_ready(self, cover_path: Path | None, worker: CoverArtWorker) -> None:
        if worker is not self._cover_worker:
            return  # superseded by a newer file selection -- ignore.
        self._cover_path = cover_path
        self.cover_ready.emit()
        # Updates the already-showing panel's own thumbnail in place --
        # a no-op if the source turned out ineligible (the info panel
        # isn't the visible widget in that case at all).
        if self._manifest is not None and self._manifest.eligible:
            self._info_panel.show_eligible(self._manifest, cover_path)


def _error_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {_ACCENT};")
    return label


#: The six row labels _InfoPanel always shows, in the same order,
#: whether currently in its placeholder or its real (eligible) state.
_INFO_ROW_LABELS = (
    "Audio track",
    "Duration",
    "Chapters",
    "Metadata",
    "Saved transcript",
    "Temp storage needed",
)


#: Elision width for each row's value label (ADR-0046 addendum) -- a
#: row is always exactly one line tall this way, in every state
#: (placeholder or real), which is what actually guarantees the cover
#: thumbnail's fixed size and the rows beside it stay in sync. Letting
#: values word-wrap (the original design) meant a long real value (e.g.
#: a long Metadata key list) could grow a row to two lines while the
#: placeholder's own dash never would, so a size measured from one
#: state didn't reliably hold for the other -- this removes that
#: possibility outright rather than trying to keep two measurements in
#: sync. The full, untruncated text is always still available as the
#: row's tooltip.
_ROW_VALUE_ELIDE_WIDTH = 500


class _InfoPanel(QFrame):
    """The Source step's persistent info panel: built once and updated
    in place across the placeholder / inspecting / eligible states
    (ADR-0046 addendum), rather than torn down and rebuilt on every
    transition. The previous "swap in a different widget" design caused
    two real problems: a visible flicker gap while a new file's
    inspection was in flight (the old widget was removed before the new
    one existed), and a placeholder whose measured height could drift
    from the real panel's, since they were two different widget
    instances that each had to independently arrive at the same number.
    Updating one persistent widget's fields removes both possibilities
    structurally rather than patching around them.

    Ineligible/error results still use their own separate, simpler
    widgets, swapped in in this panel's place — they have a
    fundamentally different shape (a variable-length reasons list, or a
    single message, not six fixed rows and a cover) and aren't the
    problem this persistent panel targets.
    """

    #: Fires when the "Change temp storage location…" link on the "Temp
    #: storage needed" row is clicked. Re-emitted by SourceStep, not
    #: acted on here — this panel has no reach into the wizard shell that
    #: owns Settings.
    open_settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)

        # Its own full-width row, left-aligned to the frame itself and
        # rendered larger than the persistent file card's own compact
        # badge -- not indented to match the info-rows column below it,
        # so it reads as the headline of this whole panel rather than
        # one more detail inside it.
        #
        # Always visible, never setVisible(False) -- a hidden widget is
        # skipped entirely when Qt computes this layout's height, which
        # is exactly what caused a real, measured height difference
        # between the placeholder and eligible states (this row's own
        # height disappearing and reappearing, not anything to do with
        # the info rows below it). Kept present with empty text and no
        # "state" property (so neither the eligible-green nor
        # ineligible-red QSS rule matches, leaving it visually blank —
        # just the base rule's padding/font-size, no color) while in
        # the placeholder state instead.
        self._heading = QLabel("")
        self._heading.setObjectName("eligibleBadgeLarge")
        root.addWidget(self._heading, alignment=Qt.AlignmentFlag.AlignLeft)

        content_row = QHBoxLayout()
        root.addLayout(content_row)

        self._cover_label = QLabel()
        self._cover_label.setObjectName("coverThumb")
        content_row.addWidget(self._cover_label)

        rows_widget = QWidget()
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        content_row.addWidget(rows_widget, stretch=1)

        self._row_values: dict[str, QLabel] = {}
        for label_text in _INFO_ROW_LABELS:
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setObjectName("statusLabel")
            label.setFixedWidth(140)
            if label_text == "Temp storage needed":
                label.setToolTip(
                    "The most disk space used at once while rendering — "
                    "temporary working files plus the final output file "
                    "that's kept afterward."
                )
            row.addWidget(label)
            value = QLabel("—")
            row.addWidget(value, stretch=1)
            if label_text == "Temp storage needed":
                change_location_btn = QPushButton("Change temp storage location…")
                change_location_btn.setFlat(True)
                change_location_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                change_location_btn.setStyleSheet(
                    f"QPushButton {{ border: none; background: transparent; "
                    f"color: {_ACCENT}; text-decoration: underline; }}"
                )
                change_location_btn.clicked.connect(
                    lambda _checked=False: self.open_settings_requested.emit()
                )
                row.addWidget(change_location_btn)
            rows_layout.addLayout(row)
            self._row_values[label_text] = value

        # Measured once, from this fixed six-row shape -- valid for
        # every later state since every row is always exactly one line
        # (see _ROW_VALUE_ELIDE_WIDTH above), so this never needs
        # re-measuring.
        self._cover_size = rows_widget.sizeHint().height()

        self.show_placeholder()

    def _set_row(self, label_text: str, full_text: str) -> None:
        value = self._row_values[label_text]
        metrics = value.fontMetrics()
        value.setText(
            metrics.elidedText(
                full_text, Qt.TextElideMode.ElideRight, _ROW_VALUE_ELIDE_WIDTH
            )
        )
        value.setToolTip(full_text)

    def show_placeholder(self) -> None:
        self._heading.setText("")
        self._heading.setProperty("state", "")
        _repolish(self._heading)
        set_cover_pixmap(
            self._cover_label, None, self._cover_size, fallback_text="Cover"
        )
        for label_text in _INFO_ROW_LABELS:
            self._set_row(label_text, "—")

    def show_eligible(self, manifest: MediaManifest, cover_path: Path | None) -> None:
        self._heading.setText("✓ Eligible for filtering")
        self._heading.setProperty("state", "eligible")
        _repolish(self._heading)
        set_cover_pixmap(
            self._cover_label, cover_path, self._cover_size, fallback_text="Cover"
        )

        track = next(
            (t for t in manifest.tracks if t.index == manifest.selected_track_index),
            None,
        )
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
            self._set_row("Audio track", track_desc + fallback)
        else:
            self._set_row("Audio track", "Unknown")

        self._set_row("Duration", _format_duration(manifest.duration_ms))
        self._set_row("Chapters", str(len(manifest.chapters)))

        if manifest.required_metadata:
            self._set_row(
                "Metadata", ", ".join(sorted(manifest.required_metadata.keys()))
            )
        else:
            self._set_row("Metadata", "None found")

        transcript = find_compatible_transcript(manifest.fingerprint)
        if transcript is not None:
            self._set_row(
                "Saved transcript",
                f"Found — {transcript.engine.name} {transcript.engine.version} "
                f"· {transcript.engine.model}",
            )
        else:
            self._set_row("Saved transcript", "None found for this source")

        estimate = estimate_storage_bytes(manifest)
        self._set_row(
            "Temp storage needed", _format_bytes(estimate) if estimate else "Unknown"
        )


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
