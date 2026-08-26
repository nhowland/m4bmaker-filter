"""Tests for m4bmaker.gui.filter.wizard.review_step.ReviewStep (PRD §7.2
stage 6, §9.4; ADR-0010).

Uses real backend objects throughout — a real CatalogService, a real
Transcript, and a real Scan produced by the real matcher
(m4bmaker.filter.matcher.scan_transcript via scan.run_scan) — not a
hand-built fake ScanHit list, so these tests exercise the actual
integration the wizard will run in production, not just this widget's
own rendering logic in isolation.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.models import AttenuationSettings, ReviewStatus
from m4bmaker.filter.scan import run_scan
from m4bmaker.filter.transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)
from m4bmaker.gui.filter.wizard.review_step import (
    _COL_CONFIDENCE,
    _COL_CONTEXT,
    _COL_INCLUDED,
    _COL_TERM,
    _COL_TIME,
    ReviewStep,
    _mask_term,
)

pytestmark = pytest.mark.usefixtures("qapp")

NORMALIZATION_VERSION = 1


def _item(table: QTableWidget, row: int, col: int) -> QTableWidgetItem:
    cell = table.item(row, col)
    assert cell is not None
    return cell


def _word(
    text: str, start_ms: int, end_ms: int, confidence: float | None = None
) -> TranscriptWord:
    return TranscriptWord(
        text=text,
        normalized=text.lower(),
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=confidence,
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


@pytest.fixture()
def service() -> CatalogService:
    return CatalogService()


@pytest.fixture()
def step() -> ReviewStep:
    return ReviewStep()


class _Fixture:
    """Bundles a real Scan + its source Transcript/CatalogService so tests
    can both load a step with it and independently assert against it."""

    def __init__(self, service: CatalogService) -> None:
        cat_profanity = service.create_category("Profanity")
        cat_slurs = service.create_category("Slurs", mask_all_terms=True)
        self.darn, _ = service.create_entry(cat_profanity.id, "darn")
        self.heck, _ = service.create_entry(cat_profanity.id, "heck")
        self.slur, _ = service.create_entry(cat_slurs.id, "badword")
        profile = service.create_profile(
            "Test",
            entry_ids=[self.darn.id, self.heck.id, self.slur.id],
            # Deliberately small, explicit padding -- this fixture's whole
            # point is exercising *distinct, non-merging* intervals per
            # hit; coupling that to whatever the app's own production
            # default happens to be would silently break this fixture's
            # design intent every time that default changes for unrelated
            # reasons (as it did in ADR-0023).
            attenuation=AttenuationSettings(lead_padding_ms=60, tail_padding_ms=80),
        )
        self.snapshot = service.create_snapshot(profile.id)
        self.service = service

        self.words = [
            _word("and", 0, 100),
            _word("then", 100, 200),
            _word("he", 200, 300),
            _word("said", 300, 400),
            _word("darn", 500, 600, confidence=0.9),
            _word("it", 700, 800),
            _word("was", 800, 900),
            _word("heck", 1000, 1100, confidence=None),
            _word("no", 1200, 1300),
            _word("badword", 1500, 1600, confidence=0.5),
            _word("really", 1700, 1800),
        ]
        self.transcript = _transcript(self.words)
        self.scan = run_scan(self.transcript, self.snapshot, NORMALIZATION_VERSION)

    def load(self, step: ReviewStep) -> None:
        step.set_scan(
            self.scan, self.service, self.transcript, source_duration_ms=60_000
        )


@pytest.fixture()
def fixture(service: CatalogService) -> _Fixture:
    return _Fixture(service)


class TestEmptyState:
    def test_renders_without_error_before_any_scan_is_set(
        self, step: ReviewStep
    ) -> None:
        assert step is not None
        assert step._table.rowCount() == 0

    def test_stat_strip_shows_zeros(self, step: ReviewStep) -> None:
        assert step._stat_strip._total.text() == "0"
        assert step._stat_strip._included.text() == "0"

    def test_can_advance_defaults_true(self, step: ReviewStep) -> None:
        assert step.can_advance() is True


class TestMaskTerm:
    def test_masks_middle_keeping_first_and_last_letter(self) -> None:
        assert _mask_term("value") == "v***e"
        assert _mask_term("badword") == "b*****d"

    def test_short_term_still_masks(self) -> None:
        assert _mask_term("ab") == "a*"


class TestSetScan:
    def test_loads_real_scan_hits_into_table(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._table.rowCount() == 3

    def test_stat_strip_matches_build_report(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._stat_strip._total.text() == "3"
        assert step._stat_strip._included.text() == "3"
        assert step._stat_strip._excluded.text() == "0"
        assert step._stat_strip._unique_terms.text() == "3"

    def test_profanity_terms_display_unmasked(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        terms = {
            _item(step._table, r, _COL_TERM).text()
            for r in range(step._table.rowCount())
        }
        assert "darn" in terms
        assert "heck" in terms

    def test_slur_category_term_displays_masked(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        terms = {
            _item(step._table, r, _COL_TERM).text()
            for r in range(step._table.rowCount())
        }
        assert "badword" not in terms
        assert "b*****d" in terms

    def test_context_column_shows_surrounding_words(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        darn_row = next(
            r
            for r in range(step._table.rowCount())
            if _item(step._table, r, _COL_TERM).text() == "darn"
        )
        context = _item(step._table, darn_row, _COL_CONTEXT).text()
        assert "and then he said" in context
        assert "it was heck" in context

    def test_confidence_displayed_as_percentage_or_na(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        conf_values = {
            _item(step._table, r, _COL_CONFIDENCE).text()
            for r in range(step._table.rowCount())
        }
        assert "90%" in conf_values
        assert "n/a" in conf_values

    def test_time_column_formatted_as_clock(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        times = [
            _item(step._table, r, _COL_TIME).text()
            for r in range(step._table.rowCount())
        ]
        assert any(t.startswith("0:00:") for t in times)


class TestIncludeExclude:
    def test_unchecking_included_box_calls_scan_decide(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        item = _item(step._table, 0, _COL_INCLUDED)
        hit_id = item.data(Qt.ItemDataRole.UserRole)
        item.setCheckState(Qt.CheckState.Unchecked)

        assert fixture.scan.status_for(hit_id) == ReviewStatus.EXCLUDED
        assert step._stat_strip._included.text() == "2"
        assert step._stat_strip._excluded.text() == "1"

    def test_rechecking_restores_included_status(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        # Toggling rebuilds the table (same full-refresh pattern
        # CatalogWindow already uses), so the row-0 item object from
        # before the first toggle is a destroyed C++ object afterward —
        # each check must re-fetch a fresh item, exactly as a second real
        # click would hit a freshly rendered cell.
        fixture.load(step)
        item = _item(step._table, 0, _COL_INCLUDED)
        hit_id = item.data(Qt.ItemDataRole.UserRole)
        item.setCheckState(Qt.CheckState.Unchecked)

        item = _item(step._table, 0, _COL_INCLUDED)
        item.setCheckState(Qt.CheckState.Checked)

        assert fixture.scan.status_for(hit_id) == ReviewStatus.INCLUDED
        assert step._stat_strip._included.text() == "3"

    def test_bulk_exclude_selected_rows(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        # selectRow() replaces rather than accumulates the selection even
        # in ExtendedSelection mode (verified directly against Qt) — a
        # real multi-row selection comes from ctrl/shift-click, simulated
        # here via the selection model directly.
        selection_model = step._table.selectionModel()
        for row in (0, 1):
            selection_model.select(
                step._table.model().index(row, 0),
                selection_model.SelectionFlag.Select
                | selection_model.SelectionFlag.Rows,
            )
        step._bulk_set(False)

        assert step._stat_strip._excluded.text() == "2"
        assert step._bulk_label.text() == ""  # selection cleared after bulk action

    def test_bulk_buttons_disabled_with_no_selection(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._bulk_include_btn.isEnabled() is False
        assert step._bulk_exclude_btn.isEnabled() is False

    def test_bulk_buttons_enable_on_selection(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        assert step._bulk_include_btn.isEnabled() is True
        assert step._bulk_label.text() == "1 selected"


class TestFilters:
    def test_category_filter_narrows_table(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        slurs_index = step._category_combo.findData(fixture.slur.category_id)
        step._category_combo.setCurrentIndex(slurs_index)
        assert step._table.rowCount() == 1
        assert _item(step._table, 0, _COL_TERM).text() == "b*****d"

    def test_term_filter_narrows_to_one_entry(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        darn_index = step._term_combo.findData(fixture.darn.id)
        step._term_combo.setCurrentIndex(darn_index)
        assert step._table.rowCount() == 1
        assert _item(step._table, 0, _COL_TERM).text() == "darn"

    def test_confidence_below_25_filter_excludes_everything(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        # darn=0.9, badword=0.5, heck=None -- none are < 0.25.
        below_25_index = step._confidence_combo.findData("below_25")
        step._confidence_combo.setCurrentIndex(below_25_index)
        assert step._table.rowCount() == 0

    def test_confidence_below_75_filter_narrows_to_badword(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        below_75_index = step._confidence_combo.findData("below_75")
        step._confidence_combo.setCurrentIndex(below_75_index)
        assert step._table.rowCount() == 1
        assert _item(step._table, 0, _COL_TERM).text() == "b*****d"

    def test_confidence_below_90_filter_excludes_exactly_90(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        # darn's confidence is exactly 0.9 -- "below 90%" must exclude it,
        # leaving only badword (0.5).
        below_90_index = step._confidence_combo.findData("below_90")
        step._confidence_combo.setCurrentIndex(below_90_index)
        assert step._table.rowCount() == 1
        assert _item(step._table, 0, _COL_TERM).text() == "b*****d"

    def test_confidence_unavailable_filter(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        unavailable_index = step._confidence_combo.findData("unavailable")
        step._confidence_combo.setCurrentIndex(unavailable_index)
        assert step._table.rowCount() == 1
        assert _item(step._table, 0, _COL_TERM).text() == "heck"

    def test_state_filter_after_excluding_a_hit(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        item = _item(step._table, 0, _COL_INCLUDED)
        item.setCheckState(Qt.CheckState.Unchecked)

        excluded_index = step._state_combo.findData(ReviewStatus.EXCLUDED.value)
        step._state_combo.setCurrentIndex(excluded_index)
        assert step._table.rowCount() == 1

    def test_sort_by_time_descending(self, step: ReviewStep, fixture: _Fixture) -> None:
        fixture.load(step)
        step._direction_btn.click()
        times = [
            _item(step._table, r, _COL_TIME).text()
            for r in range(step._table.rowCount())
        ]
        assert times == sorted(times, reverse=True)


class TestRenderPlanTab:
    def test_plan_summary_reflects_included_hits(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert "3 intervals" in step._plan_summary_label.text()

    def test_excluding_a_hit_removes_its_interval(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        item = _item(step._table, 0, _COL_INCLUDED)
        item.setCheckState(Qt.CheckState.Unchecked)
        assert "2 intervals" in step._plan_summary_label.text()

    def test_empty_scan_shows_empty_plan_message(self, step: ReviewStep) -> None:
        assert step._plan_layout.count() >= 1


class TestCurrentRenderPlan:
    def test_none_before_any_scan_is_set(self, step: ReviewStep) -> None:
        assert step.current_render_plan() is None

    def test_matches_the_on_screen_plan_tab_summary(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        plan = step.current_render_plan()
        assert plan is not None
        assert len(plan.intervals) == 3

    def test_live_reflects_a_later_exclude_decision(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        item = _item(step._table, 0, _COL_INCLUDED)
        item.setCheckState(Qt.CheckState.Unchecked)

        plan = step.current_render_plan()

        assert plan is not None
        assert len(plan.intervals) == 2

    def test_uses_the_snapshot_attenuation_when_none_overridden(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        plan = step.current_render_plan()
        assert plan is not None
        assert plan.attenuation == fixture.snapshot.attenuation

    def test_not_cached_returns_a_fresh_plan_object_each_call(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        first = step.current_render_plan()
        second = step.current_render_plan()
        assert first == second
        assert first is not second
