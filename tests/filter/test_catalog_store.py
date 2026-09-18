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
    def test_returns_seeded_service_without_raising(self, tmp_path: Path) -> None:
        """ADR-0022: a missing catalog file means first run, not an
        intentionally emptied catalog — load_catalog seeds the default
        "Profanity" category rather than returning nothing."""
        service = load_catalog(tmp_path / "does-not-exist.json")
        categories = service.list_categories()
        assert len(categories) == 1
        assert categories[0].name == "Profanity"
        assert len(service.list_entries()) > 0

    def test_seeded_first_run_catalog_is_saved_immediately(
        self, tmp_path: Path
    ) -> None:
        """Otherwise a session that never touches the catalog would
        re-seed (and duplicate) on every later launch, since nothing
        would exist on disk to stop the "file doesn't exist" branch from
        firing again."""
        path = tmp_path / "catalog.json"
        assert not path.exists()

        load_catalog(path)

        assert path.exists()
        second_load = load_catalog(path)
        assert len(second_load.list_categories()) == 1


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

    def test_corrupted_file_is_backed_up_before_the_fallback_is_returned(
        self, tmp_path: Path
    ) -> None:
        # A real incident, not hypothetical: this exact fallback path
        # once silently discarded a real, hand-curated catalog with
        # nothing but a log line. The unreadable bytes must always
        # survive somewhere on disk after this.
        path = tmp_path / "catalog.json"
        original_bytes = "{not valid json"
        path.write_text(original_bytes, encoding="utf-8")
        load_catalog(path)
        backups = list(tmp_path.glob("catalog.json.unreadable-*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original_bytes

    def test_original_unreadable_file_is_left_in_place_too(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "catalog.json"
        path.write_text("{not valid json", encoding="utf-8")
        load_catalog(path)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "{not valid json"

    def test_missing_file_creates_no_backup(self, tmp_path: Path) -> None:
        load_catalog(tmp_path / "does-not-exist.json")
        assert list(tmp_path.glob("*.bak")) == []

    def test_healthy_file_creates_no_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "catalog.json"
        save_catalog(CatalogService(), path)
        load_catalog(path)
        assert list(tmp_path.glob("*.bak")) == []

    def test_on_recovery_called_with_the_backup_path_when_unparseable(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "catalog.json"
        path.write_text("{not valid json", encoding="utf-8")
        received = []
        load_catalog(path, on_recovery=received.append)
        assert len(received) == 1
        assert received[0].name.startswith("catalog.json.unreadable-")
        assert received[0].exists()

    def test_on_recovery_not_called_on_a_healthy_load(self, tmp_path: Path) -> None:
        path = tmp_path / "catalog.json"
        save_catalog(CatalogService(), path)
        received = []
        load_catalog(path, on_recovery=received.append)
        assert received == []

    def test_on_recovery_not_called_on_first_run(self, tmp_path: Path) -> None:
        received = []
        load_catalog(tmp_path / "does-not-exist.json", on_recovery=received.append)
        assert received == []

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


class TestDefaultPathIsAlwaysIsolatedInTests:
    """Proves the ``tests/conftest.py`` ``_isolated_filter_data_root``
    autouse fixture actually redirects the real default path away from
    the real per-user directory — not just trusted by design.

    This is exactly the code path (``save_catalog()``/``load_catalog()``
    with no explicit path) that once silently overwrote a real, hand-
    curated production ``catalog.json`` when a *different* test forgot
    its own local patch of ``save_catalog``. Neither test method here
    patches anything locally, on purpose — proving the global safety
    net alone is enough to prevent that class of mistake, without
    relying on every test file remembering the convention.
    """

    def test_default_catalog_path_never_resolves_under_the_real_home_library(
        self,
    ) -> None:
        from m4bmaker.filter.catalog_store import catalog_path

        resolved = catalog_path()
        real_library = Path.home() / "Library" / "Application Support"
        assert not str(resolved).startswith(str(real_library))

    def test_save_and_load_with_no_local_patch_still_round_trips_safely(
        self,
    ) -> None:
        service = CatalogService()
        service.create_category("Profanity")
        save_catalog(service)  # no explicit path, no local patch either
        restored = load_catalog()
        assert len(restored.list_categories()) == 1
