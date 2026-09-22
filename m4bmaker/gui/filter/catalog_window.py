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

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService, ImportPlan
from m4bmaker.filter.catalog_store import (
    CatalogImportError,
    backup_before_import,
    read_export_file,
    save_catalog,
    write_export_file,
)
from m4bmaker.filter.models import (
    CatalogEntry,
    Category,
    FilterProfile,
    SchemaValidationError,
)

_COL_CATEGORY_NAME = 0
_COL_CATEGORY_MASK = 1

_COL_ENTRY_PHRASE = 0
_COL_ENTRY_ENABLED = 1
_COL_ENTRY_MASK = 2
_COL_ENTRY_NOTES = 3

_ID_ROLE = Qt.ItemDataRole.UserRole


class CatalogWindow(QMainWindow):
    """Secondary window for category/word catalog management.

    :attr:`closed` fires whenever this window is closed (including a
    plain "hide, don't destroy" close under the lazy-create-and-reuse
    pattern ``MainWindow``/the wizard's Profile step both use) — added so
    a caller holding the same ``CatalogService`` instance (e.g.
    ``ProfileStep``, ADR-0016) can refresh anything it derived from the
    catalog (category/word counts) once editing here is done, without
    polling.
    """

    closed = Signal()

    def __init__(self, service: CatalogService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._show_archived = False
        self._selected_category_id: str | None = None

        self.setWindowTitle("Word List")
        self.setMinimumSize(640, 420)
        self.resize(820, 520)

        self._build_ui()
        self._refresh_categories()

    # ── persistence ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        save_catalog(self._service)

    def _run_dialog(self, dialog: QDialog) -> int:
        """Trivial wrapper around ``dialog.exec()`` — kept as a plain
        method on this class, not a bare call to the Qt-wrapped
        ``QDialog.exec()`` itself, so tests can patch it reliably
        (``unittest.mock.patch.object`` does not reliably take effect on
        a C++-bound method like this and can hang a headless test on the
        real modal call instead — same fix ``ReviewStep._run_dialog``
        already established)."""
        return dialog.exec()

    # ── export / import (ADR-0055) ──────────────────────────────────────────

    def _on_export(self) -> None:
        dialog = _ExportDialog(self._service, self)
        if self._run_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        categories, entries, profiles = dialog.resolve_records()

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Word List", "word-list-export.json", "JSON Files (*.json)"
        )
        if not path:
            return
        write_export_file(categories, entries, profiles, Path(path))
        self._set_status(f"Exported to {Path(path).name}.")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Word List", "", "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        import_path = Path(path)

        try:
            categories, entries, profiles = read_export_file(import_path)
        except CatalogImportError as exc:
            QMessageBox.warning(self, "Can't Import This File", str(exc))
            return

        plan = self._service.plan_import(categories, entries, profiles)
        dialog = _ImportPreviewDialog(plan, import_path.name, self)
        if self._run_dialog(dialog) != QDialog.DialogCode.Accepted:
            return

        backup_path = backup_before_import()
        summary = self._service.apply_import(
            plan, overwrite_duplicates=dialog.overwrite_duplicates
        )
        self._save()
        self._refresh_categories()

        changed = summary.new_categories + summary.new_words + summary.new_profiles
        if dialog.overwrite_duplicates:
            changed += summary.duplicate_words + summary.duplicate_profiles
        if changed == 0:
            self._set_status(
                f"Import complete — everything in {import_path.name} already "
                "matched your Word List, nothing changed."
            )
        else:
            backup_note = (
                f" Backed up your existing Word List to {backup_path.name} first."
                if backup_path is not None
                else ""
            )
            self._set_status(
                f"Imported {summary.new_words} new word(s), "
                f"{summary.new_categories} new categor"
                f"{'y' if summary.new_categories == 1 else 'ies'}, "
                f"{summary.new_profiles} new profile(s) from "
                f"{import_path.name}.{backup_note}"
            )

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        splitter.addWidget(self._build_categories_pane())
        splitter.addWidget(self._build_entries_pane())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # Export/Import act on the whole catalog (or a scope picked
        # inside their own dialog), not one pane's own item-management
        # row — a footer row of their own, with the status label
        # (Export/Import's own result text) directly to their left
        # rather than up at the top of the window, away from the
        # buttons that caused it.
        footer_row = QHBoxLayout()
        self._status_label = QLabel("")
        self._status_label.setObjectName("catalogStatusLabel")
        footer_row.addWidget(self._status_label, stretch=1)

        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._on_export)
        footer_row.addWidget(export_btn)

        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._on_import)
        footer_row.addWidget(import_btn)

        root.addLayout(footer_row)

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
        # Click any header (e.g. "Phrase") to sort by that column — makes
        # it easy to spot an existing word (or a close variant) before
        # adding what turns out to be a duplicate. Population in
        # _refresh_entries() suspends this while inserting rows — Qt
        # re-sorts on every insertRow()/setItem() otherwise, which can
        # scatter a row's own cells across the wrong rows mid-populate.
        self._entry_table.setSortingEnabled(True)
        # Without an explicit initial sort, Qt's own default sort
        # indicator on a freshly-sortable header is descending (Z-A) —
        # not the ascending order a User opening this window for the
        # first time expects. Set once here, not per-refresh, so a later
        # User click on a header still controls the order from then on.
        self._entry_table.sortByColumn(_COL_ENTRY_PHRASE, Qt.SortOrder.AscendingOrder)
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
        move_entry_btn = QPushButton("Move to Category…")
        move_entry_btn.clicked.connect(self._move_entry)
        entry_btn_row.addWidget(move_entry_btn)
        entry_btn_row.addStretch(1)
        layout.addLayout(entry_btn_row)

        return pane

    # ── theme ────────────────────────────────────────────────────────────────

    def apply_stylesheet(self, dark: bool) -> None:
        from m4bmaker.gui.styles import get_stylesheet

        self.setStyleSheet(get_stylesheet(dark))

    # ── lifecycle ────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        self.closed.emit()

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
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
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
        self._entry_table.setSortingEnabled(False)
        self._entry_table.blockSignals(True)
        self._entry_table.setRowCount(0)
        category_id = self._selected_category_id
        if category_id is None:
            self._entries_label.setText("Words")
            self._entry_table.setEnabled(False)
            self._new_phrase_input.setEnabled(False)
            self._entry_table.blockSignals(False)
            self._entry_table.setSortingEnabled(True)
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
        self._entry_table.setSortingEnabled(True)

    def _add_entry(self) -> None:
        category_id = self._selected_category_id
        if category_id is None:
            return
        phrase = self._new_phrase_input.text().strip()
        if not phrase:
            return
        # CatalogService.create_entry() itself still allows duplicates
        # (PRD §9.2: "warn about duplicates," not reject them — callers
        # decide) — this UI's own manual "+ Word" flow is where the User
        # asked for an outright block instead, checked with the same
        # normalized-phrase matching the real Matcher uses, so "Shit"
        # and "shit" are correctly treated as the same word.
        duplicate = self._service.find_duplicate_entry(category_id, phrase)
        if duplicate is not None:
            self._set_status(
                f"“{phrase}” is already in this category, as "
                f"“{duplicate.canonical_phrase}” — not added again."
            )
            return
        try:
            entry, _ = self._service.create_entry(category_id, phrase)
        except SchemaValidationError as exc:
            QMessageBox.warning(self, "Invalid Word", str(exc))
            return
        self._save()
        self._new_phrase_input.clear()
        self._refresh_entries()
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
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._service.delete_entry(entry_id)
        self._save()
        self._refresh_entries()
        self._set_status(f"Removed “{entry.canonical_phrase}”.")

    def _move_entry(self) -> None:
        entry_id = self._current_entry_id()
        if entry_id is None:
            return
        entry = self._service.get_entry(entry_id)
        # Archived categories aren't offered as move targets — moving an
        # active word into one would silently drop it out of any live
        # filtering, the same reasoning _add_entry() never lets a User
        # add straight into an archived category.
        other_categories = [
            c
            for c in self._service.list_categories(include_archived=False)
            if c.id != entry.category_id
        ]
        if not other_categories:
            QMessageBox.information(
                self,
                "Move Word",
                "There are no other categories to move this word to.",
            )
            return
        names = [c.name for c in other_categories]
        chosen_name, ok = QInputDialog.getItem(
            self,
            "Move Word",
            f"Move “{entry.canonical_phrase}” to:",
            names,
            editable=False,
        )
        if not ok:
            return
        target = other_categories[names.index(chosen_name)]
        # Same normalized-phrase duplicate check and outright block as
        # _add_entry()'s own manual-add flow (ADR-0040) — a move that
        # would create a duplicate in the target category gets the same
        # treatment as adding one there directly.
        duplicate = self._service.find_duplicate_entry(
            target.id, entry.canonical_phrase
        )
        if duplicate is not None:
            self._set_status(
                f"“{entry.canonical_phrase}” already exists in “{target.name}”, "
                f"as “{duplicate.canonical_phrase}” — not moved."
            )
            return
        self._service.update_entry(entry_id, category_id=target.id)
        self._save()
        self._refresh_entries()
        self._set_status(f"Moved “{entry.canonical_phrase}” to “{target.name}”.")

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


class _ExportDialog(QDialog):
    """Export scope picker — ADR-0055. "Everything" / "Selected
    categories" / "Selected profiles", chosen here rather than three
    separate buttons on the main window — matches the mockup
    (``docs/design/catalog-import-export-wireframe.html``)."""

    def __init__(self, service: CatalogService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._pressed_check_state = Qt.CheckState.Unchecked
        self.setWindowTitle("Export Word List")
        self.setMinimumSize(360, 420)
        self._build_ui()
        self._on_scope_changed()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(QLabel("Choose what to include, then save it as a JSON file."))

        self._all_radio = QRadioButton("Everything")
        self._all_radio.setChecked(True)
        self._categories_radio = QRadioButton("Selected categories")
        self._profiles_radio = QRadioButton("Selected profiles")
        self._scope_group = QButtonGroup(self)
        for radio in (self._all_radio, self._categories_radio, self._profiles_radio):
            self._scope_group.addButton(radio)
            radio.toggled.connect(self._on_scope_changed)
            root.addWidget(radio)

        self._category_list = self._build_checklist(
            self._service.list_categories(include_archived=False)
        )
        root.addWidget(self._category_list, stretch=1)

        self._profile_list = self._build_checklist(
            self._service.list_profiles(include_archived=False)
        )
        root.addWidget(self._profile_list, stretch=1)

        # Exactly one of _category_list/_profile_list/_all_spacer is
        # visible at a time (see _on_scope_changed), each with stretch=1
        # — without this, the "Everything" scope leaves no widget to
        # absorb the dialog's leftover height, and Qt's layout crams
        # every control down at the bottom with a large blank gap above
        # it instead of packing them tightly at the top.
        self._all_spacer = QWidget()
        root.addWidget(self._all_spacer, stretch=1)

        self._archived_cb = QCheckBox("Include archived items")
        self._archived_cb.setToolTip(
            'Only applies to "Everything" — a true backup, not a share.'
        )
        root.addWidget(self._archived_cb)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        export_btn = QPushButton("Export…")
        export_btn.setDefault(True)
        export_btn.clicked.connect(self._on_export_clicked)
        btn_row.addWidget(export_btn)
        root.addLayout(btn_row)

    def _build_checklist(self, items: list[Any]) -> QListWidget:
        """A checkbox list where clicking anywhere on a row — the glyph
        or the rest of the row — toggles it, matching the mockup's own
        one-click-target `<label>` rows. Selection highlighting is
        turned off entirely: a blue "selected" row that isn't also
        checked reads as "chosen" when it isn't, so only the checkmark
        should ever say that.

        Qt's item views already toggle a checkable item's state
        natively, but only for a click landing precisely on the tiny
        indicator rect — a plain click anywhere else on the row just
        selects it and leaves the checkbox alone, the opposite of what
        this dialog needs. ``itemPressed``/``itemClicked`` bracket
        that native toggle (it applies on release, so a press-time
        snapshot always predates it): if the state changed between
        press and click, the native toggle already handled an
        indicator click and nothing more should happen here; if it
        didn't, the click landed elsewhere on the row and this method
        applies the toggle itself. Toggling unconditionally in
        ``itemClicked`` would double-apply on an indicator click
        (native toggle, then this one canceling it back out)."""
        list_widget = QListWidget()
        list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        list_widget.itemPressed.connect(self._remember_pressed_check_state)
        list_widget.itemClicked.connect(self._toggle_item_check_state)
        for entity in items:
            item = QListWidgetItem(entity.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(_ID_ROLE, entity.id)
            list_widget.addItem(item)
        return list_widget

    def _remember_pressed_check_state(self, item: QListWidgetItem) -> None:
        self._pressed_check_state = item.checkState()

    def _toggle_item_check_state(self, item: QListWidgetItem) -> None:
        if item.checkState() != self._pressed_check_state:
            return  # the indicator's own native click toggle already fired
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _on_scope_changed(self) -> None:
        is_categories = self._categories_radio.isChecked()
        is_profiles = self._profiles_radio.isChecked()
        self._category_list.setVisible(is_categories)
        self._profile_list.setVisible(is_profiles)
        self._all_spacer.setVisible(not is_categories and not is_profiles)
        self._archived_cb.setEnabled(self._all_radio.isChecked())

    @staticmethod
    def _checked_ids(list_widget: QListWidget) -> list[str]:
        ids = []
        for row in range(list_widget.count()):
            item = list_widget.item(row)
            assert item is not None
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(_ID_ROLE))
        return ids

    def _on_export_clicked(self) -> None:
        if self._categories_radio.isChecked() and not self._checked_ids(
            self._category_list
        ):
            QMessageBox.warning(
                self, "Nothing Selected", "Choose at least one category to export."
            )
            return
        if self._profiles_radio.isChecked() and not self._checked_ids(
            self._profile_list
        ):
            QMessageBox.warning(
                self, "Nothing Selected", "Choose at least one profile to export."
            )
            return
        self.accept()

    def resolve_records(
        self,
    ) -> tuple[list[Category], list[CatalogEntry], list[FilterProfile]]:
        """Resolve the chosen scope into the records to write — called by
        the caller after this dialog accepts."""
        if self._categories_radio.isChecked():
            return self._service.export_categories(
                self._checked_ids(self._category_list)
            )
        if self._profiles_radio.isChecked():
            return self._service.export_profiles(self._checked_ids(self._profile_list))
        return self._service.export_everything(
            include_archived=self._archived_cb.isChecked()
        )


class _ImportPreviewDialog(QDialog):
    """Shows exactly what an import would do — new vs. duplicate words,
    new vs. merged categories, new vs. duplicate profiles — before
    anything is written (ADR-0055's core safety requirement). The
    caller applies the same *plan* this dialog previewed via
    ``CatalogService.apply_import`` once it accepts, so the two can
    never drift apart."""

    def __init__(
        self, plan: ImportPlan, file_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._plan = plan
        self.overwrite_duplicates = False
        self.setWindowTitle(f"Import Preview — {file_name}")
        self.setMinimumSize(420, 480)
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        note = QLabel("Nothing is written until you press Import.")
        note.setObjectName("statusLabel")
        root.addWidget(note)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        root.addWidget(self._summary_label)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        root.addWidget(self._tree, stretch=1)

        self._overwrite_cb = QCheckBox("Overwrite duplicates")
        self._overwrite_cb.toggled.connect(self._on_overwrite_toggled)
        root.addWidget(self._overwrite_cb)

        sub = QLabel(
            "Off by default — a duplicate keeps your existing notes and "
            "mask setting unless you check this."
        )
        sub.setObjectName("statusLabel")
        sub.setWordWrap(True)
        root.addWidget(sub)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        import_btn = QPushButton("Import")
        import_btn.setDefault(True)
        import_btn.clicked.connect(self.accept)
        btn_row.addWidget(import_btn)
        root.addLayout(btn_row)

    def _on_overwrite_toggled(self) -> None:
        self.overwrite_duplicates = self._overwrite_cb.isChecked()
        self._populate()

    def _duplicate_label(self) -> str:
        return (
            "duplicate — will overwrite"
            if self.overwrite_duplicates
            else "duplicate — skipped"
        )

    def _populate(self) -> None:
        self._tree.clear()
        new_categories = 0
        new_words = 0
        duplicate_words = 0

        for cat_plan in self._plan.categories:
            if cat_plan.is_new_category:
                new_categories += 1
            suffix = (
                "new category" if cat_plan.is_new_category else "merges into existing"
            )
            cat_item = QTreeWidgetItem(self._tree)
            cat_item.setText(0, f"{cat_plan.name} ({suffix})")
            for entry_plan in cat_plan.entries:
                word_item = QTreeWidgetItem(cat_item)
                if entry_plan.is_duplicate:
                    duplicate_words += 1
                    word_item.setText(
                        0, f"{entry_plan.canonical_phrase} — {self._duplicate_label()}"
                    )
                else:
                    new_words += 1
                    word_item.setText(0, f"{entry_plan.canonical_phrase} — new")
            cat_item.setExpanded(True)

        new_profiles = 0
        duplicate_profiles = 0
        if self._plan.profiles:
            profiles_item = QTreeWidgetItem(self._tree)
            profiles_item.setText(0, "Profiles")
            for profile_plan in self._plan.profiles:
                profile_item = QTreeWidgetItem(profiles_item)
                if profile_plan.is_duplicate:
                    duplicate_profiles += 1
                    profile_item.setText(
                        0, f"{profile_plan.name} — {self._duplicate_label()}"
                    )
                else:
                    new_profiles += 1
                    profile_item.setText(0, f"{profile_plan.name} — new")
            profiles_item.setExpanded(True)

        duplicate_verb = (
            "will be overwritten" if self.overwrite_duplicates else "will be skipped"
        )
        self._summary_label.setText(
            f"{new_categories} new categor{'y' if new_categories == 1 else 'ies'} · "
            f"{new_words} new word(s) · "
            f"{duplicate_words + duplicate_profiles} duplicate(s) found "
            f"({duplicate_verb}) · "
            f"{new_profiles} new profile(s)"
        )
