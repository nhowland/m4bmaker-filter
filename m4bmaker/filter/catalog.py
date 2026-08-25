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
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

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
