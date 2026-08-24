"""Tests for m4bmaker.filter.interval_planner (PRD §8.3)."""

from __future__ import annotations

from m4bmaker.filter.interval_planner import build_render_plan
from m4bmaker.filter.models import AttenuationSettings, ScanHit


def _hit(hit_id: str, start_ms: int, end_ms: int) -> ScanHit:
    return ScanHit(
        id=hit_id,
        scan_id="scan-1",
        raw_tokens=("x",),
        entry_id="e-1",
        category_id="cat-1",
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=None,
        match_rule="exact_token",
    )


DEFAULT = AttenuationSettings()  # lead=60 tail=80 merge=20 fade_in=15 fade_out=15


class TestSingleHit:
    def test_padding_applied(self) -> None:
        plan = build_render_plan([_hit("h-1", 1000, 1400)], 60_000, DEFAULT)
        assert len(plan.intervals) == 1
        iv = plan.intervals[0]
        assert iv.start_ms == 1000 - 60
        assert iv.end_ms == 1400 + 80
        assert iv.hit_ids == ("h-1",)
        assert iv.fade_in_ms == 15
        assert iv.fade_out_ms == 15

    def test_start_clamped_to_zero(self) -> None:
        plan = build_render_plan([_hit("h-1", 10, 200)], 60_000, DEFAULT)
        assert plan.intervals[0].start_ms == 0

    def test_end_clamped_to_duration(self) -> None:
        plan = build_render_plan([_hit("h-1", 59_950, 59_990)], 60_000, DEFAULT)
        assert plan.intervals[0].end_ms == 60_000

    def test_zero_duration_disables_end_clamp(self) -> None:
        # source_duration_ms == 0 means "unknown" — must not clamp end to 0.
        plan = build_render_plan([_hit("h-1", 1000, 1400)], 0, DEFAULT)
        assert plan.intervals[0].end_ms == 1400 + 80


class TestMerging:
    def test_overlapping_intervals_merge(self) -> None:
        # h-1 padded: [940, 1480]; h-2 padded: [1440, 1880] — overlap.
        hits = [_hit("h-1", 1000, 1400), _hit("h-2", 1500, 1800)]
        plan = build_render_plan(hits, 60_000, DEFAULT)
        assert len(plan.intervals) == 1
        assert plan.intervals[0].hit_ids == ("h-1", "h-2")
        assert plan.intervals[0].start_ms == 940
        assert plan.intervals[0].end_ms == 1880

    def test_near_adjacent_within_merge_threshold_merges(self) -> None:
        # gap between padded intervals must be <= merge_adjacency_ms (20ms).
        hits = [_hit("h-1", 1000, 1100), _hit("h-2", 1260, 1400)]
        # h-1 padded end = 1180; h-2 padded start = 1200 -> gap 20ms, merges.
        plan = build_render_plan(hits, 60_000, DEFAULT)
        assert len(plan.intervals) == 1

    def test_gap_above_merge_threshold_stays_separate(self) -> None:
        hits = [_hit("h-1", 1000, 1100), _hit("h-2", 1300, 1400)]
        # h-1 padded end = 1180; h-2 padded start = 1240 -> gap 60ms > 20ms.
        plan = build_render_plan(hits, 60_000, DEFAULT)
        assert len(plan.intervals) == 2

    def test_three_way_merge_preserves_all_hit_ids(self) -> None:
        hits = [
            _hit("h-1", 1000, 1100),
            _hit("h-2", 1150, 1200),
            _hit("h-3", 1210, 1300),
        ]
        plan = build_render_plan(hits, 60_000, DEFAULT)
        assert len(plan.intervals) == 1
        assert plan.intervals[0].hit_ids == ("h-1", "h-2", "h-3")

    def test_out_of_order_input_still_sorted_and_merged(self) -> None:
        hits = [_hit("h-2", 1500, 1800), _hit("h-1", 1000, 1400)]
        plan = build_render_plan(hits, 60_000, DEFAULT)
        assert len(plan.intervals) == 1
        assert plan.intervals[0].hit_ids == ("h-1", "h-2")


class TestEmptyHits:
    def test_no_hits_returns_no_intervals(self) -> None:
        plan = build_render_plan([], 60_000, DEFAULT)
        assert plan.intervals == ()
        assert plan.source_duration_ms == 60_000
