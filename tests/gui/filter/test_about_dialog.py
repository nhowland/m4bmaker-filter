"""Tests for m4bmaker.gui.filter.about_dialog.AboutLanguageFilterDialog
(ADR-0048).

Pure static content, no data/service dependencies — these tests just
confirm the dialog builds, shows every real feature, and closes
cleanly under both themes.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QWidget

from m4bmaker.gui.filter.about_dialog import _FEATURES, AboutLanguageFilterDialog

pytestmark = pytest.mark.usefixtures("qapp")


def _all_text(widget: QWidget) -> str:
    return " ".join(label.text() for label in widget.findChildren(QLabel))


@pytest.fixture()
def dlg() -> AboutLanguageFilterDialog:
    return AboutLanguageFilterDialog()


class TestConstruction:
    def test_renders_without_error(self, dlg: AboutLanguageFilterDialog) -> None:
        assert dlg is not None

    def test_window_title(self, dlg: AboutLanguageFilterDialog) -> None:
        assert dlg.windowTitle() == "About Language Filter"

    def test_is_a_dialog_not_a_window(self, dlg: AboutLanguageFilterDialog) -> None:
        # Informational only — a QDialog (modal, no menu bar of its own),
        # not one more QMainWindow in this feature's lazy-create-and-reuse
        # secondary-window family, since there's no state to preserve
        # between opens.
        assert isinstance(dlg, QDialog)


class TestContent:
    def test_app_name_present(self, dlg: AboutLanguageFilterDialog) -> None:
        assert "Filter Audiobook Language" in _all_text(dlg)

    def test_every_real_feature_title_shown(
        self, dlg: AboutLanguageFilterDialog
    ) -> None:
        text = _all_text(dlg)
        for title, _body in _FEATURES:
            assert title in text

    def test_every_real_feature_description_shown(
        self, dlg: AboutLanguageFilterDialog
    ) -> None:
        text = _all_text(dlg)
        for _title, body in _FEATURES:
            assert body in text

    def test_at_least_the_core_workflow_steps_are_named(
        self, dlg: AboutLanguageFilterDialog
    ) -> None:
        # Not a formal requirement of exact wording — just confirms the
        # overview actually orients a new reader to the real wizard flow,
        # not just a bare feature list with no sense of order.
        text = _all_text(dlg).lower()
        for step in ("transcribe", "review", "render"):
            assert step in text


class TestClose:
    def test_close_button_accepts_the_dialog(
        self, dlg: AboutLanguageFilterDialog
    ) -> None:
        close_btn = next(
            b for b in dlg.findChildren(QPushButton) if b.text() == "Close"
        )
        received: list[int] = []
        dlg.accepted.connect(lambda: received.append(1))
        close_btn.click()
        assert received == [1]


class TestTheme:
    def test_apply_stylesheet_does_not_raise(
        self, dlg: AboutLanguageFilterDialog
    ) -> None:
        dlg.apply_stylesheet(True)
        dlg.apply_stylesheet(False)
