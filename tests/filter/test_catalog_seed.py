"""Tests for m4bmaker.filter.catalog_seed (ADR-0022)."""

from __future__ import annotations

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_seed import PROFANITY_WORDS, seed_default_catalog


class TestSeedDefaultCatalog:
    def test_creates_one_profanity_category(self) -> None:
        service = CatalogService()
        seed_default_catalog(service)
        categories = service.list_categories()
        assert len(categories) == 1
        assert categories[0].name == "Profanity"

    def test_creates_an_entry_for_every_word(self) -> None:
        service = CatalogService()
        seed_default_catalog(service)
        category = service.list_categories()[0]
        entries = service.list_entries(category.id)
        assert len(entries) == len(PROFANITY_WORDS)

    def test_does_not_seed_a_slurs_or_other_sensitive_category(self) -> None:
        """ADR-0022 D2: Profanity only — picking a default slur list is a
        subjective editorial call this app shouldn't make unilaterally."""
        service = CatalogService()
        seed_default_catalog(service)
        names = {c.name for c in service.list_categories()}
        assert names == {"Profanity"}


class TestProfanityWordsList:
    def test_no_duplicate_words(self) -> None:
        """create_entry() allows duplicates rather than rejecting them
        (PRD §9.2 — a caller decides what to do with the warning) — the
        seed list itself must not rely on that to avoid double entries."""
        assert len(PROFANITY_WORDS) == len(set(PROFANITY_WORDS))

    def test_no_blank_or_whitespace_only_words(self) -> None:
        assert all(word.strip() for word in PROFANITY_WORDS)
