"""Scan orchestration: run the Matcher, hold review decisions, build the
review-screen report (PRD §9.4, §17.4 G2).

This module is the ScanJob's business logic. It does not talk to the Job
Orchestrator or any persistence layer — those are later gates; a scan here
lives only as long as the caller holds the :class:`Scan` object.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .interval_planner import build_render_plan
from .matcher import scan_transcript
from .models import (
    AttenuationSettings,
    FilterProfileSnapshot,
    ReviewDecision,
    ReviewStatus,
    ScanHit,
)
from .transcript import Transcript, TranscriptWord


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Scan:
    """One scan run: immutable raw hits plus a mutable, scan-scoped set of
    review decisions (PRD §9.1, §9.4).

    A hit with no explicit decision defaults to
    :attr:`~m4bmaker.filter.models.ReviewStatus.INCLUDED` — PRD §7.2's
    happy path expects the User to review and *exclude* unwanted hits, not
    opt every hit in individually.

    Re-scanning is always a new :class:`Scan` with a new ``id`` — decisions
    are never copied from a prior scan (PRD §15.2 acceptance criterion 8).
    """

    id: str
    transcript_schema_version: int
    source_fingerprint: str
    profile_snapshot: FilterProfileSnapshot
    normalization_version: int
    hits: tuple[ScanHit, ...]
    created_at: str = field(default_factory=_now)
    _decisions: dict[str, ReviewDecision] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._hit_ids = {h.id for h in self.hits}

    def decide(self, hit_id: str, status: ReviewStatus) -> ReviewDecision:
        if hit_id not in self._hit_ids:
            raise KeyError(f"hit_id {hit_id!r} is not part of scan {self.id!r}")
        decision = ReviewDecision(
            hit_id=hit_id, scan_id=self.id, status=status, decided_at=_now()
        )
        self._decisions[hit_id] = decision
        return decision

    def status_for(self, hit_id: str) -> ReviewStatus:
        decision = self._decisions.get(hit_id)
        return decision.status if decision is not None else ReviewStatus.INCLUDED

    def included_hits(self) -> list[ScanHit]:
        return [h for h in self.hits if self.status_for(h.id) == ReviewStatus.INCLUDED]


def run_scan(
    transcript: Transcript,
    profile_snapshot: FilterProfileSnapshot,
    normalization_version: int,
) -> Scan:
    """Run the Matcher against *transcript*/*profile_snapshot* and return a
    fresh :class:`Scan` with no review decisions yet applied."""
    scan_id = _new_id()
    hits = tuple(scan_transcript(transcript, profile_snapshot, scan_id))
    return Scan(
        id=scan_id,
        transcript_schema_version=transcript.schema_version,
        source_fingerprint=transcript.source.fingerprint,
        profile_snapshot=profile_snapshot,
        normalization_version=normalization_version,
        hits=hits,
    )


@dataclass(frozen=True)
class ScanReport:
    """Review-screen summary numbers (PRD §9.4)."""

    total_raw_hits: int
    included_hits: int
    excluded_hits: int
    manual_hits: int
    unique_terms_hit: int
    category_counts: dict[str, int]
    term_counts: dict[str, int]
    total_planned_attenuated_duration_ms: int


def build_report(
    scan: Scan,
    source_duration_ms: int,
    attenuation: AttenuationSettings | None = None,
) -> ScanReport:
    """Build the review-screen report for *scan*.

    ``total_planned_attenuated_duration_ms`` is computed from the actual
    merged :class:`~m4bmaker.filter.models.RenderPlan` for the currently
    included hits (via the Interval Planner), not a naive sum of raw hit
    spans — summing raw spans would double-count overlapping hits and
    ignore padding/merging, which PRD §9.4's "planned" wording implies
    should reflect the real render plan. *attenuation* defaults to the
    snapshot's own settings; overriding it lets a caller preview a
    different padding/merge configuration without re-scanning.
    """
    effective_attenuation = attenuation or scan.profile_snapshot.attenuation

    category_counts: Counter[str] = Counter()
    term_counts: Counter[str] = Counter()
    included = excluded = manual = 0

    for hit in scan.hits:
        status = scan.status_for(hit.id)
        category_counts[hit.category_id] += 1
        term_counts[hit.entry_id] += 1
        if status is ReviewStatus.INCLUDED:
            included += 1
        elif status is ReviewStatus.EXCLUDED:
            excluded += 1
        else:
            manual += 1

    plan = build_render_plan(
        scan.included_hits(), source_duration_ms, effective_attenuation
    )
    total_duration = sum(iv.end_ms - iv.start_ms for iv in plan.intervals)

    return ScanReport(
        total_raw_hits=len(scan.hits),
        included_hits=included,
        excluded_hits=excluded,
        manual_hits=manual,
        unique_terms_hit=len(term_counts),
        category_counts=dict(category_counts),
        term_counts=dict(term_counts),
        total_planned_attenuated_duration_ms=total_duration,
    )


class TranscriptWordIndex:
    """O(1) hit-to-word lookup for PRD §9.4's per-hit context requirement
    ("Up to five recognized words before/after for local context, subject
    to transcript boundaries").

    Built once per transcript, not per hit — a review screen commonly asks
    for context on every visible hit, and a linear scan of
    ``transcript.words()`` per hit would be O(hits × words) against a
    transcript that can run to ~150k words (PRD §16.2's scale note in
    ``matcher.py``).
    """

    def __init__(self, transcript: Transcript) -> None:
        self._words: list[TranscriptWord] = transcript.words()
        self._by_start_ms: dict[int, int] = {
            w.start_ms: i for i, w in enumerate(self._words)
        }
        self._by_end_ms: dict[int, int] = {
            w.end_ms: i for i, w in enumerate(self._words)
        }

    def context(
        self, hit: ScanHit, window: int = 5
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return ``(before, after)`` recognized-word text, each up to
        *window* words, clamped to the transcript's boundaries. Returns two
        empty tuples if *hit* doesn't correspond to this transcript (its
        exact start/end timestamps aren't found) rather than raising —
        context is a display nicety, not something a caller should have to
        guard against for every render.
        """
        start_idx = self._by_start_ms.get(hit.start_ms)
        end_idx = self._by_end_ms.get(hit.end_ms)
        if start_idx is None or end_idx is None:
            return (), ()
        before = tuple(
            w.text for w in self._words[max(0, start_idx - window) : start_idx]
        )
        after = tuple(w.text for w in self._words[end_idx + 1 : end_idx + 1 + window])
        return before, after
