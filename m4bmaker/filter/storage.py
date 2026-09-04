"""Local storage roots and atomic-write helpers for the filtering feature.

Implements the storage-location and atomicity requirements from PRD §12.4
("OS-appropriate per-user application-data locations, not the install
directory") and §12.5 ("Use safe staging files and atomic rename/move").
Mirrors two patterns already proven in the base project: ``gui/prefs.py``'s
use of ``platformdirs`` for a per-user config root, and ``encoder.py``'s
``.partial`` -> ``os.replace`` atomic-write pattern.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir, user_data_dir

_APP_NAME = "m4bmaker"
_FILTER_SUBDIR = "filter"


def data_root() -> Path:
    """Per-user durable root for filtering artifacts: models, transcripts,
    the catalog/profile/job database, and reports (PRD §12.4). Distinct
    from the install directory and from the existing ``prefs.json`` config
    root — this feature's data volume (models, long transcripts) is a
    different lifecycle concern than lightweight scalar preferences."""
    return Path(user_data_dir(_APP_NAME)) / _FILTER_SUBDIR


def cache_root() -> Path:
    """Per-user cache root for recreatable intermediates only (decode
    scratch space, render staging). Safe to delete entirely at any time;
    nothing stored here is User-owned data (PRD §12.4)."""
    return Path(user_cache_dir(_APP_NAME)) / _FILTER_SUBDIR


def models_dir() -> Path:
    """Where downloaded STT models live — a User-configured override
    (``settings.py``'s ``"models_dir"``) if set, else the default under
    :func:`data_root`. Imports ``settings`` locally to avoid a circular
    import (``settings.py`` itself imports :func:`data_root` from here)."""
    from .settings import get as get_setting

    override = get_setting("models_dir")
    return Path(override) if override else data_root() / "models"


def transcripts_dir() -> Path:
    """Where saved transcripts live — same override pattern as
    :func:`models_dir`, via ``settings.py``'s ``"transcripts_dir"``."""
    from .settings import get as get_setting

    override = get_setting("transcripts_dir")
    return Path(override) if override else data_root() / "transcripts"


def temp_root() -> Path:
    """Where large render/transcription scratch files are written — a
    User-configured override (``settings.py``'s ``"temp_dir"``) if set,
    else :func:`cache_root`. Same override pattern as :func:`models_dir` /
    :func:`transcripts_dir`, but falling back to :func:`cache_root` rather
    than a :func:`data_root` subdir, since scratch PCM/WAV files are
    exactly the "recreatable, safe to delete" content cache_root() already
    documents itself as being for."""
    from .settings import get as get_setting

    override = get_setting("temp_dir")
    return Path(override) if override else cache_root()


def output_dir_override() -> Path | None:
    """The User-configured default folder for filtered output
    (``settings.py``'s ``"output_dir"``), or ``None`` if unset — unlike
    :func:`models_dir`/:func:`transcripts_dir`, there is no fixed default
    to fall back to here: an unset override means "next to the source
    file", which only :func:`~m4bmaker.filter.renderer.default_output_path`
    (the one caller) knows how to compute, since it needs the source
    path to do it."""
    from .settings import get as get_setting

    override = get_setting("output_dir")
    return Path(override) if override else None


def reports_dir() -> Path:
    return data_root() / "reports"


def database_path() -> Path:
    """SQLite database path for job/catalog/profile state (PRD §14.3).
    No connection is opened here — this module only defines the path; the
    Job Orchestrator / Catalog Service (later gates) own the connection and
    schema."""
    return data_root() / "filter.db"


def ensure_dirs() -> None:
    """Create every durable storage directory if absent. Idempotent."""
    for d in (
        data_root(),
        cache_root(),
        models_dir(),
        transcripts_dir(),
        temp_root(),
        reports_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, data: Any) -> None:
    """Write *data* as JSON to *path* atomically.

    Stages to a sibling temp file in the same directory — required so the
    final :func:`os.replace` is same-filesystem and therefore atomic — then
    renames onto *path*. On any failure the temp file is removed and *path*
    is left untouched, matching the guarantee ``encoder.py:encode()``
    already makes for the existing conversion output.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    """Read and parse JSON from *path*.

    Raises :class:`FileNotFoundError` / :class:`json.JSONDecodeError`
    directly. Schema/version validation of the parsed content is the
    caller's responsibility (PRD §12.5: "Validate schema/version/size
    before importing JSON artifacts") — this helper only handles the I/O.
    """
    return json.loads(path.read_text(encoding="utf-8"))
