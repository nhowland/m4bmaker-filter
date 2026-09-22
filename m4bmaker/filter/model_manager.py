"""Model Manager: known-model catalog, checksum-verified download, atomic
staging, and local install state (PRD §10.1, §12.4, D-11, D-13, D-14).

Downloads use only :mod:`urllib.request` from the standard library — no new
HTTP dependency (no ``requests``), per PRD §17.1 rule 5 ("no unapproved
dependencies") and the base project's existing minimal-dependency posture.

**Checksum provenance (see ``docs/adr/0001-stt-engine-integration.md``'s G3
spike findings):** whisper.cpp's own model-download script does no
checksum verification at all, so ``KNOWN_MODELS`` below carries SHA-256
values computed by *this project* directly from the downloaded files during
the G3 spike (2026-08-24) — not values whisper.cpp or Hugging Face publish.
If the upstream files ever change, these pinned values will correctly
start rejecting the new download as a checksum mismatch; updating them is
a deliberate, reviewed action, not automatic.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MiB read/hash/write chunks while streaming


@dataclass(frozen=True)
class ModelSpec:
    """One entry in the known-model catalog (PRD §10.1 table)."""

    name: str  # e.g. "base.en" — matches whisper.cpp's own model naming
    label: str  # user-facing label, e.g. "Recommended"
    url: str
    sha256: str
    size_bytes: int

    def filename(self) -> str:
        return f"ggml-{self.name}.bin"


#: PRD §10.1: base.en is the default recommended model; small.en is the
#: optional higher-recognition-effort download. Real sizes/checksums
#: captured directly from https://huggingface.co/ggerganov/whisper.cpp
#: during the G3 spike (2026-08-24) — both files were downloaded, run
#: successfully against a real audio fixture, and hashed locally.
KNOWN_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="base.en",
        label="Recommended",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp"
            "/resolve/main/ggml-base.en.bin"
        ),
        sha256="a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
        size_bytes=147_964_211,
    ),
    ModelSpec(
        name="small.en",
        label="Higher recognition effort",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp"
            "/resolve/main/ggml-small.en.bin"
        ),
        sha256="c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
        size_bytes=487_614_201,
    ),
)


def get_model_spec(name: str) -> ModelSpec:
    for spec in KNOWN_MODELS:
        if spec.name == name:
            return spec
    raise KeyError(f"Unknown model name: {name!r}")


class ModelDownloadError(Exception):
    """Raised on a network/HTTP failure during download."""


class ModelChecksumMismatchError(Exception):
    """Raised when a downloaded (or previously installed) file's SHA-256
    does not match the pinned value in its :class:`ModelSpec`."""

    def __init__(self, spec: ModelSpec, actual_sha256: str) -> None:
        self.spec = spec
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"Checksum mismatch for {spec.name}: expected {spec.sha256}, "
            f"got {actual_sha256}."
        )


class ModelDownloadCancelled(Exception):
    """Raised when *cancel_event* fires during a download."""


def model_path(spec: ModelSpec, dest_dir: Path) -> Path:
    return dest_dir / spec.filename()


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def is_installed(spec: ModelSpec, dest_dir: Path) -> bool:
    """Cheap presence check: file exists and its size matches the pinned
    spec. Does **not** recompute the checksum — use :func:`verify_installed`
    when corruption is a real possibility (e.g. resuming after a crash,
    PRD §11.3's "Verify... model checksum... before resume"), since hashing
    a multi-hundred-megabyte file on every UI refresh would be wasteful.
    """
    path = model_path(spec, dest_dir)
    return path.is_file() and path.stat().st_size == spec.size_bytes


def verify_installed(spec: ModelSpec, dest_dir: Path) -> bool:
    """Recompute the installed file's SHA-256 and compare against the
    pinned value. Returns ``False`` if the file is absent or corrupt."""
    path = model_path(spec, dest_dir)
    if not path.is_file():
        return False
    return _sha256_of_file(path) == spec.sha256


def list_installed(dest_dir: Path) -> list[ModelSpec]:
    return [spec for spec in KNOWN_MODELS if is_installed(spec, dest_dir)]


def remove_model(spec: ModelSpec, dest_dir: Path) -> None:
    """Delete the installed model file. Idempotent — no error if absent."""
    model_path(spec, dest_dir).unlink(missing_ok=True)


def download_model(
    spec: ModelSpec,
    dest_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_event: "threading.Event | None" = None,
) -> Path:
    """Download *spec* into *dest_dir*, verify its checksum, and atomically
    install it. Returns the final installed path.

    Requires HTTPS (PRD §10.1). Streams to a sibling ``.partial`` file in
    *dest_dir* while computing SHA-256 incrementally (one read pass, not a
    separate hashing pass afterward — matters for files this size). On
    checksum mismatch, network failure, or cancellation, the partial file
    is removed and no file is left at the final path; a pre-existing good
    install at the final path is untouched until the new download has
    fully verified, matching the atomic-write pattern already used
    elsewhere in this codebase (``encoder.py``'s ``.partial`` ->
    ``os.replace``, ``storage.write_json_atomic``).

    *progress_callback*, if given, is called as ``(downloaded_bytes,
    total_bytes)`` after each chunk. *total_bytes* is ``spec.size_bytes``
    (known up front from the pinned catalog), not read from the server —
    PRD §11.5 distinguishes determinate progress (known total) from
    indeterminate; this is always determinate.
    """
    if not spec.url.startswith("https://"):
        raise ValueError(f"Model URL must use HTTPS: {spec.url!r}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = model_path(spec, dest_dir)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{spec.filename()}.", suffix=".partial", dir=str(dest_dir)
    )
    tmp_path = Path(tmp_name)

    digest = hashlib.sha256()
    downloaded = 0

    try:
        with os.fdopen(fd, "wb") as out_file:
            try:
                response = urllib.request.urlopen(spec.url)  # noqa: S310
            except OSError as exc:
                raise ModelDownloadError(
                    f"Failed to start download for {spec.name}: {exc}"
                ) from exc
            with response:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ModelDownloadCancelled(
                            f"Download of {spec.name} was cancelled."
                        )
                    try:
                        chunk = response.read(_CHUNK_SIZE)
                    except OSError as exc:
                        raise ModelDownloadError(
                            f"Download of {spec.name} failed: {exc}"
                        ) from exc
                    if not chunk:
                        break
                    out_file.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, spec.size_bytes)

        actual = digest.hexdigest()
        if actual != spec.sha256:
            raise ModelChecksumMismatchError(spec, actual)

        os.replace(tmp_path, final_path)
        return final_path
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
