"""Tests for m4bmaker.filter.settings — persistent filtering-feature
settings (storage-location overrides, transcription defaults).

Deliberately its own file, own store, own defaults — distinct from
``gui/prefs.py`` (dark mode, update checks), which predates this fork
and is kept under separate ownership. Same load/save/get/set shape,
mirrored test structure to ``tests/gui/test_prefs.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import m4bmaker.filter.settings as settings_mod
from m4bmaker.filter.settings import get, load, save, set


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect _settings_path() to a tmp directory for every test."""
    data_dir = tmp_path / "m4bmaker_test_data"
    monkeypatch.setattr(
        settings_mod, "_settings_path", lambda: data_dir / "settings.json"
    )


class TestLoad:
    def test_returns_defaults_when_no_file_exists(self) -> None:
        result = load()
        assert result == {
            "models_dir": None,
            "transcripts_dir": None,
            "output_dir": None,
            "temp_dir": None,
            "preferred_model": None,
        }

    def test_returns_dict(self) -> None:
        assert isinstance(load(), dict)

    def test_existing_value_overrides_default(self) -> None:
        path = settings_mod._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"models_dir": "/custom/models"}), encoding="utf-8")
        result = load()
        assert result["models_dir"] == "/custom/models"
        assert result["transcripts_dir"] is None  # unset keys still default

    def test_corrupt_json_falls_back_to_defaults(self) -> None:
        path = settings_mod._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("this is not json {{{{", encoding="utf-8")
        result = load()
        assert result["models_dir"] is None

    def test_non_dict_root_falls_back_to_defaults(self) -> None:
        path = settings_mod._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        result = load()
        assert result["models_dir"] is None


class TestSaveLoadRoundTrip:
    def test_value_persists(self) -> None:
        save({"output_dir": "/books/filtered"})
        assert load()["output_dir"] == "/books/filtered"

    def test_save_creates_data_directory(self) -> None:
        path = settings_mod._settings_path()
        assert not path.parent.exists()
        save({"preferred_model": "base.en"})
        assert path.parent.exists()
        assert path.exists()

    def test_save_silently_ignores_permission_error(self) -> None:
        with patch("builtins.open", side_effect=PermissionError("denied")):
            save({"preferred_model": "base.en"})  # must not raise


class TestSetGet:
    def test_get_default_when_no_file(self) -> None:
        assert get("models_dir") is None

    def test_set_persists_and_get_returns_it(self) -> None:
        set("transcripts_dir", "/books/transcripts")
        assert get("transcripts_dir") == "/books/transcripts"

    def test_set_none_clears_a_previous_override(self) -> None:
        set("output_dir", "/books/filtered")
        set("output_dir", None)
        assert get("output_dir") is None

    def test_set_one_key_does_not_disturb_another(self) -> None:
        set("models_dir", "/custom/models")
        set("preferred_model", "small.en")
        assert get("models_dir") == "/custom/models"
        assert get("preferred_model") == "small.en"
