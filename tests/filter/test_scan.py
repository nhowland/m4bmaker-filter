"""Tests for m4bmaker.filter.scan — scan orchestration and reporting (PRD §9.4)."""

from __future__ import annotations

import pytest

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.models import ReviewStatus
from m4bmaker.filter.scan import build_report, run_scan
from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)

NORMALIZATION_VERSION = 1


def _word(text: str, start_ms: int, end_ms: int):
    return TranscriptWord(
        text=text, normalized=text.lower(), start_ms=start_ms, end_ms=end_ms
    )


def _transcript(words: list[TranscriptWord]) -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint="sha256:x", duration_ms=60_000, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp", version="v", model="base.en", model_checksum="c"
        ),
        segments=(
            TranscriptSegment(
                id="chunk-0",
                start_ms=0,
                end_ms=60_000,
                status=SegmentStatus.COMPLETED,
                words=tuple(words),
            ),
        ),
    )


@pytest.fixture
def service() -> CatalogService:
    return CatalogService()


def _profile_with_two_terms(service: CatalogService):
    cat = service.create_category("Profanity")
    darn, _ = service.create_entry(cat.id, "darn")
    heck, _ = service.create_entry(cat.id, "heck")
    profile = service.create_profile("Test Profile", entry_ids=[darn.id, heck.id])
    snapshot = service.create_snapshot(profile.id)
    return snapshot, darn, heck


class TestRunScan:
    def test_produces_hits_and_records_provenance(
        self, service: CatalogService
    ) -> None:
        snapshot, darn, _ = _profile_with_two_terms(service)
        transcript = _transcript([_word("darn", 0, 300)])

        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)

        assert len(scan.hits) == 1
        assert scan.hits[0].entry_id == darn.id
        assert scan.source_fingerprint == "sha256:x"
        assert scan.transcript_schema_version == 1
        assert scan.profile_snapshot is snapshot
        assert scan.normalization_version == NORMALIZATION_VERSION

    def test_hits_default_to_included(self, service: CatalogService) -> None:
        snapshot, darn, _ = _profile_with_two_terms(service)
        transcript = _transcript([_word("darn", 0, 300)])
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        assert scan.status_for(scan.hits[0].id) == ReviewStatus.INCLUDED
        assert scan.included_hits() == list(scan.hits)


class TestReviewDecisions:
    def test_exclude_removes_from_included_hits(self, service: CatalogService) -> None:
        snapshot, darn, _ = _profile_with_two_terms(service)
        transcript = _transcript([_word("darn", 0, 300)])
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        hit_id = scan.hits[0].id

        scan.decide(hit_id, ReviewStatus.EXCLUDED)

        assert scan.status_for(hit_id) == ReviewStatus.EXCLUDED
        assert scan.included_hits() == []

    def test_raw_hits_survive_exclusion(self, service: CatalogService) -> None:
        snapshot, darn, _ = _profile_with_two_terms(service)
        transcript = _transcript([_word("darn", 0, 300)])
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        hit_id = scan.hits[0].id
        scan.decide(hit_id, ReviewStatus.EXCLUDED)
        assert len(scan.hits) == 1  # raw scan history preserved

    def test_deciding_unknown_hit_raises(self, service: CatalogService) -> None:
        snapshot, _, _ = _profile_with_two_terms(service)
        transcript = _transcript([])
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        with pytest.raises(KeyError):
            scan.decide("nonexistent-hit", ReviewStatus.EXCLUDED)


class TestRescanCreatesNewRevision:
    def test_rescan_does_not_copy_decisions(self, service: CatalogService) -> None:
        snapshot, darn, _ = _profile_with_two_terms(service)
        transcript = _transcript([_word("darn", 0, 300)])

        scan1 = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        scan1.decide(scan1.hits[0].id, ReviewStatus.EXCLUDED)

        scan2 = run_scan(transcript, snapshot, NORMALIZATION_VERSION)

        assert scan2.id != scan1.id
        # scan2's hit (even though it matches the same word) starts fresh —
        # a new hit ID space entirely, defaulting to INCLUDED.
        assert scan2.status_for(scan2.hits[0].id) == ReviewStatus.INCLUDED


class TestBuildReport:
    def test_counts_match_expectations(self, service: CatalogService) -> None:
        snapshot, darn, heck = _profile_with_two_terms(service)
        transcript = _transcript(
            [
                _word("darn", 0, 300),
                _word("heck", 1000, 1300),
                _word("darn", 2000, 2300),
            ]
        )
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        assert len(scan.hits) == 3

        report = build_report(scan, source_duration_ms=60_000)

        assert report.total_raw_hits == 3
        assert report.included_hits == 3
        assert report.excluded_hits == 0
        assert report.manual_hits == 0
        assert report.unique_terms_hit == 2  # darn, heck
        assert report.category_counts[scan.hits[0].category_id] == 3

    def test_excluded_hit_lowers_included_count_and_duration(
        self, service: CatalogService
    ) -> None:
        snapshot, darn, _ = _profile_with_two_terms(service)
        transcript = _transcript([_word("darn", 0, 300), _word("darn", 2000, 2300)])
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)

        report_before = build_report(scan, source_duration_ms=60_000)
        scan.decide(scan.hits[0].id, ReviewStatus.EXCLUDED)
        report_after = build_report(scan, source_duration_ms=60_000)

        assert report_after.included_hits == report_before.included_hits - 1
        assert report_after.excluded_hits == 1
        assert (
            report_after.total_planned_attenuated_duration_ms
            < report_before.total_planned_attenuated_duration_ms
        )

    def test_duration_reflects_merged_render_plan_not_naive_sum(
        self, service: CatalogService
    ) -> None:
        # Two hits close enough together to merge in the interval planner —
        # naive summing of raw hit spans would double count the overlap.
        snapshot, darn, _ = _profile_with_two_terms(service)
        transcript = _transcript([_word("darn", 1000, 1100), _word("darn", 1120, 1200)])
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)

        report = build_report(scan, source_duration_ms=60_000)

        # Merged into one interval: padded [940, 1280] = 340ms, not two
        # separate padded spans summed (240ms + 160ms = 400ms).
        assert report.total_planned_attenuated_duration_ms == 340

    def test_zero_hits_report(self, service: CatalogService) -> None:
        snapshot, _, _ = _profile_with_two_terms(service)
        transcript = _transcript([])
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        report = build_report(scan, source_duration_ms=60_000)
        assert report.total_raw_hits == 0
        assert report.total_planned_attenuated_duration_ms == 0
