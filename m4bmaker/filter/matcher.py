"""Deterministic exact token/phrase matcher (PRD §9.3, §15.2).

Consumes a completed :class:`~m4bmaker.filter.transcript.Transcript` and a
:class:`~m4bmaker.filter.models.FilterProfileSnapshot` and produces the
immutable raw :class:`~m4bmaker.filter.models.ScanHit` list. Matching is a
pure function of its inputs — no I/O, no persistence — so it is exercised
entirely with in-memory fixtures, matching PRD §16.2's unit-test scope for
"normalization, matching."

**Performance note:** this is a straightforward O(words × distinct phrase
lengths) scan, not a trie/Aho-Corasick construction. For a 20-hour book
(~150k words at typical speech rate) against a catalog with a handful of
distinct phrase lengths, this is expected to be fast relative to the STT
step it follows — but it has not been benchmarked against a reference
fixture yet (no such fixture exists until G3/G4). Flagging here rather than
silently assuming it scales, per PRD §17.1 rule 9 ("report uncertainty").
"""

from __future__ import annotations

from .models import MAX_PHRASE_GAP_MS, FilterProfileSnapshot, ScanHit, SnapshotEntry
from .transcript import Transcript, TranscriptWord


def _phrase_tokens(normalized_phrase: str) -> tuple[str, ...]:
    return tuple(normalized_phrase.split(" ")) if normalized_phrase else ()


def scan_transcript(
    transcript: Transcript,
    snapshot: FilterProfileSnapshot,
    scan_id: str,
) -> list[ScanHit]:
    """Return every exact token/phrase match of *snapshot*'s entries against
    *transcript*'s words, as immutable :class:`ScanHit` records.

    Matching rules (PRD §9.3):

    - A single-word entry matches one recognized word whose ``normalized``
      form equals the entry's normalized phrase (``match_rule="exact_token"``).
    - A multi-word entry matches a contiguous run of recognized words whose
      normalized forms equal the phrase's tokens in order
      (``match_rule="exact_phrase"``), **only** if every adjacent pair in
      that run has a gap (next word's ``start_ms`` minus previous word's
      ``end_ms``) at or below :data:`MAX_PHRASE_GAP_MS`.
    - Overlapping hits from different entries (e.g. a single-word entry and
      a multi-word entry that both start at the same recognized word) are
      **not** deduplicated here — PRD §9.4 requires overlapping hits to
      "remain separately counted in reporting" and merge only at the
      Interval Planner stage.
    - Hit ``confidence`` is the minimum confidence across the matched
      word(s) when every matched word has a confidence value, else
      ``None`` — a conservative choice so a single low-confidence word in a
      phrase can't be hidden behind higher-confidence neighbors.
    """
    words: list[TranscriptWord] = transcript.words()
    entries_by_length: dict[int, list[SnapshotEntry]] = {}
    for entry in snapshot.entries:
        tokens = _phrase_tokens(entry.normalized_phrase)
        if not tokens:
            continue
        entries_by_length.setdefault(len(tokens), []).append(entry)

    hits: list[ScanHit] = []
    hit_counter = 0
    n = len(words)

    for start_i in range(n):
        for length, candidates in entries_by_length.items():
            end_i = start_i + length
            if end_i > n:
                continue
            window = words[start_i:end_i]

            if length > 1:
                gap_ok = all(
                    (window[k + 1].start_ms - window[k].end_ms) <= MAX_PHRASE_GAP_MS
                    for k in range(length - 1)
                )
                if not gap_ok:
                    continue

            window_normalized = tuple(w.normalized for w in window)
            for entry in candidates:
                if window_normalized != _phrase_tokens(entry.normalized_phrase):
                    continue
                hit_counter += 1
                confidences = [w.confidence for w in window if w.confidence is not None]
                confidence = (
                    min(confidences) if len(confidences) == len(window) else None
                )
                hits.append(
                    ScanHit(
                        id=f"{scan_id}-hit-{hit_counter:06d}",
                        scan_id=scan_id,
                        raw_tokens=tuple(w.text for w in window),
                        entry_id=entry.entry_id,
                        category_id=entry.category_id,
                        start_ms=window[0].start_ms,
                        end_ms=window[-1].end_ms,
                        confidence=confidence,
                        match_rule="exact_token" if length == 1 else "exact_phrase",
                    )
                )

    return hits
