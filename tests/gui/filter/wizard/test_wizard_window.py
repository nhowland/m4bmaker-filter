"""Tests for m4bmaker.gui.filter.wizard.wizard_window.WizardWindow
(ADR-0010, PRD §7.2).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from m4bmaker.filter.model_manager import KNOWN_MODELS
from m4bmaker.filter.models import MediaManifest
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.gui.filter.wizard.placeholder_step import PlaceholderStep
from m4bmaker.gui.filter.wizard.review_step import ReviewStep
from m4bmaker.gui.filter.wizard.source_step import SourceStep
from m4bmaker.gui.filter.wizard.stepper import STEP_LABELS
from m4bmaker.gui.filter.wizard.transcribe_step import TranscribeStep
from m4bmaker.gui.filter.wizard.transcript_step import TranscriptStep
from m4bmaker.gui.filter.wizard.wizard_window import WizardWindow

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture()
def win(tmp_path: Path) -> WizardWindow:
    # models_dest_dir points TranscriptStep at tmp_path rather than the
    # real, user-wide models directory — see WizardWindow's own
    # docstring for that param.
    return WizardWindow(models_dest_dir=tmp_path)


def _make_source_eligible(win: WizardWindow) -> None:
    """Advance Source past its own gate by injecting an eligible manifest
    directly. These navigation tests exercise the shell's own mechanics
    (advancing/back/stepper sync) — SourceStep's own gating logic has its
    own dedicated tests in test_source_step.py — so this deliberately
    bypasses the real file-picker/inspection flow rather than duplicating
    that coverage here.
    """
    source = win._steps[STEP_LABELS.index("Source")]
    assert isinstance(source, SourceStep)
    manifest = MediaManifest(
        schema_version=1,
        source_path="/books/a.m4b",
        fingerprint="sha256:x",
        duration_ms=10_000,
        tracks=(),
        selected_track_index=None,
        selected_track_is_fallback=False,
        chapters=(),
        required_metadata={},
        cover_present=False,
        eligible=True,
    )
    source._on_inspect_finished(manifest)


def _make_transcript_ready(win: WizardWindow) -> None:
    """Pre-install base.en into TranscriptStep's own dest_dir so that once
    the shell auto-pushes Source's manifest into it (see
    WizardWindow._push_source_to_transcript, triggered by _on_continue),
    find_compatible_transcript() finds nothing for this fake fingerprint,
    the model chooser picks the pre-installed model by default, and
    Continue enables — same "test the shell's mechanics, not the step's
    own logic" split as _make_source_eligible above; TranscriptStep's own
    choice/download logic has its own dedicated tests.
    """
    transcript_step = win._steps[STEP_LABELS.index("Transcript")]
    assert isinstance(transcript_step, TranscriptStep)
    dest_dir = transcript_step._dest_dir
    base_en = KNOWN_MODELS[0]
    path = dest_dir / base_en.filename()
    path.touch()
    os.truncate(path, base_en.size_bytes)


def _make_transcribe_ready(win: WizardWindow) -> None:
    """Deliver a fake completed Transcript directly to TranscribeStep's own
    result handler, bypassing the real TranscribeWorker/whisper.cpp/job
    store entirely — same "test the shell, not the step" split as the two
    helpers above; TranscribeStep's own worker/state-machine logic has its
    own dedicated tests.
    """
    transcribe_step = win._steps[STEP_LABELS.index("Transcribe")]
    assert isinstance(transcribe_step, TranscribeStep)
    # set_transcript_choice() is what a real flow calls first, setting
    # _manifest before this step ever runs — set directly here (rather
    # than via the real method, which would also do a real job-store
    # lookup) so the fingerprint matches what real navigation pushes in
    # later, and TranscribeStep's own re-entry guard (same fingerprint,
    # already completed -> no-op) doesn't clobber this on the next
    # _on_continue().
    manifest = MediaManifest(
        schema_version=1,
        source_path="/books/a.m4b",
        fingerprint="sha256:x",
        duration_ms=10_000,
        tracks=(),
        selected_track_index=None,
        selected_track_is_fallback=False,
        chapters=(),
        required_metadata={},
        cover_present=False,
        eligible=True,
    )
    transcribe_step._manifest = manifest
    transcript = Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint="sha256:x", duration_ms=10_000, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp", version="1.9.2", model="base.en", model_checksum="x"
        ),
        segments=(),
    )
    transcribe_step._on_result_ready(transcript)


class TestConstruction:
    def test_window_creates_without_error(self, win: WizardWindow) -> None:
        assert win is not None

    def test_has_one_step_widget_per_label(self, win: WizardWindow) -> None:
        assert len(win._steps) == len(STEP_LABELS)

    def test_source_step_is_the_real_widget(self, win: WizardWindow) -> None:
        source_index = STEP_LABELS.index("Source")
        assert isinstance(win._steps[source_index], SourceStep)

    def test_transcript_step_is_the_real_widget(self, win: WizardWindow) -> None:
        transcript_index = STEP_LABELS.index("Transcript")
        assert isinstance(win._steps[transcript_index], TranscriptStep)

    def test_transcribe_step_is_the_real_widget(self, win: WizardWindow) -> None:
        transcribe_index = STEP_LABELS.index("Transcribe")
        assert isinstance(win._steps[transcribe_index], TranscribeStep)

    def test_review_step_is_the_real_widget(self, win: WizardWindow) -> None:
        review_index = STEP_LABELS.index("Review")
        assert isinstance(win._steps[review_index], ReviewStep)

    def test_every_other_step_is_a_placeholder(self, win: WizardWindow) -> None:
        real_indices = {
            STEP_LABELS.index("Source"),
            STEP_LABELS.index("Transcript"),
            STEP_LABELS.index("Transcribe"),
            STEP_LABELS.index("Review"),
        }
        for i, step in enumerate(win._steps):
            if i not in real_indices:
                assert isinstance(step, PlaceholderStep)

    def test_starts_on_first_step(self, win: WizardWindow) -> None:
        assert win._active == 0
        assert win._title_label.text() == "Select Source"
        assert win._back_btn.isEnabled() is False

    def test_apply_stylesheet_does_not_raise(self, win: WizardWindow) -> None:
        win.apply_stylesheet(True)
        win.apply_stylesheet(False)


class TestNavigation:
    def test_continue_advances_one_step(self, win: WizardWindow) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        win._on_continue()
        assert win._active == 1
        assert win._title_label.text() == "Choose Transcript Path"

    def test_continue_tracks_furthest_reached(self, win: WizardWindow) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        win._on_continue()
        win._on_continue()
        assert win._furthest == 2

    def test_continue_stops_at_last_step(self, win: WizardWindow) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        for _ in range(len(STEP_LABELS) + 2):
            win._on_continue()
        assert win._active == len(STEP_LABELS) - 1
        assert win._continue_btn.text() == "Done"

    def test_back_disabled_on_first_step(self, win: WizardWindow) -> None:
        win._on_back()
        assert win._active == 0

    def test_back_returns_one_step(self, win: WizardWindow) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        win._on_continue()
        win._on_continue()
        win._on_back()
        assert win._active == 1

    def test_stepper_click_beyond_furthest_is_rejected(self, win: WizardWindow) -> None:
        win._go_to_step(3)
        assert win._active == 0

    def test_stepper_click_within_furthest_navigates(self, win: WizardWindow) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        win._on_continue()
        win._on_continue()
        win._go_to_step(0)
        assert win._active == 0
        win._go_to_step(2)
        assert win._active == 2

    def test_stepper_reflects_current_progress(self, win: WizardWindow) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        win._on_continue()
        assert win._stepper._cells[0]._badge.property("stepState") == "done"
        assert win._stepper._cells[1]._badge.property("stepState") == "current"


class TestNavButtonsFollowStepCanAdvance:
    def test_continue_disabled_when_step_cannot_advance(
        self, win: WizardWindow
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        review_index = STEP_LABELS.index("Review")
        for _ in range(review_index):
            win._on_continue()
        assert win._active == review_index
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


class TestSourceToTranscriptWiring:
    def test_continue_past_source_pushes_manifest_to_transcript(
        self, win: WizardWindow
    ) -> None:
        _make_source_eligible(win)
        source = win._steps[STEP_LABELS.index("Source")]
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        assert isinstance(source, SourceStep)
        assert isinstance(transcript_step, TranscriptStep)
        assert transcript_step.manifest is None

        win._on_continue()

        assert transcript_step.manifest is source.manifest

    def test_re_pushed_on_a_second_pass_with_a_different_source(
        self, win: WizardWindow
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        win._on_continue()  # Source -> Transcript
        win._on_back()  # Transcript -> Source

        source = win._steps[STEP_LABELS.index("Source")]
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        assert isinstance(source, SourceStep)
        assert isinstance(transcript_step, TranscriptStep)

        new_manifest = MediaManifest(
            schema_version=1,
            source_path="/books/b.m4b",
            fingerprint="sha256:different",
            duration_ms=20_000,
            tracks=(),
            selected_track_index=None,
            selected_track_is_fallback=False,
            chapters=(),
            required_metadata={},
            cover_present=False,
            eligible=True,
        )
        source._on_inspect_finished(new_manifest)
        win._on_continue()

        assert transcript_step.manifest is new_manifest


class TestTranscriptReuseSkipsTranscribe:
    def test_reuse_requested_jumps_to_profile_and_marks_transcribe_skipped(
        self, win: WizardWindow
    ) -> None:
        _make_source_eligible(win)
        win._on_continue()  # Source -> Transcript
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        assert isinstance(transcript_step, TranscriptStep)

        transcript_step.reuse_requested.emit()

        profile_index = STEP_LABELS.index("Profile")
        transcribe_index = STEP_LABELS.index("Transcribe")
        assert win._active == profile_index
        assert win._furthest == profile_index
        assert transcribe_index in win._skipped
        assert win._stepper._cells[transcribe_index]._badge.text() == "»"

    def test_visiting_transcribe_for_real_afterward_clears_skip(
        self, win: WizardWindow
    ) -> None:
        _make_source_eligible(win)
        win._on_continue()  # Source -> Transcript
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        assert isinstance(transcript_step, TranscriptStep)
        transcript_step.reuse_requested.emit()

        transcribe_index = STEP_LABELS.index("Transcribe")
        assert transcribe_index in win._skipped

        win._go_to_step(STEP_LABELS.index("Transcript"))
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        win._on_continue()  # Transcript -> Transcribe, for real this time

        assert win._active == transcribe_index
        assert transcribe_index not in win._skipped
