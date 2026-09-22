"""Tests for m4bmaker.gui.filter.wizard.transcript_view (ADR-0053, Option 1).

The two pieces of logic that matter most (which words a real ScanHit
covers, and how a raw drag snaps to a word/hit-bounded selection) are
pure functions, tested directly with no widget or event loop involved.
Widget-level tests drive selection through real QTextCursor character
positions (position-independent, unlike pixel/mouse coordinates) rather
than simulated QMouseEvents, matching how this project already tests
button clicks by calling .click() rather than raw event dispatch.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QMenu

from m4bmaker.filter.models import ScanHit
from m4bmaker.filter.transcript import TranscriptWord
from m4bmaker.gui.filter.wizard.transcript_view import (
    TranscriptView,
    hit_word_flags,
    snap_selection,
)

pytestmark = pytest.mark.usefixtures("qapp")


def _word(
    text: str, start_ms: int, end_ms: int, confidence: float | None = 0.9
) -> TranscriptWord:
    return TranscriptWord(
        text=text,
        normalized=text.lower(),
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=confidence,
    )


def _hit(start_ms: int, end_ms: int) -> ScanHit:
    return ScanHit(
        id="hit",
        scan_id="scan",
        raw_tokens=(),
        entry_id="entry",
        category_id="cat",
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=0.9,
        match_rule="exact_token",
    )


class TestHitWordFlags:
    def test_no_hits_means_nothing_flagged(self) -> None:
        words = [_word("a", 0, 100), _word("b", 100, 200)]
        assert hit_word_flags(words, []) == [False, False]

    def test_word_inside_a_hit_span_is_flagged(self) -> None:
        words = [_word("a", 0, 100), _word("darn", 100, 200), _word("c", 200, 300)]
        assert hit_word_flags(words, [_hit(100, 200)]) == [False, True, False]

    def test_multi_word_hit_flags_every_covered_word(self) -> None:
        words = [
            _word("a", 0, 100),
            _word("son", 100, 200),
            _word("of", 200, 300),
            _word("c", 300, 400),
        ]
        assert hit_word_flags(words, [_hit(100, 300)]) == [False, True, True, False]

    def test_overlapping_hits_are_merged_not_double_counted(self) -> None:
        words = [_word("a", 0, 100), _word("b", 100, 200), _word("c", 200, 300)]
        flags = hit_word_flags(words, [_hit(50, 150), _hit(120, 250)])
        assert flags == [True, True, True]

    def test_empty_words_returns_empty(self) -> None:
        assert hit_word_flags([], [_hit(0, 100)]) == []


class TestSnapSelection:
    def test_forward_drag_within_hit_free_range(self) -> None:
        assert snap_selection(0, 2, [False, False, False, False]) == (0, 2)

    def test_backward_drag_normalizes_to_lo_hi(self) -> None:
        assert snap_selection(3, 1, [False, False, False, False]) == (1, 3)

    def test_single_click_no_drag(self) -> None:
        assert snap_selection(2, 2, [False, False, False]) == (2, 2)

    def test_drag_stops_before_a_hit_it_would_cross(self) -> None:
        # word 2 is a hit; dragging 0->3 should truncate to 0..1
        assert snap_selection(0, 3, [False, False, True, False]) == (0, 1)

    def test_starting_on_a_hit_yields_no_selection(self) -> None:
        assert snap_selection(1, 3, [False, True, False, False]) is None


@pytest.fixture()
def words() -> list[TranscriptWord]:
    return [
        _word("The", 0, 100),
        _word("Donut", 100, 200),
        _word("darn", 300, 400),
        _word("gate", 400, 500),
        _word("system", 500, 600),
    ]


@pytest.fixture()
def view(words: list[TranscriptWord]) -> TranscriptView:
    v = TranscriptView()
    v.load_words(words, [_hit(300, 400)])
    return v


def _format_at(view: TranscriptView, char_pos: int) -> QTextCharFormat:
    cursor = QTextCursor(view.document())
    cursor.setPosition(char_pos)
    cursor.setPosition(char_pos + 1, QTextCursor.MoveMode.KeepAnchor)
    return cursor.charFormat()


class TestLoadWords:
    def test_renders_every_word_in_order(self, view: TranscriptView) -> None:
        assert view.toPlainText().split() == ["The", "Donut", "darn", "gate", "system"]

    def test_hit_word_is_flagged_internally(self, view: TranscriptView) -> None:
        assert [s.is_hit for s in view._spans] == [False, False, True, False, False]

    def test_reloading_clears_previous_selection(
        self, view: TranscriptView, words: list[TranscriptWord]
    ) -> None:
        view._set_selection((0, 1))
        view.load_words(words, [])
        assert view.selected_words() == []


class TestSelection:
    def test_selecting_a_single_word_by_cursor_position(
        self, view: TranscriptView
    ) -> None:
        span = view._spans[1]  # "Donut"
        cursor = view.textCursor()
        cursor.setPosition(span.char_start)
        cursor.setPosition(span.char_start, QTextCursor.MoveMode.MoveAnchor)
        view.setTextCursor(cursor)
        view._snap_current_selection()
        assert view.selection_text() == "Donut"

    def test_dragging_across_several_words_selects_all_of_them(
        self, view: TranscriptView
    ) -> None:
        cursor = view.textCursor()
        cursor.setPosition(view._spans[0].char_start)
        cursor.setPosition(view._spans[1].char_end, QTextCursor.MoveMode.KeepAnchor)
        view.setTextCursor(cursor)
        view._snap_current_selection()
        assert view.selection_text() == "The Donut"

    def test_dragging_through_a_hit_stops_at_its_edge(
        self, view: TranscriptView
    ) -> None:
        cursor = view.textCursor()
        cursor.setPosition(view._spans[0].char_start)
        cursor.setPosition(view._spans[4].char_end, QTextCursor.MoveMode.KeepAnchor)
        view.setTextCursor(cursor)
        view._snap_current_selection()
        # spans[2] ("darn") is the hit -- selection must stop at spans[1]
        assert view.selection_text() == "The Donut"

    def test_clicking_directly_on_a_hit_selects_nothing(
        self, view: TranscriptView
    ) -> None:
        span = view._spans[2]  # "darn", the hit
        cursor = view.textCursor()
        cursor.setPosition(span.char_start)
        view.setTextCursor(cursor)
        view._snap_current_selection()
        assert view.selected_words() == []

    def test_selection_changed_emitted_once_per_gesture(
        self, view: TranscriptView
    ) -> None:
        received = []
        view.selection_changed.connect(lambda: received.append(1))
        cursor = view.textCursor()
        cursor.setPosition(view._spans[0].char_start)
        view.setTextCursor(cursor)
        view._snap_current_selection()
        assert len(received) == 1


class TestMarkPending:
    def test_pending_words_are_recorded(self, view: TranscriptView) -> None:
        view.mark_pending([view._spans[0].word])
        assert view._spans[0].word.start_ms in view._pending_starts_ms

    def test_marking_pending_clears_the_current_selection(
        self, view: TranscriptView
    ) -> None:
        view._set_selection((0, 0))
        view.mark_pending([view._spans[0].word])
        assert view.selected_words() == []

    def test_pending_word_survives_low_confidence_toggle(
        self, view: TranscriptView
    ) -> None:
        # A pending word must not get silently reformatted back to plain
        # when the "highlight uncertain words" toggle is flipped.
        view.mark_pending([view._spans[0].word])
        view.set_low_confidence_hint(True)
        view.set_low_confidence_hint(False)
        assert view._spans[0].word.start_ms in view._pending_starts_ms


class TestLowConfidenceHint:
    def test_off_by_default(self, view: TranscriptView) -> None:
        assert view._show_low_confidence is False

    def test_toggle_records_state(self, view: TranscriptView) -> None:
        view.set_low_confidence_hint(True)
        assert view._show_low_confidence is True

    def test_indices_exclude_hits_high_confidence_and_missing_confidence(
        self,
    ) -> None:
        """The qualifying set is computed once by load_words() and is
        what set_low_confidence_hint()/_apply_low_confidence_formatting()
        touch on every toggle — this is the whole performance fix, so
        it must contain exactly the right words, not every word."""
        words = [
            _word("darn", 0, 100, confidence=0.5),  # low confidence
            _word("gate", 100, 200, confidence=0.95),  # high confidence
            _word("crap", 200, 300, confidence=0.5),  # low confidence, but a hit
            _word("okay", 300, 400, confidence=None),  # no confidence data
        ]
        v = TranscriptView()
        v.load_words(words, [_hit(200, 300)])
        assert v._low_confidence_indices == [0]

    def test_indices_exclude_short_words_regardless_of_confidence(self) -> None:
        """Whisper's per-word confidence tracks how acoustically distinct
        a word's pronunciation was, not whether it was transcribed
        correctly -- short, fast, unstressed words ("is", "he", "do")
        score low regardless of correctness, which flooded this toggle
        with noise in real use. A word shorter than the length floor
        never qualifies, no matter how low its confidence is."""
        words = [
            _word("is", 0, 100, confidence=0.1),
            _word("he", 100, 200, confidence=0.1),
            _word("darn", 200, 300, confidence=0.1),
        ]
        v = TranscriptView()
        v.load_words(words, [])
        assert v._low_confidence_indices == [2]

    def test_enabling_highlights_only_the_qualifying_word(self) -> None:
        """A background wash, not a subtle underline -- a dotted
        underline read as too hard to spot while scanning real running
        text."""
        words = [
            _word("darn", 0, 100, confidence=0.5),
            _word("gate", 100, 200, confidence=0.95),
        ]
        v = TranscriptView()
        v.load_words(words, [])

        v.set_low_confidence_hint(True)

        assert _format_at(v, v._spans[0].char_start).background().style() != (
            Qt.BrushStyle.NoBrush
        )
        assert _format_at(v, v._spans[1].char_start).background().style() == (
            Qt.BrushStyle.NoBrush
        )

    def test_disabling_reverts_the_highlight(self) -> None:
        words = [_word("darn", 0, 100, confidence=0.5)]
        v = TranscriptView()
        v.load_words(words, [])
        v.set_low_confidence_hint(True)

        v.set_low_confidence_hint(False)

        assert (
            _format_at(v, v._spans[0].char_start).background().style()
            == Qt.BrushStyle.NoBrush
        )

    def test_pending_word_is_left_alone_even_when_enabled(self) -> None:
        """mark_pending()'s own dashed underline must not be overwritten
        by the low-confidence highlight — _apply_low_confidence_formatting
        must keep skipping a pending word, not just skip it before it's
        pending."""
        words = [_word("darn", 0, 100, confidence=0.5)]
        v = TranscriptView()
        v.load_words(words, [])
        v.mark_pending([words[0]])

        v.set_low_confidence_hint(True)

        fmt = _format_at(v, v._spans[0].char_start)
        assert fmt.underlineStyle() == QTextCharFormat.UnderlineStyle.DashUnderline


class TestJumpToNextHit:
    def test_no_hits_is_a_noop(self) -> None:
        v = TranscriptView()
        v.load_words([_word("a", 0, 100)], [])
        v.jump_to_next_hit()  # must not raise
        assert v._last_hit_jump_idx == -1

    def test_advances_to_the_next_hit_each_call(self) -> None:
        words = [_word(f"w{i}", i * 100, i * 100 + 100) for i in range(6)]
        hits = [_hit(100, 200), _hit(400, 500)]
        v = TranscriptView()
        v.load_words(words, hits)
        v.jump_to_next_hit()
        assert v._last_hit_jump_idx == 1
        v.jump_to_next_hit()
        assert v._last_hit_jump_idx == 4

    def test_wraps_around_after_the_last_hit(self) -> None:
        words = [_word(f"w{i}", i * 100, i * 100 + 100) for i in range(3)]
        v = TranscriptView()
        v.load_words(words, [_hit(0, 100)])
        v.jump_to_next_hit()
        assert v._last_hit_jump_idx == 0
        v.jump_to_next_hit()
        assert v._last_hit_jump_idx == 0


class TestContextMenu:
    def test_right_clicking_an_unselected_word_selects_it_first(
        self, view: TranscriptView
    ) -> None:
        with patch.object(TranscriptView, "_exec_context_menu", return_value=None):
            view._show_context_menu_at(view._spans[3].char_start, QPoint(0, 0))
        assert view.selection_text() == "gate"

    def test_right_clicking_a_hit_does_nothing(self, view: TranscriptView) -> None:
        with patch.object(TranscriptView, "_exec_context_menu", return_value=None):
            view._show_context_menu_at(view._spans[2].char_start, QPoint(0, 0))
        assert view.selected_words() == []

    def test_choosing_add_emits_add_requested(self, view: TranscriptView) -> None:
        received = []
        view.add_requested.connect(lambda: received.append(1))

        def fake_exec(self: TranscriptView, menu: QMenu, pos: QPoint) -> object:
            return menu.actions()[1]  # [Play, Add]

        with patch.object(TranscriptView, "_exec_context_menu", fake_exec):
            view._show_context_menu_at(view._spans[0].char_start, QPoint(0, 0))
        assert received == [1]

    def test_choosing_play_emits_play_requested(self, view: TranscriptView) -> None:
        received = []
        view.play_requested.connect(lambda: received.append(1))

        def fake_exec(self: TranscriptView, menu: QMenu, pos: QPoint) -> object:
            return menu.actions()[0]  # [Play, Add]

        with patch.object(TranscriptView, "_exec_context_menu", fake_exec):
            view._show_context_menu_at(view._spans[0].char_start, QPoint(0, 0))
        assert received == [1]

    def test_right_clicking_already_selected_word_keeps_selection(
        self, view: TranscriptView
    ) -> None:
        view._set_selection((0, 1))
        with patch.object(TranscriptView, "_exec_context_menu", return_value=None):
            view._show_context_menu_at(view._spans[0].char_start, QPoint(0, 0))
        assert view.selection_text() == "The Donut"
