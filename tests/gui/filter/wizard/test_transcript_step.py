"""Tests for m4bmaker.gui.filter.wizard.transcript_step.TranscriptStep
(PRD §7.2 stage 2; ADR-0014).

The MediaInspectWorker equivalent here is ModelDownloadWorker; its real
download_model() call is mocked (same convention test_workers.py and
test_model_manager_window.py already use for it), but everything
downstream — find_compatible_transcript(), is_installed(),
download_coordinator — runs for real against real objects and a real
tmp_path filesystem, not fakes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QRadioButton, QWidget

from m4bmaker.filter.model_manager import KNOWN_MODELS, ModelDownloadCancelled
from m4bmaker.filter.models import AudioTrack, MediaManifest
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.gui.filter.wizard.transcript_step import TranscriptStep
from m4bmaker.gui.filter.workers import download_coordinator

pytestmark = pytest.mark.usefixtures("qapp")

_BASE_EN = KNOWN_MODELS[0]
_SMALL_EN = KNOWN_MODELS[1]


def _manifest(fingerprint: str = "sha256:real") -> MediaManifest:
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
        required_metadata={},
        cover_present=True,
        eligible=True,
    )


def _transcript(fingerprint: str = "sha256:real") -> Transcript:
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=TranscriptSource(
            fingerprint=fingerprint, duration_ms=48_693_108, selected_audio_stream=0
        ),
        engine=TranscriptEngine(
            name="whisper.cpp",
            version="1.9.2",
            model="base.en",
            model_checksum="sha256:c",
        ),
        segments=(),
    )


def _install(spec, dest_dir: Path) -> None:
    path = dest_dir / spec.filename()
    path.write_bytes(b"\x00" * spec.size_bytes)


@pytest.fixture()
def step(tmp_path: Path) -> TranscriptStep:
    return TranscriptStep(dest_dir=tmp_path)


class TestEmptyState:
    def test_renders_without_error(self, step: TranscriptStep) -> None:
        assert step is not None

    def test_can_advance_false_before_set_source(self, step: TranscriptStep) -> None:
        assert step.can_advance() is False

    def test_manifest_none_before_set_source(self, step: TranscriptStep) -> None:
        assert step.manifest is None


class TestSetSourceNoCompatibleTranscript:
    def test_mode_is_choose_model(self, step: TranscriptStep, tmp_path: Path) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert step.compatible_transcript is None
        assert _first_body_widget(step).findChild(QRadioButton) is not None

    def test_can_advance_false_when_default_model_not_installed(
        self, step: TranscriptStep
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert step.can_advance() is False

    def test_can_advance_true_when_a_model_is_already_installed(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert step.can_advance() is True
        assert step.chosen_model == _BASE_EN

    def test_both_catalog_models_shown_with_real_install_state(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        text = _all_text(_first_body_widget(step))
        assert "base.en" in text
        assert "small.en" in text
        assert "✓ installed" in text
        assert "not installed" in text

    def test_can_advance_changed_signal_fires(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        received: list[bool] = []
        step.can_advance_changed.connect(received.append)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert received[-1] is False


class TestSetSourceCompatibleTranscriptFound:
    def test_mode_is_found(self, step: TranscriptStep) -> None:
        transcript = _transcript()
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=transcript,
        ):
            step.set_source(_manifest())
        assert step.compatible_transcript is transcript

    def test_can_advance_is_false_until_explicit_choice(
        self, step: TranscriptStep
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=_transcript(),
        ):
            step.set_source(_manifest())
        assert step.can_advance() is False

    def test_found_panel_shows_engine_details(self, step: TranscriptStep) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=_transcript(),
        ):
            step.set_source(_manifest())
        text = _all_text(_first_body_widget(step))
        assert "whisper.cpp" in text
        assert "1.9.2" in text
        assert "base.en" in text
        assert "13h 31m 33s" in text

    def test_use_existing_emits_reuse_requested(self, step: TranscriptStep) -> None:
        received: list[None] = []
        step.reuse_requested.connect(lambda: received.append(None))
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=_transcript(),
        ):
            step.set_source(_manifest())
        use_btn = _find_button(step, "Use existing")
        use_btn.click()
        assert len(received) == 1

    def test_transcribe_again_switches_to_choose_mode(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=_transcript(),
        ):
            step.set_source(_manifest())
        again_btn = _find_button(step, "Transcribe again instead")
        again_btn.click()
        assert _first_body_widget(step).findChild(QRadioButton) is not None
        assert step.can_advance() is False  # default model not installed

    def test_transcribe_again_then_installed_model_allows_advance(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=_transcript(),
        ):
            step.set_source(_manifest())
        _find_button(step, "Transcribe again instead").click()
        assert step.can_advance() is True


class TestReenteringSetSource:
    def test_second_call_with_different_fingerprint_re_derives_state(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=_transcript(fingerprint="sha256:one"),
        ):
            step.set_source(_manifest(fingerprint="sha256:one"))
        assert step.compatible_transcript is not None

        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest(fingerprint="sha256:two"))
        assert step.compatible_transcript is None
        assert _first_body_widget(step).findChild(QRadioButton) is not None


class TestModelSelection:
    def test_selecting_uninstalled_model_disables_advance(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert step.can_advance() is True
        radio = _find_radio(step, "small.en")
        radio.setChecked(True)
        assert step.chosen_model == _SMALL_EN
        assert step.can_advance() is False

    def test_selecting_installed_model_enables_advance(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)
        _install(_SMALL_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        radio = _find_radio(step, "small.en")
        radio.setChecked(True)
        assert step.can_advance() is True


class TestPreferredModelSetting:
    """The Settings window's "Preferred Model" default (ADR-0035) — only
    honored when that model is actually installed; otherwise falls back
    to the pre-existing "first installed, else the catalog's first
    entry" logic exactly as if no preference were set."""

    def test_installed_preference_is_preselected(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)
        _install(_SMALL_EN, tmp_path)
        with (
            patch(
                "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
                return_value=None,
            ),
            patch(
                "m4bmaker.gui.filter.wizard.transcript_step.get_setting",
                return_value="small.en",
            ),
        ):
            step.set_source(_manifest())
        assert step.chosen_model == _SMALL_EN

    def test_uninstalled_preference_falls_back_to_first_installed(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)  # small.en preferred but not installed
        with (
            patch(
                "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
                return_value=None,
            ),
            patch(
                "m4bmaker.gui.filter.wizard.transcript_step.get_setting",
                return_value="small.en",
            ),
        ):
            step.set_source(_manifest())
        assert step.chosen_model == _BASE_EN

    def test_no_preference_uses_existing_default_logic(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_SMALL_EN, tmp_path)
        with (
            patch(
                "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
                return_value=None,
            ),
            patch(
                "m4bmaker.gui.filter.wizard.transcript_step.get_setting",
                return_value=None,
            ),
        ):
            step.set_source(_manifest())
        assert step.chosen_model == _SMALL_EN


class TestDownload:
    def test_download_button_shown_only_for_uninstalled_models(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert _find_button(step, "Download") is not None

    def test_successful_download_installs_and_enables_advance(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        def fake_download(spec, dest_dir, progress_callback=None, cancel_event=None):
            path = dest_dir / spec.filename()
            path.write_bytes(b"\x00" * spec.size_bytes)
            return path

        # base.en pre-installed so "Download" is unambiguous — the only
        # remaining Download button is small.en's own.
        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        radio = _find_radio(step, "small.en")
        radio.setChecked(True)
        assert step.can_advance() is False

        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            _find_button(step, "Download").click()
            worker = step._download_worker
            assert worker is not None
            worker.wait(3000)

        QApplication.processEvents()
        assert step._download_worker is None
        assert step.can_advance() is True
        assert download_coordinator.active_name is None

    def test_download_cancelled_resets_state(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        def fake_download(spec, dest_dir, progress_callback=None, cancel_event=None):
            raise ModelDownloadCancelled("cancelled")

        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        radio = _find_radio(step, "small.en")
        radio.setChecked(True)

        with patch(
            "m4bmaker.gui.filter.workers.download_model", side_effect=fake_download
        ):
            _find_button(step, "Download").click()
            worker = step._download_worker
            assert worker is not None
            worker.wait(3000)

        QApplication.processEvents()
        assert step._download_worker is None
        assert download_coordinator.active_name is None
        assert step.can_advance() is False

    def test_download_error_shows_message_box_when_visible(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        step.show()
        _install(_BASE_EN, tmp_path)
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        radio = _find_radio(step, "small.en")
        radio.setChecked(True)

        with (
            patch(
                "m4bmaker.gui.filter.workers.download_model",
                side_effect=RuntimeError("network failure"),
            ),
            patch(
                "m4bmaker.gui.filter.wizard.transcript_step.QMessageBox.critical"
            ) as mock_critical,
        ):
            _find_button(step, "Download").click()
            worker = step._download_worker
            assert worker is not None
            worker.wait(3000)
            QApplication.processEvents()

        assert download_coordinator.active_name is None
        mock_critical.assert_called_once()

    def test_blocked_when_coordinator_held_elsewhere(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert download_coordinator.try_acquire("base.en") is True
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.QMessageBox.information"
        ) as mock_info:
            _find_button(step, "Download").click()
        assert step._download_worker is None
        mock_info.assert_called_once()


class TestManageModels:
    def test_opens_model_manager_window(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.ModelManagerWindow"
        ) as mock_window_cls:
            _find_button(step, "Manage Transcription Models").click()
        mock_window_cls.assert_called_once_with(dest_dir=tmp_path, parent=step)

    def test_reused_on_second_call(self, step: TranscriptStep) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.ModelManagerWindow"
        ) as mock_window_cls:
            step._on_manage_models()
            step._on_manage_models()
        mock_window_cls.assert_called_once()

    def test_closing_model_manager_refreshes_install_state(
        self, step: TranscriptStep, tmp_path: Path
    ) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ):
            step.set_source(_manifest())
        assert step.can_advance() is False  # default model not installed yet

        with patch(
            "m4bmaker.gui.filter.wizard.transcript_step.ModelManagerWindow"
        ) as mock_window_cls:
            instance = mock_window_cls.return_value
            step._on_manage_models()
            closed_slot = instance.closed.connect.call_args[0][0]

        _install(_BASE_EN, tmp_path)  # simulate a download made in that window
        closed_slot()

        assert step.can_advance() is True


def _first_body_widget(step: TranscriptStep) -> QWidget:
    item = step._body_layout.itemAt(0)
    assert item is not None
    widget = item.widget()
    assert widget is not None
    return widget


def _all_text(widget: QWidget) -> str:
    labels = list(widget.findChildren(QLabel))
    if isinstance(widget, QLabel):
        labels.append(widget)
    radios = widget.findChildren(QRadioButton)
    texts = [label.text() for label in labels] + [r.text() for r in radios]
    return " ".join(texts)


def _find_button(step: TranscriptStep, text_contains: str):
    for btn in step.findChildren(QPushButton):
        if text_contains in btn.text():
            return btn
    raise AssertionError(f"no button containing {text_contains!r}")


def _find_radio(step: TranscriptStep, name: str) -> QRadioButton:
    for radio in step.findChildren(QRadioButton):
        if radio.text() == name:
            return radio
    raise AssertionError(f"no radio button named {name!r}")
