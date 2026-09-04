"""Word-variation scanner (ADR-0036).

Finds real transcript words that look like variants of a filter
profile's existing catalog entries but wouldn't be caught by the exact-
match :func:`~m4bmaker.filter.matcher.scan_transcript` on its own —
stemmed forms (``"shuck"`` -> ``"shucking"``) and whisper.cpp
tokenization splits (``"shuck"`` -> adjacent tokens ``"sh"``/``"uck"``,
or more than two pieces — see :data:`_MAX_SPLIT_PIECES`) — for a User to
review and optionally add to the catalog.

**A catalog-curation aid, not a Matcher change.** The Matcher stays
deliberately exact-match-only (no stemming/wildcard support) per an
earlier, explicit decision — this module never touches it. It only
*suggests* literal phrases a User can choose to add, which the existing
exact-match Matcher then catches on a later scan exactly like any other
catalog entry — a split-token suggestion like ``"sh uck"`` becomes an
ordinary multi-word catalog phrase, caught by the Matcher's already-
existing (length-generic) adjacent-word-gap phrase logic, needing no
special case there.

**Performance note**, matching ``matcher.py``'s own disclosed stance: a
straightforward O(words x entries) scan, not indexed — not benchmarked
against a real ~20-hour-book fixture. Flagged here rather than silently
assumed to scale, same as that module's own PRD §17.1 rule 9 note.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from .matcher import scan_transcript
from .models import MAX_PHRASE_GAP_MS, FilterProfileSnapshot, ScanHit, SnapshotEntry
from .transcript import Transcript, TranscriptWord

KIND_STEM_MATCH = "stem_match"
KIND_SPLIT_TOKEN = "split_token"

#: Suffixes checked in :func:`_stem_suffix` — a small, explicit, auditable
#: list rather than a general stemmer (e.g. Porter): predictable behavior
#: matters more than recall for a moderation catalog, where an
#: unexpected auto-suggestion is worse than a missed one. Does not
#: handle consonant doubling (e.g. "run" -> "running") — a known,
#: deliberate scope limit, not an oversight.
_SUFFIXES = ("ing", "ers", "er", "ed", "es", "s")

_CONTEXT_WORDS_BEFORE = 4
_CONTEXT_WORDS_AFTER = 4

#: Maximum consecutive transcript tokens joined together when checking
#: for a whisper.cpp split of one catalog word. Real production splits
#: have run from 2 pieces (the common case, e.g. ``"C"``/``"rap"``) up to
#: 4 (``"G"``/``"odd"``/``"amn"``/``"it"`` for ``"damn"`` inside
#: "Goddamnit" — though even at 4 pieces that specific real case isn't
#: recovered, since whisper's own split there doesn't land on an exact
#: letter boundary of the target word; see docs/adr/0042). Deliberately
#: *exact*-match only, same as the original 2-piece check — no edit-
#: distance/fuzzy matching is added here, since a threshold loose enough
#: to catch a genuine multi-piece split would also start matching
#: unrelated real words (e.g. "folk" sits at edit-distance 2 from
#: "fuck"), the false-positive risk this module's docstring already
#: warns against.
_MAX_SPLIT_PIECES = 4

#: A synthetic scan_id for the internal scan_transcript() probe this
#: module runs to find what the real Matcher already covers — never
#: persisted or shown to the User, so any fixed string is fine.
_PROBE_SCAN_ID = "variation-scan-probe"


@dataclass(frozen=True)
class WordVariationSuggestion:
    """One candidate variation found in a transcript, deduplicated and
    counted across every occurrence (see :func:`find_word_variations`)."""

    surface_text: str
    kind: str  # KIND_STEM_MATCH | KIND_SPLIT_TOKEN
    related_entry_id: str
    related_category_id: str
    related_phrase: str
    occurrence_count: int
    context: str


@dataclass
class _Group:
    kind: str
    entry: SnapshotEntry
    surface_text: str
    context: str
    count: int = 0


def _stem_suffix(base: str, candidate: str) -> str | None:
    """Return the recognized suffix if *candidate* looks like *base* plus
    one of :data:`_SUFFIXES`, else ``None``. Handles the common
    "drop a trailing e before -ing/-ed" case (e.g. "bake" -> "baking")."""
    if candidate == base:
        return None
    if candidate.startswith(base):
        tail = candidate[len(base) :]
    elif base.endswith("e") and candidate.startswith(base[:-1]):
        tail = candidate[len(base) - 1 :]
    else:
        return None
    return tail if tail in _SUFFIXES else None


def _context_snippet(words: list[TranscriptWord], center_index: int) -> str:
    start = max(0, center_index - _CONTEXT_WORDS_BEFORE)
    end = min(len(words), center_index + _CONTEXT_WORDS_AFTER + 1)
    return " ".join(w.text.strip() for w in words[start:end])


def _covered_word_indices(words: list[TranscriptWord], hits: list[ScanHit]) -> set[int]:
    """Indices of every word already inside a real Matcher hit — a
    suggestion overlapping any of these would just be re-suggesting
    something the Matcher already catches.

    Words are time-ordered, and every hit's ``start_ms`` is exactly some
    word's own ``start_ms`` (hits are always built from contiguous word
    windows — see ``matcher.scan_transcript``) — so a binary search
    directly to that word, then walking forward while still inside the
    hit, is exact and avoids an O(hits x words) scan.
    """
    starts = [w.start_ms for w in words]
    covered: set[int] = set()
    for hit in hits:
        idx = bisect.bisect_left(starts, hit.start_ms)
        while idx < len(words) and words[idx].start_ms < hit.end_ms:
            covered.add(idx)
            idx += 1
    return covered


def find_word_variations(
    transcript: Transcript, snapshot: FilterProfileSnapshot
) -> list[WordVariationSuggestion]:
    """Scan *transcript* for stemmed-form and split-token variations of
    *snapshot*'s catalog entries, excluding anything the real Matcher
    would already catch. Returns one entry per distinct surface form,
    sorted by occurrence count descending (ties broken alphabetically) —
    a variation seen many times is a stronger real signal than a
    one-off, the same prioritization named when this feature was scoped.

    Only single-word entries are checked — split-token detection is
    inherently about one target word whisper.cpp broke into
    :data:`_MAX_SPLIT_PIECES` or fewer pieces, and stem-suffix checking
    against an existing multi-word phrase isn't a meaningful operation.
    """
    words = transcript.words()
    real_hits = scan_transcript(transcript, snapshot, scan_id=_PROBE_SCAN_ID)
    covered = _covered_word_indices(words, real_hits)
    single_word_entries = [
        e for e in snapshot.entries if " " not in e.normalized_phrase
    ]

    groups: dict[tuple[str, str, str], _Group] = {}

    def _record(
        key: tuple[str, str, str],
        kind: str,
        entry: SnapshotEntry,
        surface_text: str,
        center_index: int,
    ) -> None:
        group = groups.get(key)
        if group is None:
            groups[key] = _Group(
                kind=kind,
                entry=entry,
                surface_text=surface_text,
                context=_context_snippet(words, center_index),
            )
            group = groups[key]
        group.count += 1

    for i, word in enumerate(words):
        if i in covered:
            continue
        for entry in single_word_entries:
            if _stem_suffix(entry.normalized_phrase, word.normalized) is not None:
                key = (KIND_STEM_MATCH, entry.entry_id, word.normalized)
                _record(key, KIND_STEM_MATCH, entry, word.text.strip(), i)
                break  # first matching entry is enough for this word

    for i in range(len(words)):
        if i in covered:
            continue
        joined = words[i].normalized
        for n in range(2, _MAX_SPLIT_PIECES + 1):
            j = i + n - 1
            if j >= len(words) or j in covered:
                break
            if words[j].start_ms - words[j - 1].end_ms > MAX_PHRASE_GAP_MS:
                break
            joined += words[j].normalized
            for entry in single_word_entries:
                if joined == entry.normalized_phrase:
                    key = (KIND_SPLIT_TOKEN, entry.entry_id, joined)
                    surface_text = " ".join(w.text.strip() for w in words[i : j + 1])
                    _record(key, KIND_SPLIT_TOKEN, entry, surface_text, i)
                    break

    suggestions = [
        WordVariationSuggestion(
            surface_text=g.surface_text,
            kind=g.kind,
            related_entry_id=g.entry.entry_id,
            related_category_id=g.entry.category_id,
            related_phrase=g.entry.canonical_phrase,
            occurrence_count=g.count,
            context=g.context,
        )
        for g in groups.values()
    ]
    suggestions.sort(key=lambda s: (-s.occurrence_count, s.surface_text))
    return suggestions
