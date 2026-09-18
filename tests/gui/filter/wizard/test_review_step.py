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

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

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
    _MODE_CONTEXT,
    _MODE_PADDED,
    _AddToCatalogDialog,
    _ChapterPickerDialog,
    ReviewStep,
    _mask_term,
)
from m4bmaker.gui.player import AudioPlayerWidget

pytestmark = pytest.mark.usefixtures("qapp")

NORMALIZATION_VERSION = 1


def _item(table: QTableWidget, row: int, col: int) -> QTableWidgetItem:
    cell = table.item(row, col)
    assert cell is not None
    return cell


def _find_button(widget: QWidget, text_contains: str) -> QPushButton:
    for btn in widget.findChildren(QPushButton):
        if text_contains in btn.text():
            return btn
    raise AssertionError(f"no button containing {text_contains!r}")


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
            self.scan,
            self.service,
            self.transcript,
            source_duration_ms=60_000,
            source_path=Path("/books/test.m4b"),
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

    def test_attenuated_total_explains_overlap_is_not_double_counted(
        self, step: ReviewStep
    ) -> None:
        # A User asked for this after being surprised that "Total hits"
        # can be higher than the number of distinct silenced spots —
        # overlapping hits (e.g. a word and a phrase containing it) merge
        # into one interval, and this number already reflects that.
        assert "only counted once" in step._stat_strip._attenuated.toolTip()

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
    def test_note_explains_overlapping_hits_merge(self, step: ReviewStep) -> None:
        notes = [
            w
            for w in step.findChildren(QLabel)
            if "combined into one silenced section" in w.text()
        ]
        assert len(notes) == 1
        assert "darn" in notes[0].text()

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

    def test_plan_group_is_wrapped_in_a_scroll_area(self, step: ReviewStep) -> None:
        # A real scan can produce hundreds of merged intervals -- without a
        # scroll area, the group box just grows to fit all of them (well
        # past any real window height), and depending on how the wizard
        # shell's actual fixed window size constrains this tab, the whole
        # list can render as entirely invisible rather than merely "cut off
        # after a screenful" (reproduced directly against a fixed-size
        # QMainWindow, not just asserted here).
        scroll = step.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.widget() is step._plan_group
        assert scroll.widgetResizable() is True

    def test_large_scan_remains_visible_in_a_realistic_fixed_size_window(
        self, step: ReviewStep, service: CatalogService
    ) -> None:
        cat = service.create_category("Profanity")
        darn, _ = service.create_entry(cat.id, "darn")
        profile = service.create_profile("Big", entry_ids=[darn.id])
        snapshot = service.create_snapshot(profile.id)

        words = []
        t = 0
        for _ in range(120):
            words.append(_word("darn", t, t + 300))
            t += 5000  # spaced far apart -> no merging, ~120 distinct intervals
        transcript = _transcript(words)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        step.set_scan(
            scan,
            service,
            transcript,
            source_duration_ms=t + 1000,
            source_path=Path("/books/big.m4b"),
        )
        assert step._plan_layout.count() == 120

        win = QMainWindow()
        win.setCentralWidget(step)
        win.setFixedSize(1200, 800)
        win.show()
        tabw = step.findChild(QTabWidget)
        assert tabw is not None
        tabw.setCurrentIndex(1)
        QApplication.processEvents()

        first_item = step._plan_layout.itemAt(0)
        assert first_item is not None
        first_label = first_item.widget()
        assert first_label is not None
        assert first_label.isVisible()
        assert not first_label.visibleRegion().isEmpty()
        win.close()


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


class TestHitPreviewPlayback:
    """ADR-0052: hear a selected hit's own audio before deciding to
    include or exclude it. AudioPlayerWidget itself has its own
    dedicated tests (test_player.py) — these treat it as a black box,
    same "test the shell, not the already-tested widget" split this
    file already uses for the real Scan/CatalogService objects."""

    def test_idle_before_any_selection(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._preview_idle_label.isHidden() is False
        assert step._audio_player.isHidden() is True
        assert step._selected_hit is None

    def test_selecting_one_row_loads_padded_window_paused(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        with patch.object(step._audio_player, "load_paused") as mock_load:
            step._table.selectRow(0)  # sorted by time: darn @ 500-600ms first

        assert step._selected_hit is not None
        assert step._selected_hit.entry_id == fixture.darn.id
        # AttenuationSettings(lead_padding_ms=60, tail_padding_ms=80)
        assert step._preview_windows[_MODE_PADDED] == (440, 680)
        # ±2000ms, clamped to [0, source_duration_ms] -- 500-2000 clamps to 0
        assert step._preview_windows[_MODE_CONTEXT] == (0, 2600)
        mock_load.assert_called_once_with(Path("/books/test.m4b"), 440)
        assert step._preview_idle_label.isHidden() is True
        assert step._audio_player.isHidden() is False

    def test_selecting_two_rows_shows_idle_and_clears_selection(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        selection_model = step._table.selectionModel()
        selection_model.select(
            step._table.model().index(1, 0),
            selection_model.SelectionFlag.Select | selection_model.SelectionFlag.Rows,
        )

        assert step._selected_hit is None
        assert step._preview_idle_label.isHidden() is False
        assert step._audio_player.isHidden() is True

    def test_deselecting_shows_idle(self, step: ReviewStep, fixture: _Fixture) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        step._table.clearSelection()
        assert step._selected_hit is None
        assert step._preview_idle_label.isHidden() is False

    def test_mode_toggle_switches_to_context_window_and_reloads_paused(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)

        with (
            patch.object(step._audio_player, "pause") as mock_pause,
            patch.object(step._audio_player, "load_paused") as mock_load,
        ):
            step._mode_context_btn.setChecked(True)

        mock_pause.assert_called_once()
        mock_load.assert_called_once_with(Path("/books/test.m4b"), 0)
        assert step._preview_mode == _MODE_CONTEXT

    def test_toggling_back_to_padded_reloads_padded_window(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        step._mode_context_btn.setChecked(True)

        with patch.object(step._audio_player, "load_paused") as mock_load:
            step._mode_padded_btn.setChecked(True)

        mock_load.assert_called_once_with(Path("/books/test.m4b"), 440)
        assert step._preview_mode == _MODE_PADDED

    def test_selecting_a_different_row_resets_mode_to_padded(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        step._mode_context_btn.setChecked(True)
        assert step._preview_mode == _MODE_CONTEXT

        step._table.selectRow(1)  # heck @ 1000-1100ms

        assert step._preview_mode == _MODE_PADDED
        assert step._mode_padded_btn.isChecked() is True

    def test_play_click_starts_play_clip_with_the_active_window(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        with (
            patch.object(
                type(step._audio_player),
                "is_playing",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(step._audio_player, "play_clip") as mock_play_clip,
        ):
            step._preview_play_btn.click()
        mock_play_clip.assert_called_once_with(Path("/books/test.m4b"), 440, 680)

    def test_play_click_while_playing_pauses_and_resets_to_clip_start(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        """No separate Stop control (ADR-0052) — clicking mid-playback
        halts *and* resets to this clip's own start, same as letting it
        finish naturally already does."""
        fixture.load(step)
        step._table.selectRow(0)
        with (
            patch.object(
                type(step._audio_player),
                "is_playing",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(step._audio_player, "pause") as mock_pause,
            patch.object(step._audio_player, "load_paused") as mock_load,
        ):
            step._preview_play_btn.click()
        mock_pause.assert_called_once()
        mock_load.assert_called_once_with(Path("/books/test.m4b"), 440)

    def test_playback_state_changed_signal_updates_button_icon(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        step._on_audio_playback_state_changed(True)
        assert step._preview_play_btn.text() == "⏸"
        step._on_audio_playback_state_changed(False)
        assert step._preview_play_btn.text() == "▶"

    def test_preview_label_shows_term_and_tooltip_has_padding_detail(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        assert "darn" in step._preview_label.text()
        tooltip = step._preview_label.toolTip()
        assert "darn" in tooltip
        assert "lead-in" in tooltip
        assert "tail" in tooltip

    def test_context_mode_tooltip_explains_before_after(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        step._mode_context_btn.setChecked(True)
        assert "before / after" in step._preview_label.toolTip()

    def test_masked_term_stays_masked_in_preview_label(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(2)  # badword @ 1500-1600ms, in a masked category
        assert step._selected_hit is not None
        assert step._selected_hit.entry_id == fixture.slur.id
        assert "badword" not in step._preview_label.toolTip()
        assert _mask_term("badword") in step._preview_label.toolTip()

    def test_long_term_is_elided_with_full_text_in_tooltip(
        self, step: ReviewStep, service: CatalogService
    ) -> None:
        cat = service.create_category("Crude language")
        phrase = "a genuinely absurdly long multi word catalog phrase entry"
        entry, _ = service.create_entry(cat.id, phrase)
        profile = service.create_profile("Long", entry_ids=[entry.id])
        snapshot = service.create_snapshot(profile.id)
        words = [_word(w, i * 200, i * 200 + 150) for i, w in enumerate(phrase.split())]
        transcript = _transcript(words)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        step.set_scan(
            scan,
            service,
            transcript,
            source_duration_ms=10_000,
            source_path=Path("/books/long.m4b"),
        )

        step._table.selectRow(0)

        full = f'Previewing "{phrase}"'
        assert step._preview_label.text() != full
        assert "…" in step._preview_label.text()
        assert phrase in step._preview_label.toolTip()

    def test_hide_event_stops_playback(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        with patch.object(step._audio_player, "stop") as mock_stop:
            step.hideEvent(QHideEvent())
        mock_stop.assert_called_once()

    def test_set_scan_again_resets_preview_to_idle(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        assert step._selected_hit is not None

        with patch.object(step._audio_player, "stop") as mock_stop:
            fixture.load(step)

        mock_stop.assert_called_once()
        assert step._selected_hit is None
        assert step._preview_idle_label.isHidden() is False

    def test_audio_player_built_with_show_controls_false(
        self, step: ReviewStep
    ) -> None:
        # The dock drives its own Play (and has no Stop at all) rather
        # than this widget's own whole-file pause/resume semantics, and
        # shows its own clip-relative progress rather than this
        # widget's own file-absolute slider/time — none of the four
        # built-in row widgets may appear in the row.
        assert isinstance(step._audio_player, AudioPlayerWidget)
        row = step._audio_player.layout().itemAt(0).layout()
        widgets = [row.itemAt(i).widget() for i in range(row.count())]
        assert step._audio_player._play_btn not in widgets
        assert step._audio_player._stop_btn not in widgets
        assert step._audio_player._slider not in widgets
        assert step._audio_player._time_lbl not in widgets
        assert step._preview_play_btn in widgets
        assert step._preview_progress in widgets
        assert step._preview_time_label in widgets

    def test_selecting_a_hit_resets_progress_display_immediately(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        # padded window for "darn" (500-600ms, lead=60/tail=80) is 240ms
        assert step._preview_progress.value() == 0
        assert step._preview_time_label.text() == "0.0s / 0.2s"

    def test_position_changed_updates_progress_relative_to_the_clip(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)  # padded window: 440-680ms (240ms long)
        step._on_audio_position_changed(560)  # 120ms into the clip == 50%
        assert step._preview_progress.value() == 500
        assert step._preview_time_label.text() == "0.1s / 0.2s"

    def test_position_changed_clamps_to_the_clip_bounds(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._table.selectRow(0)
        step._on_audio_position_changed(999_999)  # far past the clip's own end
        assert step._preview_progress.value() == 1000
        step._on_audio_position_changed(0)  # before the clip's own start
        assert step._preview_progress.value() == 0

    def test_position_changed_before_any_selection_is_a_noop(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._on_audio_position_changed(5000)  # must not raise with no hit selected

    def test_context_column_does_not_wrap(self, step: ReviewStep) -> None:
        assert step._table.wordWrap() is False

    def test_context_cell_tooltip_has_the_full_text(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        item = _item(step._table, 0, _COL_CONTEXT)
        assert item.toolTip() == item.text()
        assert len(item.toolTip()) > 0


class TestTranscriptTabPlacement:
    def test_transcript_is_the_last_tab(self, step: ReviewStep) -> None:
        tabs = step.findChild(QTabWidget)
        assert tabs is not None
        labels = [tabs.tabText(i) for i in range(tabs.count())]
        assert labels == ["Hits", "Render Plan", "Transcript"]

    def test_transcript_tab_has_an_optional_tooltip(self, step: ReviewStep) -> None:
        tabs = step.findChild(QTabWidget)
        assert tabs is not None
        tooltip = tabs.tabToolTip(2)
        assert "optional" in tooltip.lower()

    def test_low_confidence_toggle_is_off_by_default(self, step: ReviewStep) -> None:
        assert step._lowconf_checkbox.isChecked() is False

    def test_legend_appears_above_the_chapter_selector(self, step: ReviewStep) -> None:
        """The legend explains what a Contributor is about to see in
        the transcript below -- it belongs with the other informational
        text at the top of the tab, not buried in the button row."""
        pane = step._transcript_legend_label.parentWidget()
        assert pane is not None
        layout = pane.layout()
        assert layout is not None

        legend_idx = None
        chapter_row_idx = None
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is step._transcript_legend_label:
                legend_idx = i
            sub_layout = item.layout()
            if sub_layout is not None:
                for j in range(sub_layout.count()):
                    sub_item = sub_layout.itemAt(j)
                    if sub_item.widget() is step._chapter_button:
                        chapter_row_idx = i
        assert legend_idx is not None
        assert chapter_row_idx is not None
        assert legend_idx < chapter_row_idx

    def test_legend_explains_both_visual_treatments(self, step: ReviewStep) -> None:
        """A Contributor seeing bold/struck-through words and, once the
        toggle is on, shaded words, needs to find out what each one
        means somewhere they'll actually see it -- not only a tooltip
        on the checkbox."""
        text = step._transcript_legend_label.text().lower()
        assert "struck" in text or "strikethrough" in text
        assert "hits" in text or "hit" in text
        assert "uncertain" in text or "sure" in text

    def test_lowconf_toggle_is_labeled_experimental(self, step: ReviewStep) -> None:
        """Real-app testing found this toggle still flags mostly
        ordinary, correctly-transcribed words even after the length
        floor -- the same signal ADR-0053's own real-data validation
        already found unreliable for Option 4. Not removed, but the
        checkbox's own label must say so, not just the legend paragraph
        above it, so a Contributor scanning the controls (not reading
        the paragraph) still sees the caveat."""
        assert "experimental" in step._lowconf_checkbox.text().lower()

    def test_lowconf_toggle_tooltip_explains_the_caveat(self, step: ReviewStep) -> None:
        tooltip = step._lowconf_checkbox.toolTip().lower()
        assert "experimental" in tooltip
        assert "confidence" in tooltip

    def test_legend_calls_out_the_experimental_caveat(self, step: ReviewStep) -> None:
        text = step._transcript_legend_label.text().lower()
        assert "experimental" in text


class TestTranscriptTabChapters:
    def test_chapter_labels_has_one_entry_for_the_fixtures_single_segment(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert len(step._chapter_labels) == 1

    def test_first_chapter_is_loaded_by_default(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._transcript_view.toPlainText().split() == [
            w.text for w in fixture.words
        ]

    def test_set_scan_again_resets_to_the_first_chapter(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        fixture.load(step)  # a second, independent scan/transcript
        assert step._chapter_index == 0

    def test_chapter_button_shows_the_current_chapter_label(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._chapter_labels[0] in step._chapter_button.text()

    def test_clicking_the_chapter_button_opens_a_picker_dialog(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        """Not a QComboBox popup -- its native macOS popup resisted
        three separate attempts to make it scroll instead of rendering
        every chapter at once. A button opening an ordinary QDialog
        with a QListWidget sidesteps that native popup entirely."""
        fixture.load(step)
        with patch.object(
            ReviewStep, "_run_dialog", return_value=QDialog.DialogCode.Rejected
        ) as mock_run:
            step._on_chapter_button_clicked()
        assert mock_run.call_count == 1
        (dialog,), _ = mock_run.call_args
        assert isinstance(dialog, _ChapterPickerDialog)

    def test_accepting_the_picker_jumps_to_the_chosen_chapter(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)

        def _fake_run(self: ReviewStep, dialog: _ChapterPickerDialog) -> int:
            dialog.selected_index = 0
            return QDialog.DialogCode.Accepted

        with patch.object(ReviewStep, "_run_dialog", _fake_run):
            step._on_chapter_button_clicked()
        assert step._chapter_index == 0

    def test_cancelling_the_picker_leaves_the_chapter_unchanged(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        with patch.object(
            ReviewStep, "_run_dialog", return_value=QDialog.DialogCode.Rejected
        ):
            step._on_chapter_button_clicked()
        assert step._chapter_index == 0

    def test_no_chapters_is_a_no_op(self, step: ReviewStep) -> None:
        with patch.object(ReviewStep, "_run_dialog") as mock_run:
            step._on_chapter_button_clicked()
        mock_run.assert_not_called()

    def test_loading_a_chapter_skips_the_low_confidence_pass_when_unchecked(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        """Highlighting is off by default -- a fresh load already leaves
        every word unhighlighted, so re-applying that is pure waste on
        every chapter switch, which is exactly the bug that made this
        tab slow to begin with."""
        assert step._lowconf_checkbox.isChecked() is False
        with patch.object(
            step._transcript_view, "set_low_confidence_hint"
        ) as mock_hint:
            fixture.load(step)
        mock_hint.assert_not_called()

    def test_loading_a_chapter_still_applies_the_hint_when_checked(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._lowconf_checkbox.setChecked(True)
        with patch.object(
            step._transcript_view, "set_low_confidence_hint"
        ) as mock_hint:
            step._load_chapter(0)
        mock_hint.assert_called_once_with(True)


class TestChapterPickerDialog:
    """ADR-0053: replaces QComboBox's own native popup, which resisted
    three separate attempts to make it scroll instead of rendering
    every chapter at once. Plain QListWidget behavior, tested directly
    rather than only through ReviewStep's own wiring."""

    _LABELS = [
        "1 — 0:00:00.0–0:00:31.5",
        "2 — 0:00:31.5–0:01:02.0",
        "3 — 0:01:02.0–0:01:30.0",
    ]

    def test_lists_every_label(self) -> None:
        dialog = _ChapterPickerDialog(self._LABELS, 0)
        assert dialog._list.count() == 3
        assert [dialog._list.item(i).text() for i in range(3)] == self._LABELS

    def test_current_chapter_is_preselected(self) -> None:
        dialog = _ChapterPickerDialog(self._LABELS, 1)
        assert dialog._list.currentRow() == 1

    def test_double_clicking_a_row_selects_it_and_accepts(self) -> None:
        dialog = _ChapterPickerDialog(self._LABELS, 0)
        item = dialog._list.item(2)
        assert item is not None
        with patch.object(dialog, "accept") as mock_accept:
            dialog._list.itemActivated.emit(item)
        assert dialog.selected_index == 2
        mock_accept.assert_called_once()

    def test_go_button_selects_the_current_row(self) -> None:
        dialog = _ChapterPickerDialog(self._LABELS, 0)
        dialog._list.setCurrentRow(1)
        with patch.object(dialog, "accept") as mock_accept:
            _find_button(dialog, "Go").click()
        assert dialog.selected_index == 1
        mock_accept.assert_called_once()

    def test_cancel_button_rejects_without_setting_selection(self) -> None:
        dialog = _ChapterPickerDialog(self._LABELS, 0)
        with patch.object(dialog, "reject") as mock_reject:
            _find_button(dialog, "Cancel").click()
        assert dialog.selected_index is None
        mock_reject.assert_called_once()


class TestTranscriptTabRendering:
    def test_hit_words_are_flagged_in_the_view(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        hit_texts = {s.word.text for s in step._transcript_view._spans if s.is_hit}
        assert hit_texts == {"darn", "heck", "badword"}

    def test_plain_words_are_not_flagged(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        plain_texts = {
            s.word.text for s in step._transcript_view._spans if not s.is_hit
        }
        assert plain_texts == {"and", "then", "he", "said", "it", "was", "no", "really"}


class TestTranscriptTabPreview:
    def test_idle_before_any_selection(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._transcript_idle_label.isHidden() is False
        assert step._transcript_audio_player.isHidden() is True
        assert step._transcript_add_btn.isEnabled() is False

    def test_selecting_a_plain_word_loads_the_preview(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))  # "and", 0-100ms
        assert step._transcript_preview_window == (0, 2100)
        assert step._transcript_idle_label.isHidden() is True
        assert step._transcript_add_btn.isEnabled() is True

    def test_deselecting_returns_to_idle(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))
        step._transcript_view._set_selection(None)
        assert step._transcript_preview_window is None
        assert step._transcript_idle_label.isHidden() is False
        assert step._transcript_add_btn.isEnabled() is False

    def test_context_window_clamps_to_zero_at_the_start(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))  # starts at 0ms
        start_ms, _ = step._transcript_preview_window
        assert start_ms == 0

    def test_play_click_starts_play_clip_with_the_context_window(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))
        with (
            patch.object(
                type(step._transcript_audio_player),
                "is_playing",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(step._transcript_audio_player, "play_clip") as mock_play,
        ):
            step._transcript_play_btn.click()
        mock_play.assert_called_once_with(Path("/books/test.m4b"), 0, 2100)

    def test_play_click_while_playing_pauses_and_resets(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))
        with (
            patch.object(
                type(step._transcript_audio_player),
                "is_playing",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(step._transcript_audio_player, "pause") as mock_pause,
            patch.object(step._transcript_audio_player, "load_paused") as mock_load,
        ):
            step._transcript_play_btn.click()
        mock_pause.assert_called_once()
        mock_load.assert_called_once_with(Path("/books/test.m4b"), 0)

    def test_playback_state_changed_updates_button_icon(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))
        step._on_transcript_playback_state_changed(True)
        assert step._transcript_play_btn.text() == "⏸"
        step._on_transcript_playback_state_changed(False)
        assert step._transcript_play_btn.text() == "▶"

    def test_position_changed_updates_progress_relative_to_the_clip(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))  # window (0, 2100)
        step._on_transcript_position_changed(1050)
        assert step._transcript_progress.value() == 500  # halfway
        assert "1.1s" in step._transcript_time_label.text()

    def test_hide_event_stops_the_transcript_audio_player(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        with patch.object(step._transcript_audio_player, "stop") as mock_stop:
            step.hideEvent(QHideEvent())
        mock_stop.assert_called_once()


class TestTranscriptTabAddToCatalog:
    def test_add_button_disabled_with_no_selection_even_after_load(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._transcript_add_btn.isEnabled() is False

    def test_accepting_the_dialog_marks_the_word_pending(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))  # "and"
        with patch.object(
            ReviewStep, "_run_dialog", return_value=QDialog.DialogCode.Accepted
        ):
            step._on_transcript_add_clicked()
        assert fixture.words[0].start_ms in step._transcript_view._pending_starts_ms

    # Whether accepting the dialog actually *creates* the catalog entry is
    # the dialog's own responsibility (see TestAddToCatalogDialog below) --
    # patching _run_dialog here bypasses the dialog entirely, so this
    # layer only needs to test what ReviewStep itself does once the
    # dialog reports Accepted (mark pending, bump the count), not
    # re-verify catalog creation through a mock that never really ran it.

    def test_rejecting_the_dialog_does_not_mark_pending_or_create_anything(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))
        with patch.object(
            ReviewStep, "_run_dialog", return_value=QDialog.DialogCode.Rejected
        ):
            step._on_transcript_add_clicked()
        assert step._transcript_view._pending_starts_ms == set()
        assert step._transcript_added_count == 0

    def test_rescan_banner_hidden_until_first_addition(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        assert step._rescan_banner.isHidden() is True

    def test_rescan_banner_shows_singular_count_correctly(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        step._transcript_view._set_selection((0, 0))
        with patch.object(
            ReviewStep, "_run_dialog", return_value=QDialog.DialogCode.Accepted
        ):
            step._on_transcript_add_clicked()
        assert step._rescan_banner.isHidden() is False
        text = step._rescan_banner_label.text()
        assert "1 new catalog entry " in text
        assert "entries" not in text

    def test_rescan_banner_shows_plural_count_correctly(
        self, step: ReviewStep, fixture: _Fixture
    ) -> None:
        fixture.load(step)
        with patch.object(
            ReviewStep, "_run_dialog", return_value=QDialog.DialogCode.Accepted
        ):
            step._transcript_view._set_selection((0, 0))
            step._on_transcript_add_clicked()
            step._transcript_view._set_selection((1, 1))
            step._on_transcript_add_clicked()
        assert "2 new catalog entries " in step._rescan_banner_label.text()

    def test_go_to_scan_button_emits_signal(self, step: ReviewStep) -> None:
        received = []
        step.go_to_scan_requested.connect(lambda: received.append(1))
        step._rescan_banner.findChild(type(step._transcript_add_btn))  # sanity: exists
        go_btn = [
            w
            for w in step._rescan_banner.findChildren(type(step._transcript_add_btn))
            if w.text() == "Go to Scan →"
        ][0]
        go_btn.click()
        assert received == [1]


class TestAddToCatalogDialog:
    # _on_add() calls the real save_catalog(), which defaults to the
    # real per-user catalog.json path when no path is given -- exactly
    # the pattern test_word_variation_dialog.py already patches at its
    # import site for this exact reason (see its own module docstring).
    # Missing this here once already overwrote a real, hand-curated
    # production catalog during this session; every test in this class
    # patches it, including the duplicate-phrase test, which shouldn't
    # reach save_catalog at all but must never be trusted to prove that
    # by not blowing up if it does.

    def test_creates_entry_in_the_chosen_category_and_accepts(
        self, service: CatalogService
    ) -> None:
        category = service.create_category("Mild")
        dialog = _AddToCatalogDialog(service, "darnit")
        idx = dialog._category_combo.findData(category.id)
        dialog._category_combo.setCurrentIndex(idx)
        with patch("m4bmaker.gui.filter.wizard.review_step.save_catalog") as mocked:
            dialog._on_add()
        mocked.assert_called_once_with(service)
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert service.find_duplicate_entry(category.id, "darnit") is not None

    def test_duplicate_phrase_shows_a_message_and_does_not_accept(
        self, service: CatalogService
    ) -> None:
        category = service.create_category("Mild")
        service.create_entry(category.id, "darnit")
        dialog = _AddToCatalogDialog(service, "darnit")
        idx = dialog._category_combo.findData(category.id)
        dialog._category_combo.setCurrentIndex(idx)
        with patch("m4bmaker.gui.filter.wizard.review_step.save_catalog") as mocked:
            dialog._on_add()
        mocked.assert_not_called()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert "already in this category" in dialog._status_label.text()
