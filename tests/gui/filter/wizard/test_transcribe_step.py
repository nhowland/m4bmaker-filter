"""Tests for m4bmaker.gui.filter.wizard.transcribe_step.TranscribeStep
(PRD §7.2 stage 3; ADR-0015).

TranscribeWorker's real transcription call is never actually driven here
(it wraps a real background QThread that would try to run real
ffmpeg/whisper-cli otherwise) — the worker class itself is patched at
construction time, and this step's own signal handlers
(_on_progress/_on_paused/etc.) are invoked directly to simulate exactly
what the real worker would emit, same convention test_source_step.py and
test_transcript_step.py already use for their own workers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from m4bmaker.filter.job_store import JobStore, connect
from m4bmaker.filter.jobs import JobState, JobType
from m4bmaker.filter.model_manager import KNOWN_MODELS
from m4bmaker.filter.models import ChapterInfo, MediaManifest
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.gui.filter.wizard.transcribe_step import TranscribeStep

pytestmark = pytest.mark.usefixtures("qapp")

_BASE_EN = KNOWN_MODELS[0]


def _manifest(
    fingerprint: str = "sha256:real",
    chapters: tuple[ChapterInfo, ...] = (),
    duration_ms: int = 1_800_000,
) -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path="/books/dcc.m4b",
        fingerprint=fingerprint,
        duration_ms=duration_ms,
        tracks=(),
        selected_track_index=0,
        selected_track_is_fallback=False,
        chapters=chapters,
        required_metadata={},
        cover_present=True,
        eligible=True,
    )


def _transcript(fingerprint: str = "sha256:real") -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint=fingerprint, duration_ms=1_800_000, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp", version="1.9.2", model="base.en", model_checksum="c"
        ),
        segments=(),
    )


@pytest.fixture()
def step(tmp_path: Path) -> TranscribeStep:
    return TranscribeStep(
        models_dest_dir=tmp_path / "models",
        transcripts_dest_dir=tmp_path / "transcripts",
        db_path=tmp_path / "filter.db",
    )


def _first_body_widget(step: TranscribeStep):  # noqa: ANN202
    item = step._body_layout.itemAt(0)
    assert item is not None
    widget = item.widget()
    assert widget is not None
    return widget


def _all_text(widget) -> str:  # noqa: ANN001
    labels = list(widget.findChildren(QLabel))
    if isinstance(widget, QLabel):
        labels.append(widget)
    buttons = list(widget.findChildren(QPushButton))
    texts = [label.text() for label in labels] + [b.text() for b in buttons]
    return " ".join(texts)


def _find_button(step: TranscribeStep, text_contains: str) -> QPushButton:
    for btn in step.findChildren(QPushButton):
        if text_contains in btn.text():
            return btn
    raise AssertionError(f"no button containing {text_contains!r}")


class TestNotReady:
    def test_renders_without_error(self, step: TranscribeStep) -> None:
        assert step is not None

    def test_can_advance_false(self, step: TranscribeStep) -> None:
        assert step.can_advance() is False

    def test_transcript_none(self, step: TranscribeStep) -> None:
        assert step.transcript is None


class TestSetTranscriptChoiceNoExistingJob:
    def test_state_ready_shows_model_and_plan(self, step: TranscribeStep) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        assert step.can_advance() is False
        text = _all_text(_first_body_widget(step))
        assert "base.en" in text
        assert "Start Transcription" in text

    def test_chapter_aligned_plan_shown_when_chapters_present(
        self, step: TranscribeStep
    ) -> None:
        chapters = (
            ChapterInfo(index=1, title="Chapter 1", start_ms=0),
            ChapterInfo(index=2, title="Chapter 2", start_ms=600_000),
        )
        step.set_transcript_choice(
            _manifest(chapters=chapters, duration_ms=1_200_000), _BASE_EN
        )
        text = _all_text(_first_body_widget(step))
        assert "2 chunks" in text
        assert "one per chapter" in text

    def test_fallback_plan_shown_when_no_chapters(self, step: TranscribeStep) -> None:
        step.set_transcript_choice(_manifest(chapters=()), _BASE_EN)
        text = _all_text(_first_body_widget(step))
        assert "no chapter markers" in text


class TestSetTranscriptChoiceExistingJob:
    def test_existing_paused_job_shows_paused_state(
        self, step: TranscribeStep, tmp_path: Path
    ) -> None:
        store = JobStore(connect(tmp_path / "filter.db"))
        store.create_job(
            "job-1", JobType.TRANSCRIPTION, resource={"fingerprint": "sha256:real"}
        )
        store.transition("job-1", JobState.PREPARING)
        store.transition("job-1", JobState.RUNNING)
        store.update_progress("job-1", "Transcribed chunk 1/2", 0.5)
        store.transition("job-1", JobState.PAUSING)
        store.transition("job-1", JobState.PAUSED)

        step.set_transcript_choice(_manifest(), _BASE_EN)

        assert step.can_advance() is False
        text = _all_text(_first_body_widget(step))
        assert "Paused" in text
        assert _find_button(step, "Resume") is not None

    def test_existing_needs_attention_job_shows_that_state(
        self, step: TranscribeStep, tmp_path: Path
    ) -> None:
        store = JobStore(connect(tmp_path / "filter.db"))
        store.create_job(
            "job-1", JobType.TRANSCRIPTION, resource={"fingerprint": "sha256:real"}
        )
        store.transition("job-1", JobState.PREPARING)
        store.transition("job-1", JobState.RUNNING)
        store.set_error("job-1", "err", "whisper-cli crashed")
        store.transition("job-1", JobState.NEEDS_ATTENTION, "whisper-cli crashed")

        step.set_transcript_choice(_manifest(), _BASE_EN)

        text = _all_text(_first_body_widget(step))
        assert "Needs attention" in text
        assert "whisper-cli crashed" in text
        assert _find_button(step, "Retry") is not None

    def test_unrelated_job_for_different_source_is_ignored(
        self, step: TranscribeStep, tmp_path: Path
    ) -> None:
        store = JobStore(connect(tmp_path / "filter.db"))
        store.create_job(
            "job-other",
            JobType.TRANSCRIPTION,
            resource={"fingerprint": "sha256:different"},
        )
        store.transition("job-other", JobState.PREPARING)
        store.transition("job-other", JobState.RUNNING)
        store.transition("job-other", JobState.PAUSING)
        store.transition("job-other", JobState.PAUSED)

        step.set_transcript_choice(_manifest(fingerprint="sha256:real"), _BASE_EN)

        text = _all_text(_first_body_widget(step))
        assert "Start Transcription" in text


class TestReentryGuard:
    def test_already_completed_same_source_is_a_noop(
        self, step: TranscribeStep
    ) -> None:
        manifest = _manifest()
        step.set_transcript_choice(manifest, _BASE_EN)
        step._on_result_ready(_transcript())
        assert step.can_advance() is True

        step.set_transcript_choice(manifest, _BASE_EN)
        assert step.can_advance() is True
        assert step.transcript is not None

    def test_different_source_after_completion_re_derives(
        self, step: TranscribeStep
    ) -> None:
        step.set_transcript_choice(_manifest(fingerprint="sha256:one"), _BASE_EN)
        step._on_result_ready(_transcript(fingerprint="sha256:one"))
        assert step.can_advance() is True

        step.set_transcript_choice(_manifest(fingerprint="sha256:two"), _BASE_EN)
        assert step.can_advance() is False
        text = _all_text(_first_body_widget(step))
        assert "Start Transcription" in text


class TestStartAndRunning:
    def test_start_click_launches_worker_and_enters_running(
        self, step: TranscribeStep
    ) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch(
            "m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            _find_button(step, "Start Transcription").click()
            mock_cls.assert_called_once()
            mock_cls.return_value.start.assert_called_once()
        assert step.can_advance() is False
        text = _all_text(_first_body_widget(step))
        assert "Pause" in text
        assert "Cancel" in text

    def test_progress_updates_bar_and_label(self, step: TranscribeStep) -> None:
        chapters = tuple(
            ChapterInfo(index=i, title=f"Chapter {i}", start_ms=(i - 1) * 600_000)
            for i in range(1, 4)
        )
        step.set_transcript_choice(
            _manifest(chapters=chapters, duration_ms=1_800_000), _BASE_EN
        )
        with patch("m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"):
            _find_button(step, "Start Transcription").click()
        step._on_progress("Transcribed chunk 2/3", 2 / 3)
        assert step._progress_bar.value() == 66
        text = _all_text(_first_body_widget(step))
        assert "Chapter 2 of 3" in text

    def test_pause_click_calls_request_pause_on_worker(
        self, step: TranscribeStep
    ) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch(
            "m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"
        ) as mock_cls:
            mock_worker = MagicMock()
            mock_cls.return_value = mock_worker
            _find_button(step, "Start Transcription").click()
        _find_button(step, "Pause").click()
        mock_worker.request_pause.assert_called_once()

    def test_cancel_click_calls_request_cancel_on_worker(
        self, step: TranscribeStep
    ) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch(
            "m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"
        ) as mock_cls:
            mock_worker = MagicMock()
            mock_cls.return_value = mock_worker
            _find_button(step, "Start Transcription").click()
        _find_button(step, "Cancel").click()
        mock_worker.request_cancel.assert_called_once()


class TestTerminalStates:
    def test_paused_signal_shows_paused_panel(self, step: TranscribeStep) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch("m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"):
            _find_button(step, "Start Transcription").click()
        step._on_paused()
        assert step.can_advance() is False
        text = _all_text(_first_body_widget(step))
        assert "Paused" in text

    def test_cancelled_signal_returns_to_ready(self, step: TranscribeStep) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch("m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"):
            _find_button(step, "Start Transcription").click()
        step._on_cancelled()
        text = _all_text(_first_body_widget(step))
        assert "Start Transcription" in text

    def test_needs_attention_signal_shows_that_panel(
        self, step: TranscribeStep
    ) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch("m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"):
            _find_button(step, "Start Transcription").click()
        step._on_needs_attention("whisper-cli crashed")
        assert step.can_advance() is False
        text = _all_text(_first_body_widget(step))
        assert "whisper-cli crashed" in text

    def test_result_ready_enables_advance(self, step: TranscribeStep) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch("m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"):
            _find_button(step, "Start Transcription").click()
        step._on_result_ready(_transcript())
        assert step.can_advance() is True
        assert step.transcript is not None
        text = _all_text(_first_body_widget(step))
        assert "complete" in text.lower()

    def test_retry_click_relaunches_worker_in_retry_mode(
        self, step: TranscribeStep
    ) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch("m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"):
            _find_button(step, "Start Transcription").click()
        step._on_needs_attention("boom")
        with patch(
            "m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            _find_button(step, "Retry").click()
            assert mock_cls.call_args.args[-1] == "retry"

    def test_resume_click_relaunches_worker_in_resume_mode(
        self, step: TranscribeStep
    ) -> None:
        step.set_transcript_choice(_manifest(), _BASE_EN)
        with patch("m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"):
            _find_button(step, "Start Transcription").click()
        step._on_paused()
        with patch(
            "m4bmaker.gui.filter.wizard.transcribe_step.TranscribeWorker"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            _find_button(step, "Resume").click()
            assert mock_cls.call_args.args[-1] == "resume"
