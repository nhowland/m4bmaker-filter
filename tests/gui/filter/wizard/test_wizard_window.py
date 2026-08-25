"""Tests for m4bmaker.gui.filter.wizard.wizard_window.WizardWindow
(ADR-0010, PRD §7.2).
"""

from __future__ import annotations

import pytest

from m4bmaker.gui.filter.wizard.placeholder_step import PlaceholderStep
from m4bmaker.gui.filter.wizard.review_step import ReviewStep
from m4bmaker.gui.filter.wizard.stepper import STEP_LABELS
from m4bmaker.gui.filter.wizard.wizard_window import WizardWindow

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture()
def win() -> WizardWindow:
    return WizardWindow()


class TestConstruction:
    def test_window_creates_without_error(self, win: WizardWindow) -> None:
        assert win is not None

    def test_has_one_step_widget_per_label(self, win: WizardWindow) -> None:
        assert len(win._steps) == len(STEP_LABELS)

    def test_review_step_is_the_real_widget(self, win: WizardWindow) -> None:
        review_index = STEP_LABELS.index("Review")
        assert isinstance(win._steps[review_index], ReviewStep)

    def test_every_other_step_is_a_placeholder(self, win: WizardWindow) -> None:
        review_index = STEP_LABELS.index("Review")
        for i, step in enumerate(win._steps):
            if i != review_index:
                assert isinstance(step, PlaceholderStep)

    def test_starts_on_first_step(self, win: WizardWindow) -> None:
        assert win._active == 0
        assert win._title_label.text() == "Source"
        assert win._back_btn.isEnabled() is False

    def test_apply_stylesheet_does_not_raise(self, win: WizardWindow) -> None:
        win.apply_stylesheet(True)
        win.apply_stylesheet(False)


class TestNavigation:
    def test_continue_advances_one_step(self, win: WizardWindow) -> None:
        win._on_continue()
        assert win._active == 1
        assert win._title_label.text() == "Transcript"

    def test_continue_tracks_furthest_reached(self, win: WizardWindow) -> None:
        win._on_continue()
        win._on_continue()
        assert win._furthest == 2

    def test_continue_stops_at_last_step(self, win: WizardWindow) -> None:
        for _ in range(len(STEP_LABELS) + 2):
            win._on_continue()
        assert win._active == len(STEP_LABELS) - 1
        assert win._continue_btn.text() == "Done"

    def test_back_disabled_on_first_step(self, win: WizardWindow) -> None:
        win._on_back()
        assert win._active == 0

    def test_back_returns_one_step(self, win: WizardWindow) -> None:
        win._on_continue()
        win._on_continue()
        win._on_back()
        assert win._active == 1

    def test_stepper_click_beyond_furthest_is_rejected(self, win: WizardWindow) -> None:
        win._go_to_step(3)
        assert win._active == 0

    def test_stepper_click_within_furthest_navigates(self, win: WizardWindow) -> None:
        win._on_continue()
        win._on_continue()
        win._go_to_step(0)
        assert win._active == 0
        win._go_to_step(2)
        assert win._active == 2

    def test_stepper_reflects_current_progress(self, win: WizardWindow) -> None:
        win._on_continue()
        assert win._stepper._cells[0]._badge.property("stepState") == "done"
        assert win._stepper._cells[1]._badge.property("stepState") == "current"


class TestNavButtonsFollowStepCanAdvance:
    def test_continue_disabled_when_step_cannot_advance(
        self, win: WizardWindow
    ) -> None:
        review_index = STEP_LABELS.index("Review")
        for _ in range(review_index):
            win._on_continue()
        review = win._steps[review_index]
        assert isinstance(review, ReviewStep)

        # ReviewStep never blocks Continue (base default) — flip a
        # PlaceholderStep's advance-ability via monkeypatch to prove the
        # shell actually listens to can_advance_changed rather than
        # ignoring it.
        step = win._steps[win._active]
        step.can_advance = lambda: False  # type: ignore[method-assign]
        step.can_advance_changed.emit(False)
        assert win._continue_btn.isEnabled() is False
