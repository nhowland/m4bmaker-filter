"""Catalog management window: category/word CRUD (PRD §9.2, §2.1).

First screen of G5, built and persisted independently of the rest of the
guided workflow (PRD §7) — a User can build up their word catalog at any
time, not only mid-wizard. Profile management (selecting entries into a
named, reusable filter profile) is a distinct PRD capability from catalog
management and is deliberately not built here; it belongs with the
wizard's "select profile" step, a later increment.

Persistence: every mutation (create/rename/archive/delete/toggle) saves
the whole catalog immediately via ``catalog_store.save_catalog`` — the
same "persist on every change, no separate Save button" choice
``gui/prefs.py`` already makes for app preferences. This trades a
handful of extra small disk writes for the guarantee that closing the
window (or the app crashing) never loses an edit.

Archive-vs-hard-delete (PRD §9.2) needs no branching in this UI at all —
``CatalogService.delete_category``/``delete_entry`` already decide that
internally based on whether a saved profile references the item, so the
"Delete" button here always calls the same method regardless of which
outcome results.

Masking (fork-specific, not in PRD §9.1 — see ``docs/adr/0011-catalog-masking.md``):
both tables carry a "Mask" checkbox column, mirroring "Enabled"'s exact
checkbox pattern. Checking a category's Mask masks every term in it in
the Review screen regardless of each entry's own flag; an entry's own
Mask still applies even if its category isn't masked
(``CatalogService.is_masked`` composes the two by OR).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_store import save_catalog
from m4bmaker.filter.models import SchemaValidationError

_COL_CATEGORY_NAME = 0
_COL_CATEGORY_MASK = 1

_COL_ENTRY_PHRASE = 0
_COL_ENTRY_ENABLED = 1
_COL_ENTRY_MASK = 2
_COL_ENTRY_NOTES = 3

_ID_ROLE = Qt.ItemDataRole.UserRole


class CatalogWindow(QMainWindow):
    """Secondary window for category/word catalog management."""

    def __init__(self, service: CatalogService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._show_archived = False
        self._selected_category_id: str | None = None

        self.setWindowTitle("Word Catalog")
        self.setMinimumSize(640, 420)
        self.resize(820, 520)

        self._build_ui()
        self._refresh_categories()

    # ── persistence ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        save_catalog(self._service)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._status_label = QLabel("")
        self._status_label.setObjectName("catalogStatusLabel")
        root.addWidget(self._status_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        splitter.addWidget(self._build_categories_pane())
        splitter.addWidget(self._build_entries_pane())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    def _build_categories_pane(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Categories"))

        self._category_table = QTableWidget(0, 2)
        self._category_table.setHorizontalHeaderLabels(["Name", "Mask"])
        self._category_table.horizontalHeader().setSectionResizeMode(
            _COL_CATEGORY_NAME, QHeaderView.ResizeMode.Stretch
        )
        self._category_table.horizontalHeader().setSectionResizeMode(
            _COL_CATEGORY_MASK, QHeaderView.ResizeMode.ResizeToContents
        )
        mask_header = self._category_table.horizontalHeaderItem(_COL_CATEGORY_MASK)
        if mask_header is not None:
            mask_header.setToolTip(
                "Mask every term in this category in the Review screen, "
                "regardless of each term's own Mask setting."
            )
        self._category_table.verticalHeader().setVisible(False)
        self._category_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._category_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._category_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._category_table.itemSelectionChanged.connect(self._on_category_selected)
        self._category_table.itemChanged.connect(self._on_category_item_changed)
        layout.addWidget(self._category_table, stretch=1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Category")
        add_btn.clicked.connect(self._add_category)
        btn_row.addWidget(add_btn)

        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename_category)
        btn_row.addWidget(rename_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_category)
        btn_row.addWidget(delete_btn)
        layout.addLayout(btn_row)

        self._show_archived_cb = QCheckBox("Show archived")
        self._show_archived_cb.toggled.connect(self._on_show_archived_toggled)
        layout.addWidget(self._show_archived_cb)

        return pane

    def _build_entries_pane(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)

        self._entries_label = QLabel("Words")
        layout.addWidget(self._entries_label)

        self._entry_table = QTableWidget(0, 4)
        self._entry_table.setHorizontalHeaderLabels(
            ["Phrase", "Enabled", "Mask", "Notes"]
        )
        header = self._entry_table.horizontalHeader()
        header.setSectionResizeMode(
            _COL_ENTRY_PHRASE, QHeaderView.ResizeMode.Interactive
        )
        header.setSectionResizeMode(
            _COL_ENTRY_ENABLED, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(
            _COL_ENTRY_MASK, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(_COL_ENTRY_NOTES, QHeaderView.ResizeMode.Stretch)
        mask_header = self._entry_table.horizontalHeaderItem(_COL_ENTRY_MASK)
        if mask_header is not None:
            mask_header.setToolTip(
                "Mask just this term in the Review screen — its category "
                "doesn't need to be masked too."
            )
        self._entry_table.verticalHeader().setVisible(False)
        self._entry_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._entry_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._entry_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._entry_table.itemChanged.connect(self._on_entry_item_changed)
        layout.addWidget(self._entry_table, stretch=1)

        add_row = QHBoxLayout()
        self._new_phrase_input = QLineEdit()
        self._new_phrase_input.setPlaceholderText("Add a word or phrase…")
        self._new_phrase_input.returnPressed.connect(self._add_entry)
        add_row.addWidget(self._new_phrase_input, stretch=1)

        add_entry_btn = QPushButton("+ Word")
        add_entry_btn.clicked.connect(self._add_entry)
        add_row.addWidget(add_entry_btn)
        layout.addLayout(add_row)

        entry_btn_row = QHBoxLayout()
        delete_entry_btn = QPushButton("Delete")
        delete_entry_btn.clicked.connect(self._delete_entry)
        entry_btn_row.addWidget(delete_entry_btn)
        entry_btn_row.addStretch(1)
        layout.addLayout(entry_btn_row)

        return pane

    # ── theme ────────────────────────────────────────────────────────────────

    def apply_stylesheet(self, dark: bool) -> None:
        from m4bmaker.gui.styles import get_stylesheet

        self.setStyleSheet(get_stylesheet(dark))

    # ── status ───────────────────────────────────────────────────────────────

    def _set_status(self, message: str) -> None:
        self._status_label.setText(message)

    # ── categories ───────────────────────────────────────────────────────────

    def _refresh_categories(self) -> None:
        categories = self._service.list_categories(include_archived=self._show_archived)
        self._category_table.blockSignals(True)
        self._category_table.setRowCount(0)
        for category in categories:
            row = self._category_table.rowCount()
            self._category_table.insertRow(row)
            label = category.name + (" (archived)" if category.archived else "")
            item = QTableWidgetItem(label)
            item.setData(_ID_ROLE, category.id)
            self._category_table.setItem(row, _COL_CATEGORY_NAME, item)

            mask_item = QTableWidgetItem()
            mask_item.setFlags(
                (mask_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            mask_item.setCheckState(
                Qt.CheckState.Checked
                if category.mask_all_terms
                else Qt.CheckState.Unchecked
            )
            mask_item.setData(_ID_ROLE, category.id)
            self._category_table.setItem(row, _COL_CATEGORY_MASK, mask_item)
        self._category_table.blockSignals(False)

        # Preserve selection if the previously-selected category still exists
        # in the (possibly just-changed) archived-visibility filter.
        found = False
        if self._selected_category_id is not None:
            for row in range(self._category_table.rowCount()):
                row_item = self._category_table.item(row, _COL_CATEGORY_NAME)
                if (
                    row_item is not None
                    and row_item.data(_ID_ROLE) == self._selected_category_id
                ):
                    self._category_table.selectRow(row)
                    found = True
                    break
        if not found:
            self._selected_category_id = None
        self._refresh_entries()

    def _current_category_id(self) -> str | None:
        rows = self._category_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._category_table.item(rows[0].row(), _COL_CATEGORY_NAME)
        return item.data(_ID_ROLE) if item is not None else None

    def _on_category_selected(self) -> None:
        self._selected_category_id = self._current_category_id()
        self._refresh_entries()

    def _on_show_archived_toggled(self) -> None:
        self._show_archived = self._show_archived_cb.isChecked()
        self._refresh_categories()

    def _on_category_item_changed(self, item: QTableWidgetItem) -> None:
        category_id = item.data(_ID_ROLE)
        if category_id is None or item.column() != _COL_CATEGORY_MASK:
            return
        masked = item.checkState() == Qt.CheckState.Checked
        self._service.update_category(category_id, mask_all_terms=masked)
        self._save()

    def _add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "New Category", "Category name:")
        if not ok:
            return
        try:
            category = self._service.create_category(name)
        except SchemaValidationError as exc:
            QMessageBox.warning(self, "Invalid Category", str(exc))
            return
        self._save()
        self._selected_category_id = category.id
        self._refresh_categories()
        self._set_status(f"Added category “{category.name}”.")

    def _rename_category(self) -> None:
        category_id = self._current_category_id()
        if category_id is None:
            return
        category = self._service.get_category(category_id)
        name, ok = QInputDialog.getText(
            self, "Rename Category", "Category name:", text=category.name
        )
        if not ok:
            return
        try:
            self._service.update_category(category_id, name=name)
        except SchemaValidationError as exc:
            QMessageBox.warning(self, "Invalid Category", str(exc))
            return
        self._save()
        self._refresh_categories()

    def _delete_category(self) -> None:
        category_id = self._current_category_id()
        if category_id is None:
            return
        category = self._service.get_category(category_id)
        confirm = QMessageBox.question(
            self,
            "Delete Category",
            f"Delete “{category.name}” and its words? If this category is "
            "used by a saved profile, it will be archived instead of removed.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._service.delete_category(category_id)
        self._save()
        self._selected_category_id = None
        self._refresh_categories()
        self._set_status(f"Removed “{category.name}”.")

    # ── entries ──────────────────────────────────────────────────────────────

    def _refresh_entries(self) -> None:
        self._entry_table.blockSignals(True)
        self._entry_table.setRowCount(0)
        category_id = self._selected_category_id
        if category_id is None:
            self._entries_label.setText("Words")
            self._entry_table.setEnabled(False)
            self._new_phrase_input.setEnabled(False)
            self._entry_table.blockSignals(False)
            return

        self._entry_table.setEnabled(True)
        self._new_phrase_input.setEnabled(True)
        category = self._service.get_category(category_id)
        entries = self._service.list_entries(
            category_id=category_id, include_archived=self._show_archived
        )
        self._entries_label.setText(f"Words in “{category.name}”")

        for entry in entries:
            row = self._entry_table.rowCount()
            self._entry_table.insertRow(row)

            phrase_label = entry.canonical_phrase + (
                " (archived)" if entry.archived else ""
            )
            phrase_item = QTableWidgetItem(phrase_label)
            phrase_item.setData(_ID_ROLE, entry.id)
            phrase_item.setFlags(phrase_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._entry_table.setItem(row, _COL_ENTRY_PHRASE, phrase_item)

            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(
                (enabled_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            enabled_item.setCheckState(
                Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
            )
            enabled_item.setData(_ID_ROLE, entry.id)
            self._entry_table.setItem(row, _COL_ENTRY_ENABLED, enabled_item)

            mask_item = QTableWidgetItem()
            mask_item.setFlags(
                (mask_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            mask_item.setCheckState(
                Qt.CheckState.Checked if entry.mask else Qt.CheckState.Unchecked
            )
            mask_item.setData(_ID_ROLE, entry.id)
            self._entry_table.setItem(row, _COL_ENTRY_MASK, mask_item)

            notes_item = QTableWidgetItem(entry.notes)
            notes_item.setData(_ID_ROLE, entry.id)
            self._entry_table.setItem(row, _COL_ENTRY_NOTES, notes_item)

        self._entry_table.blockSignals(False)

    def _add_entry(self) -> None:
        category_id = self._selected_category_id
        if category_id is None:
            return
        phrase = self._new_phrase_input.text().strip()
        if not phrase:
            return
        try:
            entry, duplicate = self._service.create_entry(category_id, phrase)
        except SchemaValidationError as exc:
            QMessageBox.warning(self, "Invalid Word", str(exc))
            return
        self._save()
        self._new_phrase_input.clear()
        self._refresh_entries()
        if duplicate is not None:
            self._set_status(
                f"Added “{entry.canonical_phrase}” — note: this looks "
                f"like a duplicate of “{duplicate.canonical_phrase}” already "
                "in this category."
            )
        else:
            self._set_status(f"Added “{entry.canonical_phrase}”.")

    def _current_entry_id(self) -> str | None:
        rows = self._entry_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._entry_table.item(rows[0].row(), _COL_ENTRY_PHRASE)
        return item.data(_ID_ROLE) if item is not None else None

    def _delete_entry(self) -> None:
        entry_id = self._current_entry_id()
        if entry_id is None:
            return
        entry = self._service.get_entry(entry_id)
        confirm = QMessageBox.question(
            self,
            "Delete Word",
            f"Delete “{entry.canonical_phrase}”? If it is used by a saved "
            "profile, it will be archived instead of removed.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._service.delete_entry(entry_id)
        self._save()
        self._refresh_entries()
        self._set_status(f"Removed “{entry.canonical_phrase}”.")

    def _on_entry_item_changed(self, item: QTableWidgetItem) -> None:
        entry_id = item.data(_ID_ROLE)
        if entry_id is None:
            return
        column = item.column()
        if column == _COL_ENTRY_ENABLED:
            enabled = item.checkState() == Qt.CheckState.Checked
            self._service.update_entry(entry_id, enabled=enabled)
            self._save()
        elif column == _COL_ENTRY_MASK:
            masked = item.checkState() == Qt.CheckState.Checked
            self._service.update_entry(entry_id, mask=masked)
            self._save()
        elif column == _COL_ENTRY_NOTES:
            self._service.update_entry(entry_id, notes=item.text())
            self._save()
