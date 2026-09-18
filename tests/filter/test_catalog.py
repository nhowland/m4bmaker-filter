"""Tests for m4bmaker.filter.catalog — catalog/profile CRUD (PRD §9.2)."""

from __future__ import annotations

import pytest

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.models import (
    AttenuationSettings,
    CatalogEntry,
    Category,
    FilterProfile,
    SchemaValidationError,
)


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


class TestExportEverything:
    """ADR-0055's "Everything" export scope."""

    def test_returns_every_category_entry_and_profile(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.create_profile("Family Friendly", entry_ids=[entry.id])

        categories, entries, profiles = service.export_everything()

        assert [c.name for c in categories] == ["Profanity"]
        assert [e.canonical_phrase for e in entries] == ["darn"]
        assert [p.name for p in profiles] == ["Family Friendly"]

    def test_excludes_archived_by_default(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.archive_entry(entry.id)
        archived_cat = service.create_category("Old")
        service.archive_category(archived_cat.id)
        profile = service.create_profile("Family Friendly")
        service.archive_profile(profile.id)

        categories, entries, profiles = service.export_everything()

        assert [c.name for c in categories] == ["Profanity"]
        assert entries == []
        assert profiles == []

    def test_include_archived_opts_in(self, service: CatalogService) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        service.archive_entry(entry.id)

        categories, entries, profiles = service.export_everything(include_archived=True)

        assert [c.name for c in categories] == ["Profanity"]
        assert [e.canonical_phrase for e in entries] == ["darn"]


class TestExportCategories:
    """ADR-0055's "Selected categories" export scope — no profiles."""

    def test_returns_only_selected_categories_and_their_entries(
        self, service: CatalogService
    ) -> None:
        cat_a = service.create_category("Profanity")
        service.create_entry(cat_a.id, "darn")
        cat_b = service.create_category("Religious")
        service.create_entry(cat_b.id, "heck")

        categories, entries, profiles = service.export_categories([cat_a.id])

        assert [c.name for c in categories] == ["Profanity"]
        assert [e.canonical_phrase for e in entries] == ["darn"]
        assert profiles == []

    def test_excludes_archived_entries_in_a_selected_category(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        service.create_entry(cat.id, "darn")
        archived_entry, _ = service.create_entry(cat.id, "heck")
        service.archive_entry(archived_entry.id)

        _, entries, _ = service.export_categories([cat.id])

        assert [e.canonical_phrase for e in entries] == ["darn"]


class TestExportProfiles:
    """ADR-0055's "Selected profiles" export scope — pulls in the
    categories/entries a profile's entry_ids resolve to, transitively."""

    def test_pulls_in_referenced_categories_and_entries(
        self, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        other_cat = service.create_category("Religious")
        service.create_entry(other_cat.id, "heck")  # not referenced by the profile
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])

        categories, entries, profiles = service.export_profiles([profile.id])

        assert [c.name for c in categories] == ["Profanity"]
        assert [e.canonical_phrase for e in entries] == ["darn"]
        assert [p.name for p in profiles] == ["Family Friendly"]

    def test_drops_archived_referenced_entries(self, service: CatalogService) -> None:
        # ADR-0055: unlike create_snapshot(), a profile export does not
        # keep an archived-but-referenced entry -- "include archived" is
        # reserved for the "Everything" scope alone.
        cat = service.create_category("Profanity")
        entry, _ = service.create_entry(cat.id, "darn")
        profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
        service.archive_entry(entry.id)

        categories, entries, profiles = service.export_profiles([profile.id])

        assert entries == []
        assert categories == []
        assert [p.name for p in profiles] == ["Family Friendly"]


class TestPlanImport:
    """ADR-0055: a dry-run of what an import would do, computed against
    the service's current state."""

    def test_new_category_and_new_word(self, service: CatalogService) -> None:
        imported_cat = Category(id="c1", name="Slang")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="bonkers"
        )

        plan = service.plan_import([imported_cat], [imported_entry], [])

        assert len(plan.categories) == 1
        cat_plan = plan.categories[0]
        assert cat_plan.is_new_category is True
        assert cat_plan.existing_category_id is None
        assert len(cat_plan.entries) == 1
        assert cat_plan.entries[0].is_duplicate is False

    def test_matches_existing_category_by_case_insensitive_name(
        self, service: CatalogService
    ) -> None:
        local_cat = service.create_category("Profanity")
        imported_cat = Category(id="c1", name="PROFANITY")

        plan = service.plan_import([imported_cat], [], [])

        cat_plan = plan.categories[0]
        assert cat_plan.is_new_category is False
        assert cat_plan.existing_category_id == local_cat.id

    def test_word_already_in_the_matched_category_is_a_duplicate(
        self, service: CatalogService
    ) -> None:
        local_cat = service.create_category("Profanity")
        local_entry, _ = service.create_entry(local_cat.id, "damn")
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="DAMN"
        )

        plan = service.plan_import([imported_cat], [imported_entry], [])

        entry_plan = plan.categories[0].entries[0]
        assert entry_plan.is_duplicate is True
        assert entry_plan.existing_entry_id == local_entry.id

    def test_word_in_a_new_category_is_never_a_duplicate(
        self, service: CatalogService
    ) -> None:
        # There's no local category to check duplicates against yet.
        imported_cat = Category(id="c1", name="Slang")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="bonkers"
        )

        plan = service.plan_import([imported_cat], [imported_entry], [])

        assert plan.categories[0].entries[0].is_duplicate is False

    def test_matches_existing_profile_by_case_insensitive_name(
        self, service: CatalogService
    ) -> None:
        local_profile = service.create_profile("Family Friendly")
        imported_profile = FilterProfile(id="p1", name="family friendly")

        plan = service.plan_import([], [], [imported_profile])

        profile_plan = plan.profiles[0]
        assert profile_plan.is_duplicate is True
        assert profile_plan.existing_profile_id == local_profile.id

    def test_new_profile(self, service: CatalogService) -> None:
        imported_profile = FilterProfile(id="p1", name="Strict Filter")

        plan = service.plan_import([], [], [imported_profile])

        profile_plan = plan.profiles[0]
        assert profile_plan.is_duplicate is False
        assert profile_plan.existing_profile_id is None

    def test_archived_imported_records_are_dropped(
        self, service: CatalogService
    ) -> None:
        imported_cat = Category(id="c1", name="Slang", archived=True)
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="bonkers", archived=True
        )
        imported_profile = FilterProfile(id="p1", name="Old", archived=True)

        plan = service.plan_import([imported_cat], [imported_entry], [imported_profile])

        assert plan.categories == ()
        assert plan.profiles == ()


class TestApplyImport:
    """ADR-0055: apply_import() must do exactly what plan_import()
    computed -- new categories/words/profiles created, duplicates skipped
    unless overwrite_duplicates is set, profile entry_ids remapped to
    the resulting local entries."""

    def test_creates_new_category_and_word(self, service: CatalogService) -> None:
        imported_cat = Category(id="c1", name="Slang")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="bonkers"
        )
        plan = service.plan_import([imported_cat], [imported_entry], [])

        summary = service.apply_import(plan)

        assert summary.new_categories == 1
        assert summary.new_words == 1
        assert summary.duplicate_words == 0
        local_categories = service.list_categories()
        assert [c.name for c in local_categories] == ["Slang"]
        local_entries = service.list_entries(category_id=local_categories[0].id)
        assert [e.canonical_phrase for e in local_entries] == ["bonkers"]

    def test_merges_into_existing_category_rather_than_duplicating_it(
        self, service: CatalogService
    ) -> None:
        local_cat = service.create_category("Profanity")
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="crud"
        )
        plan = service.plan_import([imported_cat], [imported_entry], [])

        service.apply_import(plan)

        assert len(service.list_categories()) == 1
        entries = service.list_entries(category_id=local_cat.id)
        assert [e.canonical_phrase for e in entries] == ["crud"]

    def test_duplicate_word_is_skipped_by_default(
        self, service: CatalogService
    ) -> None:
        local_cat = service.create_category("Profanity")
        local_entry, _ = service.create_entry(local_cat.id, "damn", notes="original")
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="damn", notes="from import"
        )
        plan = service.plan_import([imported_cat], [imported_entry], [])

        summary = service.apply_import(plan)

        assert summary.new_words == 0
        assert summary.duplicate_words == 1
        assert service.get_entry(local_entry.id).notes == "original"
        assert len(service.list_entries(category_id=local_cat.id)) == 1

    def test_duplicate_word_is_overwritten_when_opted_in(
        self, service: CatalogService
    ) -> None:
        local_cat = service.create_category("Profanity")
        local_entry, _ = service.create_entry(local_cat.id, "damn", notes="original")
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1",
            category_id="c1",
            canonical_phrase="damn",
            notes="from import",
            mask=True,
        )
        plan = service.plan_import([imported_cat], [imported_entry], [])

        service.apply_import(plan, overwrite_duplicates=True)

        updated = service.get_entry(local_entry.id)
        assert updated.notes == "from import"
        assert updated.mask is True

    def test_re_importing_your_own_backup_is_a_no_op_by_default(
        self, service: CatalogService
    ) -> None:
        """ADR-0054's lesson, verified directly: importing an export of
        your own current catalog must not duplicate anything when
        duplicates are (as by default) skipped."""
        cat = service.create_category("Profanity")
        service.create_entry(cat.id, "damn")
        service.create_profile("Family Friendly", entry_ids=[])
        categories, entries, profiles = service.export_everything()

        plan = service.plan_import(categories, entries, profiles)
        summary = service.apply_import(plan)

        assert summary.new_categories == 0
        assert summary.new_words == 0
        assert summary.new_profiles == 0
        assert summary.duplicate_words == 1
        assert summary.duplicate_profiles == 1
        assert len(service.list_categories()) == 1
        assert len(service.list_entries(category_id=cat.id)) == 1
        assert len(service.list_profiles()) == 1

    def test_new_profile_entry_ids_are_remapped_to_local_entries(
        self, service: CatalogService
    ) -> None:
        imported_cat = Category(id="c1", name="Slang")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="bonkers"
        )
        imported_profile = FilterProfile(id="p1", name="Strict", entry_ids=["e1"])
        plan = service.plan_import([imported_cat], [imported_entry], [imported_profile])

        service.apply_import(plan)

        local_profile = service.list_profiles()[0]
        local_entry = service.list_entries()[0]
        assert local_profile.entry_ids == [local_entry.id]
        assert local_entry.id != "e1"

    def test_new_profile_entry_ids_remap_to_an_existing_duplicate_entry(
        self, service: CatalogService
    ) -> None:
        local_cat = service.create_category("Profanity")
        local_entry, _ = service.create_entry(local_cat.id, "damn")
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="damn"
        )
        imported_profile = FilterProfile(id="p1", name="Strict", entry_ids=["e1"])
        plan = service.plan_import([imported_cat], [imported_entry], [imported_profile])

        service.apply_import(plan)

        local_profile = [p for p in service.list_profiles() if p.name == "Strict"][0]
        assert local_profile.entry_ids == [local_entry.id]

    def test_duplicate_profile_is_skipped_by_default(
        self, service: CatalogService
    ) -> None:
        local_profile = service.create_profile("Family Friendly")
        imported_profile = FilterProfile(id="p1", name="Family Friendly")
        plan = service.plan_import([], [], [imported_profile])

        summary = service.apply_import(plan)

        assert summary.new_profiles == 0
        assert summary.duplicate_profiles == 1
        assert len(service.list_profiles()) == 1
        assert service.get_profile(local_profile.id).name == "Family Friendly"

    def test_duplicate_profile_is_overwritten_when_opted_in(
        self, service: CatalogService
    ) -> None:
        local_cat = service.create_category("Profanity")
        local_entry, _ = service.create_entry(local_cat.id, "damn")
        local_profile = service.create_profile("Family Friendly", entry_ids=[])
        imported_cat = Category(id="c1", name="Profanity")
        imported_entry = CatalogEntry(
            id="e1", category_id="c1", canonical_phrase="damn"
        )
        imported_profile = FilterProfile(
            id="p1", name="Family Friendly", entry_ids=["e1"]
        )
        plan = service.plan_import([imported_cat], [imported_entry], [imported_profile])

        service.apply_import(plan, overwrite_duplicates=True)

        updated = service.get_profile(local_profile.id)
        assert updated.entry_ids == [local_entry.id]
