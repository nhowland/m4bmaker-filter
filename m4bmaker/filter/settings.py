"""Persistent user settings for the audiobook-filtering feature.

Distinct from ``gui/prefs.py`` — the base app's own shared, lightweight
config (dark mode, update checks) — on purpose: those predate this fork
and stay under the base app's own config root, untouched by it. This
module's settings are scoped entirely to filtering (storage-location
overrides, transcription defaults) and live under this feature's own
``storage.data_root()``, alongside models/transcripts/the catalog
database — not the install directory, not the base app's ``prefs.json``
(mirrors the same boundary ``storage.py``'s own module docstring already
draws for durable feature data vs. scalar preferences).

Same "small JSON file, defaults merged in, I/O errors logged and
swallowed rather than raised" shape as ``gui/prefs.py`` — proven simple
enough there that a second, differently-scoped copy of the same pattern
is preferable to sharing one file across two different lifecycles.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .storage import data_root

_log = logging.getLogger(__name__)

_SETTINGS_FILE = "settings.json"

#: Returned when the file is absent or a key is missing. ``None`` for the
#: four directory overrides means "no override — use the built-in
#: default", not "unset the built-in default to nothing".
_DEFAULTS: dict[str, Any] = {
    "models_dir": None,
    "transcripts_dir": None,
    "output_dir": None,
    "temp_dir": None,
    "preferred_model": None,
}


def _settings_path() -> Path:
    return data_root() / _SETTINGS_FILE


def load() -> dict[str, Any]:
    """Return the stored settings dict, falling back to defaults on any error."""
    path = _settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings file root is not a JSON object")
        return {**_DEFAULTS, **data}
    except FileNotFoundError:
        return dict(_DEFAULTS)
    except Exception as exc:  # corrupt JSON, permission error, etc.
        _log.warning("Could not read settings from %s: %s", path, exc)
        return dict(_DEFAULTS)


def save(settings: dict[str, Any]) -> None:
    """Write *settings* to disk, silently ignoring any I/O errors."""
    path = _settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception as exc:
        _log.warning("Could not write settings to %s: %s", path, exc)


def get(key: str) -> Any:
    """Return a single setting value by key."""
    return load().get(key, _DEFAULTS.get(key))


def set(key: str, value: Any) -> None:
    """Update a single setting key and persist immediately."""
    settings = load()
    settings[key] = value
    save(settings)
