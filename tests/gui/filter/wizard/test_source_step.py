"""Tests for m4bmaker.gui.filter.wizard.source_step.SourceStep (PRD §7.2
stage 1; ADR-0013).

The MediaInspectWorker's ffprobe subprocess call is mocked (same
convention test_workers.py already uses for it) — but everything
downstream of a returned MediaManifest (eligibility display, storage
estimate, transcript lookup) runs for real against real
MediaManifest/Transcript objects, not fakes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QLabel, QWidget

from m4bmaker.filter.models import AudioTrack, MediaManifest
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.gui.filter.wizard.source_step import SourceStep, _format_duration

pytestmark = pytest.mark.usefixtures("qapp")


def _eligible_manifest(fingerprint: str = "sha256:real") -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path="/books/dcc.m4b",
        fingerprint=fingerprint,
        duration_ms=48_693_108,
        tracks=(
            AudioTrack(
                index=0,
                codec_name="aac",
                is_default=True,
                channels=2,
                sample_rate=44_100,
                bit_rate=126_000,
            ),
        ),
        selected_track_index=0,
        selected_track_is_fallback=False,
        chapters=(),
        required_metadata={"title": "Dungeon Crawler Carl", "author": "Matt Dinniman"},
        cover_present=True,
        eligible=True,
    )


def _ineligible_manifest() -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path="/books/bad.m4b",
        fingerprint="sha256:bad",
        duration_ms=0,
        tracks=(
            AudioTrack(
                index=0,
                codec_name="mp3",
                is_default=True,
                channels=2,
                sample_rate=44_100,
                bit_rate=128_000,
            ),
        ),
        selected_track_index=0,
        selected_track_is_fallback=False,
        chapters=(),
        required_metadata={},
        cover_present=False,
        eligible=False,
        ineligibility_reasons=(
            "Primary audio track is MP3, not AAC.",
            "No chapters found.",
        ),
    )


@pytest.fixture()
def step() -> SourceStep:
    return SourceStep()


def _select_and_finish(step: SourceStep, path: Path, manifest: MediaManifest) -> None:
    """Select *path*, then deliver *manifest* exactly as
    MediaInspectWorker's result_ready signal would — without ever
    starting a real background thread. A real one (pointed at a
    non-existent tmp_path file) would eventually fire its own
    error/result callback asynchronously and could overwrite whatever
    state this test just asserted on, depending on thread timing — so
    the worker class is patched out for the selection itself.
    """
    with patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"):
        step._picker.set_path(path)
    step._on_inspect_finished(manifest)


class TestFormatDuration:
    def test_hours_minutes_seconds(self) -> None:
        assert _format_duration(48_693_108) == "13h 31m 33s"

    def test_minutes_seconds_only(self) -> None:
        assert _format_duration(125_000) == "2m 5s"

    def test_seconds_only(self) -> None:
        assert _format_duration(45_000) == "45s"


class TestEmptyState:
    def test_renders_without_error(self, step: SourceStep) -> None:
        assert step is not None

    def test_can_advance_false_before_any_file(self, step: SourceStep) -> None:
        assert step.can_advance() is False

    def test_manifest_is_none_before_any_file(self, step: SourceStep) -> None:
        assert step.manifest is None


class TestFileSelection:
    def test_selecting_file_starts_inspection(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        path = tmp_path / "book.m4b"
        # Patch the worker class itself so no real QThread actually starts
        # — a real one would race the next test's qapp.processEvents().
        with patch(
            "m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"
        ) as mock_worker_cls:
            step._picker.set_path(path)
            mock_worker_cls.assert_called_once_with(path)
            mock_worker_cls.return_value.start.assert_called_once()
        assert "Inspecting" in step._status_label.text()
        assert step.can_advance() is False


class TestEligibleResult:
    def test_manifest_stored_and_can_advance_true(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        assert step.manifest is not None
        assert step.manifest.eligible is True
        assert step.can_advance() is True

    def test_can_advance_changed_signal_fires_true(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        received: list[bool] = []
        step.can_advance_changed.connect(received.append)
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        assert received[-1] is True

    def test_status_label_clears_on_success(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        assert step._status_label.text() == ""

    def test_storage_estimate_uses_real_formula(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # Real fixture numbers (ADR-0007): ~8.6GB/stage * 3 (source,
        # envelope, filtered) + the AAC output at the source's own bit
        # rate — cross-checked against renderer.estimate_storage_bytes
        # directly, not just "some GB text appears somewhere".
        from m4bmaker.filter.renderer import estimate_storage_bytes

        manifest = _eligible_manifest()
        expected_bytes = estimate_storage_bytes(manifest)
        assert expected_bytes > 20 * 1024**3  # sanity: tens of GB for this book

        _select_and_finish(step, tmp_path / "book.m4b", manifest)
        panel = _first_result_widget(step)
        text = _all_text(panel)
        expected_gb = expected_bytes / (1024**3)
        assert f"{expected_gb:.1f} GB" in text

    def test_no_saved_transcript_shows_none_found(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.source_step.find_compatible_transcript",
            return_value=None,
        ):
            _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        assert "None found" in _all_text(panel)

    def test_compatible_transcript_found_shows_engine_details(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        transcript = Transcript(
            schema_version=1,
            status=TranscriptStatus.COMPLETE,
            source=TranscriptSource(
                fingerprint="sha256:real",
                duration_ms=48_693_108,
                selected_audio_stream=0,
            ),
            engine=TranscriptEngine(
                name="whisper.cpp",
                version="1.9.2",
                model="base.en",
                model_checksum="sha256:c",
            ),
            segments=(),
        )
        with patch(
            "m4bmaker.gui.filter.wizard.source_step.find_compatible_transcript",
            return_value=transcript,
        ):
            _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        text = _all_text(panel)
        assert "whisper.cpp" in text
        assert "1.9.2" in text
        assert "base.en" in text

    def test_metadata_fields_listed(self, step: SourceStep, tmp_path: Path) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        text = _all_text(panel)
        assert "author" in text
        assert "title" in text


class TestIneligibleResult:
    def test_can_advance_stays_false(self, step: SourceStep, tmp_path: Path) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _ineligible_manifest())
        assert step.can_advance() is False

    def test_all_reasons_shown_not_just_first(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _ineligible_manifest())
        panel = _first_result_widget(step)
        text = _all_text(panel)
        assert "Primary audio track is MP3" in text
        assert "No chapters found" in text


class TestInspectionError:
    def test_worker_error_disables_advance_and_shows_message(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        with patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"):
            step._picker.set_path(tmp_path / "book.m4b")
        step._on_inspect_error("ffprobe not found.")
        assert step.can_advance() is False
        panel = _first_result_widget(step)
        assert "ffprobe not found" in _all_text(panel)


class TestPickerDragAndDrop:
    def test_accepts_m4b_extension(self, step: SourceStep) -> None:
        assert step._picker._is_accepted(Path("book.m4b")) is True
        assert step._picker._is_accepted(Path("book.mp3")) is False

    def test_drop_of_m4b_file_emits_file_selected(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        path = tmp_path / "dropped.m4b"
        received: list[Path] = []
        step._picker.file_selected.connect(received.append)

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        event = QDropEvent(
            step._picker.rect().center().toPointF(),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        # step._picker is wired to the real SourceStep, so dropEvent()
        # would also kick off a real MediaInspectWorker via
        # _on_file_selected — patched out for the same reason
        # _select_and_finish() patches it.
        with patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"):
            step._picker.dropEvent(event)
        assert received == [path]


def _first_result_widget(step: SourceStep) -> QWidget:
    item = step._result_layout.itemAt(0)
    assert item is not None
    widget = item.widget()
    assert widget is not None
    return widget


def _all_text(widget: QWidget) -> str:
    """All QLabel text under *widget*, including *widget* itself if it
    is one — the inspection-error panel is a single bare QLabel, not a
    container, so restricting to findChildren() alone would miss it."""
    labels = list(widget.findChildren(QLabel))
    if isinstance(widget, QLabel):
        labels.append(widget)
    return " ".join(label.text() for label in labels)
