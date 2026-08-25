"""The filter wizard's shell window (ADR-0010, PRD §7.2).

Ports ``docs/design/wizard-shell-wireframe.html``'s approved shell
design: a horizontal stepper across the top, a content pane below driven
by a ``QStackedWidget`` (one :class:`~.step_base.WizardStep` per PRD
§7.2 stage), and a Back/Continue footer.

Unlike the wireframe's own consistent-height trick (a runtime JS probe
that measured every step's rendered content once to size the window),
Qt's layout system handles this natively — ``QStackedWidget`` already
sizes itself to its largest child, so every step shares one window size
for free, with no measurement workaround needed.

Only Review (index 5) has a real step widget; the other seven are
:class:`~.placeholder_step.PlaceholderStep` stand-ins, since only the
shell and Review have been through ADR-0010's wireframe review so far.
The wizard is still fully navigable end-to-end today — Back/Continue and
the stepper's click-to-revisit all work against the placeholders exactly
as they will once each step gets built for real.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .placeholder_step import PlaceholderStep
from .review_step import ReviewStep
from .step_base import WizardStep
from .stepper import STEP_LABELS, StepperWidget

_REVIEW_INDEX = STEP_LABELS.index("Review")

_PLACEHOLDER_SUBTITLES: dict[int, str] = {
    0: "Inspect eligibility, selected audio track, duration, chapters, "
    "and storage estimate.",
    1: "Reuse a compatible saved transcript, or set up local transcription.",
    2: "Durable and resumable — progress persists across app restarts, "
    "picking up chapter by chapter.",
    3: "Pick a saved filter profile, or manage the underlying word "
    "catalog without leaving the wizard.",
    4: "Matches the transcript against an immutable snapshot of the "
    "selected profile.",
    6: "Confirm the output plan, then attenuate, encode, mux, and validate.",
    7: "Validation status, output location, and the persisted filter report.",
}


class WizardWindow(QMainWindow):
    """Top-level window hosting the whole filter wizard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter Audiobook")
        self.setMinimumSize(760, 560)
        self.resize(900, 640)

        self._active = 0
        self._furthest = 0

        self._build_ui()
        self._render()

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stepper = StepperWidget()
        self._stepper.step_clicked.connect(self._go_to_step)
        root.addWidget(self._stepper)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(26, 20, 26, 6)
        header_layout.setSpacing(4)
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        header_layout.addWidget(self._title_label)
        self._subtitle_label = QLabel()
        self._subtitle_label.setObjectName("statusLabel")
        self._subtitle_label.setWordWrap(True)
        header_layout.addWidget(self._subtitle_label)
        root.addWidget(header)

        self._stack = QStackedWidget()
        self._steps: list[WizardStep] = self._build_steps()
        for step in self._steps:
            self._stack.addWidget(step)
        root.addWidget(self._stack, stretch=1)

        nav = QWidget()
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(20, 10, 20, 14)
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._on_back)
        nav_layout.addWidget(self._back_btn)
        nav_layout.addStretch(1)
        self._continue_btn = QPushButton("Continue")
        self._continue_btn.clicked.connect(self._on_continue)
        nav_layout.addWidget(self._continue_btn)
        root.addWidget(nav)

    def _build_steps(self) -> list[WizardStep]:
        steps: list[WizardStep] = []
        for i, label in enumerate(STEP_LABELS):
            step: WizardStep
            if i == _REVIEW_INDEX:
                step = ReviewStep()
            else:
                step = PlaceholderStep(label, _PLACEHOLDER_SUBTITLES.get(i, ""))
            step.can_advance_changed.connect(self._update_nav_buttons)
            steps.append(step)
        return steps

    # ── navigation ───────────────────────────────────────────────────────

    def _go_to_step(self, index: int) -> None:
        if index > self._furthest:
            return
        self._active = index
        self._render()

    def _on_back(self) -> None:
        if self._active > 0:
            self._active -= 1
            self._render()

    def _on_continue(self) -> None:
        if self._active < len(self._steps) - 1:
            self._active += 1
            self._furthest = max(self._furthest, self._active)
            self._render()

    def _render(self) -> None:
        step = self._steps[self._active]
        self._stack.setCurrentIndex(self._active)
        self._title_label.setText(step.step_title)
        self._subtitle_label.setText(step.step_subtitle)
        self._stepper.set_progress(self._active, self._furthest)
        self._back_btn.setEnabled(self._active > 0)
        is_last = self._active == len(self._steps) - 1
        self._continue_btn.setText("Done" if is_last else "Continue")
        self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        self._continue_btn.setEnabled(self._steps[self._active].can_advance())

    # ── theme ────────────────────────────────────────────────────────────

    def apply_stylesheet(self, dark: bool) -> None:
        from m4bmaker.gui.styles import get_stylesheet

        self.setStyleSheet(get_stylesheet(dark))
