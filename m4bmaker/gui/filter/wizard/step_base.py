"""Common interface every wizard step widget implements (ADR-0010).

Kept deliberately small: a step is just a ``QWidget`` with a title/
subtitle pair for the content pane's header (mirroring the wireframe's
eyebrow/title/sub block) and a ``can_advance`` signal so the shell knows
when to enable Continue — a step decides for itself what "ready to move
on" means (a placeholder never is; Review always is, since PRD §7.1
treats reviewing as optional-but-available, not a hard gate).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class WizardStep(QWidget):
    """Base class for one step's content widget.

    ``step_title``/``step_subtitle`` are plain attributes, not
    properties — they're fixed per step type and never change at
    runtime, unlike ``can_advance`` which a step re-evaluates as its own
    state changes and announces via :attr:`can_advance_changed`.
    """

    step_title: str = ""
    step_subtitle: str = ""

    can_advance_changed = Signal(bool)

    def can_advance(self) -> bool:
        """Whether the wizard shell should allow Continue from this step.
        Base default is always-advanceable — a step widget that needs to
        gate progress (e.g. "select a source first") overrides this and
        emits :attr:`can_advance_changed` whenever the answer changes."""
        return True
