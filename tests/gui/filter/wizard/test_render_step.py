"""Tests for m4bmaker.gui.filter.wizard.render_step.RenderStep
(PRD §7.2 stage 7, §8; wireframe pass 2026-08-25, ADR-0019).

RenderWorker's real background thread is never actually driven here (it
wraps real ffmpeg calls) — the worker class is patched at construction
time (its ``.start()`` becomes a no-op on the mock instance), and this
step's own signal handlers (``_on_progress``/``_on_validating``/
``_on_result_ready``/``_on_error``) are invoked directly to simulate
exactly what the real worker would emit, same convention
test_scan_step.py/test_transcribe_step.py already use for their own
workers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton

from m4bmaker.filter.models import (
    AttenuationSettings,
    AudioTrack,
    MediaManifest,
    RenderInterval,
    RenderPlan,
)
from m4bmaker.filter.renderer import RenderResult
from m4bmaker.filter.validator import Severity, ValidationIssue, ValidationReport
from m4bmaker.gui.filter.wizard.render_step import RenderStep

pytestmark = pytest.mark.usefixtures("qapp")


def _manifest(
    fingerprint: str = "sha256:real",
    bit_rate: int | None = 126_000,
    codec_name: str | None = "aac",
    source_path: str = "/books/dcc.m4b",
) -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path=source_path,
        fingerprint=fingerprint,
        duration_ms=1_800_000,
        tracks=(
            AudioTrack(
                index=0,
                codec_name=codec_name,
                is_default=True,
                channels=2,
                sample_rate=44_100,
                bit_rate=bit_rate,
            ),
        ),
        selected_track_index=0,
        selected_track_is_fallback=False,
        chapters=(),
        required_metadata={},
        cover_present=True,
        eligible=True,
    )


def _empty_plan(duration_ms: int = 1_800_000) -> RenderPlan:
    return RenderPlan(
        intervals=(), attenuation=AttenuationSettings(), source_duration_ms=duration_ms
    )


def _plan_with_one_interval(duration_ms: int = 1_800_000) -> RenderPlan:
    return RenderPlan(
        intervals=(
            RenderInterval(
                start_ms=1_000,
                end_ms=2_000,
                fade_in_ms=15,
                fade_out_ms=15,
                hit_ids=("h1", "h2"),
            ),
        ),
        attenuation=AttenuationSettings(),
        source_duration_ms=duration_ms,
    )


@pytest.fixture()
def step() -> RenderStep:
    return RenderStep()


def _all_text(widget) -> str:  # noqa: ANN001
    labels = list(widget.findChildren(QLabel))
    if isinstance(widget, QLabel):
        labels.append(widget)
    buttons = list(widget.findChildren(QPushButton))
    line_edits = list(widget.findChildren(QLineEdit))
    combos = list(widget.findChildren(QComboBox))
    texts = (
        [label.text() for label in labels]
        + [b.text() for b in buttons]
        + [e.text() for e in line_edits]
        + [c.currentText() for c in combos]
    )
    return " ".join(texts)


def _body_text(step: RenderStep) -> str:
    item = step._body_layout.itemAt(0)
    assert item is not None
    widget = item.widget()
    assert widget is not None
    return _all_text(widget)


def _find_button(step: RenderStep, text_contains: str) -> QPushButton:
    for btn in step.findChildren(QPushButton):
        if text_contains in btn.text():
            return btn
    raise AssertionError(f"no button containing {text_contains!r}")


class TestNotReady:
    def test_renders_without_error(self, step: RenderStep) -> None:
        assert step is not None

    def test_can_advance_false(self, step: RenderStep) -> None:
        assert step.can_advance() is False


class TestSetInputsReady:
    def test_shows_real_defaults(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _plan_with_one_interval())
        assert step.can_advance() is False
        text = _body_text(step)
        assert "(filtered).m4b" in text
        assert "128k" in text  # 126kbps source snaps to 128k
        assert "Start Render" in text
        assert "1 (merged from 2 hits)" in text

    def test_output_path_defaults_next_to_source(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        assert step._output_path is not None
        assert step._output_path.parent == Path("/books")
        assert step._output_path.name == "dcc (filtered).m4b"


class TestBrowseOutput:
    def test_change_updates_output_path(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        with patch(
            "m4bmaker.gui.filter.wizard.render_step.QFileDialog.getSaveFileName",
            return_value=("/elsewhere/custom.m4b", ""),
        ):
            _find_button(step, "Change…").click()
        assert step._output_path == Path("/elsewhere/custom.m4b")
        assert step._output_input.text() == "/elsewhere/custom.m4b"

    def test_cancelling_the_dialog_keeps_the_default(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        original = step._output_path
        with patch(
            "m4bmaker.gui.filter.wizard.render_step.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            _find_button(step, "Change…").click()
        assert step._output_path == original


class TestStart:
    def test_click_start_transitions_to_running_and_starts_worker(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        with patch(
            "m4bmaker.gui.filter.wizard.render_step.RenderWorker"
        ) as mock_worker_cls:
            _find_button(step, "Start Render").click()
            mock_worker_cls.return_value.start.assert_called_once()
        text = _body_text(step)
        assert "Rendering" in text

    def test_worker_constructed_with_source_path_plan_and_chosen_bitrate(
        self, step: RenderStep
    ) -> None:
        manifest = _manifest(source_path="/books/dcc.m4b")
        plan = _empty_plan()
        step.set_inputs(manifest, plan)
        step._bitrate_combo.setCurrentText("64k")
        with patch(
            "m4bmaker.gui.filter.wizard.render_step.RenderWorker"
        ) as mock_worker_cls:
            _find_button(step, "Start Render").click()
            call = mock_worker_cls.call_args
        assert call.args[0] == Path("/books/dcc.m4b")
        assert call.args[1] is manifest
        assert call.args[2] is plan
        assert call.args[4] == "64k"

    def test_no_cancel_button_while_running(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        with pytest.raises(AssertionError):
            _find_button(step, "Cancel")


class TestProgressAndValidating:
    def test_progress_updates_label_and_bar(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        step._on_progress("Applying attenuation…", 0.5)
        assert step._progress_label.text() == "Applying attenuation…"
        assert step._progress_bar.value() == 50

    def test_validating_switches_to_indeterminate(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        step._on_validating()
        assert step._progress_label.text() == "Validating output…"
        assert step._progress_bar.maximum() == 0


class TestResultReady:
    def test_passing_validation_completes(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        result = RenderResult(output_path=Path("/books/out.m4b"), duration_ms=1_800_000)
        validation = ValidationReport(issues=())
        report_path = Path("/books/out.filter-report.json")

        step._on_result_ready(result, validation, report_path)

        assert step.can_advance() is True
        text = _body_text(step)
        assert "Passed validation" in text
        assert "/books/out.m4b" in text
        assert "/books/out.filter-report.json" in text

    def test_public_properties_expose_the_real_result(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        result = RenderResult(output_path=Path("/books/out.m4b"), duration_ms=1_800_000)
        validation = ValidationReport(issues=())
        report_path = Path("/books/out.filter-report.json")

        step._on_result_ready(result, validation, report_path)

        assert step.result is result
        assert step.validation is validation
        assert step.report_path == report_path

    def test_public_properties_none_before_any_result(self, step: RenderStep) -> None:
        assert step.result is None
        assert step.validation is None
        assert step.report_path is None

    def test_passing_with_warnings_shows_them(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        result = RenderResult(output_path=Path("/books/out.m4b"), duration_ms=1_800_000)
        validation = ValidationReport(
            issues=(
                ValidationIssue(
                    check="metadata",
                    severity=Severity.WARNING,
                    message="Best-effort field 'series' not preserved.",
                ),
            )
        )

        step._on_result_ready(result, validation, Path("/books/out.filter-report.json"))

        assert step.can_advance() is True
        assert "series" in _body_text(step)

    def test_failed_validation_shows_needs_attention_not_complete(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        result = RenderResult(output_path=Path("/books/out.m4b"), duration_ms=1_800_000)
        validation = ValidationReport(
            issues=(
                ValidationIssue(
                    check="duration",
                    severity=Severity.ERROR,
                    message="Output duration differs by 500ms.",
                ),
            )
        )

        step._on_result_ready(result, validation, Path("/books/out.filter-report.json"))

        assert step.can_advance() is False
        text = _body_text(step)
        assert "Needs attention" in text
        assert "Output duration differs by 500ms." in text
        assert _find_button(step, "Retry") is not None


class TestErrorSignal:
    def test_worker_error_shows_needs_attention(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        step._on_error("ffmpeg exploded")

        text = _body_text(step)
        assert "Needs attention" in text
        assert "ffmpeg exploded" in text
        assert step.can_advance() is False

    def test_retry_starts_a_new_worker(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        step._on_error("boom")
        with patch(
            "m4bmaker.gui.filter.wizard.render_step.RenderWorker"
        ) as mock_worker_cls:
            _find_button(step, "Retry").click()
            mock_worker_cls.return_value.start.assert_called_once()


class TestReentryGuard:
    def test_same_inputs_while_completed_is_a_no_op(self, step: RenderStep) -> None:
        manifest = _manifest()
        plan = _empty_plan()
        step.set_inputs(manifest, plan)
        result = RenderResult(output_path=Path("/books/out.m4b"), duration_ms=1)
        validation = ValidationReport(issues=())
        step._on_result_ready(result, validation, Path("/books/out.filter-report.json"))

        step.set_inputs(_manifest(), _empty_plan())

        assert step.can_advance() is True

    def test_different_plan_resets_to_ready(self, step: RenderStep) -> None:
        manifest = _manifest()
        step.set_inputs(manifest, _empty_plan())
        result = RenderResult(output_path=Path("/books/out.m4b"), duration_ms=1)
        validation = ValidationReport(issues=())
        step._on_result_ready(result, validation, Path("/books/out.filter-report.json"))

        step.set_inputs(manifest, _plan_with_one_interval())

        assert step.can_advance() is False
        assert "Start Render" in _body_text(step)

    def test_different_manifest_resets_to_ready(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(fingerprint="sha256:one"), _empty_plan())
        result = RenderResult(output_path=Path("/books/out.m4b"), duration_ms=1)
        validation = ValidationReport(issues=())
        step._on_result_ready(result, validation, Path("/books/out.filter-report.json"))

        step.set_inputs(_manifest(fingerprint="sha256:two"), _empty_plan())

        assert step.can_advance() is False
