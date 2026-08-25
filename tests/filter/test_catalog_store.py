"""Tests for m4bmaker.filter.catalog_store — JSON catalog persistence
(PRD §12.4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from m4bmaker.filter import storage
from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_store import load_catalog, save_catalog


class TestSaveAndLoadRoundTrip:
    def test_categories_and_entries_round_trip(self, tmp_path: Path) -> None:
        service = CatalogService()
        cat = service.create_category("Profanity", description="Swear words")
        entry, _ = service.create_entry(cat.id, "darn", notes="mild")

        path = tmp_path / "catalog.json"
        save_catalog(service, path)
        restored = load_catalog(path)

        restored_cat = restored.get_category(cat.id)
        assert restored_cat.name == "Profanity"
        assert restored_cat.description == "Swear words"
        restored_entry = restored.get_entry(entry.id)
        assert restored_entry.canonical_phrase == "darn"
        assert restored_entry.notes == "mild"
        assert restored_entry.category_id == cat.id

    def test_mask_fields_round_trip(self, tmp_path: Path) -> None:
        service = CatalogService()
        cat = service.create_category("Slurs", mask_all_terms=True)
        masked_entry, _ = service.create_entry(cat.id, "sensitive-word", mask=True)
        plain_entry, _ = service.create_entry(cat.id, "other-word")

        path = tmp_path / "catalog.json"
        save_catalog(service, path)
        restored = load_catalog(path)

        assert restored.get_category(cat.id).mask_all_terms is True
        assert restored.get_entry(masked_entry.id).mask is True
        assert restored.get_entry(plain_entry.id).mask is False
        assert restored.is_masked(masked_entry.id) is True
        assert restored.is_masked(plain_entry.id) is True  # category-wide mask

    def test_profile_and_attenuation_round_trip(self, tmp_path: Path) -> None:
        from m4bmaker.filter.models import AttenuationSettings

        service = CatalogService()
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        custom = AttenuationSettings(gain_floor_db=-70.0, fade_in_ms=20)
        profile = service.create_profile(
            "Family Friendly", entry_ids=[entry.id], attenuation=custom
        )

        path = tmp_path / "catalog.json"
        save_catalog(service, path)
        restored = load_catalog(path)

        restored_profile = restored.get_profile(profile.id)
        assert restored_profile.name == "Family Friendly"
        assert restored_profile.entry_ids == [entry.id]
        assert restored_profile.attenuation.gain_floor_db == -70.0
        assert restored_profile.attenuation.fade_in_ms == 20

    def test_archived_items_are_preserved(self, tmp_path: Path) -> None:
        service = CatalogService()
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.archive_entry(entry.id)

        path = tmp_path / "catalog.json"
        save_catalog(service, path)
        restored = load_catalog(path)

        assert restored.get_entry(entry.id).archived is True
        # Excluded from the default (non-archived) listing, as before.
        assert restored.list_entries(category_id=cat.id) == []
        assert restored.list_entries(category_id=cat.id, include_archived=True) != []

    def test_revisions_are_preserved(self, tmp_path: Path) -> None:
        service = CatalogService()
        cat = service.create_category("Profanity")
        service.update_category(cat.id, description="v2")

        path = tmp_path / "catalog.json"
        save_catalog(service, path)
        restored = load_catalog(path)

        assert restored.get_category(cat.id).revision == 2

    def test_empty_catalog_round_trips_cleanly(self, tmp_path: Path) -> None:
        service = CatalogService()
        path = tmp_path / "catalog.json"
        save_catalog(service, path)
        restored = load_catalog(path)
        assert restored.list_categories() == []
        assert restored.list_entries() == []
        assert restored.list_profiles() == []

    def test_snapshotting_a_loaded_profile_still_works(self, tmp_path: Path) -> None:
        """The reconstructed service must be fully functional, not just a
        read-only echo — e.g. create_snapshot() depends on internal state
        that from_records() populates directly."""
        service = CatalogService()
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        profile = service.create_profile("Test", entry_ids=[entry.id])

        path = tmp_path / "catalog.json"
        save_catalog(service, path)
        restored = load_catalog(path)

        snapshot = restored.create_snapshot(profile.id)
        assert len(snapshot.entries) == 1
        assert snapshot.entries[0].normalized_phrase == "darn"


class TestLoadMissingFile:
    def test_returns_empty_service_without_raising(self, tmp_path: Path) -> None:
        service = load_catalog(tmp_path / "does-not-exist.json")
        assert service.list_categories() == []


class TestLoadCorruptedFile:
    def test_malformed_json_returns_empty_service(self, tmp_path: Path) -> None:
        path = tmp_path / "catalog.json"
        path.write_text("{not valid json", encoding="utf-8")
        service = load_catalog(path)
        assert service.list_categories() == []

    def test_wrong_shape_returns_empty_service(self, tmp_path: Path) -> None:
        path = tmp_path / "catalog.json"
        path.write_text('{"categories": "not-a-list"}', encoding="utf-8")
        service = load_catalog(path)
        assert service.list_categories() == []

    def test_missing_file_does_not_log_a_warning(self, tmp_path: Path) -> None:
        with patch("m4bmaker.filter.catalog_store._log") as mock_log:
            load_catalog(tmp_path / "does-not-exist.json")
        mock_log.warning.assert_not_called()

    def test_corrupted_file_logs_a_warning(self, tmp_path: Path) -> None:
        path = tmp_path / "catalog.json"
        path.write_text("{not valid json", encoding="utf-8")
        with patch("m4bmaker.filter.catalog_store._log") as mock_log:
            load_catalog(path)
        mock_log.warning.assert_called_once()

    def test_pre_masking_schema_loads_with_mask_defaults(self, tmp_path: Path) -> None:
        """A catalog.json saved before mask_all_terms/mask existed (ADR-0011)
        must still load — new fields fall back to their dataclass defaults
        rather than raising, since Category(**c)/CatalogEntry(**e) only
        requires keys with no default to be present."""
        path = tmp_path / "catalog.json"
        storage.write_json_atomic(
            path,
            {
                "schemaVersion": 1,
                "categories": [
                    {
                        "id": "cat-1",
                        "name": "Profanity",
                        "description": "",
                        "enabled_by_default": True,
                        "display_order": 0,
                        "revision": 1,
                        "archived": False,
                    }
                ],
                "entries": [
                    {
                        "id": "e-1",
                        "category_id": "cat-1",
                        "canonical_phrase": "darn",
                        "enabled": True,
                        "notes": "",
                        "revision": 1,
                        "archived": False,
                    }
                ],
                "profiles": [],
            },
        )

        restored = load_catalog(path)

        assert restored.get_category("cat-1").mask_all_terms is False
        assert restored.get_entry("e-1").mask is False
        assert restored.is_masked("e-1") is False


class TestDefaultPath:
    def test_save_and_load_use_platformdirs_root_by_default(
        self, tmp_path: Path
    ) -> None:
        with patch("m4bmaker.filter.storage.user_data_dir", return_value=str(tmp_path)):
            service = CatalogService()
            service.create_category("Profanity")
            save_catalog(service)  # no explicit path -> default location
            restored = load_catalog()  # same default location
        assert len(restored.list_categories()) == 1
