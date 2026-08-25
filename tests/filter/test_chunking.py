"""Tests for m4bmaker.filter.chunking (PRD §11.3)."""

from __future__ import annotations

import pytest

from m4bmaker.filter.chunking import (
    PRODUCTION_CHAPTER_CHUNK_MS,
    PRODUCTION_CHAPTERLESS_CHUNK_MS,
    PRODUCTION_OVERLAP_MS,
    ChunkPlan,
    chapter_for_chunk,
    default_chunk_params,
    default_chunk_plan,
    merge_segment_words,
    plan_chunks,
)
from m4bmaker.filter.models import ChapterInfo
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


class TestChapterAwarePlanning:
    def test_no_chapter_data_falls_back_to_uniform(self) -> None:
        with_chapters = plan_chunks(
            100_000, chunk_ms=30_000, overlap_ms=5_000, chapter_start_times_ms=None
        )
        without = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        assert with_chapters == without

    def test_empty_chapter_list_falls_back_to_uniform(self) -> None:
        with_chapters = plan_chunks(
            100_000, chunk_ms=30_000, overlap_ms=5_000, chapter_start_times_ms=[]
        )
        without = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        assert with_chapters == without

    def test_short_chapters_each_become_one_chunk(self) -> None:
        # Four chapters of 10/20/20/10 minutes; chunk_ms budget is 15 minutes.
        chapters = [0, 600_000, 1_800_000, 3_000_000]
        duration = 3_600_000
        plans = plan_chunks(
            duration,
            chunk_ms=900_000,
            overlap_ms=5_000,
            chapter_start_times_ms=chapters,
        )
        # Chapter 1 (10min) and chapter 4 (10min) fit in one chunk each;
        # chapters 2 and 3 (20min each) exceed the 15min budget and split.
        owned_ranges = [(p.owned_start_ms, p.owned_end_ms) for p in plans]
        assert owned_ranges[0] == (0, 600_000)  # chapter 1, whole
        assert owned_ranges[-1] == (3_000_000, 3_600_000)  # chapter 4, whole

    def test_long_chapter_is_subdivided(self) -> None:
        # One 40-minute chapter, 15-minute chunk budget -> must split.
        chapters = [0]
        duration = 2_400_000  # 40 min
        plans = plan_chunks(
            duration,
            chunk_ms=900_000,
            overlap_ms=5_000,
            chapter_start_times_ms=chapters,
        )
        assert len(plans) > 1
        for p in plans:
            assert p.owned_end_ms - p.owned_start_ms <= 900_000

    def test_coverage_is_contiguous_with_no_gaps(self) -> None:
        chapters = [0, 600_000, 1_800_000, 3_000_000]
        duration = 3_600_000
        plans = plan_chunks(
            duration,
            chunk_ms=900_000,
            overlap_ms=5_000,
            chapter_start_times_ms=chapters,
        )
        assert plans[0].owned_start_ms == 0
        assert plans[-1].owned_end_ms == duration
        for i in range(len(plans) - 1):
            assert plans[i].owned_end_ms == plans[i + 1].owned_start_ms

    def test_overlap_still_applied_at_chapter_boundary(self) -> None:
        # Two short chapters, each well under the chunk budget — still
        # expect the first chapter's audio slice to extend past its own
        # owned end by overlap_ms, per the "safety margin, not an
        # assumption" rationale in the module docstring.
        chapters = [0, 300_000]
        duration = 600_000
        plans = plan_chunks(
            duration,
            chunk_ms=900_000,
            overlap_ms=5_000,
            chapter_start_times_ms=chapters,
        )
        assert len(plans) == 2
        assert plans[0].owned_end_ms == 300_000
        assert plans[0].end_ms == 305_000  # padded past the chapter boundary

    def test_leading_span_before_first_chapter_is_not_dropped(self) -> None:
        # First declared chapter starts at 10s, implying a 10s preamble.
        chapters = [10_000, 300_000]
        duration = 600_000
        plans = plan_chunks(
            duration,
            chunk_ms=900_000,
            overlap_ms=5_000,
            chapter_start_times_ms=chapters,
        )
        assert plans[0].owned_start_ms == 0
        assert plans[0].owned_end_ms == 10_000

    def test_out_of_range_and_duplicate_chapter_starts_are_dropped_defensively(
        self,
    ) -> None:
        duration = 600_000
        chapters = [
            0,
            300_000,
            300_000,
            900_000,
            -50,
        ]  # dup + beyond duration + negative
        plans = plan_chunks(
            duration,
            chunk_ms=900_000,
            overlap_ms=5_000,
            chapter_start_times_ms=chapters,
        )
        # Must not raise, and must still cover the full duration with no gaps.
        assert plans[0].owned_start_ms == 0
        assert plans[-1].owned_end_ms == duration
        for i in range(len(plans) - 1):
            assert plans[i].owned_end_ms == plans[i + 1].owned_start_ms

    def test_single_chapter_covering_whole_short_file(self) -> None:
        plans = plan_chunks(
            10_000, chunk_ms=30_000, overlap_ms=5_000, chapter_start_times_ms=[0]
        )
        assert len(plans) == 1
        assert plans[0] == ChunkPlan(
            index=0, start_ms=0, end_ms=10_000, owned_start_ms=0, owned_end_ms=10_000
        )

    def test_all_invalid_chapter_starts_falls_back_to_uniform(self) -> None:
        # Every entry is out of range for this duration.
        with_chapters = plan_chunks(
            100_000,
            chunk_ms=30_000,
            overlap_ms=5_000,
            chapter_start_times_ms=[500_000, -1],
        )
        without = plan_chunks(100_000, chunk_ms=30_000, overlap_ms=5_000)
        assert with_chapters == without


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


def _chapter(index: int, title: str, start_ms: int) -> ChapterInfo:
    return ChapterInfo(index=index, title=title, start_ms=start_ms)


class TestDefaultChunkPlan:
    def test_chapterless_uses_chapterless_production_default(self) -> None:
        plans = default_chunk_plan(duration_ms=3_600_000, chapters=())
        expected = plan_chunks(
            3_600_000, PRODUCTION_CHAPTERLESS_CHUNK_MS, PRODUCTION_OVERLAP_MS
        )
        assert plans == expected

    def test_chaptered_uses_chapter_production_ceiling(self) -> None:
        chapters = (
            _chapter(1, "Chapter 1", 0),
            _chapter(2, "Chapter 2", 1_000_000),
        )
        plans = default_chunk_plan(duration_ms=2_000_000, chapters=chapters)
        expected = plan_chunks(
            2_000_000,
            PRODUCTION_CHAPTER_CHUNK_MS,
            PRODUCTION_OVERLAP_MS,
            [c.start_ms for c in chapters],
        )
        assert plans == expected

    def test_short_chapters_each_become_exactly_one_chunk(self) -> None:
        # Both chapters are far under the 45-minute ceiling, so each is one
        # whole chunk -- the common case chapter_for_chunk relies on.
        chapters = (
            _chapter(1, "Chapter 1", 0),
            _chapter(2, "Chapter 2", 600_000),
        )
        plans = default_chunk_plan(duration_ms=1_200_000, chapters=chapters)
        assert len(plans) == 2

    def test_chapter_over_ceiling_is_subdivided(self) -> None:
        long_chapter_ms = PRODUCTION_CHAPTER_CHUNK_MS * 3
        chapters = (_chapter(1, "Chapter 1", 0),)
        plans = default_chunk_plan(duration_ms=long_chapter_ms, chapters=chapters)
        assert len(plans) > 1


class TestChapterForChunk:
    def test_finds_containing_chapter(self) -> None:
        chapters = (
            _chapter(1, "Chapter 1", 0),
            _chapter(2, "Chapter 2", 600_000),
            _chapter(3, "Chapter 3", 1_200_000),
        )
        plans = default_chunk_plan(duration_ms=1_800_000, chapters=chapters)
        # Each short chapter is exactly one chunk here.
        assert chapter_for_chunk(plans[0], chapters) == chapters[0]
        assert chapter_for_chunk(plans[1], chapters) == chapters[1]
        assert chapter_for_chunk(plans[2], chapters) == chapters[2]

    def test_subdivided_chapter_all_sub_chunks_map_to_same_chapter(self) -> None:
        long_chapter_ms = PRODUCTION_CHAPTER_CHUNK_MS * 3
        chapters = (_chapter(1, "Chapter 1", 0),)
        plans = default_chunk_plan(duration_ms=long_chapter_ms, chapters=chapters)
        assert len(plans) > 1
        for plan in plans:
            assert chapter_for_chunk(plan, chapters) == chapters[0]

    def test_no_chapters_returns_none(self) -> None:
        plans = default_chunk_plan(duration_ms=100_000, chapters=())
        assert chapter_for_chunk(plans[0], ()) is None

    def test_last_chapter_extends_to_duration_end(self) -> None:
        chapters = (
            _chapter(1, "Chapter 1", 0),
            _chapter(2, "Chapter 2", 500_000),
        )
        plans = default_chunk_plan(duration_ms=900_000, chapters=chapters)
        last_plan = plans[-1]
        assert chapter_for_chunk(last_plan, chapters) == chapters[1]


class TestDefaultChunkParams:
    def test_chapterless_returns_chapterless_default(self) -> None:
        assert default_chunk_params(()) == (
            PRODUCTION_CHAPTERLESS_CHUNK_MS,
            PRODUCTION_OVERLAP_MS,
        )

    def test_chaptered_returns_chapter_ceiling(self) -> None:
        chapters = (_chapter(1, "Chapter 1", 0),)
        assert default_chunk_params(chapters) == (
            PRODUCTION_CHAPTER_CHUNK_MS,
            PRODUCTION_OVERLAP_MS,
        )
