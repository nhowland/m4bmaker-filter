"""Tests for m4bmaker.gui.filter.wizard.profile_step.ProfileStep
(ADR-0016, PRD §7.2 stage 4, §9.1, §9.4).

Same headless-Qt approach as test_catalog_window.py/
test_profile_editor_dialog.py: real widget tree under
QT_QPA_PLATFORM=offscreen, driven like a User would, asserted against
both widget state and the real CatalogService. ProfileEditorDialog's
own exec()/QMessageBox.question are patched at the module level
profile_step.py imports them from, so no real modal blocks the test
runner.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QRadioButton

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
from m4bmaker.gui.filter.wizard.profile_step import ProfileStep

pytestmark = pytest.mark.usefixtures("qapp")


def _sample_transcript(path) -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint="sha256:abc", duration_ms=1_000, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp", version="1.9.2", model="base.en", model_checksum="x"
        ),
        segments=(
            TranscriptSegment(
                id="chunk-000000",
                start_ms=0,
                end_ms=1_000,
                status=SegmentStatus.COMPLETED,
                words=(
                    TranscriptWord(text="hi", normalized="hi", start_ms=0, end_ms=100),
                ),
            ),
        ),
        path=path,
    )


@pytest.fixture()
def mock_save() -> Iterator[MagicMock]:
    with patch("m4bmaker.gui.filter.wizard.profile_step.save_catalog") as mocked:
        yield mocked


@pytest.fixture()
def service() -> CatalogService:
    return CatalogService()


@pytest.fixture()
def step(service: CatalogService, mock_save: MagicMock) -> ProfileStep:
    return ProfileStep(service=service)


def _radio_labels(step: ProfileStep) -> list[str]:
    return [
        step._list_layout.itemAt(i).widget().text()
        for i in range(step._list_layout.count())
    ]


class TestEmptyState:
    def test_no_profiles_shows_empty_label_and_blocks_advance(
        self, step: ProfileStep
    ) -> None:
        assert step._empty_label.isVisibleTo(step) is True
        assert step._list_container.isVisibleTo(step) is False
        assert step.can_advance() is False
        assert step.selected_profile_id is None

    def test_edit_and_archive_disabled_when_empty(self, step: ProfileStep) -> None:
        assert step._edit_btn.isEnabled() is False
        assert step._archive_btn.isEnabled() is False


class TestProfileList:
    def test_lists_real_profiles_with_live_counts(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.create_profile("Family Friendly", entry_ids=[entry.id])

        step._refresh_profiles()

        labels = _radio_labels(step)
        assert len(labels) == 1
        assert "Family Friendly" in labels[0]
        assert "1 categories" in labels[0]
        assert "1 words" in labels[0]

    def test_archived_profiles_are_not_offered(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        profile = service.create_profile("Retired")
        service.archive_profile(profile.id)

        step._refresh_profiles()

        assert _radio_labels(step) == []
        assert step.can_advance() is False

    def test_first_profile_selected_by_default(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        p1 = service.create_profile("First")
        service.create_profile("Second")

        step._refresh_profiles()

        assert step.selected_profile_id == p1.id
        assert step.can_advance() is True

    def test_selecting_a_different_radio_updates_selection(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        service.create_profile("First")
        service.create_profile("Second")
        step._refresh_profiles()

        radios = [
            step._list_layout.itemAt(i).widget()
            for i in range(step._list_layout.count())
        ]
        assert all(isinstance(r, QRadioButton) for r in radios)
        radios[1].setChecked(True)

        second = service.list_profiles()[1]
        assert step.selected_profile_id == second.id

    def test_missing_entry_id_is_skipped_defensively_in_counts(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        service.create_profile("Dangling", entry_ids=["does-not-exist"])
        step._refresh_profiles()
        labels = _radio_labels(step)
        assert "0 categories" in labels[0]
        assert "0 words" in labels[0]


class TestNewProfile:
    def test_accepted_dialog_selects_and_lists_new_profile(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        created = service.create_profile("Created Elsewhere")

        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.ProfileEditorDialog"
        ) as mock_dialog_cls:
            instance = mock_dialog_cls.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.saved_profile_id = created.id
            step._on_new_profile()

        assert step.selected_profile_id == created.id
        assert step.can_advance() is True

    def test_cancelled_dialog_changes_nothing(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.ProfileEditorDialog"
        ) as mock_dialog_cls:
            instance = mock_dialog_cls.return_value
            instance.exec.return_value = QDialog.DialogCode.Rejected
            step._on_new_profile()

        assert service.list_profiles() == []
        assert step.selected_profile_id is None


class TestEditProfile:
    def test_edit_opens_dialog_for_selected_profile_and_refreshes(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        profile = service.create_profile("Original")
        step._refresh_profiles()

        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.ProfileEditorDialog"
        ) as mock_dialog_cls:
            instance = mock_dialog_cls.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            step._on_edit_profile()

        mock_dialog_cls.assert_called_once_with(
            service, profile_id=profile.id, parent=step
        )

    def test_edit_with_no_selection_does_nothing(self, step: ProfileStep) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.ProfileEditorDialog"
        ) as mock_dialog_cls:
            step._on_edit_profile()
        mock_dialog_cls.assert_not_called()


class TestArchiveProfile:
    def test_confirmed_archive_removes_it_from_the_list(
        self, service: CatalogService, step: ProfileStep, mock_save: MagicMock
    ) -> None:
        service.create_profile("Goodbye")
        step._refresh_profiles()

        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            step._on_archive_profile()

        assert _radio_labels(step) == []
        assert service.list_profiles() == []
        mock_save.assert_called_once()

    def test_declined_archive_keeps_it(
        self, service: CatalogService, step: ProfileStep, mock_save: MagicMock
    ) -> None:
        service.create_profile("Stays")
        step._refresh_profiles()

        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            step._on_archive_profile()

        assert len(service.list_profiles()) == 1
        mock_save.assert_not_called()


class TestManageCatalog:
    def test_opens_catalog_window_sharing_the_same_service(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.CatalogWindow"
        ) as mock_window_cls:
            step._on_manage_catalog()
        mock_window_cls.assert_called_once_with(service, parent=step)

    def test_reused_on_second_call(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.CatalogWindow"
        ) as mock_window_cls:
            step._on_manage_catalog()
            step._on_manage_catalog()
        mock_window_cls.assert_called_once()

    def test_closing_catalog_window_refreshes_counts(
        self, service: CatalogService, step: ProfileStep
    ) -> None:
        entry_holder = service.create_category("Profanity")
        service.create_profile("Family Friendly")

        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.CatalogWindow"
        ) as mock_window_cls:
            instance = mock_window_cls.return_value
            step._on_manage_catalog()
            closed_slot = instance.closed.connect.call_args[0][0]

        service.create_entry(entry_holder.id, "darn")
        profile_id = service.list_profiles()[0].id
        service.update_profile(profile_id, entry_ids=[service.list_entries()[0].id])
        closed_slot()

        assert "1 words" in _radio_labels(step)[0]


class TestViewTranscript:
    """ADR-0022: Profile gets whichever real Transcript the wizard shell
    found (Transcribe's own output, or one reused via Transcript step)
    purely to offer a "View Transcript" action — this doesn't affect
    profile selection at all."""

    def test_button_hidden_until_a_transcript_is_set(self, step: ProfileStep) -> None:
        assert step._view_transcript_btn.isVisibleTo(step) is False

    def test_set_transcript_shows_the_button(self, step: ProfileStep, tmp_path) -> None:
        step.set_transcript(_sample_transcript(tmp_path / "book.m4bt.json"))
        assert step._view_transcript_btn.isVisibleTo(step) is True

    def test_set_transcript_none_hides_the_button(
        self, step: ProfileStep, tmp_path
    ) -> None:
        step.set_transcript(_sample_transcript(tmp_path / "book.m4bt.json"))
        step.set_transcript(None)
        assert step._view_transcript_btn.isVisibleTo(step) is False

    def test_clicking_view_transcript_opens_the_generated_text_file(
        self, step: ProfileStep, tmp_path
    ) -> None:
        transcript_path = tmp_path / "book.m4bt.json"
        step.set_transcript(_sample_transcript(transcript_path))

        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.QDesktopServices.openUrl"
        ) as mock_open:
            step._on_view_transcript()

        text_path = tmp_path / "book.m4bt.txt"
        assert text_path.read_text(encoding="utf-8") == "hi"
        mock_open.assert_called_once()

    def test_clicking_view_transcript_with_none_is_a_no_op(
        self, step: ProfileStep
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.profile_step.QDesktopServices.openUrl"
        ) as mock_open:
            step._on_view_transcript()
        mock_open.assert_not_called()
