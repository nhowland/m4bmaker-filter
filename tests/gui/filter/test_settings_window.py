"""Tests for m4bmaker.gui.filter.settings_window.SettingsWindow (ADR-0035).

Isolates m4bmaker.filter.settings's real on-disk store the same way
test_settings.py does — this window must never touch the real
~/Library/Application Support/... settings.json during a test run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import m4bmaker.filter.settings as settings_mod
from m4bmaker.filter.model_manager import KNOWN_MODELS
from m4bmaker.gui.filter.settings_window import SettingsWindow

pytestmark = pytest.mark.usefixtures("qapp")

_BASE_EN = KNOWN_MODELS[0]
_SMALL_EN = KNOWN_MODELS[1]


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "m4bmaker_test_data"
    monkeypatch.setattr(
        settings_mod, "_settings_path", lambda: data_dir / "settings.json"
    )


@pytest.fixture()
def win() -> SettingsWindow:
    return SettingsWindow()


class TestConstruction:
    def test_renders_without_error(self, win: SettingsWindow) -> None:
        assert win is not None

    def test_window_title(self, win: SettingsWindow) -> None:
        assert win.windowTitle() == "Settings"


class TestFolderRows:
    @pytest.mark.parametrize(
        "key", ["models_dir", "transcripts_dir", "output_dir", "temp_dir"]
    )
    def test_browse_sets_and_persists_override(
        self, win: SettingsWindow, key: str
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.settings_window.QFileDialog.getExistingDirectory",
            return_value="/custom/path",
        ):
            win._on_browse(key)
        assert settings_mod.get(key) == "/custom/path"
        assert win._row_edits[key].text() == "/custom/path"

    @pytest.mark.parametrize(
        "key", ["models_dir", "transcripts_dir", "output_dir", "temp_dir"]
    )
    def test_cancelled_browse_leaves_override_unset(
        self, win: SettingsWindow, key: str
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.settings_window.QFileDialog.getExistingDirectory",
            return_value="",  # Qt's own "cancelled" convention
        ):
            win._on_browse(key)
        assert settings_mod.get(key) is None

    @pytest.mark.parametrize(
        "key", ["models_dir", "transcripts_dir", "output_dir", "temp_dir"]
    )
    def test_reset_clears_override(self, win: SettingsWindow, key: str) -> None:
        settings_mod.set(key, "/custom/path")
        win._refresh_row(key)
        assert win._row_reset_btns[key].isEnabled() is True

        win._on_reset(key)

        assert settings_mod.get(key) is None
        assert win._row_reset_btns[key].isEnabled() is False

    def test_reset_button_disabled_when_nothing_overridden(
        self, win: SettingsWindow
    ) -> None:
        for key in ("models_dir", "transcripts_dir", "output_dir", "temp_dir"):
            assert win._row_reset_btns[key].isEnabled() is False

    def test_output_dir_shows_default_description_when_unset(
        self, win: SettingsWindow
    ) -> None:
        assert "Same folder as the source book" in win._row_edits["output_dir"].text()

    def test_models_dir_shows_a_real_path_when_unset(self, win: SettingsWindow) -> None:
        # Unlike output_dir, models_dir() always resolves to a real path
        # even with no override — no "(default)" placeholder text needed.
        text = win._row_edits["models_dir"].text()
        assert text != ""
        assert "default" not in text.lower()

    def test_temp_dir_shows_a_real_path_when_unset(self, win: SettingsWindow) -> None:
        # Same as models_dir — temp_root() always resolves to cache_root()
        # with no override, never a bare "(default)" placeholder.
        text = win._row_edits["temp_dir"].text()
        assert text != ""
        assert "default" not in text.lower()

    def test_field_tooltip_shows_the_full_value(self, win: SettingsWindow) -> None:
        # A long path can still be visually clipped by the field's own
        # width — the tooltip is the fallback way to read all of it.
        edit = win._row_edits["models_dir"]
        assert edit.toolTip() == edit.text()


class TestPreferredModel:
    def test_defaults_to_no_preference(self, win: SettingsWindow) -> None:
        assert win._model_combo.currentData() is None

    def test_existing_preference_is_preselected_on_open(self) -> None:
        settings_mod.set("preferred_model", "small.en")
        win = SettingsWindow()
        assert win._model_combo.currentData() == "small.en"

    def test_selecting_a_model_persists_it(self, win: SettingsWindow) -> None:
        index = win._model_combo.findData("small.en")
        win._model_combo.setCurrentIndex(index)
        assert settings_mod.get("preferred_model") == "small.en"

    def test_selecting_no_preference_clears_it(self, win: SettingsWindow) -> None:
        settings_mod.set("preferred_model", "small.en")
        win2 = SettingsWindow()
        win2._model_combo.setCurrentIndex(0)  # "No preference"
        assert settings_mod.get("preferred_model") is None


class TestCloseEvent:
    def test_close_emits_closed_signal(self, win: SettingsWindow) -> None:
        received = []
        win.closed.connect(lambda: received.append(True))
        win.close()
        assert received == [True]
