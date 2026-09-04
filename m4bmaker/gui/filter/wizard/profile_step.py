"""Profile wizard step (PRD §7.2 stage 4, §9.1, §9.4; ADR-0016).

Selection only — profile authoring itself lives in
:class:`~m4bmaker.gui.filter.profile_editor_dialog.ProfileEditorDialog`,
per ADR-0016 (which corrected an earlier, wrong assumption that it
belonged in ``CatalogWindow``; ``catalog_window.py``'s own docstring had
already ruled that out in ADR-0008). This step lists real saved
``FilterProfile`` records, lets a User pick one, and opens the editor for
create/edit/archive — nothing here mutates the catalog directly.

The same live ``CatalogService`` instance is shared with any
``CatalogWindow``/``ProfileEditorDialog`` this step opens, rather than
each loading its own copy — see ADR-0016's disclosed cross-window
staleness limitation for the one gap that sharing doesn't close (a
*separately opened* ``CatalogWindow``, e.g. from ``MainWindow``'s own
Tools menu, still holds its own instance; this step's own
``CatalogWindow`` reuses this step's instance and refreshes on close).
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_store import save_catalog
from m4bmaker.filter.models import FilterProfile
from m4bmaker.filter.transcript import Transcript
from m4bmaker.filter.transcript_text import ensure_transcript_text

from ..catalog_window import CatalogWindow
from ..profile_editor_dialog import ProfileEditorDialog
from ..word_variation_dialog import WordVariationDialog
from .step_base import WizardStep


class ProfileStep(WizardStep):
    """Pick a saved filter profile, or create/edit/archive one."""

    step_title = "Select Profile"
    step_subtitle = (
        "Choose a filter profile to use for this scan — a copy of its "
        "word list is locked in once scanning starts, so later edits "
        "won't change results already in progress."
    )

    def __init__(self, service: CatalogService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._selected_profile_id: str | None = None
        self._catalog_window: CatalogWindow | None = None
        self._transcript: Transcript | None = None

        root = QVBoxLayout(self)

        self._empty_label = QLabel("No filter profiles yet.")
        root.addWidget(self._empty_label)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._list_container, stretch=1)

        row_actions = QHBoxLayout()
        self._edit_btn = QPushButton("Edit")
        self._edit_btn.clicked.connect(self._on_edit_profile)
        row_actions.addWidget(self._edit_btn)
        self._archive_btn = QPushButton("Archive")
        self._archive_btn.clicked.connect(self._on_archive_profile)
        row_actions.addWidget(self._archive_btn)
        root.addLayout(row_actions)

        bottom_row = QHBoxLayout()
        new_btn = QPushButton("+ New Profile")
        new_btn.clicked.connect(self._on_new_profile)
        bottom_row.addWidget(new_btn)
        self._view_transcript_btn = QPushButton("View Transcript")
        self._view_transcript_btn.clicked.connect(self._on_view_transcript)
        self._view_transcript_btn.setVisible(False)
        bottom_row.addWidget(self._view_transcript_btn)
        # Nested at a tighter spacing than bottom_row's own (default)
        # inter-button gap, so proximity itself reads as "this caption
        # belongs to that button" rather than looking like one more
        # independent item in the row.
        find_more_words_group = QHBoxLayout()
        find_more_words_group.setContentsMargins(0, 0, 0, 0)
        find_more_words_group.setSpacing(4)
        self._find_more_words_btn = QPushButton("Find More Words…")
        self._find_more_words_btn.clicked.connect(self._on_find_more_words)
        self._find_more_words_btn.setVisible(False)
        find_more_words_group.addWidget(self._find_more_words_btn)
        self._find_more_words_caption = QLabel(
            "Scans your transcript for likely variations of catalog words"
        )
        self._find_more_words_caption.setObjectName("statusLabel")
        self._find_more_words_caption.setVisible(False)
        find_more_words_group.addWidget(self._find_more_words_caption)
        bottom_row.addLayout(find_more_words_group)
        bottom_row.addStretch(1)
        manage_btn = QPushButton("Word List…")
        manage_btn.clicked.connect(self._on_manage_catalog)
        bottom_row.addWidget(manage_btn)
        root.addLayout(bottom_row)

        self._refresh_profiles()

    # ── transcript hand-off (ADR-0022) ──────────────────────────────────

    def set_transcript(self, transcript: Transcript | None) -> None:
        """Called by the wizard shell with whichever real Transcript
        exists (Transcribe's own output, or one reused via Transcript
        step) every time the User continues into this step. Only used
        to offer "View Transcript" — Profile's own selection logic
        doesn't otherwise depend on it."""
        self._transcript = transcript
        self._view_transcript_btn.setVisible(transcript is not None)
        self._find_more_words_btn.setVisible(transcript is not None)
        self._find_more_words_caption.setVisible(transcript is not None)

    def _on_view_transcript(self) -> None:
        if self._transcript is None:
            return
        text_path = ensure_transcript_text(self._transcript)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(text_path)))

    # ── wizard step interface ───────────────────────────────────────────

    def can_advance(self) -> bool:
        return self._selected_profile_id is not None

    @property
    def selected_profile_id(self) -> str | None:
        return self._selected_profile_id

    # ── profile counts ───────────────────────────────────────────────────

    def _profile_counts(self, profile: FilterProfile) -> tuple[int, int]:
        """``(category_count, word_count)`` for *profile*, computed live
        from its ``entry_ids`` — not stored fields, so they can never go
        stale relative to the catalog they're derived from."""
        category_ids: set[str] = set()
        word_count = 0
        for entry_id in profile.entry_ids:
            try:
                entry = self._service.get_entry(entry_id)
            except KeyError:
                # Referenced but no longer exists — unreachable via this
                # service's own API (delete_entry blocks it while
                # referenced), same defensive stance create_snapshot()
                # already documents for this exact situation.
                continue
            category_ids.add(entry.category_id)
            word_count += 1
        return len(category_ids), word_count

    # ── list rendering ───────────────────────────────────────────────────

    def _clear_list(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_profiles(self) -> None:
        profiles = self._service.list_profiles(include_archived=False)
        self._clear_list()

        if not profiles:
            self._empty_label.setVisible(True)
            self._list_container.setVisible(False)
            self._selected_profile_id = None
            self._edit_btn.setEnabled(False)
            self._archive_btn.setEnabled(False)
            self._find_more_words_btn.setEnabled(False)
            self.can_advance_changed.emit(self.can_advance())
            return

        self._empty_label.setVisible(False)
        self._list_container.setVisible(True)

        previous = self._selected_profile_id
        still_present = any(p.id == previous for p in profiles)
        chosen = previous if still_present else profiles[0].id

        # No QButtonGroup needed: QRadioButton's autoExclusive default
        # already makes siblings under the same parent widget (all of
        # these share ``_list_container``, via ``_list_layout``) mutually
        # exclusive on its own.
        for profile in profiles:
            categories, words = self._profile_counts(profile)
            label = f"{profile.name}  ({categories} categories · {words} words)"
            radio = QRadioButton(label)
            radio.setChecked(profile.id == chosen)
            self._list_layout.addWidget(radio)
            radio.toggled.connect(
                lambda checked, pid=profile.id: self._on_profile_toggled(checked, pid)
            )
        self._selected_profile_id = chosen
        self._edit_btn.setEnabled(True)
        self._archive_btn.setEnabled(True)
        self._find_more_words_btn.setEnabled(True)
        self.can_advance_changed.emit(self.can_advance())

    def _on_profile_toggled(self, checked: bool, profile_id: str) -> None:
        if not checked:
            return
        self._selected_profile_id = profile_id
        self._find_more_words_btn.setEnabled(True)
        self.can_advance_changed.emit(self.can_advance())

    # ── actions ──────────────────────────────────────────────────────────

    def _on_new_profile(self) -> None:
        dialog = ProfileEditorDialog(self._service, profile_id=None, parent=self)
        if dialog.exec():
            self._selected_profile_id = dialog.saved_profile_id
            self._refresh_profiles()

    def _on_edit_profile(self) -> None:
        if self._selected_profile_id is None:
            return
        dialog = ProfileEditorDialog(
            self._service, profile_id=self._selected_profile_id, parent=self
        )
        if dialog.exec():
            self._refresh_profiles()

    def _on_archive_profile(self) -> None:
        if self._selected_profile_id is None:
            return
        profile = self._service.get_profile(self._selected_profile_id)
        confirm = QMessageBox.question(
            self,
            "Archive Profile",
            f"Archive “{profile.name}”? It stays available to any scan "
            "that already used it, but won't be offered as a choice here "
            "anymore.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._service.archive_profile(self._selected_profile_id)
        save_catalog(self._service)
        self._selected_profile_id = None
        self._refresh_profiles()

    def _on_find_more_words(self) -> None:
        if self._transcript is None or self._selected_profile_id is None:
            return
        snapshot = self._service.create_snapshot(self._selected_profile_id)
        dialog = WordVariationDialog(
            self._service, self._transcript, snapshot, parent=self
        )
        dialog.exec()
        # The dialog persists each addition immediately on its own "+
        # Add" click (ADR-0036) — refreshed unconditionally here since
        # there's no accept/reject distinction to key off, unlike
        # ProfileEditorDialog's own save-on-accept-only flow.
        self._refresh_profiles()

    def _on_manage_catalog(self) -> None:
        if self._catalog_window is None:
            self._catalog_window = CatalogWindow(self._service, parent=self)
            self._catalog_window.closed.connect(self._refresh_profiles)
        self._catalog_window.show()
        self._catalog_window.raise_()
        self._catalog_window.activateWindow()
