"""Tests for m4bmaker.gui.filter.word_variation_dialog.WordVariationDialog
(ADR-0036).

Same headless-Qt approach as test_profile_step.py: a real CatalogService
instance, save_catalog mocked at the module it's imported into so no
real disk I/O happens.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)
from m4bmaker.gui.filter.word_variation_dialog import (
    _COL_ACTION,
    _COL_WHY,
    _KIND_TOOLTIPS,
    WordVariationDialog,
)

pytestmark = pytest.mark.usefixtures("qapp")


def _word(text: str, start_ms: int, end_ms: int) -> TranscriptWord:
    return TranscriptWord(
        text=text, normalized=text.lower(), start_ms=start_ms, end_ms=end_ms
    )


def _transcript(words: list[TranscriptWord]) -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint="sha256:x", duration_ms=60_000, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp", version="v", model="base.en", model_checksum="c"
        ),
        segments=(
            TranscriptSegment(
                id="chunk-0",
                start_ms=0,
                end_ms=60_000,
                status=SegmentStatus.COMPLETED,
                words=tuple(words),
            ),
        ),
    )


def _flush_deferred_deletes(qapp: QApplication) -> None:
    """Every "+Add" click replaces a table cell's QPushButton via
    ``setCellWidget``, which schedules the old one for ``deleteLater()``
    — unlike every other GUI test fixture in this suite (see ADR-0023),
    this is real Qt deferred-deletion queue growth, not CPython cyclic
    garbage, so ``tests/gui/conftest.py``'s ``gc.disable()`` mitigation
    doesn't cover it. Left unflushed across this file's several
    add-button tests, the backlog was large enough to reproduce
    ADR-0023's exact native-teardown segfault signature in
    ``test_window.py``'s own fixture, in a full-suite run — flushing
    immediately after each click, the same sequence that fixture already
    uses, is the fix."""
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    qapp.processEvents()


@pytest.fixture()
def mock_save() -> Iterator[MagicMock]:
    with patch("m4bmaker.gui.filter.word_variation_dialog.save_catalog") as mocked:
        yield mocked


@pytest.fixture()
def service() -> CatalogService:
    return CatalogService()


@pytest.fixture()
def category_id(service: CatalogService) -> str:
    return service.create_category("Profanity").id


@pytest.fixture()
def profile_with_shuck(service: CatalogService, category_id: str) -> str:
    entry, _ = service.create_entry(category_id, "shuck")
    profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
    return profile.id


class TestResultsFound:
    def test_populates_table_with_suggestion(
        self, service: CatalogService, profile_with_shuck: str, mock_save: MagicMock
    ) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        assert dialog._table.isVisibleTo(dialog) is True
        assert dialog._table.rowCount() == 1
        word_item = dialog._table.item(0, 0)
        related_item = dialog._table.item(0, 1)
        assert word_item is not None
        assert related_item is not None
        assert word_item.text() == "shucking"
        assert "shuck" in related_item.text()

    def test_action_column_is_wide_enough_for_the_add_button(
        self, service: CatalogService, profile_with_shuck: str, mock_save: MagicMock
    ) -> None:
        """Regression guard: two different automatic-sizing approaches
        (a one-time resizeColumnsToContents() call, then a persistent
        ResizeToContents mode) both measured this column's real cell
        widget incorrectly in manual testing, clipping "+ Add" to "Ad"
        — the column is now a fixed, explicitly computed width instead,
        which this checks the real button actually fits inside."""
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        add_btn = dialog._table.cellWidget(0, _COL_ACTION)
        assert isinstance(add_btn, QPushButton)
        column_width = dialog._table.columnWidth(_COL_ACTION)
        assert column_width >= add_btn.sizeHint().width()

    def test_why_column_tooltip_explains_the_match_kind(
        self, service: CatalogService, profile_with_shuck: str, mock_save: MagicMock
    ) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        why_item = dialog._table.item(0, _COL_WHY)
        assert why_item is not None
        assert why_item.toolTip() != ""
        assert why_item.toolTip() in _KIND_TOOLTIPS.values()

    def test_add_button_creates_catalog_entry_and_persists(
        self,
        service: CatalogService,
        profile_with_shuck: str,
        mock_save: MagicMock,
        qapp: QApplication,
    ) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        add_btn = dialog._table.cellWidget(0, _COL_ACTION)
        assert isinstance(add_btn, QPushButton)
        add_btn.click()
        _flush_deferred_deletes(qapp)

        phrases = [e.canonical_phrase for e in service.list_entries()]
        assert "shucking" in phrases
        mock_save.assert_called_once_with(service)

    def test_add_button_row_shows_added_confirmation(
        self,
        service: CatalogService,
        profile_with_shuck: str,
        mock_save: MagicMock,
        qapp: QApplication,
    ) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        add_btn = dialog._table.cellWidget(0, _COL_ACTION)
        assert isinstance(add_btn, QPushButton)
        add_btn.click()
        _flush_deferred_deletes(qapp)

        replaced = dialog._table.cellWidget(0, _COL_ACTION)
        assert isinstance(replaced, QLabel)
        assert "Added" in replaced.text()

    def test_added_entry_goes_into_the_related_entrys_category(
        self,
        service: CatalogService,
        profile_with_shuck: str,
        mock_save: MagicMock,
        qapp: QApplication,
    ) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        add_btn = dialog._table.cellWidget(0, _COL_ACTION)
        assert isinstance(add_btn, QPushButton)
        add_btn.click()
        _flush_deferred_deletes(qapp)

        [new_entry] = [
            e for e in service.list_entries() if e.canonical_phrase == "shucking"
        ]
        [original_entry] = [
            e for e in service.list_entries() if e.canonical_phrase == "shuck"
        ]
        assert new_entry.category_id == original_entry.category_id


class TestAlreadyInCatalog:
    """A suggestion is only checked against the *selected profile's*
    entries (variation_scan.py's own documented scope) — a word already
    in the catalog under the same category, just not yet part of this
    profile, wouldn't be excluded by that alone. ADR-0040 extends the
    same "no real duplicates" guard from CatalogWindow's manual add to
    here."""

    def test_shows_already_in_catalog_instead_of_add_button(
        self, service: CatalogService, category_id: str, mock_save: MagicMock
    ) -> None:
        entry, _ = service.create_entry(category_id, "shuck")
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
        # "shucking" already exists in the catalog, but isn't part of
        # this profile's own entry_ids — the scan itself can't see that
        # from the snapshot alone.
        service.create_entry(category_id, "shucking")

        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile.id)
        dialog = WordVariationDialog(service, transcript, snapshot)

        assert dialog._table.rowCount() == 1
        cell = dialog._table.cellWidget(0, _COL_ACTION)
        assert isinstance(cell, QLabel)
        assert cell.text() == "Already in catalog"

    def test_does_not_create_a_duplicate_entry(
        self, service: CatalogService, category_id: str, mock_save: MagicMock
    ) -> None:
        entry, _ = service.create_entry(category_id, "shuck")
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
        service.create_entry(category_id, "shucking")

        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile.id)
        WordVariationDialog(service, transcript, snapshot)

        phrases = [e.canonical_phrase for e in service.list_entries()]
        assert phrases.count("shucking") == 1
        mock_save.assert_not_called()

    def test_a_word_in_a_different_category_is_not_treated_as_a_duplicate(
        self, service: CatalogService, category_id: str, mock_save: MagicMock
    ) -> None:
        entry, _ = service.create_entry(category_id, "shuck")
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
        other_category = service.create_category("Other")
        service.create_entry(other_category.id, "shucking")

        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = service.create_snapshot(profile.id)
        dialog = WordVariationDialog(service, transcript, snapshot)

        cell = dialog._table.cellWidget(0, _COL_ACTION)
        assert isinstance(cell, QPushButton)
        assert cell.text() == "+ Add"


class TestNoResults:
    def test_empty_state_shown_when_nothing_found(
        self, service: CatalogService, profile_with_shuck: str, mock_save: MagicMock
    ) -> None:
        transcript = _transcript([_word("hello", 0, 500)])  # no relation to "shuck"
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        assert dialog._table.isVisibleTo(dialog) is False
        assert dialog._empty_label.isVisibleTo(dialog) is True


class TestExplainerText:
    def test_explains_what_the_dialog_does_before_the_scan_runs(
        self, service: CatalogService, profile_with_shuck: str, mock_save: MagicMock
    ) -> None:
        transcript = _transcript([_word("hello", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        explainer = dialog.findChildren(QLabel)[0]
        assert explainer.text().startswith("Looks for words")
        assert explainer.isVisibleTo(dialog) is True
        assert explainer.wordWrap() is True

    def test_clarifies_added_words_go_to_the_catalog_not_the_profile(
        self, service: CatalogService, profile_with_shuck: str, mock_save: MagicMock
    ) -> None:
        # profile_with_shuck creates a profile named "Family Friendly" —
        # the clarification must name the real profile, not a generic
        # placeholder, so a User with several profiles knows which one
        # this does *not* automatically affect.
        transcript = _transcript([_word("hello", 0, 500)])
        snapshot = service.create_snapshot(profile_with_shuck)
        dialog = WordVariationDialog(service, transcript, snapshot)

        explainer = dialog.findChildren(QLabel)[0]
        assert "adds it to your Word List" in explainer.text()
        assert "“Family Friendly”" in explainer.text()
        assert "not automatically" in explainer.text()
