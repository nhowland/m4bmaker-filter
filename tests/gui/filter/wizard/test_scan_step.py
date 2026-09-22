"""Tests for m4bmaker.gui.filter.wizard.scan_step.ScanStep
(PRD §7.2 stage 5, §9.4; wireframe pass 2026-08-25).

ScanWorker's real background thread is never actually driven here — the
worker class is patched at construction time (its ``.start()`` becomes a
no-op on the mock instance), and this step's own signal handlers
(``_on_result_ready``/``_on_error``) are invoked directly to simulate
exactly what the real worker would emit, same convention
test_transcribe_step.py already uses for its own worker.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.scan import build_report, run_scan
from m4bmaker.filter.models import NORMALIZATION_VERSION
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.gui.filter.wizard.scan_step import ScanStep

pytestmark = pytest.mark.usefixtures("qapp")


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
def service() -> CatalogService:
    return CatalogService()


@pytest.fixture()
def profile_id(service: CatalogService) -> str:
    category = service.create_category("Profanity")
    entry, _ = service.create_entry(category.id, "darn")
    profile = service.create_profile("Family Friendly", entry_ids=[entry.id])
    return profile.id


@pytest.fixture()
def step() -> ScanStep:
    return ScanStep()


def _all_text(widget) -> str:  # noqa: ANN001
    labels = list(widget.findChildren(QLabel))
    if isinstance(widget, QLabel):
        labels.append(widget)
    buttons = list(widget.findChildren(QPushButton))
    texts = [label.text() for label in labels] + [b.text() for b in buttons]
    return " ".join(texts)


def _body_text(step: ScanStep) -> str:
    item = step._body_layout.itemAt(0)
    assert item is not None
    widget = item.widget()
    assert widget is not None
    return _all_text(widget)


def _find_button(step: ScanStep, text_contains: str) -> QPushButton:
    for btn in step.findChildren(QPushButton):
        if text_contains in btn.text():
            return btn
    raise AssertionError(f"no button containing {text_contains!r}")


class TestNotReady:
    def test_renders_without_error(self, step: ScanStep) -> None:
        assert step is not None

    def test_can_advance_false(self, step: ScanStep) -> None:
        assert step.can_advance() is False

    def test_scan_none(self, step: ScanStep) -> None:
        assert step.scan is None


class TestSetInputsReady:
    def test_shows_transcript_and_profile(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        step.set_inputs(_transcript(), service, profile_id)
        assert step.can_advance() is False
        text = _body_text(step)
        assert "base.en" in text
        assert "Family Friendly" in text
        assert "Start Scan" in text


class TestViewTranscript:
    """ADR-0022: available as soon as this step has a transcript, not
    only once a scan has run — matches Profile's own placement."""

    def test_clicking_view_transcript_opens_the_generated_text_file(
        self,
        step: ScanStep,
        service: CatalogService,
        profile_id: str,
        tmp_path: Path,
    ) -> None:
        transcript_path = tmp_path / "book.m4bt.json"
        step.set_inputs(
            replace(_transcript(), path=transcript_path), service, profile_id
        )

        with patch(
            "m4bmaker.gui.filter.wizard.scan_step.QDesktopServices.openUrl"
        ) as mock_open:
            _find_button(step, "View Transcript").click()

        mock_open.assert_called_once()


class TestStart:
    def test_click_start_transitions_to_running_and_starts_worker(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        step.set_inputs(_transcript(), service, profile_id)
        with patch(
            "m4bmaker.gui.filter.wizard.scan_step.ScanWorker"
        ) as mock_worker_cls:
            _find_button(step, "Start Scan").click()
            mock_worker_cls.return_value.start.assert_called_once()
        text = _body_text(step)
        assert "Scanning" in text

    def test_progress_bar_uses_the_shared_job_progress_style(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        """Matches Render/Transcribe's own progress bars (objectName
        "jobProgress" in styles.py) rather than the thin default QSS."""
        step.set_inputs(_transcript(), service, profile_id)
        with patch("m4bmaker.gui.filter.wizard.scan_step.ScanWorker"):
            _find_button(step, "Start Scan").click()
        bar = step.findChild(QProgressBar)
        assert bar is not None
        assert bar.objectName() == "jobProgress"

    def test_start_snapshots_the_current_profile(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        step.set_inputs(_transcript(), service, profile_id)
        with patch(
            "m4bmaker.gui.filter.wizard.scan_step.ScanWorker"
        ) as mock_worker_cls:
            _find_button(step, "Start Scan").click()
            snapshot = mock_worker_cls.call_args.args[1]
        assert snapshot.profile_id == profile_id
        assert snapshot.name == "Family Friendly"

    def test_invalid_profile_id_shows_needs_attention(
        self, step: ScanStep, service: CatalogService
    ) -> None:
        step.set_inputs(_transcript(), service, "does-not-exist")
        _find_button(step, "Start Scan").click()
        assert step.can_advance() is False
        text = _body_text(step)
        assert "Needs attention" in text
        assert _find_button(step, "Retry") is not None


class TestResultReady:
    def test_completed_shows_real_report_fields(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        transcript = _transcript()
        step.set_inputs(transcript, service, profile_id)
        snapshot = service.create_snapshot(profile_id)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)

        step._on_result_ready(scan)

        assert step.can_advance() is True
        assert step.scan is scan
        text = _body_text(step)
        report = build_report(scan, transcript.source.duration_ms)
        assert f"Hits found: {report.total_raw_hits}" in text
        assert "Family Friendly" in text
        assert "rev. 1" in text
        assert "Re-scan" in text

    def test_re_scan_button_starts_a_new_worker(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        transcript = _transcript()
        step.set_inputs(transcript, service, profile_id)
        snapshot = service.create_snapshot(profile_id)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        step._on_result_ready(scan)

        with patch(
            "m4bmaker.gui.filter.wizard.scan_step.ScanWorker"
        ) as mock_worker_cls:
            _find_button(step, "Re-scan").click()
            mock_worker_cls.return_value.start.assert_called_once()


class TestErrorSignal:
    def test_worker_error_shows_needs_attention(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        step.set_inputs(_transcript(), service, profile_id)
        step._on_error("whisper.cpp exploded")

        text = _body_text(step)
        assert "Needs attention" in text
        assert "whisper.cpp exploded" in text
        assert step.can_advance() is False


class TestReentryGuard:
    def test_same_inputs_while_completed_is_a_no_op(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        transcript = _transcript()
        step.set_inputs(transcript, service, profile_id)
        snapshot = service.create_snapshot(profile_id)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        step._on_result_ready(scan)

        step.set_inputs(_transcript(), service, profile_id)

        assert step.scan is scan
        assert step.can_advance() is True

    def test_different_profile_resets_to_ready(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        transcript = _transcript()
        step.set_inputs(transcript, service, profile_id)
        snapshot = service.create_snapshot(profile_id)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        step._on_result_ready(scan)

        other_profile = service.create_profile("Profanity Only")
        step.set_inputs(transcript, service, other_profile.id)

        assert step.scan is None
        assert step.can_advance() is False
        assert "Start Scan" in _body_text(step)

    def test_different_transcript_resets_to_ready(
        self, step: ScanStep, service: CatalogService, profile_id: str
    ) -> None:
        transcript = _transcript(fingerprint="sha256:one")
        step.set_inputs(transcript, service, profile_id)
        snapshot = service.create_snapshot(profile_id)
        scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
        step._on_result_ready(scan)

        step.set_inputs(_transcript(fingerprint="sha256:two"), service, profile_id)

        assert step.scan is None
        assert step.can_advance() is False
