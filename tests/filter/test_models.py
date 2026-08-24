"""Tests for m4bmaker.filter.models — domain schemas (PRD §9.1, §14.4)."""

from __future__ import annotations

import pytest

from m4bmaker.filter.models import (
    MAX_PHRASE_GAP_MS,
    AttenuationSettings,
    AudioTrack,
    CatalogEntry,
    Category,
    ChapterInfo,
    FilterProfile,
    FilterProfileSnapshot,
    MediaManifest,
    ReviewDecision,
    ReviewStatus,
    ScanHit,
    SchemaValidationError,
    normalize_phrase,
    normalize_token,
)


class TestNormalizeToken:
    def test_casefolds(self) -> None:
        assert normalize_token("DAMN") == "damn"

    def test_strips_ordinary_punctuation(self) -> None:
        assert normalize_token("hell!") == "hell"
        assert normalize_token("wait, what?") == "wait what"

    def test_preserves_apostrophes(self) -> None:
        assert normalize_token("don't") == "don't"

    def test_folds_apostrophe_variants_to_ascii(self) -> None:
        assert normalize_token("don’t") == "don't"
        assert normalize_token("don‘t") == "don't"

    def test_collapses_whitespace_runs(self) -> None:
        assert normalize_token("wait   what") == "wait what"

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert normalize_token("  hell  ") == "hell"

    def test_empty_and_none_return_empty(self) -> None:
        assert normalize_token("") == ""
        assert normalize_token(None) == ""  # type: ignore[arg-type]

    def test_normalize_phrase_is_normalize_token_for_now(self) -> None:
        assert normalize_phrase("Go To Hell!") == normalize_token("Go To Hell!")


class TestCategory:
    def test_valid_category(self) -> None:
        c = Category(id="cat-1", name="Profanity")
        assert c.name == "Profanity"
        assert c.enabled_by_default is True
        assert c.archived is False

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            Category(id="cat-1", name="")

    def test_whitespace_only_name_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            Category(id="cat-1", name="   ")


class TestCatalogEntry:
    def test_valid_entry(self) -> None:
        e = CatalogEntry(id="e-1", category_id="cat-1", canonical_phrase="darn")
        assert e.enabled is True

    def test_blank_phrase_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            CatalogEntry(id="e-1", category_id="cat-1", canonical_phrase="")

    def test_normalized_phrase_property(self) -> None:
        e = CatalogEntry(id="e-1", category_id="cat-1", canonical_phrase="  DARN! ")
        assert e.normalized_phrase == "darn"


class TestAttenuationSettings:
    def test_defaults_match_prd_table(self) -> None:
        s = AttenuationSettings()
        assert s.lead_padding_ms == 60
        assert s.tail_padding_ms == 80
        assert s.merge_adjacency_ms == 20
        assert s.fade_in_ms == 15
        assert s.fade_out_ms == 15
        assert s.gain_floor_db == -80.0

    def test_defaults_are_frozen(self) -> None:
        s = AttenuationSettings()
        with pytest.raises(AttributeError):
            s.lead_padding_ms = 100  # type: ignore[misc]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"lead_padding_ms": -1},
            {"lead_padding_ms": 251},
            {"tail_padding_ms": -1},
            {"tail_padding_ms": 301},
            {"merge_adjacency_ms": -1},
            {"merge_adjacency_ms": 101},
            {"fade_in_ms": 4},
            {"fade_in_ms": 51},
            {"fade_out_ms": 4},
            {"fade_out_ms": 51},
            {"gain_floor_db": -59.9},
            {"gain_floor_db": -96.1},
        ],
    )
    def test_out_of_range_rejected(self, kwargs: dict) -> None:
        with pytest.raises(SchemaValidationError):
            AttenuationSettings(**kwargs)

    def test_range_boundaries_are_inclusive(self) -> None:
        # Must not raise — these are the documented MVP boundary values.
        AttenuationSettings(
            lead_padding_ms=0,
            tail_padding_ms=0,
            merge_adjacency_ms=0,
            fade_in_ms=5,
            fade_out_ms=5,
            gain_floor_db=-60.0,
        )
        AttenuationSettings(
            lead_padding_ms=250,
            tail_padding_ms=300,
            merge_adjacency_ms=100,
            fade_in_ms=50,
            fade_out_ms=50,
            gain_floor_db=-96.0,
        )


class TestFilterProfile:
    def test_blank_name_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            FilterProfile(id="p-1", name="")

    def test_default_attenuation_is_prd_defaults(self) -> None:
        p = FilterProfile(id="p-1", name="Family Friendly")
        assert p.attenuation == AttenuationSettings()


class TestFilterProfileSnapshotIsImmutable:
    def test_frozen(self) -> None:
        snap = FilterProfileSnapshot(
            snapshot_id="snap-1",
            profile_id="p-1",
            profile_revision=1,
            name="Family Friendly",
            entry_ids=("e-1", "e-2"),
        )
        with pytest.raises(AttributeError):
            snap.name = "changed"  # type: ignore[misc]


class TestScanHit:
    def test_valid_hit(self) -> None:
        hit = ScanHit(
            id="h-1",
            scan_id="s-1",
            raw_tokens=("darn",),
            entry_id="e-1",
            category_id="cat-1",
            start_ms=1000,
            end_ms=1400,
            confidence=0.92,
            match_rule="exact_token",
        )
        assert hit.end_ms - hit.start_ms == 400

    def test_negative_start_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            ScanHit(
                id="h-1",
                scan_id="s-1",
                raw_tokens=("darn",),
                entry_id="e-1",
                category_id="cat-1",
                start_ms=-1,
                end_ms=100,
                confidence=None,
                match_rule="exact_token",
            )

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            ScanHit(
                id="h-1",
                scan_id="s-1",
                raw_tokens=("darn",),
                entry_id="e-1",
                category_id="cat-1",
                start_ms=1000,
                end_ms=999,
                confidence=None,
                match_rule="exact_token",
            )

    def test_zero_length_rejected(self) -> None:
        with pytest.raises(SchemaValidationError):
            ScanHit(
                id="h-1",
                scan_id="s-1",
                raw_tokens=("darn",),
                entry_id="e-1",
                category_id="cat-1",
                start_ms=1000,
                end_ms=1000,
                confidence=None,
                match_rule="exact_token",
            )

    def test_frozen(self) -> None:
        hit = ScanHit(
            id="h-1",
            scan_id="s-1",
            raw_tokens=("darn",),
            entry_id="e-1",
            category_id="cat-1",
            start_ms=1000,
            end_ms=1400,
            confidence=None,
            match_rule="exact_token",
        )
        with pytest.raises(AttributeError):
            hit.start_ms = 0  # type: ignore[misc]


class TestReviewDecision:
    def test_status_values(self) -> None:
        d = ReviewDecision(hit_id="h-1", scan_id="s-1", status=ReviewStatus.INCLUDED)
        assert d.status is ReviewStatus.INCLUDED
        assert {s.value for s in ReviewStatus} == {"included", "excluded", "manual"}


class TestMaxPhraseGap:
    def test_matches_prd_value(self) -> None:
        assert MAX_PHRASE_GAP_MS == 750


class TestMediaManifestConstruction:
    def test_eligible_manifest(self) -> None:
        m = MediaManifest(
            schema_version=1,
            source_path="/books/dune.m4b",
            fingerprint="sha256:abc",
            duration_ms=3_600_000,
            tracks=(
                AudioTrack(
                    index=0,
                    codec_name="aac",
                    is_default=True,
                    channels=1,
                    sample_rate=44100,
                    bit_rate=64000,
                ),
            ),
            selected_track_index=0,
            selected_track_is_fallback=False,
            chapters=(ChapterInfo(index=1, title="Chapter 1", start_ms=0),),
            required_metadata={"title": "Dune"},
            cover_present=True,
            eligible=True,
        )
        assert m.eligible
        assert m.ineligibility_reasons == ()

    def test_ineligible_manifest_carries_reasons(self) -> None:
        m = MediaManifest(
            schema_version=1,
            source_path="/books/broken.mp3",
            fingerprint="",
            duration_ms=0,
            tracks=(),
            selected_track_index=None,
            selected_track_is_fallback=False,
            chapters=(),
            required_metadata={},
            cover_present=False,
            eligible=False,
            ineligibility_reasons=("No audio track found.",),
        )
        assert not m.eligible
        assert "No audio track found." in m.ineligibility_reasons
