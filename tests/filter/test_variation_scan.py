"""Tests for m4bmaker.filter.variation_scan (ADR-0036) — finds stemmed-
form and split-token catalog-entry variations in a real transcript that
the exact-match Matcher wouldn't catch on its own."""

from __future__ import annotations

from m4bmaker.filter.models import FilterProfileSnapshot, SnapshotEntry
from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)
from m4bmaker.filter.variation_scan import (
    KIND_SPLIT_TOKEN,
    KIND_STEM_MATCH,
    find_word_variations,
)

_CATEGORY = "cat-profanity"


def _word(text: str, start_ms: int, end_ms: int) -> TranscriptWord:
    return TranscriptWord(
        text=text, normalized=text.lower(), start_ms=start_ms, end_ms=end_ms
    )


def _transcript(words: list[TranscriptWord]) -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint="sha256:x", duration_ms=600_000, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp", version="v", model="base.en", model_checksum="c"
        ),
        segments=(
            TranscriptSegment(
                id="chunk-0",
                start_ms=0,
                end_ms=600_000,
                status=SegmentStatus.COMPLETED,
                words=tuple(words),
            ),
        ),
    )


def _entry(entry_id: str, phrase: str, category: str = _CATEGORY) -> SnapshotEntry:
    return SnapshotEntry(
        entry_id=entry_id,
        category_id=category,
        canonical_phrase=phrase,
        normalized_phrase=phrase.lower(),
        revision=1,
    )


def _snapshot(entries: list[SnapshotEntry]) -> FilterProfileSnapshot:
    return FilterProfileSnapshot(
        snapshot_id="snap-1",
        profile_id="p-1",
        profile_revision=1,
        name="Test Profile",
        entries=tuple(entries),
    )


class TestStemMatch:
    def test_gerund_form_is_suggested(self) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = _snapshot([_entry("e1", "shuck")])
        suggestions = find_word_variations(transcript, snapshot)
        assert len(suggestions) == 1
        assert suggestions[0].surface_text == "shucking"
        assert suggestions[0].kind == KIND_STEM_MATCH
        assert suggestions[0].related_phrase == "shuck"
        assert suggestions[0].related_category_id == _CATEGORY

    def test_exact_match_is_not_suggested_already_caught(self) -> None:
        transcript = _transcript([_word("shuck", 0, 500)])
        snapshot = _snapshot([_entry("e1", "shuck")])
        assert find_word_variations(transcript, snapshot) == []

    def test_unrelated_word_with_wrong_tail_is_not_suggested(self) -> None:
        # "hello" is not "hell" + a recognized suffix (tail is "o").
        transcript = _transcript([_word("hello", 0, 500)])
        snapshot = _snapshot([_entry("e1", "hell")])
        assert find_word_variations(transcript, snapshot) == []

    def test_e_drop_before_ing_is_handled(self) -> None:
        transcript = _transcript([_word("baking", 0, 500)])
        snapshot = _snapshot([_entry("e1", "bake")])
        suggestions = find_word_variations(transcript, snapshot)
        assert len(suggestions) == 1
        assert suggestions[0].surface_text == "baking"

    def test_multiple_suffix_forms_each_suggested(self) -> None:
        transcript = _transcript(
            [
                _word("shucked", 0, 500),
                _word("shucker", 600, 1100),
                _word("shucks", 1200, 1700),
            ]
        )
        snapshot = _snapshot([_entry("e1", "shuck")])
        suggestions = find_word_variations(transcript, snapshot)
        surface_forms = {s.surface_text for s in suggestions}
        assert surface_forms == {"shucked", "shucker", "shucks"}

    def test_occurrences_of_the_same_form_are_counted_not_duplicated(self) -> None:
        transcript = _transcript(
            [
                _word("shucking", 0, 500),
                _word("shucking", 10_000, 10_500),
                _word("shucking", 20_000, 20_500),
            ]
        )
        snapshot = _snapshot([_entry("e1", "shuck")])
        suggestions = find_word_variations(transcript, snapshot)
        assert len(suggestions) == 1
        assert suggestions[0].occurrence_count == 3

    def test_multi_word_catalog_entries_are_not_stem_checked(self) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        snapshot = _snapshot([_entry("e1", "holy shuck")])
        assert find_word_variations(transcript, snapshot) == []


class TestSplitToken:
    def test_adjacent_split_tokens_within_gap_are_suggested(self) -> None:
        transcript = _transcript([_word("sh", 0, 200), _word("uck", 210, 500)])
        snapshot = _snapshot([_entry("e1", "shuck")])
        suggestions = find_word_variations(transcript, snapshot)
        assert len(suggestions) == 1
        assert suggestions[0].surface_text == "sh uck"
        assert suggestions[0].kind == KIND_SPLIT_TOKEN
        assert suggestions[0].related_phrase == "shuck"

    def test_gap_beyond_threshold_is_not_suggested(self) -> None:
        # MAX_PHRASE_GAP_MS is 750ms — 800ms apart must not match.
        transcript = _transcript([_word("sh", 0, 200), _word("uck", 1000, 1300)])
        snapshot = _snapshot([_entry("e1", "shuck")])
        assert find_word_variations(transcript, snapshot) == []

    def test_non_matching_pair_is_not_suggested(self) -> None:
        transcript = _transcript([_word("go", 0, 200), _word("od", 210, 500)])
        snapshot = _snapshot([_entry("e1", "shuck")])
        assert find_word_variations(transcript, snapshot) == []

    def test_three_piece_split_is_suggested(self) -> None:
        transcript = _transcript(
            [_word("sh", 0, 100), _word("u", 110, 200), _word("ck", 210, 500)]
        )
        snapshot = _snapshot([_entry("e1", "shuck")])
        suggestions = find_word_variations(transcript, snapshot)
        assert len(suggestions) == 1
        assert suggestions[0].surface_text == "sh u ck"
        assert suggestions[0].kind == KIND_SPLIT_TOKEN
        assert suggestions[0].related_phrase == "shuck"

    def test_four_piece_split_at_max_pieces_is_suggested(self) -> None:
        # _MAX_SPLIT_PIECES is 4 — a real production case (ADR-0042):
        # "goddamn" tokenized as four separate pieces.
        transcript = _transcript(
            [
                _word("g", 0, 100),
                _word("o", 110, 200),
                _word("d", 210, 300),
                _word("damn", 310, 700),
            ]
        )
        snapshot = _snapshot([_entry("e1", "goddamn")])
        suggestions = find_word_variations(transcript, snapshot)
        assert len(suggestions) == 1
        assert suggestions[0].surface_text == "g o d damn"

    def test_split_beyond_max_pieces_is_not_suggested(self) -> None:
        # Five pieces exceeds _MAX_SPLIT_PIECES (4) — must not match, even
        # though the joined text is exactly the target phrase.
        transcript = _transcript(
            [
                _word("g", 0, 100),
                _word("o", 110, 200),
                _word("d", 210, 300),
                _word("d", 310, 400),
                _word("amn", 410, 700),
            ]
        )
        snapshot = _snapshot([_entry("e1", "goddamn")])
        assert find_word_variations(transcript, snapshot) == []

    def test_gap_partway_through_a_run_stops_the_extension(self) -> None:
        # First two pieces are close together; the third is beyond
        # MAX_PHRASE_GAP_MS (750ms) from the second — the 3-piece
        # extension must not fire, even though 2 pieces alone don't
        # match anything here.
        transcript = _transcript(
            [
                _word("sh", 0, 100),
                _word("u", 110, 200),
                _word("ck", 1200, 1500),
            ]
        )
        snapshot = _snapshot([_entry("e1", "shuck")])
        assert find_word_variations(transcript, snapshot) == []

    def test_covered_word_partway_through_a_run_stops_the_extension(self) -> None:
        # "u" is already an exact Matcher hit on its own (contrived, but
        # exercises the same rule as a real overlapping hit) — a
        # 3-piece "sh"+"u"+"ck" suggestion must not incorporate it.
        transcript = _transcript(
            [
                _word("sh", 0, 100),
                _word("u", 110, 200),
                _word("ck", 210, 500),
            ]
        )
        snapshot = _snapshot([_entry("e1", "shuck"), _entry("e2", "u")])
        suggestions = find_word_variations(transcript, snapshot)
        assert suggestions == []

    def test_near_miss_word_is_not_suggested(self) -> None:
        # Regression guard for the exact risk this feature was scoped to
        # avoid (ADR-0042): a real, unrelated word that merely sounds or
        # looks similar ("folk" is edit-distance 2 from "fuck") must
        # never be suggested — only an *exact* token-concatenation match
        # counts, never fuzzy/edit-distance matching.
        transcript = _transcript([_word("folk", 0, 500)])
        snapshot = _snapshot([_entry("e1", "fuck")])
        assert find_word_variations(transcript, snapshot) == []


class TestAlreadyCoveredExclusion:
    def test_word_already_an_exact_hit_does_not_also_get_suggested(self) -> None:
        # "shuck" alone is already caught by the real Matcher — a
        # stem/split suggestion overlapping it would be pure noise.
        transcript = _transcript([_word("shuck", 0, 500)])
        snapshot = _snapshot([_entry("e1", "shuck")])
        assert find_word_variations(transcript, snapshot) == []

    def test_word_covered_by_a_different_entrys_phrase_hit_is_excluded(self) -> None:
        transcript = _transcript([_word("holy", 0, 400), _word("shucking", 410, 900)])
        snapshot = _snapshot(
            [
                _entry("e1", "shuck"),  # would otherwise stem-match "shucking"
                _entry("e2", "holy shucking"),  # exact phrase hit covers both words
            ]
        )
        # "shucking" is already caught via the "holy shucking" phrase
        # hit, so it must not also surface as a stand-alone stem-match
        # suggestion against the separate "shuck" entry.
        assert find_word_variations(transcript, snapshot) == []


class TestOrderingAndContext:
    def test_sorted_by_occurrence_count_descending(self) -> None:
        transcript = _transcript(
            [
                _word("shucking", 0, 500),
                _word("darned", 1000, 1500),
                _word("darned", 2000, 2500),
                _word("darned", 3000, 3500),
            ]
        )
        snapshot = _snapshot([_entry("e1", "shuck"), _entry("e2", "darn")])
        suggestions = find_word_variations(transcript, snapshot)
        assert [s.surface_text for s in suggestions] == ["darned", "shucking"]
        assert suggestions[0].occurrence_count == 3

    def test_context_includes_surrounding_words(self) -> None:
        transcript = _transcript(
            [
                _word("well", 0, 200),
                _word("i", 210, 300),
                _word("will", 310, 500),
                _word("be", 510, 600),
                _word("shucking", 610, 1100),
                _word("mad", 1110, 1300),
                _word("about", 1310, 1500),
                _word("this", 1510, 1700),
            ]
        )
        snapshot = _snapshot([_entry("e1", "shuck")])
        suggestions = find_word_variations(transcript, snapshot)
        assert suggestions[0].context == "well i will be shucking mad about this"


class TestEmptyInputs:
    def test_no_entries_returns_empty(self) -> None:
        transcript = _transcript([_word("shucking", 0, 500)])
        assert find_word_variations(transcript, _snapshot([])) == []

    def test_no_words_returns_empty(self) -> None:
        snapshot = _snapshot([_entry("e1", "shuck")])
        assert find_word_variations(_transcript([]), snapshot) == []
