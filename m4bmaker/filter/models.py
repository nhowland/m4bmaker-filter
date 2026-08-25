"""Domain schemas for the catalog, profile, scan, and media-manifest entities
defined in PRD §9.1 and §14.4.

G1 scope: schema + validation only (PRD §17.4: "Add versioned domain
schemas/interfaces... without UI coupling"). CRUD services, persistence, and
the matcher/scanner that produce :class:`ScanHit` records are later gates
(G2) and are not implemented here.

Every schema class carries (or is covered by) ``SCHEMA_VERSION`` so a future
migration has something concrete to version against, per PRD §14.4's
requirement that "All schemas must define version... before artifacts are
released."
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

SCHEMA_VERSION = 1

#: Versioned separately from SCHEMA_VERSION per PRD §9.3 ("Normalization
#: must be versioned"): normalization can change independently of the
#: entity schemas that reference a normalized value.
NORMALIZATION_VERSION = 1

#: PRD §9.3: "Phrase tokens may match only when adjacent recognized token
#: gaps are at or below 750 ms." Not user-configurable in MVP.
MAX_PHRASE_GAP_MS = 750


class SchemaValidationError(ValueError):
    """Raised when a domain object fails its own construction-time validation."""


def _check_range(field_name: str, value: float, low: float, high: float) -> None:
    if not (low <= value <= high):
        raise SchemaValidationError(
            f"{field_name}={value!r} is outside the allowed range [{low}, {high}]."
        )


# ── normalization (PRD §9.3) ─────────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")
_APOSTROPHE_VARIANTS = {
    "‘": "'",  # left single quotation mark
    "’": "'",  # right single quotation mark
    "ʼ": "'",  # modifier letter apostrophe
    "`": "'",
}
# Strip everything that is not a word character, whitespace, or apostrophe —
# apostrophes are kept because contractions ("don't") are meaningfully
# different catalog/recognition targets than their stripped form ("dont").
_PUNCT_STRIP_RE = re.compile(r"[^\w\s']", re.UNICODE)


def normalize_token(text: str) -> str:
    """Normalize a single recognized or catalog token per PRD §9.3.

    Applies Unicode NFKC normalization, folds apostrophe variants to a
    single canonical form, case-folds, strips ordinary punctuation (except
    apostrophes), and collapses whitespace runs. The *original* recognized
    text must still be stored and displayed separately (PRD §9.4) — this
    function is for matching only, never for display.
    """
    if not text:
        return ""
    result = unicodedata.normalize("NFKC", text)
    for variant, canonical in _APOSTROPHE_VARIANTS.items():
        result = result.replace(variant, canonical)
    result = result.casefold()
    result = _PUNCT_STRIP_RE.sub("", result)
    result = _WHITESPACE_RE.sub(" ", result).strip()
    return result


def normalize_phrase(text: str) -> str:
    """Normalize a (possibly multi-word) catalog phrase.

    Currently identical to :func:`normalize_token` — phrases are normalized
    as a whole string rather than word-by-word, since inter-word whitespace
    collapsing must happen anyway. Kept as a distinct name because catalog
    phrase normalization and single recognized-token normalization are
    different concerns in the PRD (§9.2 vs §9.3) and may diverge later.
    """
    return normalize_token(text)


# ── catalog (PRD §9.1, §9.2) ──────────────────────────────────────────────────


@dataclass
class Category:
    """A catalog category. See PRD §9.1 table.

    ``mask_all_terms`` is a fork-specific addition beyond PRD §9.1's table
    (review-screen display, not matching/scanning behavior) — see
    ``docs/adr/0011-catalog-masking.md``. It composes with
    :attr:`CatalogEntry.mask` by OR, not override: turning this on masks
    every term in the category in review-screen display regardless of each
    entry's own flag, and a User can still mask one specific entry in an
    otherwise-unmasked category. See :meth:`CatalogService.is_masked`.
    """

    id: str
    name: str
    description: str = ""
    enabled_by_default: bool = True
    mask_all_terms: bool = False
    display_order: int = 0
    revision: int = 1
    archived: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise SchemaValidationError("Category name must not be blank.")


@dataclass
class CatalogEntry:
    """A single catalog term/phrase within a category. See PRD §9.1 table.

    ``mask`` is a fork-specific addition — see
    ``docs/adr/0011-catalog-masking.md`` and :attr:`Category.mask_all_terms`.
    """

    id: str
    category_id: str
    canonical_phrase: str
    enabled: bool = True
    mask: bool = False
    notes: str = ""
    revision: int = 1
    archived: bool = False

    def __post_init__(self) -> None:
        if not self.canonical_phrase or not self.canonical_phrase.strip():
            raise SchemaValidationError("Catalog entry phrase must not be blank.")

    @property
    def normalized_phrase(self) -> str:
        """The phrase as it will actually be matched against (PRD §9.3)."""
        return normalize_phrase(self.canonical_phrase)


# ── attenuation settings (PRD §8.3) ───────────────────────────────────────────


@dataclass(frozen=True)
class AttenuationSettings:
    """Interval-plan and gain-envelope inputs. Defaults and ranges are the
    MVP table in PRD §8.3, enforced at construction time so an out-of-range
    value fails immediately rather than surfacing as a rendering defect."""

    lead_padding_ms: int = 60
    tail_padding_ms: int = 80
    merge_adjacency_ms: int = 20
    fade_in_ms: int = 15
    fade_out_ms: int = 15
    gain_floor_db: float = -80.0

    def __post_init__(self) -> None:
        _check_range("lead_padding_ms", self.lead_padding_ms, 0, 250)
        _check_range("tail_padding_ms", self.tail_padding_ms, 0, 300)
        _check_range("merge_adjacency_ms", self.merge_adjacency_ms, 0, 100)
        _check_range("fade_in_ms", self.fade_in_ms, 5, 50)
        _check_range("fade_out_ms", self.fade_out_ms, 5, 50)
        # PRD table lists the floor as "-60 to -96 dBFS equivalent" (i.e. -60
        # is the *weakest* allowed attenuation and -96 the strongest), so the
        # numeric range check is against the more negative bound as the low
        # end.
        _check_range("gain_floor_db", self.gain_floor_db, -96.0, -60.0)


# ── filter profile (PRD §9.1) ─────────────────────────────────────────────────


@dataclass
class FilterProfile:
    """A reusable, User-editable selection of catalog entries plus settings.
    Mutable — CRUD lives in the G2 Catalog Service, not here. Every scan
    freezes one of these into an immutable :class:`FilterProfileSnapshot`.
    """

    id: str
    name: str
    entry_ids: list[str] = field(default_factory=list)
    attenuation: AttenuationSettings = field(default_factory=AttenuationSettings)
    revision: int = 1
    archived: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise SchemaValidationError("Filter profile name must not be blank.")


@dataclass(frozen=True)
class SnapshotEntry:
    """A catalog entry's data frozen into a profile snapshot at scan time.

    Embedding the resolved phrase/category/revision here (rather than just
    an ``entry_id`` to look up later) is deliberate: a scan must remain
    matchable and reproducible even after the live catalog entry is edited
    or archived (PRD §9.3 requires scans to record "catalog revision IDs"
    precisely so a later edit can never silently change a historical scan's
    meaning — embedding the values, not just the revision number, is what
    actually makes that guarantee hold at match time).
    """

    entry_id: str
    category_id: str
    canonical_phrase: str
    normalized_phrase: str
    revision: int


@dataclass(frozen=True)
class FilterProfileSnapshot:
    """Immutable settings actually used by one scan/render (PRD §14.4)."""

    snapshot_id: str
    profile_id: str
    profile_revision: int
    name: str
    entries: tuple[SnapshotEntry, ...]
    attenuation: AttenuationSettings = field(default_factory=AttenuationSettings)
    created_at: str = ""  # ISO 8601, set by the caller — no wall-clock read here


# ── scan and review (PRD §9.1, §9.4) ──────────────────────────────────────────


class ReviewStatus(Enum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    MANUAL = "manual"


@dataclass(frozen=True)
class ScanHit:
    """One immutable raw match from the Matcher (PRD §9.1, §9.4). Raw hits
    are never edited or deleted — review decisions are recorded separately
    via :class:`ReviewDecision` so historical scans stay reproducible."""

    id: str
    scan_id: str
    raw_tokens: tuple[str, ...]
    entry_id: str
    category_id: str
    start_ms: int
    end_ms: int
    confidence: float | None
    match_rule: str  # e.g. "exact_token" | "exact_phrase"

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            raise SchemaValidationError("ScanHit.start_ms must not be negative.")
        if self.end_ms <= self.start_ms:
            raise SchemaValidationError(
                f"ScanHit.end_ms ({self.end_ms}) must be greater than "
                f"start_ms ({self.start_ms})."
            )


@dataclass
class ReviewDecision:
    """A User's include/exclude/manual decision on one hit, scoped to one
    scan revision (PRD §9.1, §9.4 conflict rules)."""

    hit_id: str
    scan_id: str
    status: ReviewStatus
    decided_at: str = ""  # ISO 8601


# ── media manifest (PRD §6.1, §14.4) ──────────────────────────────────────────


@dataclass(frozen=True)
class AudioTrack:
    """One audio stream found by the Media Inspector."""

    index: int
    codec_name: str | None
    is_default: bool
    channels: int | None
    sample_rate: int | None
    bit_rate: int | None


@dataclass(frozen=True)
class ChapterInfo:
    index: int
    title: str
    start_ms: int


@dataclass
class MediaManifest:
    """Canonical source inspection result (PRD §14.4). Produced fresh by the
    Media Inspector every time — this is a derived/recreatable artifact, not
    something a User edits or that persists as the source of truth."""

    schema_version: int
    source_path: str
    fingerprint: str
    duration_ms: int
    tracks: tuple[AudioTrack, ...]
    selected_track_index: int | None
    selected_track_is_fallback: bool
    chapters: tuple[ChapterInfo, ...]
    required_metadata: dict[str, str]
    cover_present: bool
    eligible: bool
    ineligibility_reasons: tuple[str, ...] = ()


# ── render plan (PRD §8.3, §14.4) ─────────────────────────────────────────────


@dataclass(frozen=True)
class RenderInterval:
    """One merged, padded attenuation interval, with provenance back to every
    raw hit that contributed to it (PRD §8.3 step 6: "Retain a many-to-one
    mapping from each merged interval to raw hit IDs")."""

    start_ms: int
    end_ms: int
    fade_in_ms: int
    fade_out_ms: int
    hit_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.end_ms <= self.start_ms:
            raise SchemaValidationError(
                f"RenderInterval.end_ms ({self.end_ms}) must be greater than "
                f"start_ms ({self.start_ms})."
            )
        if not self.hit_ids:
            raise SchemaValidationError(
                "RenderInterval must carry at least one contributing hit ID."
            )


@dataclass(frozen=True)
class RenderPlan:
    """The Interval Planner's output (PRD §14.4): merged intervals ready for
    the Renderer's gain envelope. Not itself a render — no audio has been
    touched by the existence of a RenderPlan."""

    intervals: tuple[RenderInterval, ...]
    attenuation: AttenuationSettings
    source_duration_ms: int
