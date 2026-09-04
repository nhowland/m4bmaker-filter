"""Settings window: filtering-feature preferences.

Holds storage-location overrides (downloaded models, saved transcripts,
default filtered-output folder, render/transcription scratch space) and
transcription defaults (preferred model) — the starting set the User
asked for, deliberately meant to be the basis for further per-feature
settings added later rather than a finished/closed list.

Scoped entirely to this feature via ``filter/settings.py`` — deliberately
does not touch the base app's own ``gui/prefs.py`` (dark mode, update
checks), which predates this fork and the User wants to keep managed
separately.

Every field persists immediately on change, matching the "no separate
Save button" choice ``CatalogWindow``/``gui/prefs.py`` both already
make — there is nothing here to lose by simply closing the window.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter import settings as _settings
from m4bmaker.filter.model_manager import KNOWN_MODELS
from m4bmaker.filter.storage import (
    models_dir,
    output_dir_override,
    temp_root,
    transcripts_dir,
)

_NO_PREFERENCE = "No preference"


class SettingsWindow(QMainWindow):
    """Secondary window for filtering-feature settings.

    :attr:`closed` fires whenever this window is closed — same
    lazy-create-and-reuse / refresh-on-close purpose as
    ``CatalogWindow.closed``/``ModelManagerWindow.closed``, kept here for
    consistency even though nothing currently needs to react to it (a
    future setting well might)."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row_edits: dict[str, QLineEdit] = {}
        self._row_resolvers: dict[str, Callable[[], Path | None]] = {}
        self._row_defaults: dict[str, str] = {}
        self._row_reset_btns: dict[str, QPushButton] = {}

        self.setWindowTitle("Settings")
        self._build_ui()

        # Sized to the real content height, not a hardcoded guess -- a
        # fixed number here previously left the window too short once a
        # fourth storage row (and its own real clarifying spacing) pushed
        # total content past it, silently clipping/overlapping the last
        # row's buttons rather than growing the window to fit. Measuring
        # the actual built layout means this keeps holding as rows are
        # added or removed later, instead of needing a re-guess each time.
        central = self.centralWidget()
        assert central is not None  # set by _build_ui() just above
        content_height = central.sizeHint().height()
        self.setMinimumSize(620, content_height + 20)
        self.resize(720, content_height + 40)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.addWidget(self._build_storage_section())
        root.addWidget(self._build_transcription_section())
        root.addStretch(1)

    def _build_storage_section(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Storage Locations")
        heading.setStyleSheet("font-weight: 600;")
        # Bigger than a row's own internal spacing (set in
        # _build_folder_row) on purpose — the gap between two different
        # settings needs to read as clearly larger than the gap between
        # one setting's own label and its path, or the whole section
        # reads as one undifferentiated block rather than four rows.
        layout.setSpacing(24)
        layout.addWidget(heading)

        layout.addLayout(
            self._build_folder_row(
                "Downloaded Models", "models_dir", models_dir, "Default location"
            )
        )
        layout.addLayout(
            self._build_folder_row(
                "Transcripts", "transcripts_dir", transcripts_dir, "Default location"
            )
        )
        layout.addLayout(
            self._build_folder_row(
                "Filtered Output",
                "output_dir",
                output_dir_override,
                "Same folder as the source book",
            )
        )
        layout.addLayout(
            self._build_folder_row(
                "Temp Files",
                "temp_dir",
                temp_root,
                "System cache folder",
                tooltip=(
                    "Where scratch audio is written while rendering and "
                    "transcribing — the largest consumer of disk space in "
                    "this feature, since it holds full-length uncompressed "
                    "copies of the book being processed. Point this at a "
                    "drive with plenty of free space if your system drive "
                    "is tight."
                ),
            )
        )
        return panel

    def _build_folder_row(
        self,
        label: str,
        key: str,
        resolver: Callable[[], Path | None],
        default_description: str,
        tooltip: str | None = None,
    ) -> QVBoxLayout:
        """A label + actions on one line, with the path on its own
        full-width line beneath — a path squeezed onto the same line as
        the label and both buttons only had a sliver of the window's
        width to show in, which cut off exactly the part (the actual
        folder) a User needs to read."""
        group = QVBoxLayout()
        group.setSpacing(4)

        header = QHBoxLayout()
        label_widget = QLabel(label)
        if tooltip is not None:
            label_widget.setToolTip(tooltip)
        header.addWidget(label_widget)
        header.addStretch(1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(lambda _checked=False, k=key: self._on_browse(k))
        header.addWidget(browse_btn)
        reset_btn = QPushButton("Reset to Default")
        reset_btn.clicked.connect(lambda _checked=False, k=key: self._on_reset(k))
        header.addWidget(reset_btn)
        group.addLayout(header)

        edit = QLineEdit()
        edit.setReadOnly(True)
        group.addWidget(edit)

        self._row_edits[key] = edit
        self._row_resolvers[key] = resolver
        self._row_defaults[key] = default_description
        self._row_reset_btns[key] = reset_btn
        self._refresh_row(key)
        return group

    def _refresh_row(self, key: str) -> None:
        resolved = self._row_resolvers[key]()
        text = (
            str(resolved)
            if resolved is not None
            else f"{self._row_defaults[key]} (default)"
        )
        self._row_edits[key].setText(text)
        self._row_edits[key].setCursorPosition(0)
        self._row_edits[key].setToolTip(text)
        self._row_reset_btns[key].setEnabled(_settings.get(key) is not None)

    def _on_browse(self, key: str) -> None:
        current = self._row_resolvers[key]()
        start_dir = str(current) if current is not None else str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose Folder", start_dir)
        if path:
            _settings.set(key, path)
            self._refresh_row(key)

    def _on_reset(self, key: str) -> None:
        _settings.set(key, None)
        self._refresh_row(key)

    def _build_transcription_section(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Transcription Defaults")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        row = QHBoxLayout()
        row.addWidget(QLabel("Preferred Model"))
        self._model_combo = QComboBox()
        self._model_combo.addItem(_NO_PREFERENCE, None)
        for spec in KNOWN_MODELS:
            self._model_combo.addItem(f"{spec.name} ({spec.label})", spec.name)
        current = _settings.get("preferred_model")
        index = self._model_combo.findData(current)
        self._model_combo.setCurrentIndex(index if index >= 0 else 0)
        self._model_combo.currentIndexChanged.connect(self._on_preferred_model_changed)
        row.addWidget(self._model_combo, stretch=1)
        layout.addLayout(row)

        return panel

    def _on_preferred_model_changed(self, index: int) -> None:
        _settings.set("preferred_model", self._model_combo.itemData(index))

    # ── theme ────────────────────────────────────────────────────────────

    def apply_stylesheet(self, dark: bool) -> None:
        from m4bmaker.gui.styles import get_stylesheet

        self.setStyleSheet(get_stylesheet(dark))

    # ── shutdown ─────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        self.closed.emit()
