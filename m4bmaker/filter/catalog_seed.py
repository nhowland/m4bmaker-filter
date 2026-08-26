"""First-run catalog seed data (ADR-0022, item 11 of the 2026-08-26
dry-run review).

The catalog shipped completely empty before this — every User had to
build a "Profanity" category from scratch before Scan could find
anything, which is exactly what produced the zero-hits confusion this
round investigated. This seeds one well-known, common-English profanity
word list into a "Profanity" category on first run only.

Deliberately Profanity-only. A "Slurs" (or any other) category is not
seeded here — picking which specific slurs belong on a shipped default
list is a subjective, context-dependent editorial call this app should
not make unilaterally for every User; that stays entirely User-curated,
same as today.
"""

from __future__ import annotations

from .catalog import CatalogService

#: A common, unremarkable set of everyday English profanity — not
#: exhaustive, just a reasonable starting point a User can add to or
#: prune from the Word Catalog window like any other entry.
PROFANITY_WORDS: tuple[str, ...] = (
    "damn",
    "hell",
    "shit",
    "fuck",
    "bitch",
    "bastard",
    "ass",
    "crap",
    "piss",
    "goddamn",
)


def seed_default_catalog(service: CatalogService) -> None:
    """Populate *service* with the default "Profanity" category and word
    list. Intended to be called once, only on a freshly created (empty)
    :class:`CatalogService` — see :func:`~.catalog_store.load_catalog`'s
    "file doesn't exist yet" branch, the only real caller."""
    category = service.create_category("Profanity")
    for word in PROFANITY_WORDS:
        service.create_entry(category.id, word)
