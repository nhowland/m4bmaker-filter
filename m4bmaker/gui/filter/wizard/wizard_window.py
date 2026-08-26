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

All seven of the wizard's step widgets are real (ADR-0022 merged the
former eighth stage, Complete, into Render — see below).

Source, Transcript, and Transcribe are wired together for real
(ADR-0014, ADR-0015): advancing past Source hands its ``MediaManifest``
straight to :meth:`~.transcript_step.TranscriptStep.set_source`; advancing
past Transcript hands its manifest and chosen model to
:meth:`~.transcribe_step.TranscribeStep.set_transcript_choice`. Choosing
to reuse a compatible saved transcript on the Transcript step skips
Transcribe entirely — :attr:`~.transcript_step.TranscriptStep.reuse_requested`
drives that jump, and ``StepperWidget.set_progress()``'s already-existing
(if previously unused, until ADR-0014) ``skipped`` parameter renders the
"»" glyph for it.

Profile (ADR-0016) has no predecessor step to receive data from — it
reads directly from a ``CatalogService`` this window loads once
(:attr:`_catalog_service`) and shares with the step, so a source can be
selected, a transcript chosen or transcribed, and a profile picked in any
order the stepper allows revisiting.

Scan (PRD §9.4) is the first step with two real predecessors that both
matter: advancing past Profile hands :meth:`~.scan_step.ScanStep.set_inputs`
whichever Transcript actually exists (Transcribe's own output, or the one
Transcript step found and reused — :attr:`_skipped` already tracks which,
so Scan reads the same source of truth the stepper's "»" glyph does) and
Profile's own ``selected_profile_id``, plus the shared
``CatalogService`` so Scan can freeze a snapshot of it. Advancing past
Scan in turn hands its completed ``Scan`` straight to
:meth:`~.review_step.ReviewStep.set_scan` — Review already defined that
exact hand-off (its own docstring anticipated Scan before Scan existed),
so this is the first of the two real predecessors Review was waiting on.

Render (PRD §7.2 stage 7; ADR-0019) is the last: advancing past Review
hands Source's own ``MediaManifest`` and Review's *live*
:meth:`~.review_step.ReviewStep.current_render_plan` (a method the
original Render round added — nothing outside Review could reach its
current include/exclude decisions as a real ``RenderPlan`` before) to
:meth:`~.render_step.RenderStep.set_inputs`. "Done" on this last step
closes the whole wizard window (:meth:`_on_continue`'s own special case)
— settled directly with the product owner: no separate "filter another
book" reset action is needed, since every step's own re-entry guard
already makes revisiting Source with a different file work correctly.

**The wizard was eight steps through ADR-0021; ADR-0022 merged the
former Complete step (PRD §7.2 stage 8, ADR-0020) into Render's own
Completed state**, once Render's completed panel grew its own Open
Folder button and the two screens became near-duplicates. Render's
:attr:`~.render_step.RenderStep.result`/
:attr:`~.render_step.RenderStep.validation`/
:attr:`~.render_step.RenderStep.report_path` properties (added for
Complete's original consumption) stay in place — nothing outside this
step reads them anymore, but they remain a reasonable public surface
for tests, matching how other steps expose their own terminal state.

:meth:`closeEvent` (ADR-0021) confirms before closing mid-Transcribe or
mid-Render — ADR-0010's original wireframe review already decided this
should happen, but no code ever actually did it until now. The two cases
aren't symmetric: a mid-flight ``TranscribeWorker`` gets asked to pause
(real, resumable progress via the job store, ADR-0015) before the window
closes; a mid-flight ``RenderWorker`` has no stop mechanism at all
(ADR-0019's own deliberate choice — no handle back to the underlying
ffmpeg subprocess), so that confirmation can only warn that the work in
progress will be abandoned, not offer to save it. This window is never
destroyed on close (no ``Qt.WA_DeleteOnClose``, same lazy-create-and-
reuse pattern ``CatalogWindow``/``ModelManagerWindow`` already use), so
letting an abandoned ``RenderWorker`` keep running in the background
after the window hides is safe — nothing destroys the ``QThread`` object
out from under it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_store import load_catalog
from m4bmaker.filter.transcript import Transcript

from .profile_step import ProfileStep
from .render_step import RenderStep
from .review_step import ReviewStep
from .scan_step import ScanStep
from .source_step import SourceStep
from .step_base import WizardStep
from .stepper import STEP_LABELS, StepperWidget
from .transcribe_step import TranscribeStep
from .transcript_step import TranscriptStep

_SOURCE_INDEX = STEP_LABELS.index("Source")
_TRANSCRIPT_INDEX = STEP_LABELS.index("Transcript")
_TRANSCRIBE_INDEX = STEP_LABELS.index("Transcribe")
_PROFILE_INDEX = STEP_LABELS.index("Profile")
_SCAN_INDEX = STEP_LABELS.index("Scan")
_REVIEW_INDEX = STEP_LABELS.index("Review")
_RENDER_INDEX = STEP_LABELS.index("Render")


class WizardWindow(QMainWindow):
    """Top-level window hosting the whole filter wizard."""

    def __init__(
        self,
        parent: QWidget | None = None,
        models_dest_dir: Path | None = None,
        transcripts_dest_dir: Path | None = None,
        db_path: Path | None = None,
        catalog_service: CatalogService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter Audiobook")
        self.setMinimumSize(760, 560)
        self.resize(900, 640)

        # None (the real-app default) means TranscriptStep/TranscribeStep
        # fall back to storage.models_dir()/.transcripts_dir()/
        # .database_path() themselves — these params exist so tests can
        # point them at a tmp_path instead of touching real, user-wide
        # state (mirrors ModelManagerWindow's own dest_dir constructor
        # param, same reason).
        self._models_dest_dir = models_dest_dir
        self._transcripts_dest_dir = transcripts_dest_dir
        self._db_path = db_path
        # Loaded once here, like MainWindow._show_catalog_window loads its
        # own CatalogWindow's service — ProfileStep is the sole in-process
        # owner while the wizard is open, same reasoning as that window.
        # Tests inject an in-memory CatalogService() directly instead of
        # touching the real per-user catalog.json.
        self._catalog_service = catalog_service or load_catalog()

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

        stack_wrapper = QWidget()
        stack_wrapper_layout = QVBoxLayout(stack_wrapper)
        stack_wrapper_layout.setContentsMargins(26, 0, 26, 0)
        self._stack = QStackedWidget()
        self._steps: list[WizardStep] = self._build_steps()
        for step in self._steps:
            self._stack.addWidget(step)
        stack_wrapper_layout.addWidget(self._stack)
        root.addWidget(stack_wrapper, stretch=1)

        nav = QWidget()
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(20, 10, 20, 14)
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._on_back)
        nav_layout.addWidget(self._back_btn)
        nav_layout.addStretch(1)
        self._continue_btn = QPushButton("Continue")
        self._continue_btn.setObjectName("primaryBtn")
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
            elif i == _TRANSCRIBE_INDEX:
                step = TranscribeStep(
                    models_dest_dir=self._models_dest_dir,
                    transcripts_dest_dir=self._transcripts_dest_dir,
                    db_path=self._db_path,
                )
            elif i == _PROFILE_INDEX:
                step = ProfileStep(service=self._catalog_service)
            elif i == _SCAN_INDEX:
                step = ScanStep()
            elif i == _REVIEW_INDEX:
                step = ReviewStep()
            elif i == _RENDER_INDEX:
                step = RenderStep()
            else:
                raise ValueError(f"no step widget wired up for label {label!r}")
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
                self._push_transcript_to_transcribe()
            if next_index == _PROFILE_INDEX:
                self._push_transcribe_to_profile()
            if next_index == _SCAN_INDEX:
                self._push_profile_to_scan()
            if next_index == _REVIEW_INDEX:
                self._push_scan_to_review()
            if next_index == _RENDER_INDEX:
                self._push_review_to_render()
            self._active = next_index
            self._furthest = max(self._furthest, self._active)
            self._render()
        else:
            # Last step ("Done") — settled directly with the product
            # owner: closes the whole wizard window. No separate "filter
            # another book" reset action is needed alongside it — every
            # step's own re-entry guard already makes revisiting Source
            # with a different file work correctly on its own.
            self.close()

    def _push_source_to_transcript(self) -> None:
        source = self._steps[_SOURCE_INDEX]
        transcript_step = self._steps[_TRANSCRIPT_INDEX]
        assert isinstance(source, SourceStep)
        assert isinstance(transcript_step, TranscriptStep)
        if source.manifest is not None:
            transcript_step.set_source(source.manifest)

    def _push_transcript_to_transcribe(self) -> None:
        transcript_step = self._steps[_TRANSCRIPT_INDEX]
        transcribe_step = self._steps[_TRANSCRIBE_INDEX]
        assert isinstance(transcript_step, TranscriptStep)
        assert isinstance(transcribe_step, TranscribeStep)
        manifest = transcript_step.manifest
        model_spec = transcript_step.chosen_model
        if manifest is not None and model_spec is not None:
            transcribe_step.set_transcript_choice(manifest, model_spec)

    def _current_transcript(self) -> Transcript | None:
        """Whichever predecessor actually produced the real Transcript —
        Transcribe's own output, or the one Transcript step found and
        reused (Transcribe skipped entirely, per ``_on_transcript_reuse``
        below) — ``_skipped`` is already the shell's own source of truth
        for which path was taken, same as it is for the stepper's own "»"
        glyph. Shared by both hand-offs that need "the transcript,
        whichever predecessor made it" (into Profile, and into Scan) so
        the selection logic exists in exactly one place."""
        transcript_step = self._steps[_TRANSCRIPT_INDEX]
        transcribe_step = self._steps[_TRANSCRIBE_INDEX]
        assert isinstance(transcript_step, TranscriptStep)
        assert isinstance(transcribe_step, TranscribeStep)
        return (
            transcript_step.compatible_transcript
            if _TRANSCRIBE_INDEX in self._skipped
            else transcribe_step.transcript
        )

    def _push_transcribe_to_profile(self) -> None:
        profile_step = self._steps[_PROFILE_INDEX]
        assert isinstance(profile_step, ProfileStep)
        profile_step.set_transcript(self._current_transcript())

    def _push_profile_to_scan(self) -> None:
        profile_step = self._steps[_PROFILE_INDEX]
        scan_step = self._steps[_SCAN_INDEX]
        assert isinstance(profile_step, ProfileStep)
        assert isinstance(scan_step, ScanStep)
        transcript = self._current_transcript()
        profile_id = profile_step.selected_profile_id
        if transcript is not None and profile_id is not None:
            scan_step.set_inputs(transcript, self._catalog_service, profile_id)

    def _push_scan_to_review(self) -> None:
        scan_step = self._steps[_SCAN_INDEX]
        review_step = self._steps[_REVIEW_INDEX]
        assert isinstance(scan_step, ScanStep)
        assert isinstance(review_step, ReviewStep)
        scan = scan_step.scan
        transcript = scan_step.transcript
        if scan is not None and transcript is not None:
            review_step.set_scan(
                scan,
                self._catalog_service,
                transcript,
                transcript.source.duration_ms,
            )

    def _push_review_to_render(self) -> None:
        source = self._steps[_SOURCE_INDEX]
        review_step = self._steps[_REVIEW_INDEX]
        render_step = self._steps[_RENDER_INDEX]
        assert isinstance(source, SourceStep)
        assert isinstance(review_step, ReviewStep)
        assert isinstance(render_step, RenderStep)
        manifest = source.manifest
        render_plan = review_step.current_render_plan()
        if manifest is not None and render_plan is not None:
            render_step.set_inputs(manifest, render_plan)

    def _on_transcript_reuse(self) -> None:
        """TranscriptStep chose to reuse a compatible saved transcript —
        Transcribe has nothing to do, so skip straight to Profile. This
        bypasses ``_on_continue``'s own dispatch table entirely, so the
        Transcribe -> Profile push has to happen here too, not just on
        the normal-advance path."""
        self._skipped.add(_TRANSCRIBE_INDEX)
        self._active = _PROFILE_INDEX
        self._furthest = max(self._furthest, self._active)
        self._push_transcribe_to_profile()
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

    # ── shutdown ─────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Confirm before closing mid-Transcribe or mid-Render (ADR-0010's
        original wireframe review, ADR-0021's real implementation of it).
        The two cases aren't symmetric — see this module's own docstring
        for why Transcribe gets asked to pause (real, resumable progress)
        while Render can only be warned about (no stop mechanism exists).
        """
        transcribe_step = self._steps[_TRANSCRIBE_INDEX]
        assert isinstance(transcribe_step, TranscribeStep)
        transcribe_worker = transcribe_step._worker
        if transcribe_worker is not None and transcribe_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Pause Transcription?",
                "A transcription is in progress.\n"
                "Closing now will pause it — your progress is saved and "
                "resumes next time.\n"
                "Are you sure you want to close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            transcribe_worker.request_pause()
            transcribe_worker.wait(5000)

        render_step = self._steps[_RENDER_INDEX]
        assert isinstance(render_step, RenderStep)
        render_worker = render_step._worker
        if render_worker is not None and render_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Render in Progress",
                "A render is in progress and cannot be safely stopped.\n"
                "Closing now will abandon it — you'll need to start over.\n"
                "Are you sure you want to close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            # No request_pause()/request_cancel() exists on RenderWorker
            # (ADR-0019) — nothing to ask it to do. Left running in the
            # background; safe since this window is never destroyed on
            # close (see module docstring).

        super().closeEvent(event)

    # ── theme ────────────────────────────────────────────────────────────

    def apply_stylesheet(self, dark: bool) -> None:
        from m4bmaker.gui.styles import get_stylesheet

        self.setStyleSheet(get_stylesheet(dark))
