"""Tests for m4bmaker.gui.filter.catalog_window.CatalogWindow (PRD §9.2,
§2.1, G5).

Headless behavioral tests, not visual ones: this environment cannot grant
the macOS Screen Recording / Accessibility permissions needed to
screenshot or drive the live app (see ADR-0008), so these tests
substitute for that by constructing the real widget tree under
``QT_QPA_PLATFORM=offscreen`` (set in ``tests/gui/conftest.py``) and
driving it exactly as a user would — selecting rows, checking boxes,
typing into fields, clicking buttons — then asserting on both the
resulting widget state and the underlying ``CatalogService``.

``QInputDialog.getText`` and ``QMessageBox.question`` are modal and
would block indefinitely under a test runner, so every test that
exercises a code path calling them patches the call to return
immediately, exactly as a user's dialog interaction would.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.models import CatalogEntry, Category
from m4bmaker.gui.filter.catalog_window import (
    _COL_CATEGORY_MASK,
    _COL_ENTRY_ENABLED,
    _COL_ENTRY_MASK,
    _COL_ENTRY_NOTES,
    _COL_ENTRY_PHRASE,
    _ID_ROLE,
    CatalogWindow,
    _ExportDialog,
    _ImportPreviewDialog,
)

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture()
def mock_save() -> Iterator[MagicMock]:
    with patch("m4bmaker.gui.filter.catalog_window.save_catalog") as mocked:
        yield mocked


@pytest.fixture()
def service() -> CatalogService:
    return CatalogService()


@pytest.fixture()
def win(service: CatalogService, mock_save: MagicMock) -> CatalogWindow:
    return CatalogWindow(service)


def _select_category_row(win: CatalogWindow, row: int) -> None:
    win._category_table.selectRow(row)


def _item(table: QTableWidget, row: int, col: int) -> QTableWidgetItem:
    cell = table.item(row, col)
    assert cell is not None
    return cell


def _find_button(widget: QWidget, text_contains: str) -> QPushButton:
    for btn in widget.findChildren(QPushButton):
        if text_contains in btn.text():
            return btn
    raise AssertionError(f"no button containing {text_contains!r}")


class TestConstruction:
    def test_window_creates_without_error(self, win: CatalogWindow) -> None:
        assert win is not None

    def test_starts_with_no_category_selected(self, win: CatalogWindow) -> None:
        assert win._selected_category_id is None
        assert win._entry_table.isEnabled() is False
        assert win._new_phrase_input.isEnabled() is False

    def test_apply_stylesheet_does_not_raise(self, win: CatalogWindow) -> None:
        win.apply_stylesheet(True)
        win.apply_stylesheet(False)


class TestAddCategory:
    def test_adds_category_to_service_and_table(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getText",
            return_value=("Profanity", True),
        ):
            win._add_category()

        categories = service.list_categories()
        assert len(categories) == 1
        assert categories[0].name == "Profanity"
        assert win._category_table.rowCount() == 1
        assert _item(win._category_table, 0, 0).text() == "Profanity"
        mock_save.assert_called_once()

    def test_cancelling_dialog_adds_nothing(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getText",
            return_value=("", False),
        ):
            win._add_category()

        assert service.list_categories() == []
        mock_save.assert_not_called()

    def test_invalid_name_shows_warning_and_adds_nothing(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        with (
            patch(
                "m4bmaker.gui.filter.catalog_window.QInputDialog.getText",
                return_value=("", True),
            ),
            patch(
                "m4bmaker.gui.filter.catalog_window.QMessageBox.warning"
            ) as mock_warn,
        ):
            win._add_category()

        assert service.list_categories() == []
        mock_warn.assert_called_once()
        mock_save.assert_not_called()


class TestRenameCategory:
    def test_renames_selected_category(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        cat = service.create_category("Profanty")
        win._refresh_categories()
        _select_category_row(win, 0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getText",
            return_value=("Profanity", True),
        ):
            win._rename_category()

        assert service.get_category(cat.id).name == "Profanity"
        assert _item(win._category_table, 0, 0).text() == "Profanity"

    def test_no_selection_is_a_no_op(
        self, win: CatalogWindow, mock_save: MagicMock
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getText"
        ) as mock_dialog:
            win._rename_category()
        mock_dialog.assert_not_called()


class TestDeleteCategory:
    def test_confirmed_delete_removes_unreferenced_category(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        service.create_category("Profanity")
        win._refresh_categories()
        _select_category_row(win, 0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            win._delete_category()

        assert service.list_categories() == []
        assert win._category_table.rowCount() == 0
        assert win._selected_category_id is None

    def test_declined_confirmation_keeps_category(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        service.create_category("Profanity")
        win._refresh_categories()
        _select_category_row(win, 0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            win._delete_category()

        assert len(service.list_categories()) == 1

    def test_confirmation_defaults_to_no(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        # A destructive confirmation must never default to the destructive
        # choice -- an accidental Enter/Return keypress on this dialog
        # should decline, not delete.
        service.create_category("Profanity")
        win._refresh_categories()
        _select_category_row(win, 0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as mock_question:
            win._delete_category()

        assert mock_question.call_args.args[-1] == QMessageBox.StandardButton.No


class TestShowArchivedToggle:
    def test_toggling_reveals_archived_categories(
        self, win: CatalogWindow, service: CatalogService, mock_save: MagicMock
    ) -> None:
        cat = service.create_category("Old")
        service.archive_category(cat.id)
        win._refresh_categories()
        assert win._category_table.rowCount() == 0

        win._show_archived_cb.setChecked(True)

        assert win._category_table.rowCount() == 1
        assert "(archived)" in _item(win._category_table, 0, 0).text()


class TestCategoryMasking:
    def test_new_category_mask_checkbox_starts_unchecked(
        self, win: CatalogWindow, service: CatalogService
    ) -> None:
        service.create_category("Profanity")
        win._refresh_categories()

        item = _item(win._category_table, 0, _COL_CATEGORY_MASK)
        assert item.checkState() == Qt.CheckState.Unchecked

    def test_toggling_category_mask_checkbox_updates_service(
        self, win: CatalogWindow, service: CatalogService
    ) -> None:
        cat = service.create_category("Slurs")
        win._refresh_categories()

        item = _item(win._category_table, 0, _COL_CATEGORY_MASK)
        assert item.data(_ID_ROLE) == cat.id
        item.setCheckState(Qt.CheckState.Checked)

        assert service.get_category(cat.id).mask_all_terms is True

    def test_category_mask_masks_every_entry_regardless_of_its_own_flag(
        self, win: CatalogWindow, service: CatalogService
    ) -> None:
        cat = service.create_category("Slurs")
        entry, _ = service.create_entry(cat.id, "slur-example")
        win._refresh_categories()

        item = _item(win._category_table, 0, _COL_CATEGORY_MASK)
        item.setCheckState(Qt.CheckState.Checked)

        assert service.get_entry(entry.id).mask is False
        assert service.is_masked(entry.id) is True


class TestEntries:
    @pytest.fixture()
    def category_id(self, win: CatalogWindow, service: CatalogService) -> str:
        cat = service.create_category("Profanity")
        win._refresh_categories()
        _select_category_row(win, 0)
        return cat.id

    def test_selecting_category_enables_entry_controls(
        self, win: CatalogWindow, category_id: str
    ) -> None:
        assert win._entry_table.isEnabled() is True
        assert win._new_phrase_input.isEnabled() is True
        assert win._selected_category_id == category_id

    def test_add_entry_via_text_field(
        self,
        win: CatalogWindow,
        service: CatalogService,
        category_id: str,
        mock_save: MagicMock,
    ) -> None:
        win._new_phrase_input.setText("darn")
        win._add_entry()

        entries = service.list_entries(category_id=category_id)
        assert len(entries) == 1
        assert entries[0].canonical_phrase == "darn"
        assert win._entry_table.rowCount() == 1
        assert win._new_phrase_input.text() == ""

    def test_add_entry_blank_phrase_is_a_no_op(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        win._new_phrase_input.setText("   ")
        win._add_entry()
        assert service.list_entries(category_id=category_id) == []

    def test_add_duplicate_entry_is_rejected_not_added(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        service.create_entry(category_id, "darn")
        win._refresh_entries()

        win._new_phrase_input.setText("darn")
        win._add_entry()

        assert "already" in win._status_label.text()
        assert len(service.list_entries(category_id=category_id)) == 1

    def test_add_duplicate_entry_is_case_insensitive(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        service.create_entry(category_id, "darn")
        win._refresh_entries()

        win._new_phrase_input.setText("DARN")
        win._add_entry()

        assert len(service.list_entries(category_id=category_id)) == 1

    def test_add_duplicate_entry_leaves_input_text_for_the_user_to_see(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        service.create_entry(category_id, "darn")
        win._refresh_entries()

        win._new_phrase_input.setText("darn")
        win._add_entry()

        assert win._new_phrase_input.text() == "darn"

    def test_add_entry_in_a_different_category_is_not_a_duplicate(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        service.create_entry(category_id, "darn")
        win._refresh_entries()
        other = service.create_category("Slurs")
        win._refresh_categories()
        _select_category_row(win, 1)

        win._new_phrase_input.setText("darn")
        win._add_entry()

        assert len(service.list_entries(category_id=other.id)) == 1

    def test_delete_entry_confirmed(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            win._delete_entry()

        assert service.list_entries(category_id=category_id) == []

    def test_delete_entry_confirmation_defaults_to_no(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as mock_question:
            win._delete_entry()

        assert mock_question.call_args.args[-1] == QMessageBox.StandardButton.No

    def test_toggling_enabled_checkbox_updates_service(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()

        item = _item(win._entry_table, 0, _COL_ENTRY_ENABLED)
        assert item.data(_ID_ROLE) == entry.id
        item.setCheckState(Qt.CheckState.Unchecked)

        assert service.get_entry(entry.id).enabled is False

    def test_new_entry_mask_checkbox_starts_unchecked(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        service.create_entry(category_id, "darn")
        win._refresh_entries()

        item = _item(win._entry_table, 0, _COL_ENTRY_MASK)
        assert item.checkState() == Qt.CheckState.Unchecked

    def test_toggling_mask_checkbox_updates_service(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()

        item = _item(win._entry_table, 0, _COL_ENTRY_MASK)
        assert item.data(_ID_ROLE) == entry.id
        item.setCheckState(Qt.CheckState.Checked)

        assert service.get_entry(entry.id).mask is True
        assert service.is_masked(entry.id) is True

    def test_editing_notes_updates_service(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()

        item = _item(win._entry_table, 0, _COL_ENTRY_NOTES)
        item.setText("mild")

        assert service.get_entry(entry.id).notes == "mild"

    def test_deselecting_category_disables_entry_controls(
        self, win: CatalogWindow, category_id: str
    ) -> None:
        win._category_table.clearSelection()
        assert win._selected_category_id is None
        assert win._entry_table.isEnabled() is False
        assert win._new_phrase_input.isEnabled() is False

    def test_switching_category_shows_only_its_entries(
        self,
        win: CatalogWindow,
        service: CatalogService,
        category_id: str,
    ) -> None:
        service.create_entry(category_id, "darn")
        other = service.create_category("Slurs")
        service.create_entry(other.id, "slur-word")
        win._refresh_categories()

        _select_category_row(win, 0)
        first_phrases = {
            _item(win._entry_table, r, _COL_ENTRY_PHRASE).text()
            for r in range(win._entry_table.rowCount())
        }

        _select_category_row(win, 1)
        second_phrases = {
            _item(win._entry_table, r, _COL_ENTRY_PHRASE).text()
            for r in range(win._entry_table.rowCount())
        }

        assert first_phrases == {"darn"}
        assert second_phrases == {"slur-word"}


class TestMoveEntry:
    @pytest.fixture()
    def category_id(self, win: CatalogWindow, service: CatalogService) -> str:
        cat = service.create_category("Profanity")
        win._refresh_categories()
        _select_category_row(win, 0)
        return cat.id

    def test_no_selection_is_a_no_op(
        self, win: CatalogWindow, category_id: str
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getItem"
        ) as mock_get_item:
            win._move_entry()
        mock_get_item.assert_not_called()

    def test_no_other_categories_shows_info_and_does_nothing(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.information"
        ) as mock_info:
            win._move_entry()

        mock_info.assert_called_once()
        assert service.get_entry(entry.id).category_id == category_id

    def test_moves_entry_to_chosen_category(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()
        other = service.create_category("Religious")
        win._refresh_categories()
        _select_category_row(win, 0)
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getItem",
            return_value=("Religious", True),
        ):
            win._move_entry()

        assert service.get_entry(entry.id).category_id == other.id
        assert "Moved" in win._status_label.text()

    def test_cancelling_dialog_moves_nothing(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()
        service.create_category("Religious")
        win._refresh_categories()
        _select_category_row(win, 0)
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getItem",
            return_value=("Religious", False),
        ):
            win._move_entry()

        assert service.get_entry(entry.id).category_id == category_id

    def test_archived_categories_are_not_offered_as_targets(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        service.create_entry(category_id, "darn")
        win._refresh_entries()
        # A second *active* category alongside the archived one, so
        # other_categories isn't empty — otherwise this would exercise
        # the "nothing to move to" info-dialog path instead of the one
        # under test here.
        service.create_category("Religious")
        archived = service.create_category("Old Stuff")
        service.archive_category(archived.id)
        win._refresh_categories()
        _select_category_row(win, 0)
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getItem",
            return_value=("", False),
        ) as mock_get_item:
            win._move_entry()

        offered_names = mock_get_item.call_args.args[3]
        assert "Old Stuff" not in offered_names
        assert "Religious" in offered_names

    def test_moving_into_category_with_duplicate_is_rejected_not_moved(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        entry, _ = service.create_entry(category_id, "darn")
        win._refresh_entries()
        other = service.create_category("Religious")
        service.create_entry(other.id, "darn")
        win._refresh_categories()
        _select_category_row(win, 0)
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getItem",
            return_value=("Religious", True),
        ):
            win._move_entry()

        assert "already exists" in win._status_label.text()
        assert service.get_entry(entry.id).category_id == category_id

    def test_entry_disappears_from_source_category_view_after_move(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        service.create_entry(category_id, "darn")
        win._refresh_entries()
        service.create_category("Religious")
        win._refresh_categories()
        _select_category_row(win, 0)
        win._entry_table.selectRow(0)

        with patch(
            "m4bmaker.gui.filter.catalog_window.QInputDialog.getItem",
            return_value=("Religious", True),
        ):
            win._move_entry()

        assert win._entry_table.rowCount() == 0


class TestEntrySorting:
    """Click-to-sort on the entries table (e.g. the "Phrase" column) —
    makes it easy to spot an existing word, or a close variant, before
    adding what turns out to be a duplicate."""

    @pytest.fixture()
    def category_id(self, win: CatalogWindow, service: CatalogService) -> str:
        cat = service.create_category("Profanity")
        win._refresh_categories()
        _select_category_row(win, 0)
        return cat.id

    def test_sorting_is_enabled(self, win: CatalogWindow) -> None:
        assert win._entry_table.isSortingEnabled() is True

    def test_default_sort_is_ascending_not_descending(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        # Regression guard: Qt's own default sort indicator on a freshly
        # sortable header is descending, not the A-Z a User opening this
        # window for the first time expects.
        for word in ("shit", "ass", "damn"):
            service.create_entry(category_id, word)
        win._refresh_entries()

        phrases = [
            _item(win._entry_table, r, _COL_ENTRY_PHRASE).text()
            for r in range(win._entry_table.rowCount())
        ]
        assert phrases == ["ass", "damn", "shit"]

    def test_clicking_phrase_header_sorts_alphabetically(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        for word in ("shit", "ass", "damn"):
            service.create_entry(category_id, word)
        win._refresh_entries()

        win._entry_table.sortByColumn(_COL_ENTRY_PHRASE, Qt.SortOrder.AscendingOrder)

        phrases = [
            _item(win._entry_table, r, _COL_ENTRY_PHRASE).text()
            for r in range(win._entry_table.rowCount())
        ]
        assert phrases == ["ass", "damn", "shit"]

    def test_descending_sort(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        for word in ("shit", "ass", "damn"):
            service.create_entry(category_id, word)
        win._refresh_entries()

        win._entry_table.sortByColumn(_COL_ENTRY_PHRASE, Qt.SortOrder.DescendingOrder)

        phrases = [
            _item(win._entry_table, r, _COL_ENTRY_PHRASE).text()
            for r in range(win._entry_table.rowCount())
        ]
        assert phrases == ["shit", "damn", "ass"]

    def test_each_rows_cells_stay_together_after_sort_then_refresh(
        self, win: CatalogWindow, service: CatalogService, category_id: str
    ) -> None:
        """Regression guard: sorting must be suspended while
        _refresh_entries() populates rows, or Qt can re-sort mid-insert
        and scatter a row's own cells (phrase/enabled/mask/notes) across
        the wrong table rows."""
        shit, _ = service.create_entry(category_id, "shit")
        service.update_entry(shit.id, notes="loud")
        ass, _ = service.create_entry(category_id, "ass")
        service.update_entry(ass.id, notes="mild")
        win._refresh_entries()
        win._entry_table.sortByColumn(_COL_ENTRY_PHRASE, Qt.SortOrder.AscendingOrder)

        # Triggers a fresh populate while the table is already sorted.
        service.create_entry(category_id, "damn")
        win._refresh_entries()

        for row in range(win._entry_table.rowCount()):
            phrase_item = _item(win._entry_table, row, _COL_ENTRY_PHRASE)
            notes_item = _item(win._entry_table, row, _COL_ENTRY_NOTES)
            entry = service.get_entry(phrase_item.data(_ID_ROLE))
            assert entry.canonical_phrase == phrase_item.text()
            assert entry.notes == notes_item.text()


class TestExportDialog:
    """ADR-0055's export scope picker."""

    @pytest.fixture()
    def dialog_service(self) -> CatalogService:
        service = CatalogService()
        cat = service.create_category("Profanity")
        service.create_entry(cat.id, "darn")
        service.create_profile("Family Friendly")
        return service

    def test_everything_is_the_default_scope(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        categories, entries, profiles = dialog.resolve_records()
        assert [c.name for c in categories] == ["Profanity"]
        assert [p.name for p in profiles] == ["Family Friendly"]

    def test_category_checklist_lists_local_categories(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        assert dialog._category_list.count() == 1
        item = dialog._category_list.item(0)
        assert item is not None
        assert item.text() == "Profanity"

    def test_category_list_hidden_until_that_scope_is_chosen(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        assert dialog._category_list.isHidden() is True
        dialog._categories_radio.setChecked(True)
        assert dialog._category_list.isHidden() is False

    def test_profile_list_hidden_until_that_scope_is_chosen(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        assert dialog._profile_list.isHidden() is True
        dialog._profiles_radio.setChecked(True)
        assert dialog._profile_list.isHidden() is False

    def test_archived_checkbox_disabled_outside_everything_scope(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        assert dialog._archived_cb.isEnabled() is True
        dialog._categories_radio.setChecked(True)
        assert dialog._archived_cb.isEnabled() is False

    def test_checking_a_category_resolves_only_that_scope(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        dialog._categories_radio.setChecked(True)
        item = dialog._category_list.item(0)
        assert item is not None
        item.setCheckState(Qt.CheckState.Checked)

        categories, entries, profiles = dialog.resolve_records()

        assert [c.name for c in categories] == ["Profanity"]
        assert profiles == []

    def test_categories_scope_with_nothing_checked_warns_and_does_not_accept(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        dialog._categories_radio.setChecked(True)
        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.warning"
        ) as mock_warn:
            _find_button(dialog, "Export…").click()
        mock_warn.assert_called_once()

    def test_profiles_scope_with_nothing_checked_warns_and_does_not_accept(
        self, dialog_service: CatalogService
    ) -> None:
        dialog = _ExportDialog(dialog_service)
        dialog._profiles_radio.setChecked(True)
        with patch(
            "m4bmaker.gui.filter.catalog_window.QMessageBox.warning"
        ) as mock_warn:
            _find_button(dialog, "Export…").click()
        mock_warn.assert_called_once()

    def test_include_archived_is_passed_through(
        self, dialog_service: CatalogService
    ) -> None:
        entry = dialog_service.list_entries()[0]
        dialog_service.archive_entry(entry.id)
        dialog = _ExportDialog(dialog_service)

        dialog._archived_cb.setChecked(True)
        _, entries, _ = dialog.resolve_records()

        assert len(entries) == 1

    def test_excludes_archived_by_default(self, dialog_service: CatalogService) -> None:
        entry = dialog_service.list_entries()[0]
        dialog_service.archive_entry(entry.id)
        dialog = _ExportDialog(dialog_service)

        _, entries, _ = dialog.resolve_records()

        assert entries == []


class TestImportPreviewDialog:
    """ADR-0055's import preview — must show exactly what apply_import()
    will do before anything is written."""

    @pytest.fixture()
    def preview_service(self) -> CatalogService:
        service = CatalogService()
        cat = service.create_category("Profanity")
        service.create_entry(cat.id, "damn")
        return service

    def test_new_category_and_word_are_labeled_new(
        self, preview_service: CatalogService
    ) -> None:
        imported_cat = Category(id="c1", name="Slang")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="bonkers"
        )
        plan = preview_service.plan_import([imported_cat], [imported_entry], [])

        dialog = _ImportPreviewDialog(plan, "shared.json")

        assert dialog._tree.topLevelItemCount() == 1
        cat_item = dialog._tree.topLevelItem(0)
        assert cat_item is not None
        assert "new category" in cat_item.text(0)
        word_item = cat_item.child(0)
        assert word_item is not None
        assert "bonkers" in word_item.text(0)
        assert "new" in word_item.text(0)
        assert "1 new categor" in dialog._summary_label.text()

    def test_duplicate_word_is_labeled_skipped_by_default(
        self, preview_service: CatalogService
    ) -> None:
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="damn"
        )
        plan = preview_service.plan_import([imported_cat], [imported_entry], [])

        dialog = _ImportPreviewDialog(plan, "shared.json")

        cat_item = dialog._tree.topLevelItem(0)
        assert cat_item is not None
        word_item = cat_item.child(0)
        assert word_item is not None
        assert "duplicate — skipped" in word_item.text(0)
        assert dialog.overwrite_duplicates is False

    def test_toggling_overwrite_relabels_duplicates_live(
        self, preview_service: CatalogService
    ) -> None:
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="damn"
        )
        plan = preview_service.plan_import([imported_cat], [imported_entry], [])
        dialog = _ImportPreviewDialog(plan, "shared.json")

        dialog._overwrite_cb.setChecked(True)

        assert dialog.overwrite_duplicates is True
        cat_item = dialog._tree.topLevelItem(0)
        assert cat_item is not None
        word_item = cat_item.child(0)
        assert word_item is not None
        assert "will overwrite" in word_item.text(0)
        assert "will be overwritten" in dialog._summary_label.text()

    def test_profiles_group_only_appears_when_the_plan_has_profiles(
        self, preview_service: CatalogService
    ) -> None:
        plan = preview_service.plan_import([], [], [])
        dialog = _ImportPreviewDialog(plan, "empty.json")
        assert dialog._tree.topLevelItemCount() == 0

    def test_import_button_accepts(self, preview_service: CatalogService) -> None:
        plan = preview_service.plan_import([], [], [])
        dialog = _ImportPreviewDialog(plan, "empty.json")
        with patch.object(dialog, "accept") as mock_accept:
            _find_button(dialog, "Import").click()
        mock_accept.assert_called_once()

    def test_cancel_button_rejects(self, preview_service: CatalogService) -> None:
        plan = preview_service.plan_import([], [], [])
        dialog = _ImportPreviewDialog(plan, "empty.json")
        with patch.object(dialog, "reject") as mock_reject:
            _find_button(dialog, "Cancel").click()
        mock_reject.assert_called_once()


class TestCatalogWindowExportWiring:
    def test_cancelling_the_scope_dialog_does_not_open_a_save_dialog(
        self, win: CatalogWindow
    ) -> None:
        with (
            patch.object(win, "_run_dialog", return_value=QDialog.DialogCode.Rejected),
            patch(
                "m4bmaker.gui.filter.catalog_window.QFileDialog.getSaveFileName"
            ) as mock_save,
        ):
            win._on_export()
        mock_save.assert_not_called()

    def test_cancelling_the_save_dialog_writes_nothing(
        self, win: CatalogWindow, tmp_path: Path
    ) -> None:
        with (
            patch.object(win, "_run_dialog", return_value=QDialog.DialogCode.Accepted),
            patch(
                "m4bmaker.gui.filter.catalog_window.QFileDialog.getSaveFileName",
                return_value=("", ""),
            ),
        ):
            win._on_export()
        assert list(tmp_path.glob("*.json")) == []

    def test_accepted_scope_and_save_path_writes_the_export_file(
        self, win: CatalogWindow, service: CatalogService, tmp_path: Path
    ) -> None:
        cat = service.create_category("Profanity")
        service.create_entry(cat.id, "darn")
        out_path = tmp_path / "export.json"

        with (
            patch.object(win, "_run_dialog", return_value=QDialog.DialogCode.Accepted),
            patch(
                "m4bmaker.gui.filter.catalog_window.QFileDialog.getSaveFileName",
                return_value=(str(out_path), ""),
            ),
        ):
            win._on_export()

        assert out_path.exists()
        assert "Exported" in win._status_label.text()


class TestCatalogWindowImportWiring:
    def test_cancelling_the_open_dialog_does_nothing(self, win: CatalogWindow) -> None:
        with patch(
            "m4bmaker.gui.filter.catalog_window.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            win._on_import()
        assert win._status_label.text() == ""

    def test_invalid_file_shows_a_warning_and_writes_nothing(
        self, win: CatalogWindow, tmp_path: Path
    ) -> None:
        bad_path = tmp_path / "notes.txt"
        bad_path.write_text("not an export", encoding="utf-8")

        with (
            patch(
                "m4bmaker.gui.filter.catalog_window.QFileDialog.getOpenFileName",
                return_value=(str(bad_path), ""),
            ),
            patch(
                "m4bmaker.gui.filter.catalog_window.QMessageBox.warning"
            ) as mock_warn,
        ):
            win._on_import()

        mock_warn.assert_called_once()
        assert win._service.list_categories() == []

    def test_cancelling_the_preview_dialog_writes_nothing(
        self, win: CatalogWindow, service: CatalogService, tmp_path: Path
    ) -> None:
        other = CatalogService()
        cat = other.create_category("Slang")
        other.create_entry(cat.id, "bonkers")
        categories, entries, profiles = other.export_everything()
        from m4bmaker.filter.catalog_store import write_export_file

        export_path = tmp_path / "shared.json"
        write_export_file(categories, entries, profiles, export_path)

        with (
            patch(
                "m4bmaker.gui.filter.catalog_window.QFileDialog.getOpenFileName",
                return_value=(str(export_path), ""),
            ),
            patch.object(win, "_run_dialog", return_value=QDialog.DialogCode.Rejected),
        ):
            win._on_import()

        assert service.list_categories() == []

    def test_accepted_preview_applies_the_import_and_saves(
        self,
        win: CatalogWindow,
        service: CatalogService,
        mock_save: MagicMock,
        tmp_path: Path,
    ) -> None:
        other = CatalogService()
        cat = other.create_category("Slang")
        other.create_entry(cat.id, "bonkers")
        categories, entries, profiles = other.export_everything()
        from m4bmaker.filter.catalog_store import write_export_file

        export_path = tmp_path / "shared.json"
        write_export_file(categories, entries, profiles, export_path)

        with (
            patch(
                "m4bmaker.gui.filter.catalog_window.QFileDialog.getOpenFileName",
                return_value=(str(export_path), ""),
            ),
            patch.object(win, "_run_dialog", return_value=QDialog.DialogCode.Accepted),
        ):
            win._on_import()

        assert [c.name for c in service.list_categories()] == ["Slang"]
        assert "Imported 1 new word" in win._status_label.text()
        mock_save.assert_called()

    def test_reimporting_your_own_backup_reports_nothing_changed(
        self,
        win: CatalogWindow,
        service: CatalogService,
        tmp_path: Path,
    ) -> None:
        cat = service.create_category("Profanity")
        service.create_entry(cat.id, "damn")
        categories, entries, profiles = service.export_everything()
        from m4bmaker.filter.catalog_store import write_export_file

        export_path = tmp_path / "my-backup.json"
        write_export_file(categories, entries, profiles, export_path)

        with (
            patch(
                "m4bmaker.gui.filter.catalog_window.QFileDialog.getOpenFileName",
                return_value=(str(export_path), ""),
            ),
            patch.object(win, "_run_dialog", return_value=QDialog.DialogCode.Accepted),
        ):
            win._on_import()

        assert "nothing changed" in win._status_label.text()
        assert len(service.list_entries(category_id=cat.id)) == 1
