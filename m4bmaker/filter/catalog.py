"""Catalog and profile CRUD service (PRD §9.2, §17.4 G2).

**Scope decision (not yet settled by an ADR, flagged here for review):**
this service is a pure, storage-agnostic business-logic layer operating on
in-memory dicts. PRD §14.3 defers the SQLite persistence contract's
transaction boundaries and repository design to a decision that has not
been made yet ("Before implementation, define one authoritative source for
each entity..."). Rather than guess at that contract, G2 implements the
CRUD *rules* (validation, revisioning, archive-vs-hard-delete, duplicate
detection, snapshotting) against an in-memory repository now, so a later
SQLite-backed repository can be swapped in behind the same method
signatures without touching this file's logic. This mirrors how
``pipeline.py`` in the base project separates orchestration from I/O.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import (
    AttenuationSettings,
    CatalogEntry,
    Category,
    FilterProfile,
    FilterProfileSnapshot,
    SnapshotEntry,
    normalize_phrase,
)


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── import planning (ADR-0055) ───────────────────────────────────────────


@dataclass(frozen=True)
class ImportEntryPlan:
    """One imported word's resolved fate, computed by
    :meth:`CatalogService.plan_import` and carried out unchanged by
    :meth:`CatalogService.apply_import` — the id fields exist only to
    resolve a profile's ``entry_ids`` after creation, never reused as a
    real local id (ADR-0055: an imported id is opaque outside its own
    export file)."""

    imported_entry_id: str
    canonical_phrase: str
    notes: str
    mask: bool
    is_duplicate: bool
    existing_entry_id: str | None = None


@dataclass(frozen=True)
class ImportCategoryPlan:
    """One imported category's resolved fate — matched by case-insensitive
    name against a non-archived local category, or new."""

    imported_category_id: str
    name: str
    mask_all_terms: bool
    is_new_category: bool
    existing_category_id: str | None
    entries: tuple[ImportEntryPlan, ...]


@dataclass(frozen=True)
class ImportProfilePlan:
    """One imported profile's resolved fate — matched by case-insensitive
    name against a non-archived local profile, or new."""

    imported_profile_id: str
    name: str
    is_duplicate: bool
    existing_profile_id: str | None
    entry_count: int
    attenuation: AttenuationSettings


@dataclass(frozen=True)
class ImportPlan:
    """A dry-run computed by :meth:`CatalogService.plan_import` — a
    preview UI renders this directly, and :meth:`CatalogService.
    apply_import` performs exactly what it describes, so the two can
    never drift apart (ADR-0055). Carries the raw imported records too
    (not just the plan summary) since ``apply_import`` needs each entry's
    full notes/mask and each profile's own ``entry_ids``/attenuation to
    actually create/update anything."""

    categories: tuple[ImportCategoryPlan, ...]
    profiles: tuple[ImportProfilePlan, ...]
    imported_entries: tuple[CatalogEntry, ...]
    imported_profiles: tuple[FilterProfile, ...]


@dataclass(frozen=True)
class ImportSummary:
    """What :meth:`CatalogService.apply_import` actually did."""

    new_categories: int
    new_words: int
    duplicate_words: int
    new_profiles: int
    duplicate_profiles: int


class CatalogService:
    """In-memory catalog/profile CRUD with revisioning and archive rules."""

    def __init__(self) -> None:
        self._categories: dict[str, Category] = {}
        self._entries: dict[str, CatalogEntry] = {}
        self._profiles: dict[str, FilterProfile] = {}

    # ── categories ────────────────────────────────────────────────────────

    def create_category(
        self,
        name: str,
        description: str = "",
        enabled_by_default: bool = True,
        mask_all_terms: bool = False,
    ) -> Category:
        category = Category(
            id=_new_id(),
            name=name,
            description=description,
            enabled_by_default=enabled_by_default,
            mask_all_terms=mask_all_terms,
        )
        self._categories[category.id] = category
        return category

    def get_category(self, category_id: str) -> Category:
        return self._categories[category_id]

    def list_categories(self, include_archived: bool = False) -> list[Category]:
        cats = list(self._categories.values())
        if not include_archived:
            cats = [c for c in cats if not c.archived]
        return sorted(cats, key=lambda c: c.display_order)

    def update_category(self, category_id: str, **changes: Any) -> Category:
        existing = self._categories[category_id]
        updated = replace(existing, revision=existing.revision + 1, **changes)
        self._categories[category_id] = updated
        return updated

    def archive_category(self, category_id: str) -> Category:
        return self.update_category(category_id, archived=True)

    def delete_category(self, category_id: str) -> None:
        """Hard-delete the category and its entries if nothing references
        them; otherwise soft-archive (PRD §9.2: "Deletion must be
        soft-delete/archive in MVP if historical scans/profiles reference
        the item; historical snapshots remain readable")."""
        if self._category_is_referenced(category_id):
            self.archive_category(category_id)
            return
        for entry_id in [
            e.id for e in self._entries.values() if e.category_id == category_id
        ]:
            del self._entries[entry_id]
        del self._categories[category_id]

    def _category_is_referenced(self, category_id: str) -> bool:
        entry_ids = {
            e.id for e in self._entries.values() if e.category_id == category_id
        }
        return any(
            entry_id in entry_ids
            for profile in self._profiles.values()
            for entry_id in profile.entry_ids
        )

    # ── catalog entries ──────────────────────────────────────────────────

    def find_duplicate_entry(
        self, category_id: str, phrase: str
    ) -> CatalogEntry | None:
        """Return the existing enabled entry in *category_id* whose
        normalized phrase equals *phrase*'s, or ``None``. Scoped to one
        category rather than globally, since the same word can legitimately
        appear in more than one category with different intent."""
        normalized = normalize_phrase(phrase)
        for entry in self._entries.values():
            if entry.archived or entry.category_id != category_id:
                continue
            if entry.normalized_phrase == normalized:
                return entry
        return None

    def create_entry(
        self,
        category_id: str,
        canonical_phrase: str,
        notes: str = "",
        mask: bool = False,
    ) -> tuple[CatalogEntry, CatalogEntry | None]:
        """Create a catalog entry. Returns ``(created_entry, duplicate)``
        where *duplicate* is the pre-existing conflicting entry if one
        exists, else ``None``.

        Creation still succeeds when a duplicate is found — PRD §9.2 says
        to "warn about duplicates," not reject them; only a blank phrase
        (enforced by :class:`~m4bmaker.filter.models.CatalogEntry`'s own
        validation) is an outright rejection. The caller (future UI/CLI
        layer) decides what to do with the warning.
        """
        if category_id not in self._categories:
            raise KeyError(f"Unknown category_id: {category_id!r}")
        duplicate = self.find_duplicate_entry(category_id, canonical_phrase)
        entry = CatalogEntry(
            id=_new_id(),
            category_id=category_id,
            canonical_phrase=canonical_phrase,
            notes=notes,
            mask=mask,
        )
        self._entries[entry.id] = entry
        return entry, duplicate

    def get_entry(self, entry_id: str) -> CatalogEntry:
        return self._entries[entry_id]

    def list_entries(
        self, category_id: str | None = None, include_archived: bool = False
    ) -> list[CatalogEntry]:
        entries = list(self._entries.values())
        if category_id is not None:
            entries = [e for e in entries if e.category_id == category_id]
        if not include_archived:
            entries = [e for e in entries if not e.archived]
        return entries

    def update_entry(self, entry_id: str, **changes: Any) -> CatalogEntry:
        existing = self._entries[entry_id]
        updated = replace(existing, revision=existing.revision + 1, **changes)
        self._entries[entry_id] = updated
        return updated

    def archive_entry(self, entry_id: str) -> CatalogEntry:
        return self.update_entry(entry_id, archived=True)

    def is_masked(self, entry_id: str) -> bool:
        """Whether *entry_id* should display masked (e.g. review-screen
        asterisking) rather than in plain text — a fork-specific addition,
        see ``docs/adr/0011-catalog-masking.md``.

        Composes the category's :attr:`Category.mask_all_terms` and the
        entry's own :attr:`CatalogEntry.mask` by OR, not override: turning
        masking on for a whole category masks every entry in it regardless
        of each entry's own flag, and a User can still mask one specific
        entry in an otherwise-unmasked category. Neither flag can turn the
        other off.
        """
        entry = self._entries[entry_id]
        category = self._categories.get(entry.category_id)
        return bool(category and category.mask_all_terms) or entry.mask

    def delete_entry(self, entry_id: str) -> None:
        """Hard-delete if unreferenced by any profile; otherwise soft-archive."""
        if self._entry_is_referenced(entry_id):
            self.archive_entry(entry_id)
            return
        del self._entries[entry_id]

    def _entry_is_referenced(self, entry_id: str) -> bool:
        return any(entry_id in p.entry_ids for p in self._profiles.values())

    # ── filter profiles ──────────────────────────────────────────────────

    def create_profile(
        self,
        name: str,
        entry_ids: list[str] | None = None,
        attenuation: AttenuationSettings | None = None,
    ) -> FilterProfile:
        profile = FilterProfile(
            id=_new_id(),
            name=name,
            entry_ids=list(entry_ids or []),
            attenuation=attenuation or AttenuationSettings(),
        )
        self._profiles[profile.id] = profile
        return profile

    def get_profile(self, profile_id: str) -> FilterProfile:
        return self._profiles[profile_id]

    def list_profiles(self, include_archived: bool = False) -> list[FilterProfile]:
        profiles = list(self._profiles.values())
        if not include_archived:
            profiles = [p for p in profiles if not p.archived]
        return profiles

    def update_profile(self, profile_id: str, **changes: Any) -> FilterProfile:
        existing = self._profiles[profile_id]
        updated = replace(existing, revision=existing.revision + 1, **changes)
        self._profiles[profile_id] = updated
        return updated

    def archive_profile(self, profile_id: str) -> FilterProfile:
        return self.update_profile(profile_id, archived=True)

    def create_snapshot(self, profile_id: str) -> FilterProfileSnapshot:
        """Freeze *profile*'s current entries and their current revisions
        into an immutable :class:`FilterProfileSnapshot` (PRD §14.4).

        Archived entries still referenced by the profile are included —
        historical reproducibility (PRD §9.2: "historical snapshots remain
        readable") takes priority over reflecting the catalog's current
        state. An entry ID the profile references but that no longer
        exists at all (only possible if it was hard-deleted while somehow
        still referenced, which :meth:`delete_entry` prevents) is silently
        skipped rather than raised, since this is a defensive case that
        should be unreachable via this service's own API.
        """
        profile = self._profiles[profile_id]
        entries = tuple(
            SnapshotEntry(
                entry_id=entry.id,
                category_id=entry.category_id,
                canonical_phrase=entry.canonical_phrase,
                normalized_phrase=entry.normalized_phrase,
                revision=entry.revision,
            )
            for entry_id in profile.entry_ids
            if (entry := self._entries.get(entry_id)) is not None
        )
        return FilterProfileSnapshot(
            snapshot_id=_new_id(),
            profile_id=profile.id,
            profile_revision=profile.revision,
            name=profile.name,
            entries=entries,
            attenuation=profile.attenuation,
            created_at=_now(),
        )

    # ── bulk export/import (for an external persistence layer) ─────────────

    def export_all(
        self,
    ) -> tuple[list[Category], list[CatalogEntry], list[FilterProfile]]:
        """Return every category/entry/profile, including archived ones, as
        plain lists for an external persistence layer to serialize.

        This is a bulk *read* only — it performs no I/O itself, keeping
        this service storage-agnostic as documented at the top of this
        file. See ``catalog_store.py`` for the concrete JSON repository
        that actually writes this to disk.
        """
        return (
            list(self._categories.values()),
            list(self._entries.values()),
            list(self._profiles.values()),
        )

    @classmethod
    def from_records(
        cls,
        categories: list[Category],
        entries: list[CatalogEntry],
        profiles: list[FilterProfile],
    ) -> "CatalogService":
        """Reconstruct a service from previously-exported records (e.g.
        loaded from JSON by ``catalog_store.py``).

        Bypasses ``create_category``/``create_entry``/``create_profile``'s
        creation-time side effects (new-UUID assignment, duplicate-phrase
        detection) since these records already have real IDs and were
        already validated once when first created — re-running duplicate
        detection here would incorrectly flag every entry against its own
        already-saved siblings.
        """
        service = cls()
        service._categories = {c.id: c for c in categories}
        service._entries = {e.id: e for e in entries}
        service._profiles = {p.id: p for p in profiles}
        return service

    # ── scoped export (ADR-0055) ────────────────────────────────────────────

    def export_everything(
        self, *, include_archived: bool = False
    ) -> tuple[list[Category], list[CatalogEntry], list[FilterProfile]]:
        """The "Everything" export scope: every category/entry/profile,
        archived ones only if *include_archived* — a true backup, not a
        share, per ADR-0055 (archived items never travel with the other
        two scopes at all)."""
        categories, entries, profiles = self.export_all()
        if include_archived:
            return categories, entries, profiles
        return (
            [c for c in categories if not c.archived],
            [e for e in entries if not e.archived],
            [p for p in profiles if not p.archived],
        )

    def export_categories(
        self, category_ids: Iterable[str]
    ) -> tuple[list[Category], list[CatalogEntry], list[FilterProfile]]:
        """The "Selected categories" export scope: the chosen (non-archived)
        categories and their (non-archived) entries. No profiles — a
        category-scoped export is about words, not the profiles built on
        top of them."""
        ids = set(category_ids)
        categories = [
            c for c in self.list_categories(include_archived=False) if c.id in ids
        ]
        category_id_set = {c.id for c in categories}
        entries = [
            e
            for e in self.list_entries(include_archived=False)
            if e.category_id in category_id_set
        ]
        return categories, entries, []

    def export_profiles(
        self, profile_ids: Iterable[str]
    ) -> tuple[list[Category], list[CatalogEntry], list[FilterProfile]]:
        """The "Selected profiles" export scope: the chosen (non-archived)
        profiles, plus the (non-archived) categories/entries their
        ``entry_ids`` resolve to, transitively — what a profile actually
        needs to be reconstructed on the far end. An archived entry a
        profile still references (kept for historical snapshots,
        :meth:`create_snapshot`) is deliberately dropped here rather than
        given its own "include archived" option — ADR-0055 reserves that
        option for the "Everything" scope alone."""
        ids = set(profile_ids)
        profiles = [
            p for p in self.list_profiles(include_archived=False) if p.id in ids
        ]
        referenced_entry_ids = {eid for p in profiles for eid in p.entry_ids}
        entries = [
            e
            for e in self.list_entries(include_archived=False)
            if e.id in referenced_entry_ids
        ]
        category_id_set = {e.category_id for e in entries}
        categories = [
            c
            for c in self.list_categories(include_archived=False)
            if c.id in category_id_set
        ]
        return categories, entries, profiles

    # ── import (ADR-0055) ───────────────────────────────────────────────────

    def plan_import(
        self,
        categories: list[Category],
        entries: list[CatalogEntry],
        profiles: list[FilterProfile],
    ) -> ImportPlan:
        """Dry-run an import of previously-exported records against this
        service's *current* state — computes what would happen (new vs.
        duplicate word, matched vs. new category) without changing
        anything. A preview UI renders this plan directly, and
        :meth:`apply_import` performs exactly what it describes, so the
        two can never drift apart.

        Archived records in the imported file (only possible from an
        "Everything, include archived" export) are dropped — an import
        never resurrects archived state, matching ADR-0055's decision not
        to give import its own archived-handling option either.
        """
        local_categories_by_name = {
            c.name.casefold(): c for c in self.list_categories(include_archived=False)
        }
        local_profiles_by_name = {
            p.name.casefold(): p for p in self.list_profiles(include_archived=False)
        }

        category_plans = []
        for cat in categories:
            if cat.archived:
                continue
            match = local_categories_by_name.get(cat.name.casefold())
            cat_entries = [
                e for e in entries if e.category_id == cat.id and not e.archived
            ]
            entry_plans = []
            for entry in cat_entries:
                duplicate = (
                    self.find_duplicate_entry(match.id, entry.canonical_phrase)
                    if match is not None
                    else None
                )
                entry_plans.append(
                    ImportEntryPlan(
                        imported_entry_id=entry.id,
                        canonical_phrase=entry.canonical_phrase,
                        notes=entry.notes,
                        mask=entry.mask,
                        is_duplicate=duplicate is not None,
                        existing_entry_id=duplicate.id if duplicate else None,
                    )
                )
            category_plans.append(
                ImportCategoryPlan(
                    imported_category_id=cat.id,
                    name=cat.name,
                    mask_all_terms=cat.mask_all_terms,
                    is_new_category=match is None,
                    existing_category_id=match.id if match else None,
                    entries=tuple(entry_plans),
                )
            )

        profile_plans = []
        for profile in profiles:
            if profile.archived:
                continue
            profile_match = local_profiles_by_name.get(profile.name.casefold())
            profile_plans.append(
                ImportProfilePlan(
                    imported_profile_id=profile.id,
                    name=profile.name,
                    is_duplicate=profile_match is not None,
                    existing_profile_id=profile_match.id if profile_match else None,
                    entry_count=len(profile.entry_ids),
                    attenuation=profile.attenuation,
                )
            )

        return ImportPlan(
            categories=tuple(category_plans),
            profiles=tuple(profile_plans),
            imported_entries=tuple(entries),
            imported_profiles=tuple(profiles),
        )

    def apply_import(
        self, plan: ImportPlan, *, overwrite_duplicates: bool = False
    ) -> ImportSummary:
        """Carry out *plan* exactly as :meth:`plan_import` computed it.

        A duplicate word/profile is skipped by default — never silently
        overwritten (ADR-0055, ADR-0054's own lesson) — unless
        *overwrite_duplicates* is explicitly set, in which case a
        duplicate word's ``notes``/``mask`` and a duplicate profile's
        ``entry_ids``/``attenuation`` are replaced with the imported
        version's.
        """
        new_categories = 0
        new_words = 0
        duplicate_words = 0
        new_profiles = 0
        duplicate_profiles = 0

        entry_id_map: dict[str, str] = {}

        for cat_plan in plan.categories:
            if cat_plan.is_new_category:
                local_category = self.create_category(
                    cat_plan.name, mask_all_terms=cat_plan.mask_all_terms
                )
                new_categories += 1
            else:
                assert cat_plan.existing_category_id is not None
                local_category = self.get_category(cat_plan.existing_category_id)

            for entry_plan in cat_plan.entries:
                if entry_plan.is_duplicate:
                    duplicate_words += 1
                    assert entry_plan.existing_entry_id is not None
                    entry_id_map[entry_plan.imported_entry_id] = (
                        entry_plan.existing_entry_id
                    )
                    if overwrite_duplicates:
                        self.update_entry(
                            entry_plan.existing_entry_id,
                            notes=entry_plan.notes,
                            mask=entry_plan.mask,
                        )
                else:
                    new_words += 1
                    created, _ = self.create_entry(
                        local_category.id,
                        entry_plan.canonical_phrase,
                        notes=entry_plan.notes,
                        mask=entry_plan.mask,
                    )
                    entry_id_map[entry_plan.imported_entry_id] = created.id

        imported_profiles_by_id = {p.id: p for p in plan.imported_profiles}
        for profile_plan in plan.profiles:
            imported_profile = imported_profiles_by_id[profile_plan.imported_profile_id]
            # An id the profile references that this import didn't bring
            # along (e.g. it pointed at an archived entry plan_import()
            # dropped) is skipped rather than raised — the same handling
            # create_snapshot() already gives a stale/missing entry_id.
            remapped_ids = [
                entry_id_map[eid]
                for eid in imported_profile.entry_ids
                if eid in entry_id_map
            ]
            if profile_plan.is_duplicate:
                duplicate_profiles += 1
                if overwrite_duplicates:
                    assert profile_plan.existing_profile_id is not None
                    self.update_profile(
                        profile_plan.existing_profile_id,
                        entry_ids=remapped_ids,
                        attenuation=imported_profile.attenuation,
                    )
            else:
                new_profiles += 1
                self.create_profile(
                    profile_plan.name,
                    entry_ids=remapped_ids,
                    attenuation=imported_profile.attenuation,
                )

        return ImportSummary(
            new_categories=new_categories,
            new_words=new_words,
            duplicate_words=duplicate_words,
            new_profiles=new_profiles,
            duplicate_profiles=duplicate_profiles,
        )
