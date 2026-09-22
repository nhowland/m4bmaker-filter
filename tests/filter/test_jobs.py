"""Tests for m4bmaker.filter.jobs — job state machine (PRD §11.2)."""

from __future__ import annotations

import pytest

from m4bmaker.filter.jobs import (
    ALLOWED_TRANSITIONS,
    RECOVERABLE_STATES,
    TERMINAL_STATES,
    InvalidJobTransition,
    JobState,
    JobType,
    is_terminal,
    is_valid_transition,
    validate_transition,
)


class TestEveryStateHasATransitionEntry:
    def test_all_states_present_in_matrix(self) -> None:
        assert set(ALLOWED_TRANSITIONS.keys()) == set(JobState)


class TestHappyPathTransitions:
    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            (JobState.QUEUED, JobState.PREPARING),
            (JobState.PREPARING, JobState.RUNNING),
            (JobState.RUNNING, JobState.COMPLETED),
        ],
    )
    def test_valid(self, from_state: JobState, to_state: JobState) -> None:
        assert is_valid_transition(from_state, to_state)
        validate_transition(from_state, to_state)  # must not raise


class TestPauseResumeCycle:
    def test_running_to_pausing_to_paused_to_resuming_to_running(self) -> None:
        path = [
            (JobState.RUNNING, JobState.PAUSING),
            (JobState.PAUSING, JobState.PAUSED),
            (JobState.PAUSED, JobState.RESUMING),
            (JobState.RESUMING, JobState.RUNNING),
        ]
        for from_state, to_state in path:
            assert is_valid_transition(from_state, to_state)

    def test_pausing_cannot_go_directly_back_to_running(self) -> None:
        """A pause request must resolve to PAUSED, never silently resume —
        otherwise the pause request was meaningless (PRD §11.3)."""
        assert not is_valid_transition(JobState.PAUSING, JobState.RUNNING)

    def test_paused_never_auto_resumes(self) -> None:
        """PAUSED can only leave via explicit User action (RESUMING) or
        CANCELLED/NEEDS_ATTENTION — never directly to RUNNING (PRD §11.3:
        'Do not automatically start work')."""
        assert not is_valid_transition(JobState.PAUSED, JobState.RUNNING)


class TestNeedsAttentionRecovery:
    def test_needs_attention_is_recoverable_not_terminal(self) -> None:
        assert JobState.NEEDS_ATTENTION in RECOVERABLE_STATES
        assert JobState.NEEDS_ATTENTION not in TERMINAL_STATES
        assert not is_terminal(JobState.NEEDS_ATTENTION)

    def test_needs_attention_can_be_cancelled(self) -> None:
        assert is_valid_transition(JobState.NEEDS_ATTENTION, JobState.CANCELLED)

    def test_needs_attention_can_be_requeued(self) -> None:
        """Supports the relink workflow in PRD §10.4."""
        assert is_valid_transition(JobState.NEEDS_ATTENTION, JobState.QUEUED)

    def test_needs_attention_cannot_jump_straight_to_running(self) -> None:
        assert not is_valid_transition(JobState.NEEDS_ATTENTION, JobState.RUNNING)


class TestTerminalStatesHaveNoOutgoingTransitions:
    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_no_outgoing_transitions(self, state: JobState) -> None:
        assert ALLOWED_TRANSITIONS[state] == frozenset()
        assert is_terminal(state)

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_cannot_transition_out(self, state: JobState) -> None:
        for other in JobState:
            if other is state:
                continue
            assert not is_valid_transition(state, other)


class TestInvalidTransitionRaises:
    def test_completed_to_running_raises(self) -> None:
        with pytest.raises(InvalidJobTransition) as exc_info:
            validate_transition(JobState.COMPLETED, JobState.RUNNING)
        assert exc_info.value.from_state is JobState.COMPLETED
        assert exc_info.value.to_state is JobState.RUNNING

    def test_queued_to_completed_raises(self) -> None:
        """QUEUED must go through PREPARING/RUNNING — cannot skip straight
        to COMPLETED."""
        with pytest.raises(InvalidJobTransition):
            validate_transition(JobState.QUEUED, JobState.COMPLETED)


class TestJobTypesMatchPRD:
    def test_five_job_types_exist(self) -> None:
        assert {t.value for t in JobType} == {
            "ModelDownloadJob",
            "TranscriptionJob",
            "ScanJob",
            "RenderJob",
            "ValidationJob",
        }
