"""Tests for m4bmaker.filter.catalog — catalog/profile CRUD (PRD §9.2)."""

from __future__ import annotations

import pytest

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.models import AttenuationSettings, SchemaValidationError


@pytest.fixture
def service() -> CatalogService:
    return CatalogService()


class TestCategoryCRUD:
    def test_create_and_get(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity", description="Swear words")
        assert service.get_category(cat.id) is cat
        assert cat.revision == 1
        assert cat.archived is False

    def test_blank_name_rejected(self, service: CatalogService) -> None:
        with pytest.raises(SchemaValidationError):
            service.create_category("")

    def test_list_excludes_archived_by_default(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        service.archive_category(cat.id)
        assert service.list_categories() == []
        assert service.list_categories(include_archived=True) == [
            service.get_category(cat.id)
        ]

    def test_update_bumps_revision(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        updated = service.update_category(cat.id, description="Updated")
        assert updated.revision == 2
        assert updated.description == "Updated"

    def test_update_reruns_validation(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        with pytest.raises(SchemaValidationError):
            service.update_category(cat.id, name="")

    def test_delete_unreferenced_category_hard_deletes(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        service.delete_category(cat.id)
        with pytest.raises(KeyError):
            service.get_category(cat.id)

    def test_delete_referenced_category_soft_archives(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.create_profile("My Profile", entry_ids=[entry.id])
        service.delete_category(cat.id)
        # Still readable (soft-archived), not hard-deleted.
        assert service.get_category(cat.id).archived is True

    def test_deleting_unreferenced_category_removes_its_entries(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.delete_category(cat.id)
        with pytest.raises(KeyError):
            service.get_entry(entry.id)


class TestCatalogEntryCRUD:
    def test_create_entry_against_unknown_category_raises(
        self, service: CatalogService
    ) -> None:
        with pytest.raises(KeyError):
            service.create_entry("nonexistent-category", "darn")

    def test_blank_phrase_rejected(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        with pytest.raises(SchemaValidationError):
            service.create_entry(cat.id, "")

    def test_create_entry_no_duplicate_returns_none(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, duplicate = service.create_entry(cat.id, "darn")
        assert duplicate is None
        assert entry.canonical_phrase == "darn"

    def test_create_entry_duplicate_after_normalization_warns_but_still_creates(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        first, _ = service.create_entry(cat.id, "Darn!")
        second, duplicate = service.create_entry(cat.id, "  darn  ")
        assert duplicate is not None
        assert duplicate.id == first.id
        assert second.id != first.id  # still created, just flagged

    def test_duplicate_check_scoped_to_category(self, service: CatalogService) -> None:
        cat_a = service.create_category("A")
        cat_b = service.create_category("B")
        service.create_entry(cat_a.id, "darn")
        _, duplicate = service.create_entry(cat_b.id, "darn")
        assert duplicate is None

    def test_archived_entry_not_flagged_as_duplicate(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.archive_entry(entry.id)
        _, duplicate = service.create_entry(cat.id, "darn")
        assert duplicate is None

    def test_list_entries_filtered_by_category(self, service: CatalogService) -> None:
        cat_a = service.create_category("A")
        cat_b = service.create_category("B")
        entry_a, _ = service.create_entry(cat_a.id, "darn")
        service.create_entry(cat_b.id, "heck")
        assert service.list_entries(category_id=cat_a.id) == [entry_a]

    def test_delete_unreferenced_entry_hard_deletes(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.delete_entry(entry.id)
        with pytest.raises(KeyError):
            service.get_entry(entry.id)

    def test_delete_referenced_entry_soft_archives(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.create_profile("Profile", entry_ids=[entry.id])
        service.delete_entry(entry.id)
        assert service.get_entry(entry.id).archived is True


class TestMasking:
    """mask_all_terms/mask (fork-specific, ADR-0011): review-screen display
    masking composes by OR, not override — see CatalogService.is_masked."""

    def test_defaults_to_unmasked(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        assert cat.mask_all_terms is False
        assert entry.mask is False
        assert service.is_masked(entry.id) is False

    def test_create_category_with_mask_all_terms(self, service: CatalogService) -> None:
        cat = service.create_category("Slurs", mask_all_terms=True)
        entry, _ = service.create_entry(cat.id, "slur-example")
        assert service.is_masked(entry.id) is True

    def test_create_entry_with_mask(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn", mask=True)
        assert cat.mask_all_terms is False
        assert service.is_masked(entry.id) is True

    def test_category_mask_does_not_require_entry_mask(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Slurs", mask_all_terms=True)
        entry, _ = service.create_entry(cat.id, "slur-example")
        assert entry.mask is False
        assert service.is_masked(entry.id) is True

    def test_entry_mask_survives_category_unmasked(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        masked_entry, _ = service.create_entry(cat.id, "sensitive-word", mask=True)
        plain_entry, _ = service.create_entry(cat.id, "darn")
        assert service.is_masked(masked_entry.id) is True
        assert service.is_masked(plain_entry.id) is False

    def test_update_category_mask_all_terms(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        assert service.is_masked(entry.id) is False
        service.update_category(cat.id, mask_all_terms=True)
        assert service.is_masked(entry.id) is True

    def test_update_entry_mask(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.update_entry(entry.id, mask=True)
        assert service.is_masked(entry.id) is True


class TestFilterProfileCRUD:
    def test_create_with_default_attenuation(self, service: CatalogService) -> None:
        profile = service.create_profile("Family Friendly")
        assert profile.attenuation == AttenuationSettings()
        assert profile.entry_ids == []

    def test_blank_name_rejected(self, service: CatalogService) -> None:
        with pytest.raises(SchemaValidationError):
            service.create_profile("")

    def test_update_bumps_revision(self, service: CatalogService) -> None:
        profile = service.create_profile("Family Friendly")
        updated = service.update_profile(profile.id, name="Renamed")
        assert updated.revision == 2
        assert updated.name == "Renamed"

    def test_list_excludes_archived_by_default(self, service: CatalogService) -> None:
        profile = service.create_profile("Family Friendly")
        service.archive_profile(profile.id)
        assert service.list_profiles() == []


class TestSnapshotCreation:
    def test_snapshot_captures_current_entry_state(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])

        snapshot = service.create_snapshot(profile.id)

        assert snapshot.profile_id == profile.id
        assert snapshot.profile_revision == profile.revision
        assert len(snapshot.entries) == 1
        assert snapshot.entries[0].entry_id == entry.id
        assert snapshot.entries[0].normalized_phrase == "darn"
        assert snapshot.entries[0].revision == 1

    def test_snapshot_is_unaffected_by_later_catalog_edits(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
        snapshot = service.create_snapshot(profile.id)

        service.update_entry(entry.id, canonical_phrase="heck")

        assert snapshot.entries[0].canonical_phrase == "darn"
        assert snapshot.entries[0].normalized_phrase == "darn"

    def test_snapshot_includes_archived_referenced_entries(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
        service.archive_entry(entry.id)

        snapshot = service.create_snapshot(profile.id)
        assert len(snapshot.entries) == 1
