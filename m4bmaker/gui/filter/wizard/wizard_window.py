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

Source, Transcript, and Review have real step widgets; the other five
are :class:`~.placeholder_step.PlaceholderStep` stand-ins, since only
those three have been through a wireframe review so far. The wizard is
still fully navigable end-to-end today — Back/Continue and the
stepper's click-to-revisit all work against the placeholders exactly as
they will once each step gets built for real.

Source and Transcript are wired together for real (ADR-0014): advancing
past Source hands its ``MediaManifest`` straight to
:meth:`~.transcript_step.TranscriptStep.set_source`, and choosing to
reuse a compatible saved transcript there skips Transcribe entirely —
:attr:`~.transcript_step.TranscriptStep.reuse_requested` drives that
jump, and ``StepperWidget.set_progress()``'s already-existing (if
previously unused) ``skipped`` parameter renders the "»" glyph for it.
"""

from __future__ import annotations

from pathlib import Path

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
from .source_step import SourceStep
from .step_base import WizardStep
from .stepper import STEP_LABELS, StepperWidget
from .transcript_step import TranscriptStep

_SOURCE_INDEX = STEP_LABELS.index("Source")
_TRANSCRIPT_INDEX = STEP_LABELS.index("Transcript")
_TRANSCRIBE_INDEX = STEP_LABELS.index("Transcribe")
_PROFILE_INDEX = STEP_LABELS.index("Profile")
_REVIEW_INDEX = STEP_LABELS.index("Review")

_PLACEHOLDER_SUBTITLES: dict[int, str] = {
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

    def __init__(
        self,
        parent: QWidget | None = None,
        models_dest_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter Audiobook")
        self.setMinimumSize(760, 560)
        self.resize(900, 640)

        # None (the real-app default) means TranscriptStep falls back to
        # storage.models_dir() itself — this param exists so tests can
        # point it at a tmp_path instead of touching the real,
        # user-wide models directory (mirrors ModelManagerWindow's own
        # dest_dir constructor param, same reason).
        self._models_dest_dir = models_dest_dir

        self._active = 0
        self._furthest = 0
        # Steps satisfied without running — currently only Transcribe,
        # and only when TranscriptStep's reuse_requested fires (ADR-0014).
        self._skipped: set[int] = set()

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
            if i == _SOURCE_INDEX:
                step = SourceStep()
            elif i == _TRANSCRIPT_INDEX:
                step = TranscriptStep(dest_dir=self._models_dest_dir)
            elif i == _REVIEW_INDEX:
                step = ReviewStep()
            else:
                step = PlaceholderStep(label, _PLACEHOLDER_SUBTITLES.get(i, ""))
            step.can_advance_changed.connect(self._update_nav_buttons)
            steps.append(step)
        transcript_step = steps[_TRANSCRIPT_INDEX]
        assert isinstance(transcript_step, TranscriptStep)
        transcript_step.reuse_requested.connect(self._on_transcript_reuse)
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
        # Defensive, not just cosmetic: the Continue button's disabled
        # state is the primary gate, but this guards the actual
        # transition too, in case something besides that button's own
        # click signal ever calls this (a shortcut, a future automated
        # "run wizard end-to-end" caller, etc.) — the step's own
        # can_advance() stays the single source of truth either way.
        if not self._steps[self._active].can_advance():
            return
        if self._active < len(self._steps) - 1:
            next_index = self._active + 1
            if self._active == _SOURCE_INDEX and next_index == _TRANSCRIPT_INDEX:
                self._push_source_to_transcript()
            if next_index == _TRANSCRIBE_INDEX:
                # Actually visiting it for real now, not skipping it —
                # matters if the User previously chose "Use existing",
                # went Back, and is now transcribing for real instead.
                self._skipped.discard(_TRANSCRIBE_INDEX)
            self._active = next_index
            self._furthest = max(self._furthest, self._active)
            self._render()

    def _push_source_to_transcript(self) -> None:
        source = self._steps[_SOURCE_INDEX]
        transcript_step = self._steps[_TRANSCRIPT_INDEX]
        assert isinstance(source, SourceStep)
        assert isinstance(transcript_step, TranscriptStep)
        if source.manifest is not None:
            transcript_step.set_source(source.manifest)

    def _on_transcript_reuse(self) -> None:
        """TranscriptStep chose to reuse a compatible saved transcript —
        Transcribe has nothing to do, so skip straight to Profile."""
        self._skipped.add(_TRANSCRIBE_INDEX)
        self._active = _PROFILE_INDEX
        self._furthest = max(self._furthest, self._active)
        self._render()

    def _render(self) -> None:
        step = self._steps[self._active]
        self._stack.setCurrentIndex(self._active)
        self._title_label.setText(step.step_title)
        self._subtitle_label.setText(step.step_subtitle)
        self._stepper.set_progress(self._active, self._furthest, self._skipped)
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
