"""Tests for m4bmaker.gui.filter.wizard.stepper.StepperWidget (ADR-0010).

Same headless-behavioral approach as the rest of tests/gui/filter/:
build the real widget tree under QT_QPA_PLATFORM=offscreen and assert on
resulting widget state, since this environment cannot screenshot a live
window from inside an automated test run.
"""

from __future__ import annotations

import pytest

from m4bmaker.gui.filter.wizard.stepper import STEP_LABELS, StepperWidget

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture()
def stepper() -> StepperWidget:
    return StepperWidget()


class TestConstruction:
    def test_creates_one_cell_per_step_label(self, stepper: StepperWidget) -> None:
        assert len(stepper._cells) == len(STEP_LABELS)

    def test_initial_state_is_all_locked_except_current(
        self, stepper: StepperWidget
    ) -> None:
        stepper.set_progress(current=0, furthest=0)
        assert stepper._cells[0]._badge.property("stepState") == "current"
        for i in range(1, len(STEP_LABELS)):
            assert stepper._cells[i]._badge.property("stepState") == "locked"
            assert stepper._cells[i]._clickable is False


class TestProgress:
    def test_earlier_steps_marked_done_with_checkmark(
        self, stepper: StepperWidget
    ) -> None:
        stepper.set_progress(current=3, furthest=3)
        for i in range(3):
            assert stepper._cells[i]._badge.property("stepState") == "done"
            assert stepper._cells[i]._badge.text() == "✓"
        assert stepper._cells[3]._badge.property("stepState") == "current"

    def test_steps_past_furthest_are_locked_and_not_clickable(
        self, stepper: StepperWidget
    ) -> None:
        stepper.set_progress(current=2, furthest=2)
        for i in range(3, len(STEP_LABELS)):
            assert stepper._cells[i]._badge.property("stepState") == "locked"
            assert stepper._cells[i]._clickable is False

    def test_furthest_reached_but_not_active_is_reachable_and_neutral(
        self, stepper: StepperWidget
    ) -> None:
        """Mirrors the wireframe's exact edge case: clicking Back to
        review an earlier step leaves the furthest-reached step neither
        "current" nor styled as locked — just clickable and unstyled."""
        stepper.set_progress(current=0, furthest=3)
        assert stepper._cells[3]._badge.property("stepState") == ""
        assert stepper._cells[3]._clickable is True

    def test_skipped_step_shows_skip_glyph_not_checkmark(
        self, stepper: StepperWidget
    ) -> None:
        stepper.set_progress(current=3, furthest=3, skipped={2})
        assert stepper._cells[2]._badge.property("stepState") == "done"
        assert stepper._cells[2]._badge.text() == "»"

    def test_connecting_line_fills_across_a_done_boundary(
        self, stepper: StepperWidget
    ) -> None:
        stepper.set_progress(current=2, furthest=2)
        # Cell 0 is done, so its line-after and cell 1's line-before both
        # fill — the two flexed half-segments must agree or there'd be a
        # visible color seam at the boundary (this exact bug was caught
        # and fixed in the HTML wireframe this ports).
        assert stepper._cells[0]._line_after.property("filled") == "true"
        assert stepper._cells[1]._line_before.property("filled") == "true"

    def test_line_does_not_fill_past_the_current_step(
        self, stepper: StepperWidget
    ) -> None:
        stepper.set_progress(current=1, furthest=1)
        assert stepper._cells[1]._line_after.property("filled") != "true"
        assert stepper._cells[2]._line_before.property("filled") != "true"


class TestClickSignal:
    def test_clicking_reachable_cell_emits_step_clicked(
        self, stepper: StepperWidget
    ) -> None:
        stepper.set_progress(current=0, furthest=2)
        received: list[int] = []
        stepper.step_clicked.connect(received.append)
        stepper._cells[1].clicked.emit(1)
        assert received == [1]
