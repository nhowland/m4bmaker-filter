"""Tests for m4bmaker.gui.filter.model_manager_window.ModelManagerWindow
(PRD §10.1, G5).

Same headless-behavioral approach as test_catalog_window.py: build the
real widget tree under ``QT_QPA_PLATFORM=offscreen``, drive it as a user
would, and assert on both widget state and real filesystem effects (an
installed model is a real file under ``tmp_path``, not a mock — this
window's whole job is presenting ``is_installed()``'s truth accurately).

``ModelDownloadWorker`` is a real ``QThread``; tests that exercise it
patch ``m4bmaker.gui.filter.workers.download_model`` (the blocking call
it wraps) rather than the worker itself, start the real thread, and
``wait()`` (bounded) + ``qapp.processEvents()`` for its queued signals to
be delivered before asserting — the same pattern
tests/gui/filter/test_workers.py and tests/gui/test_worker.py use.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)

from m4bmaker.filter.model_manager import (
    KNOWN_MODELS,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelSpec,
    get_model_spec,
)
from m4bmaker.gui.filter.model_manager_window import (
    _COL_NAME,
    _COL_STATUS,
    _NAME_ROLE,
    ModelManagerWindow,
)

_ProgressCallback = Callable[[int, int], None]

pytestmark = pytest.mark.usefixtures("qapp")

_BASE_EN = get_model_spec("base.en")


@pytest.fixture()
def win(tmp_path: Path) -> ModelManagerWindow:
    with patch(
        "m4bmaker.gui.filter.model_manager_window.find_whisper_cli",
        return_value=None,
    ):
        return ModelManagerWindow(dest_dir=tmp_path)


def _item(table: QTableWidget, row: int, col: int) -> QTableWidgetItem:
    cell = table.item(row, col)
    assert cell is not None
    return cell


def _select_row_for(win: ModelManagerWindow, name: str) -> None:
    for row in range(win._table.rowCount()):
        item = win._table.item(row, _COL_NAME)
        if item is not None and item.data(_NAME_ROLE) == name:
            win._table.selectRow(row)
            return
    raise AssertionError(f"no row for model {name!r}")


def _status_cell(win: ModelManagerWindow, name: str) -> str:
    for row in range(win._table.rowCount()):
        item = win._table.item(row, _COL_NAME)
        if item is not None and item.data(_NAME_ROLE) == name:
            status_item = win._table.item(row, _COL_STATUS)
            assert status_item is not None
            return status_item.text()
    raise AssertionError(f"no row for model {name!r}")


class TestConstruction:
    def test_window_creates_without_error(self, win: ModelManagerWindow) -> None:
        assert win is not None

    def test_table_lists_every_known_model(self, win: ModelManagerWindow) -> None:
        assert win._table.rowCount() == len(KNOWN_MODELS)
        names = {
            _item(win._table, r, _COL_NAME).data(_NAME_ROLE)
            for r in range(win._table.rowCount())
        }
        assert names == {spec.name for spec in KNOWN_MODELS}

    def test_all_models_start_not_installed(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        for spec in KNOWN_MODELS:
            assert _status_cell(win, spec.name) == "Not installed"

    def test_apply_stylesheet_does_not_raise(self, win: ModelManagerWindow) -> None:
        win.apply_stylesheet(True)
        win.apply_stylesheet(False)

    def test_engine_label_when_whisper_not_found(self, win: ModelManagerWindow) -> None:
        assert "not found" in win._engine_label.text()

    def test_engine_label_when_whisper_found(self, tmp_path: Path) -> None:
        with (
            patch(
                "m4bmaker.gui.filter.model_manager_window.find_whisper_cli",
                return_value="/usr/local/bin/whisper-cli",
            ),
            patch(
                "m4bmaker.gui.filter.model_manager_window.get_whisper_version",
                return_value="1.9.2",
            ),
        ):
            w = ModelManagerWindow(dest_dir=tmp_path)
        assert "1.9.2" in w._engine_label.text()


class TestModelAlreadyInstalled:
    @pytest.fixture()
    def win(self, tmp_path: Path) -> ModelManagerWindow:
        installed = tmp_path / _BASE_EN.filename()
        installed.write_bytes(b"\x00" * _BASE_EN.size_bytes)
        with patch(
            "m4bmaker.gui.filter.model_manager_window.find_whisper_cli",
            return_value=None,
        ):
            return ModelManagerWindow(dest_dir=tmp_path)

    def test_status_shows_installed(self, win: ModelManagerWindow) -> None:
        assert _status_cell(win, "base.en") == "Installed"

    def test_selecting_installed_row_enables_remove_not_download(
        self, win: ModelManagerWindow
    ) -> None:
        _select_row_for(win, "base.en")
        assert win._download_btn.isEnabled() is False
        assert win._remove_btn.isEnabled() is True


class TestSelection:
    def test_no_selection_disables_both_buttons(self, win: ModelManagerWindow) -> None:
        assert win._download_btn.isEnabled() is False
        assert win._remove_btn.isEnabled() is False

    def test_selecting_uninstalled_row_enables_download_not_remove(
        self, win: ModelManagerWindow
    ) -> None:
        _select_row_for(win, "base.en")
        assert win._download_btn.isEnabled() is True
        assert win._remove_btn.isEnabled() is False

    def test_selecting_row_populates_details_label(
        self, win: ModelManagerWindow
    ) -> None:
        _select_row_for(win, "base.en")
        assert _BASE_EN.url in win._details_label.text()
        assert _BASE_EN.sha256 in win._details_label.text()

    def test_deselecting_clears_details_label(self, win: ModelManagerWindow) -> None:
        _select_row_for(win, "base.en")
        win._table.clearSelection()
        assert win._details_label.text() == ""


class TestDownload:
    def test_successful_download_installs_and_updates_table(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        installed_path = tmp_path / _BASE_EN.filename()

        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            assert progress_callback is not None
            progress_callback(1, 2)
            installed_path.touch()
            os.truncate(installed_path, spec.size_bytes)
            return installed_path

        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            win._on_download_clicked()
            worker = win._download_worker
            assert worker is not None
            worker.wait(3000)

        QApplication.processEvents()

        assert win._download_worker is None
        assert "Installed base.en" in win._status_label.text()
        assert _status_cell(win, "base.en") == "Installed"
        assert win._progress_bar.isVisible() is False

    def test_download_cancelled_resets_ui(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            raise ModelDownloadCancelled("cancelled")

        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            win._on_download_clicked()
            worker = win._download_worker
            assert worker is not None
            worker.wait(3000)

        QApplication.processEvents()

        assert win._status_label.text() == "Download cancelled."
        assert _status_cell(win, "base.en") == "Not installed"
        assert win._cancel_btn.isVisible() is False

    def test_download_error_shows_message_box(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        win.show()  # QMessageBox.critical is only shown for a visible window
        _select_row_for(win, "base.en")
        with (
            patch(
                "m4bmaker.gui.filter.workers.download_model",
                side_effect=ModelDownloadError("network failure"),
            ),
            patch(
                "m4bmaker.gui.filter.model_manager_window.QMessageBox.critical"
            ) as mock_critical,
        ):
            win._on_download_clicked()
            worker = win._download_worker
            assert worker is not None
            worker.wait(3000)

            QApplication.processEvents()

        assert win._status_label.text() == "Download failed."
        assert _status_cell(win, "base.en") == "Not installed"
        mock_critical.assert_called_once()

    def test_cancel_button_requests_cancellation(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            assert cancel_event is not None
            cancel_event.wait(2)
            raise ModelDownloadCancelled("cancelled")

        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            win._on_download_clicked()
            worker = win._download_worker
            assert worker is not None
            worker.wait(200)
            win._on_cancel_clicked()
            worker.wait(3000)

        QApplication.processEvents()

        assert win._status_label.text() == "Download cancelled."

    def test_download_disabled_while_selection_empty(
        self, win: ModelManagerWindow
    ) -> None:
        win._on_download_clicked()  # no selection: must be a no-op
        assert win._download_worker is None

    def test_blocked_when_coordinator_held_elsewhere(
        self, win: ModelManagerWindow
    ) -> None:
        """ADR-0014: the shared download_coordinator (not just this
        window's own _downloading_name) gates starting a download — a
        download already in flight from the wizard's Transcript step
        must block this window's own Download button, not just the
        reverse. Simulated by acquiring the coordinator directly, the
        same as TranscriptStep's real download click would."""
        from m4bmaker.gui.filter.workers import download_coordinator

        assert download_coordinator.try_acquire("small.en") is True
        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.model_manager_window.QMessageBox.information"
        ) as mock_info:
            win._on_download_clicked()
        assert win._download_worker is None
        mock_info.assert_called_once()
        assert "small.en" in mock_info.call_args.args[2]

    def test_successful_download_releases_coordinator(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        from m4bmaker.gui.filter.workers import download_coordinator

        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            installed_path = tmp_path / spec.filename()
            installed_path.touch()
            os.truncate(installed_path, spec.size_bytes)
            return installed_path

        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            win._on_download_clicked()
            assert download_coordinator.active_name == "base.en"
            worker = win._download_worker
            assert worker is not None
            worker.wait(3000)

        QApplication.processEvents()
        assert download_coordinator.active_name is None


class TestRemove:
    @pytest.fixture()
    def win(self, tmp_path: Path) -> ModelManagerWindow:
        installed = tmp_path / _BASE_EN.filename()
        installed.write_bytes(b"\x00" * _BASE_EN.size_bytes)
        with patch(
            "m4bmaker.gui.filter.model_manager_window.find_whisper_cli",
            return_value=None,
        ):
            return ModelManagerWindow(dest_dir=tmp_path)

    def test_confirmed_remove_deletes_file(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.model_manager_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            win._on_remove_clicked()

        assert not (tmp_path / _BASE_EN.filename()).exists()
        assert _status_cell(win, "base.en") == "Not installed"

    def test_declined_remove_keeps_file(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.model_manager_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            win._on_remove_clicked()

        assert (tmp_path / _BASE_EN.filename()).exists()
        assert _status_cell(win, "base.en") == "Installed"

    def test_confirmation_defaults_to_no(self, win: ModelManagerWindow) -> None:
        # A destructive confirmation must never default to the destructive
        # choice -- an accidental Enter/Return keypress on this dialog
        # should decline, not remove the model.
        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.model_manager_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as mock_question:
            win._on_remove_clicked()

        assert mock_question.call_args.args[-1] == QMessageBox.StandardButton.No


class TestCloseEvent:
    def test_close_without_active_download_closes_immediately(
        self, win: ModelManagerWindow
    ) -> None:
        win.close()
        assert win.isVisible() is False

    def test_close_emits_closed_signal(self, win: ModelManagerWindow) -> None:
        received = []
        win.closed.connect(lambda: received.append(True))
        win.close()
        assert received == [True]

    def test_declined_close_during_download_does_not_emit_closed(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            assert cancel_event is not None
            cancel_event.wait(2)
            raise ModelDownloadCancelled("cancelled")

        received = []
        win.closed.connect(lambda: received.append(True))
        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            win._on_download_clicked()
            worker = win._download_worker
            assert worker is not None
            worker.wait(200)

            with patch(
                "m4bmaker.gui.filter.model_manager_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ):
                win.close()
            assert received == []

            worker.request_cancel()
            worker.wait(3000)

    def test_close_during_download_prompts_and_can_be_declined(
        self, win: ModelManagerWindow, tmp_path: Path
    ) -> None:
        def fake_download(
            spec: ModelSpec,
            dest_dir: Path,
            progress_callback: _ProgressCallback | None = None,
            cancel_event: threading.Event | None = None,
        ) -> Path:
            assert cancel_event is not None
            cancel_event.wait(2)
            raise ModelDownloadCancelled("cancelled")

        _select_row_for(win, "base.en")
        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            win._on_download_clicked()
            worker = win._download_worker
            assert worker is not None
            worker.wait(200)

            with patch(
                "m4bmaker.gui.filter.model_manager_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ):
                win.close()
            assert worker.isRunning() is True

            worker.request_cancel()
            worker.wait(3000)
