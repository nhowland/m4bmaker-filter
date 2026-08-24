"""Tests for m4bmaker.filter.storage — storage roots and atomic JSON I/O."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from m4bmaker.filter import storage


class TestStorageRoots:
    def test_data_root_under_filter_subdir(self) -> None:
        with patch("m4bmaker.filter.storage.user_data_dir", return_value="/fake/data"):
            assert storage.data_root() == Path("/fake/data/filter")

    def test_cache_root_under_filter_subdir(self) -> None:
        with patch(
            "m4bmaker.filter.storage.user_cache_dir", return_value="/fake/cache"
        ):
            assert storage.cache_root() == Path("/fake/cache/filter")

    def test_models_transcripts_reports_are_under_data_root(self) -> None:
        with patch("m4bmaker.filter.storage.user_data_dir", return_value="/fake/data"):
            assert storage.models_dir() == storage.data_root() / "models"
            assert storage.transcripts_dir() == storage.data_root() / "transcripts"
            assert storage.reports_dir() == storage.data_root() / "reports"

    def test_database_path(self) -> None:
        with patch("m4bmaker.filter.storage.user_data_dir", return_value="/fake/data"):
            assert storage.database_path() == storage.data_root() / "filter.db"


class TestEnsureDirs:
    def test_creates_all_directories(self, tmp_path: Path) -> None:
        with (
            patch(
                "m4bmaker.filter.storage.user_data_dir",
                return_value=str(tmp_path / "data"),
            ),
            patch(
                "m4bmaker.filter.storage.user_cache_dir",
                return_value=str(tmp_path / "cache"),
            ),
        ):
            storage.ensure_dirs()
            assert storage.data_root().is_dir()
            assert storage.cache_root().is_dir()
            assert storage.models_dir().is_dir()
            assert storage.transcripts_dir().is_dir()
            assert storage.reports_dir().is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        with (
            patch(
                "m4bmaker.filter.storage.user_data_dir",
                return_value=str(tmp_path / "data"),
            ),
            patch(
                "m4bmaker.filter.storage.user_cache_dir",
                return_value=str(tmp_path / "cache"),
            ),
        ):
            storage.ensure_dirs()
            storage.ensure_dirs()  # must not raise
            assert storage.data_root().is_dir()


class TestWriteJsonAtomic:
    def test_writes_readable_json(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "artifact.json"
        storage.write_json_atomic(target, {"schemaVersion": 1, "status": "complete"})
        assert json.loads(target.read_text()) == {
            "schemaVersion": 1,
            "status": "complete",
        }

    def test_leaves_no_temp_file_behind_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "artifact.json"
        storage.write_json_atomic(target, {"a": 1})
        leftovers = [p for p in tmp_path.iterdir() if p != target]
        assert leftovers == []

    def test_overwrites_existing_file_atomically(self, tmp_path: Path) -> None:
        target = tmp_path / "artifact.json"
        storage.write_json_atomic(target, {"v": 1})
        storage.write_json_atomic(target, {"v": 2})
        assert json.loads(target.read_text()) == {"v": 2}

    def test_original_untouched_when_serialization_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "artifact.json"
        storage.write_json_atomic(target, {"v": 1})

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            storage.write_json_atomic(target, {"v": Unserializable()})

        # Original content must survive a failed write, and no stray temp
        # file should be left behind.
        assert json.loads(target.read_text()) == {"v": 1}
        leftovers = [p for p in tmp_path.iterdir() if p != target]
        assert leftovers == []


class TestReadJson:
    def test_round_trips_with_write_json_atomic(self, tmp_path: Path) -> None:
        target = tmp_path / "artifact.json"
        storage.write_json_atomic(target, {"hello": "world"})
        assert storage.read_json(target) == {"hello": "world"}

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            storage.read_json(tmp_path / "missing.json")

    def test_malformed_json_raises_decode_error(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.json"
        target.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            storage.read_json(target)
