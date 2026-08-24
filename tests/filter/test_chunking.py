"""Tests for m4bmaker.filter.chunking (PRD §11.3)."""

from __future__ import annotations

import pytest

from m4bmaker.filter.chunking import ChunkPlan, merge_segment_words, plan_chunks
from m4bmaker.filter.transcript import SegmentStatus, TranscriptSegment, TranscriptWord


def _word(text: str, start_ms: int, end_ms: int) -> TranscriptWord:
    return TranscriptWord(
        text=text, normalized=text.lower(), start_ms=start_ms, end_ms=end_ms
    )


class TestPlanChunksBasics:
    def test_zero_duration_returns_no_chunks(self) -> None:
        assert plan_chunks(0, chunk_ms=30_000, overlap_ms=5_000) == []

    def test_overlap_equal_to_chunk_raises(self) -> None:
        with pytest.raises(ValueError):
            plan_chunks(60_000, chunk_ms=30_000, overlap_ms=30_000)

    def test_overlap_larger_than_chunk_raises(self) -> None:
        with pytest.raises(ValueError):
            plan_chunks(60_000, chunk_ms=30_000, overlap_ms=40_000)

    def test_single_chunk_when_duration_fits(self) -> None:
        plans = plan_chunks(10_000, chunk_ms=30_000, overlap_ms=5_000)
        assert len(plans) == 1
        assert plans[0] == ChunkPlan(
            index=0, start_ms=0, end_ms=10_000, owned_start_ms=0, owned_end_ms=10_000
        )


class TestPlanChunksCoverage:
    def test_ownership_ranges_are_contiguous_with_no_gaps_or_overlap(self) -> None:
        plans = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        for i in range(len(plans) - 1):
            assert plans[i].owned_end_ms == plans[i + 1].owned_start_ms

    def test_first_chunk_starts_at_zero(self) -> None:
        plans = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        assert plans[0].owned_start_ms == 0

    def test_last_chunk_owns_up_to_full_duration(self) -> None:
        plans = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        assert plans[-1].owned_end_ms == 100_000
        assert plans[-1].end_ms == 100_000

    def test_consecutive_audio_slices_overlap_by_exactly_overlap_ms(self) -> None:
        plans = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        # chunk 0: [0, 30000); chunk 1 starts at step=25000, so audio slices
        # [0,30000) and [25000,55000) overlap by exactly 5000ms.
        assert plans[1].start_ms == plans[0].end_ms - 5_000

    def test_step_matches_chunk_minus_overlap(self) -> None:
        plans = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        assert plans[1].start_ms - plans[0].start_ms == 25_000

    def test_indices_are_sequential(self) -> None:
        plans = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        assert [p.index for p in plans] == list(range(len(plans)))

    def test_exact_multiple_of_step_does_not_produce_empty_trailing_chunk(self) -> None:
        # duration exactly divisible by step=25000 -> must not emit a
        # zero-length trailing chunk.
        plans = plan_chunks(75_000, chunk_ms=30_000, overlap_ms=5_000)
        for p in plans:
            assert p.end_ms > p.start_ms
            assert (
                p.owned_end_ms > p.owned_start_ms or p.owned_end_ms == p.owned_start_ms
            )
        assert plans[-1].end_ms == 75_000


class TestMergeSegmentWords:
    def _segment(self, seg_id: str, words: list[TranscriptWord]) -> TranscriptSegment:
        return TranscriptSegment(
            id=seg_id,
            start_ms=0,
            end_ms=100_000,
            status=SegmentStatus.COMPLETED,
            words=tuple(words),
        )

    def test_single_chunk_keeps_all_words(self) -> None:
        plan = ChunkPlan(
            index=0, start_ms=0, end_ms=10_000, owned_start_ms=0, owned_end_ms=10_000
        )
        seg = self._segment("c0", [_word("hello", 100, 400), _word("world", 500, 800)])
        merged = merge_segment_words([(seg, plan)])
        assert [w.text for w in merged] == ["hello", "world"]

    def test_overlap_region_prefers_next_chunk(self) -> None:
        # chunk 0 owns [0, 25000); chunk 1 owns [25000, 55000). A word that
        # appears in BOTH chunks' raw transcription at ts=26000 must only
        # survive from chunk 1 (the owner of that timestamp).
        plan0 = ChunkPlan(
            index=0, start_ms=0, end_ms=30_000, owned_start_ms=0, owned_end_ms=25_000
        )
        plan1 = ChunkPlan(
            index=1,
            start_ms=25_000,
            end_ms=55_000,
            owned_start_ms=25_000,
            owned_end_ms=55_000,
        )
        seg0 = self._segment(
            "c0", [_word("boundary", 26_000, 26_400)]
        )  # in overlap, not owned
        seg1 = self._segment(
            "c1", [_word("boundary", 26_000, 26_400)]
        )  # in overlap, owned by c1
        merged = merge_segment_words([(seg0, plan0), (seg1, plan1)])
        assert len(merged) == 1  # not duplicated
        assert merged[0].start_ms == 26_000

    def test_word_before_owned_range_is_dropped(self) -> None:
        plan = ChunkPlan(
            index=1,
            start_ms=20_000,
            end_ms=50_000,
            owned_start_ms=25_000,
            owned_end_ms=55_000,
        )
        seg = self._segment("c1", [_word("early", 21_000, 21_400)])
        merged = merge_segment_words([(seg, plan)])
        assert merged == []

    def test_result_is_sorted_by_start_ms(self) -> None:
        plan0 = ChunkPlan(
            index=0, start_ms=0, end_ms=30_000, owned_start_ms=0, owned_end_ms=25_000
        )
        plan1 = ChunkPlan(
            index=1,
            start_ms=25_000,
            end_ms=55_000,
            owned_start_ms=25_000,
            owned_end_ms=55_000,
        )
        seg0 = self._segment("c0", [_word("first", 1_000, 1_400)])
        seg1 = self._segment("c1", [_word("second", 30_000, 30_400)])
        # pass out of natural order to prove sorting, not incidental order
        merged = merge_segment_words([(seg1, plan1), (seg0, plan0)])
        assert [w.text for w in merged] == ["first", "second"]

    def test_full_plan_and_merge_round_trip_recovers_all_words_once(self) -> None:
        # duration=50000 with chunk=30000/overlap=5000 (step=25000) lands
        # exactly on 2 chunks: [0,30000) owning [0,25000), and
        # [25000,50000) owning [25000,50000) as the last chunk.
        plans = plan_chunks(50_000, chunk_ms=30_000, overlap_ms=5_000)
        assert len(plans) == 2
        # Simulate each chunk "hearing" the full overlap region redundantly,
        # as real STT re-transcribing overlapping audio would.
        seg0 = self._segment(
            "c0",
            [
                _word("a", 1_000, 1_400),
                _word("boundary", 26_000, 26_400),  # in chunk0's overlap tail
            ],
        )
        seg1 = self._segment(
            "c1",
            [
                _word("boundary", 26_000, 26_400),  # same word, re-heard by chunk1
                _word("b", 40_000, 40_400),
            ],
        )
        merged = merge_segment_words([(seg0, plans[0]), (seg1, plans[1])])
        assert [w.text for w in merged] == ["a", "boundary", "b"]
