"""Job type/state model and the allowed-transition matrix (PRD §11.1-§11.2).

This module defines the state machine only. Persistence, scheduling, and the
worker-thread wiring that actually runs jobs belong to the Job Orchestrator
(PRD §14.2), which is out of scope for G1 (PRD §17.4: G1 adds "an explicit
job-state model and transition tests" and nothing that "downloads models,
transcribes, or renders production media").
"""

from __future__ import annotations

from enum import Enum


class JobType(Enum):
    """The five job types enumerated in PRD §11.1."""

    MODEL_DOWNLOAD = "ModelDownloadJob"
    TRANSCRIPTION = "TranscriptionJob"
    SCAN = "ScanJob"
    RENDER = "RenderJob"
    VALIDATION = "ValidationJob"


class JobState(Enum):
    """States from the PRD §11.2 diagram, plus the two extra terminal states
    (FAILED, CANCELLED) and the recoverable NEEDS_ATTENTION state that the
    diagram calls out in prose but does not draw inline."""

    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


#: States with no valid outgoing transition. A job in one of these states is
#: done; starting new work means creating a new job, never transitioning out.
TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
)

#: NEEDS_ATTENTION is deliberately *not* terminal: PRD §10.4 requires a
#: relink workflow and §11.2 requires "User actions for every
#: terminal/recoverable state" — a job stuck here must be resolvable by the
#: user (cancel, or requeue after fixing the underlying problem) rather than
#: being a dead end.
RECOVERABLE_STATES: frozenset[JobState] = frozenset({JobState.NEEDS_ATTENTION})

#: The allowed-transition matrix required by PRD §11.2 ("The implementation
#: must define an allowed-transition matrix"). Every entry is a deliberate
#: product decision, not just "whatever the code happened to do":
#:
#: - QUEUED can be cancelled before any work starts.
#: - PREPARING (source/model verification, per §11.3) can fail, be
#:   cancelled, or flag NEEDS_ATTENTION (e.g. a compatibility check fails).
#: - RUNNING is the only state that can request PAUSING (§11.3: "When Pause
#:   is requested, stop scheduling new chunks").
#: - PAUSING must resolve to PAUSED; it can still fail or be cancelled
#:   while the in-flight atomic unit is finishing (§11.3), but it cannot
#:   silently return to RUNNING — that would defeat the pause request.
#: - PAUSED can only leave via an explicit user action: RESUMING or
#:   CANCELLED (§11.3: "Do not automatically start work"), or
#:   NEEDS_ATTENTION if a compatibility re-check on resume fails (§11.3
#:   last bullet).
#: - RESUMING re-verifies compatibility (§11.3) before returning to RUNNING;
#:   it can fail or need attention instead.
#: - Terminal states have no outgoing transitions.
#: - NEEDS_ATTENTION resolves only via explicit user choice: cancel, or
#:   requeue (e.g. after a relink, §10.4) back to QUEUED.
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.PREPARING, JobState.CANCELLED}),
    JobState.PREPARING: frozenset(
        {
            JobState.RUNNING,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.NEEDS_ATTENTION,
        }
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.PAUSING,
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.NEEDS_ATTENTION,
        }
    ),
    JobState.PAUSING: frozenset({JobState.PAUSED, JobState.FAILED, JobState.CANCELLED}),
    JobState.PAUSED: frozenset(
        {JobState.RESUMING, JobState.CANCELLED, JobState.NEEDS_ATTENTION}
    ),
    JobState.RESUMING: frozenset(
        {JobState.RUNNING, JobState.FAILED, JobState.NEEDS_ATTENTION}
    ),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.NEEDS_ATTENTION: frozenset({JobState.CANCELLED, JobState.QUEUED}),
}


class InvalidJobTransition(Exception):
    """Raised when code attempts a transition not present in
    :data:`ALLOWED_TRANSITIONS`. Callers (the future Job Orchestrator) must
    treat this as a programming error, not a user-facing condition — the UI
    should never offer an action that would trigger it."""

    def __init__(self, from_state: JobState, to_state: JobState) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid job state transition: {from_state.value} -> {to_state.value}"
        )


def is_valid_transition(from_state: JobState, to_state: JobState) -> bool:
    """Return whether *from_state* -> *to_state* is an allowed transition."""
    return to_state in ALLOWED_TRANSITIONS[from_state]


def validate_transition(from_state: JobState, to_state: JobState) -> None:
    """Raise :class:`InvalidJobTransition` if the transition is not allowed."""
    if not is_valid_transition(from_state, to_state):
        raise InvalidJobTransition(from_state, to_state)


def is_terminal(state: JobState) -> bool:
    """Return whether *state* has no valid outgoing transition."""
    return state in TERMINAL_STATES
