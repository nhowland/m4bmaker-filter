"""JSON persistence for the catalog (categories, entries, profiles) — PRD
§12.4.

``catalog.py``'s ``CatalogService`` is deliberately storage-agnostic (see
its own module docstring — G2 built the CRUD *rules* against an in-memory
repository so a persistence layer could be swapped in later without
touching that logic). This module is that concrete repository: a JSON
file under the same ``platformdirs`` root ``storage.py`` already uses,
matching the existing ``gui/prefs.py`` pattern rather than adding SQLite
for what is small, low-concurrency data — categories/entries/profiles for
even a very large catalog are at most a few thousand small records, with
no concurrent-writer or transactional-boundary needs the job/chunk store
(ADR-0005) was built to handle.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import storage
from .catalog import CatalogService
from .catalog_seed import seed_default_catalog
from .models import AttenuationSettings, CatalogEntry, Category, FilterProfile

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_FILENAME = "catalog.json"


def catalog_path() -> Path:
    return storage.data_root() / _FILENAME


def save_catalog(service: CatalogService, path: Path | None = None) -> None:
    """Serialize *service*'s full state (including archived items) to
    *path* (default: the standard per-user catalog location), atomically."""
    categories, entries, profiles = service.export_all()
    data: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "categories": [asdict(c) for c in categories],
        "entries": [asdict(e) for e in entries],
        "profiles": [asdict(p) for p in profiles],
    }
    storage.write_json_atomic(path or catalog_path(), data)


def load_catalog(path: Path | None = None) -> CatalogService:
    """Load a :class:`CatalogService` from *path* (default: the standard
    per-user catalog location).

    Returns a fresh service seeded with the default "Profanity" category
    (:func:`~.catalog_seed.seed_default_catalog`, ADR-0022) — never
    raises — if the file doesn't exist yet (first run); a *never-seeded*
    fresh service if it can't be parsed (corruption). A corrupted catalog
    file is logged as a warning rather than surfaced as a crash: losing
    catalog data is bad, but refusing to start the app over it would be
    worse, and this is the same trust level ``gui/prefs.py`` already
    applies to its own JSON file. The seeded first-run service is saved
    immediately, not left to whatever happens to call
    :func:`save_catalog` next — otherwise closing the app without ever
    touching the catalog would silently re-seed (and duplicate) on every
    later launch, since nothing would exist on disk to make this branch
    stop firing.
    """
    target = path or catalog_path()
    if not target.exists():
        service = CatalogService()
        seed_default_catalog(service)
        save_catalog(service, path=target)
        return service

    try:
        data = storage.read_json(target)
        categories = [Category(**c) for c in data.get("categories", [])]
        entries = [CatalogEntry(**e) for e in data.get("entries", [])]
        profiles = []
        for raw_profile in data.get("profiles", []):
            profile_dict = dict(raw_profile)
            profile_dict["attenuation"] = AttenuationSettings(
                **profile_dict["attenuation"]
            )
            profiles.append(FilterProfile(**profile_dict))
    except Exception as exc:  # noqa: BLE001 — any parse/shape failure, not just JSON
        _log.warning("Could not read catalog from %s: %s", target, exc)
        return CatalogService()

    return CatalogService.from_records(categories, entries, profiles)
