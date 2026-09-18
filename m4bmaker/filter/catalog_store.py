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
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import storage
from .catalog import CatalogService
from .catalog_seed import seed_default_catalog
from .models import (
    AttenuationSettings,
    CatalogEntry,
    Category,
    FilterProfile,
    SchemaValidationError,
)

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_FILENAME = "catalog.json"

#: Versioned independently of SCHEMA_VERSION (ADR-0055) — a standalone
#: export/import file is a different artifact from the whole-catalog
#: repository format above, and needs to be recognizable/rejectable on its
#: own terms (PRD §12.5: "Validate schema/version/size before importing
#: JSON artifacts").
EXPORT_SCHEMA_VERSION = 1

#: Generous ceiling for a Word List export -- a catalog of even a few
#: thousand entries serializes to a few hundred KB; anything past this is
#: not a file this import flow should trust (PRD §12.5's "size" check).
_MAX_IMPORT_SIZE_BYTES = 10 * 1024 * 1024


class CatalogImportError(ValueError):
    """Raised when a file picked for import isn't a valid, readable Word
    List export. The GUI catches this to show a plain error dialog naming
    the problem, rather than crash or partially apply anything
    (ADR-0055)."""


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


def _timestamped_backup(target: Path, tag: str) -> Path:
    """Copy *target* (not move — it stays in place) to a timestamped
    sibling named ``<name>.<tag>-<UTC timestamp>.bak``. Shared by the
    unreadable-file recovery path below (ADR-0054) and
    :func:`backup_before_import` (ADR-0055) — both are "a risky bulk
    write is about to happen or just did, keep forensic evidence on disk
    regardless," just at different moments."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = target.with_name(f"{target.name}.{tag}-{stamp}.bak")
    shutil.copy2(target, backup_path)
    return backup_path


def _backup_unreadable_catalog(target: Path) -> Path:
    """Copy *target* to a timestamped sibling before :func:`load_catalog`
    returns a fresh fallback service for it, so a genuine parse failure
    always leaves forensic evidence on disk rather than only a log line
    nobody sees before something later overwrites *target* for real."""
    return _timestamped_backup(target, "unreadable")


def backup_before_import(path: Path | None = None) -> Path | None:
    """Snapshot the current catalog file immediately before an import's
    merged result overwrites it, so an import a Contributor regrets is
    still recoverable from disk — not just trusted to the merge logic
    getting every case right (ADR-0055, generalizing ADR-0054's
    unreadable-file backup to any risky bulk write). Returns ``None``
    (nothing to back up) if the catalog file doesn't exist yet — a
    first-run import has nothing to protect."""
    target = path or catalog_path()
    if not target.exists():
        return None
    return _timestamped_backup(target, "pre-import")


def load_catalog(
    path: Path | None = None,
    *,
    on_recovery: Callable[[Path], None] | None = None,
) -> CatalogService:
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

    A real, hand-curated catalog accumulates months of manually-reviewed
    word-list work — silently discarding it on a parse failure with
    nothing but a log line is not an acceptable failure mode on its own
    (see the real incident recorded in FORK.md / this ADR). If *target*
    exists but can't be parsed, its current bytes are copied to a
    timestamped ``<name>.unreadable-<UTC timestamp>.bak`` sibling
    *before* this function returns the fallback service, so the
    original is always recoverable even after something later calls
    :func:`save_catalog` and overwrites *target* itself. *on_recovery*,
    if given, is called with that backup path — the one case it's ever
    invoked is exactly the one case a User needs to be told about
    (``gui/window.py``'s ``_show_catalog_window`` passes a callback that
    shows a real warning dialog; existing/non-GUI callers are unaffected
    by leaving this at its default).
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
        backup_path = _backup_unreadable_catalog(target)
        _log.warning(
            "Could not read catalog from %s: %s -- backed up to %s",
            target,
            exc,
            backup_path,
        )
        if on_recovery is not None:
            on_recovery(backup_path)
        return CatalogService()

    return CatalogService.from_records(categories, entries, profiles)


# ── standalone export/import files (ADR-0055) ────────────────────────────


def write_export_file(
    categories: list[Category],
    entries: list[CatalogEntry],
    profiles: list[FilterProfile],
    path: Path,
) -> None:
    """Write *categories*/*entries*/*profiles* (already resolved to the
    chosen export scope by ``CatalogService.export_everything``/
    ``export_categories``/``export_profiles``) as a standalone,
    shareable export file — PRD §5.2/§9.2.

    Every id in the file stays exactly as it was locally — a profile's
    ``entry_ids`` need its own entries' ids to resolve *within this one
    file*. That's harmless on the far end: import never reuses an
    imported id as a real local id (``CatalogService.apply_import``
    always mints a fresh one via ``create_category``/``create_entry``/
    ``create_profile``), so an id colliding with an unrelated local
    record is not a real hazard — only ever used as a same-file
    correlation token, then discarded.
    """
    data: dict[str, Any] = {
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "categories": [asdict(c) for c in categories],
        "entries": [asdict(e) for e in entries],
        "profiles": [asdict(p) for p in profiles],
    }
    storage.write_json_atomic(path, data)


def read_export_file(
    path: Path,
) -> tuple[list[Category], list[CatalogEntry], list[FilterProfile]]:
    """Read and validate a standalone export file written by
    :func:`write_export_file`, for :meth:`CatalogService.plan_import`.

    Raises :class:`CatalogImportError` — never a bare parse exception —
    for anything that makes the file untrustworthy to import: too large,
    not valid JSON, missing/incompatible ``exportSchemaVersion``, or a
    shape that doesn't match the expected records (PRD §12.5: "Treat...
    catalog imports... as untrusted inputs" / "Validate schema/version/
    size before importing"). Nothing is changed in any service by this
    call — it only parses and validates.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CatalogImportError(f"Can't read {path.name}: {exc}") from exc
    if size > _MAX_IMPORT_SIZE_BYTES:
        raise CatalogImportError(
            f"{path.name} is {size:,} bytes, larger than the "
            f"{_MAX_IMPORT_SIZE_BYTES:,} byte limit for a Word List export."
        )

    try:
        data = storage.read_json(path)
    except (OSError, ValueError) as exc:
        raise CatalogImportError(f"{path.name} isn't valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "exportSchemaVersion" not in data:
        raise CatalogImportError(
            f"{path.name} isn't a recognized Word List export — no "
            "exportSchemaVersion field was found."
        )
    version = data["exportSchemaVersion"]
    if version != EXPORT_SCHEMA_VERSION:
        raise CatalogImportError(
            f"{path.name} was exported with a format this version of the "
            f"app doesn't recognize (exportSchemaVersion {version!r})."
        )

    try:
        categories = [Category(**c) for c in data.get("categories", [])]
        entries = [CatalogEntry(**e) for e in data.get("entries", [])]
        profiles = []
        for raw_profile in data.get("profiles", []):
            profile_dict = dict(raw_profile)
            profile_dict["attenuation"] = AttenuationSettings(
                **profile_dict["attenuation"]
            )
            profiles.append(FilterProfile(**profile_dict))
    except (TypeError, SchemaValidationError) as exc:
        raise CatalogImportError(
            f"{path.name}'s contents don't match the expected Word List "
            f"export shape: {exc}"
        ) from exc

    return categories, entries, profiles
