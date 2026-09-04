"""Tests for m4bmaker.gui.filter.profile_editor_dialog.ProfileEditorDialog
(ADR-0016, PRD §9.1, §8.3).

Same headless-Qt behavioral approach as test_catalog_window.py: build the
real widget tree under QT_QPA_PLATFORM=offscreen (tests/gui/conftest.py),
drive it as a User would (check tree items, set spinbox values, click
Save/Cancel), and assert against both widget state and the real
CatalogService underneath. save_catalog is patched at the module level
the dialog imports it from, exactly like CatalogWindow's own tests patch
it, so no real disk write happens.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTabWidget

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.models import AttenuationSettings
from m4bmaker.gui.filter.profile_editor_dialog import ProfileEditorDialog

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture()
def mock_save() -> Iterator[MagicMock]:
    with patch("m4bmaker.gui.filter.profile_editor_dialog.save_catalog") as mocked:
        yield mocked


@pytest.fixture()
def service() -> CatalogService:
    return CatalogService()


@pytest.fixture()
def stocked_service(service: CatalogService) -> CatalogService:
    """A service with two categories (Profanity: darn, heck; Slurs:
    input) — entry ids are reachable off the service via
    ``service.list_entries()``/``service.list_categories()`` in tests,
    same as production code would look them up."""
    cat_a = service.create_category("Profanity")
    cat_b = service.create_category("Slurs")
    service.create_entry(cat_a.id, "darn")
    service.create_entry(cat_a.id, "heck")
    service.create_entry(cat_b.id, "input")
    return service


def _category_item(dialog: ProfileEditorDialog, name: str):
    for i in range(dialog._tree.topLevelItemCount()):
        item = dialog._tree.topLevelItem(i)
        if item.text(0).startswith(name):
            return item
    raise AssertionError(f"no category item named {name!r}")


def _entry_id(service: CatalogService, category_name: str, phrase: str) -> str:
    for category in service.list_categories(include_archived=True):
        if category.name == category_name:
            for entry in service.list_entries(
                category_id=category.id, include_archived=True
            ):
                if entry.canonical_phrase == phrase:
                    return entry.id
    raise AssertionError(f"no entry {phrase!r} in category {category_name!r}")


class TestCreateMode:
    def test_defaults_to_attenuation_settings_defaults(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        defaults = AttenuationSettings()
        assert dialog._lead_spin.value() == defaults.lead_padding_ms
        assert dialog._tail_spin.value() == defaults.tail_padding_ms
        assert dialog._merge_spin.value() == defaults.merge_adjacency_ms
        assert dialog._fade_in_spin.value() == defaults.fade_in_ms
        assert dialog._fade_out_spin.value() == defaults.fade_out_ms
        assert dialog._gain_floor_spin.value() == defaults.gain_floor_db

    def test_tree_has_one_row_per_category_and_entry(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        assert dialog._tree.topLevelItemCount() == 2
        profanity = _category_item(dialog, "Profanity")
        assert profanity.childCount() == 2

    def test_nothing_checked_by_default(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        assert dialog._collect_checked_entry_ids() == []

    def test_checking_category_checks_all_its_entries(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        profanity = _category_item(dialog, "Profanity")
        profanity.setCheckState(0, Qt.CheckState.Checked)
        assert profanity.child(0).checkState(0) == Qt.CheckState.Checked
        assert profanity.child(1).checkState(0) == Qt.CheckState.Checked
        assert len(dialog._collect_checked_entry_ids()) == 2

    def test_unchecking_one_entry_partially_checks_category(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        profanity = _category_item(dialog, "Profanity")
        profanity.setCheckState(0, Qt.CheckState.Checked)
        profanity.child(0).setCheckState(0, Qt.CheckState.Unchecked)
        assert profanity.checkState(0) == Qt.CheckState.PartiallyChecked

    def test_save_creates_profile_with_name_entries_and_attenuation(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        dialog._name_input.setText("Family Friendly")
        profanity = _category_item(dialog, "Profanity")
        profanity.setCheckState(0, Qt.CheckState.Checked)
        dialog._lead_spin.setValue(120)

        dialog._on_save()

        profiles = stocked_service.list_profiles()
        assert len(profiles) == 1
        assert profiles[0].name == "Family Friendly"
        assert len(profiles[0].entry_ids) == 2
        assert profiles[0].attenuation.lead_padding_ms == 120
        mock_save.assert_called_once()
        assert dialog.saved_profile_id == profiles[0].id

    def test_save_accepts_the_dialog(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        dialog._name_input.setText("Family Friendly")
        with patch.object(dialog, "accept") as mocked_accept:
            dialog._on_save()
        mocked_accept.assert_called_once()

    def test_blank_name_shows_warning_and_creates_nothing(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        with patch(
            "m4bmaker.gui.filter.profile_editor_dialog.QMessageBox.warning"
        ) as mocked_warning:
            dialog._on_save()
        mocked_warning.assert_called_once()
        assert stocked_service.list_profiles() == []
        mock_save.assert_not_called()
        assert dialog.saved_profile_id is None

    def test_cancel_creates_nothing(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        dialog._name_input.setText("Ignored")
        dialog.reject()
        assert stocked_service.list_profiles() == []
        mock_save.assert_not_called()


class TestEditMode:
    def test_prefills_name_entries_and_attenuation(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        darn_id = _entry_id(stocked_service, "Profanity", "darn")
        custom = AttenuationSettings(lead_padding_ms=200)
        profile = stocked_service.create_profile(
            "Profanity Only", entry_ids=[darn_id], attenuation=custom
        )

        dialog = ProfileEditorDialog(stocked_service, profile_id=profile.id)

        assert dialog._name_input.text() == "Profanity Only"
        assert dialog._lead_spin.value() == 200
        assert dialog._collect_checked_entry_ids() == [darn_id]

    def test_save_updates_existing_profile(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        profile = stocked_service.create_profile("Original Name")
        dialog = ProfileEditorDialog(stocked_service, profile_id=profile.id)
        dialog._name_input.setText("Renamed")

        dialog._on_save()

        updated = stocked_service.get_profile(profile.id)
        assert updated.name == "Renamed"
        assert dialog.saved_profile_id == profile.id

    def test_archived_category_with_referenced_entry_still_shown(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        input_id = _entry_id(stocked_service, "Slurs", "input")
        profile = stocked_service.create_profile("Uses Archived", entry_ids=[input_id])
        slurs_category_id = stocked_service.get_entry(input_id).category_id
        stocked_service.update_category(slurs_category_id, archived=True)

        dialog = ProfileEditorDialog(stocked_service, profile_id=profile.id)

        assert dialog._tree.topLevelItemCount() == 2
        slurs_item = _category_item(dialog, "Slurs")
        assert slurs_item.child(0).checkState(0) == Qt.CheckState.Checked

    def test_archived_category_without_referenced_entry_is_hidden(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        input_id = _entry_id(stocked_service, "Slurs", "input")
        slurs_category_id = stocked_service.get_entry(input_id).category_id
        stocked_service.update_category(slurs_category_id, archived=True)

        dialog = ProfileEditorDialog(stocked_service, profile_id=None)

        assert dialog._tree.topLevelItemCount() == 1


class TestWordsSortedAlphabetically:
    """Words display in alphabetical order automatically — no sort
    control, always sorted, per the User's own request."""

    def test_entries_appear_in_alphabetical_not_insertion_order(
        self, service: CatalogService, mock_save: MagicMock
    ) -> None:
        category = service.create_category("Profanity")
        # Deliberately non-alphabetical insertion order.
        for word in ("shit", "ass", "damn", "bitch"):
            service.create_entry(category.id, word)

        dialog = ProfileEditorDialog(service, profile_id=None)

        category_item = _category_item(dialog, "Profanity")
        phrases = [
            category_item.child(i).text(0) for i in range(category_item.childCount())
        ]
        assert phrases == ["ass", "bitch", "damn", "shit"]

    def test_sort_is_case_insensitive(
        self, service: CatalogService, mock_save: MagicMock
    ) -> None:
        category = service.create_category("Profanity")
        for word in ("Zebra", "apple", "Mango"):
            service.create_entry(category.id, word)

        dialog = ProfileEditorDialog(service, profile_id=None)

        category_item = _category_item(dialog, "Profanity")
        phrases = [
            category_item.child(i).text(0) for i in range(category_item.childCount())
        ]
        assert phrases == ["apple", "Mango", "Zebra"]


class TestTabs:
    """Words and Attenuation are separate tabs (not stacked in one
    column) so the word list gets the dialog's full height while it's
    the one being worked in, per the User's own request."""

    def test_two_tabs_named_words_and_attenuation(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None
        assert tabs.count() == 2
        assert tabs.tabText(0) == "Words"
        assert tabs.tabText(1) == "Attenuation"

    def test_tree_lives_in_the_words_tab(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None
        words_tab = tabs.widget(0)
        assert words_tab is not None
        assert dialog._tree in words_tab.findChildren(type(dialog._tree))

    def test_attenuation_fields_live_in_the_attenuation_tab(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None
        attenuation_tab = tabs.widget(1)
        assert attenuation_tab is not None
        for spin in (
            dialog._lead_spin,
            dialog._tail_spin,
            dialog._merge_spin,
            dialog._fade_in_spin,
            dialog._fade_out_spin,
            dialog._gain_floor_spin,
        ):
            assert spin in attenuation_tab.findChildren(type(spin))


class TestAttenuationExplainerText:
    """Persistently visible description text under each field — the
    User's preferred replacement for a hover tooltip, which a screenshot
    can't show and which requires knowing to hover over the right thing
    in the first place."""

    @staticmethod
    def _description_labels(dialog: ProfileEditorDialog) -> list[QLabel]:
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None
        attenuation_tab = tabs.widget(1)
        assert attenuation_tab is not None
        return [
            label
            for label in attenuation_tab.findChildren(QLabel)
            if label.objectName() == "statusLabel"
        ]

    def test_one_description_label_per_field(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        assert len(self._description_labels(dialog)) == 6

    def test_every_description_is_non_empty_and_word_wrapped(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        for label in self._description_labels(dialog):
            assert label.text() != ""
            assert label.wordWrap() is True

    def test_descriptions_are_distinct_per_field(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        texts = {label.text() for label in self._description_labels(dialog)}
        assert len(texts) == 6

    def test_no_tooltips_set_on_the_fields_themselves(
        self, stocked_service: CatalogService, mock_save: MagicMock
    ) -> None:
        """Explainer text replaced tooltips rather than supplementing
        them — a leftover tooltip would just be redundant/stale."""
        dialog = ProfileEditorDialog(stocked_service, profile_id=None)
        for spin in (
            dialog._lead_spin,
            dialog._tail_spin,
            dialog._merge_spin,
            dialog._fade_in_spin,
            dialog._fade_out_spin,
            dialog._gain_floor_spin,
        ):
            assert spin.toolTip() == ""
