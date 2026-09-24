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

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton

from m4bmaker.filter.filter_report import report_path_for
from m4bmaker.filter.models import (
    AttenuationSettings,
    AudioTrack,
    MediaManifest,
    RenderInterval,
    RenderPlan,
)
from m4bmaker.filter.renderer import RenderResult
from m4bmaker.filter.validator import Severity, ValidationIssue, ValidationReport
from m4bmaker.gui.filter.wizard.render_step import _STATE_NEEDS_ATTENTION, RenderStep

pytestmark = pytest.mark.usefixtures("qapp")


def _manifest(
    fingerprint: str = "sha256:real",
    bit_rate: int | None = 126_000,
    codec_name: str | None = "aac",
    source_path: str = "/books/test-audiobook.m4b",
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


def _plan_with_n_intervals(n: int, duration_ms: int = 1_800_000) -> RenderPlan:
    return RenderPlan(
        intervals=tuple(
            RenderInterval(
                start_ms=i * 1_000,
                end_ms=i * 1_000 + 500,
                fade_in_ms=15,
                fade_out_ms=15,
                hit_ids=(f"h{i}",),
            )
            for i in range(n)
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
        assert step._output_path.name == "test-audiobook (filtered).m4b"


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


class TestBitrateHint:
    """ADR-0046: a small hint next to the bitrate dropdown, shown only
    while the current selection is still the one pick_default_bitrate()
    itself chose from the source's own bitrate."""

    def test_hint_shown_for_the_auto_picked_default(self, step: RenderStep) -> None:
        # 126,000 bps AAC -> pick_default_bitrate snaps to "128k".
        step.set_inputs(_manifest(bit_rate=126_000, codec_name="aac"), _empty_plan())
        assert step._bitrate_combo.currentText() == "128k"
        assert "source file's bitrate" in step._bitrate_hint_label.text()

    def test_hint_hidden_once_the_user_changes_it(self, step: RenderStep) -> None:
        step.set_inputs(_manifest(bit_rate=126_000, codec_name="aac"), _empty_plan())
        step._bitrate_combo.setCurrentText("64k")
        assert step._bitrate_hint_label.text() == ""

    def test_hint_reappears_if_changed_back_to_the_auto_pick(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(bit_rate=126_000, codec_name="aac"), _empty_plan())
        step._bitrate_combo.setCurrentText("64k")
        step._bitrate_combo.setCurrentText("128k")
        assert "source file's bitrate" in step._bitrate_hint_label.text()

    def test_no_hint_when_source_bitrate_is_unknown(self, step: RenderStep) -> None:
        # DEFAULT_BITRATE ("96k") is a guess, not a real match to
        # anything -- claiming it "matches the source" would be false.
        step.set_inputs(_manifest(bit_rate=None), _empty_plan())
        assert step._bitrate_hint_label.text() == ""


class TestStorageLabel:
    def test_renamed_and_explains_it_includes_the_output_file(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        text = _body_text(step)
        assert "Temp storage needed" in text
        assert "temporary working files + final output" in text
        assert "Est. storage needed" not in text


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
        manifest = _manifest(source_path="/books/test-audiobook.m4b")
        plan = _empty_plan()
        step.set_inputs(manifest, plan)
        step._bitrate_combo.setCurrentText("64k")
        with patch(
            "m4bmaker.gui.filter.wizard.render_step.RenderWorker"
        ) as mock_worker_cls:
            _find_button(step, "Start Render").click()
            call = mock_worker_cls.call_args
        assert call.args[0] == Path("/books/test-audiobook.m4b")
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

    def test_validating_with_no_intervals_stays_indeterminate(
        self, step: RenderStep
    ) -> None:
        # No render intervals at all (e.g. every hit excluded) means
        # nothing for validate_attenuation() to measure -- ADR-0044's
        # per-interval progress never fires, so this is still the old,
        # honest "nothing to estimate against" indeterminate state, not
        # a regression.
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        step._on_validating()
        assert step._progress_label.text() == "Validating output…"
        assert step._progress_bar.maximum() == 0
        assert step._est_remaining_seconds is None

    def test_validating_with_intervals_is_determinate_with_a_seeded_eta(
        self, step: RenderStep
    ) -> None:
        # ADR-0044: real intervals exist, so _on_validating() seeds an
        # upfront estimate from interval count alone (real per-interval
        # progress hasn't arrived yet) instead of going indeterminate.
        step.set_inputs(_manifest(), _plan_with_n_intervals(10))
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        step._on_validating()
        assert step._progress_bar.maximum() == 100
        assert step._progress_bar.value() == 0
        assert step._est_remaining_seconds == pytest.approx(4.0)  # 10 * 0.4s
        assert "Est. remaining" in step._eta_label.text()

    def test_validating_progress_updates_label_bar_and_eta(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(), _plan_with_n_intervals(10))
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        step._on_validating()
        # Pretend validation has been running for 2 real seconds and has
        # measured 5 of the 10 intervals so far -> rate implies 2 more
        # seconds remaining (5 left at the same 0.4s/interval pace).
        step._validating_at = time.monotonic() - 2.0

        step._on_validating_progress("Validating output… (5 of 10)", 0.5)

        assert step._progress_label.text() == "Validating output… (5 of 10)"
        assert step._progress_bar.value() == 50
        assert step._est_remaining_seconds == pytest.approx(2.0, abs=0.2)

    def test_validating_progress_ignored_after_the_step_moves_on(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(), _plan_with_n_intervals(10))
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        step._on_validating()
        step._state = _STATE_NEEDS_ATTENTION

        step._on_validating_progress("Validating output… (5 of 10)", 0.5)

        assert step._progress_bar.value() == 0


class TestEstimatedRemaining:
    """ADR-0028: an "Est. remaining" label for encode+mux specifically —
    the one render stage real measurement (ADR-0007) showed dominates
    total render time. A rough hardcoded default seeds it, then real
    streamed ffmpeg progress replaces the guess with this run's own
    measured rate, same pattern as Transcribe's ADR-0027."""

    def test_placeholder_during_the_fast_pre_encode_stages(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()

        step._on_progress("Applying attenuation…", 0.06)

        # No estimate for this stage by design (ADR-0007/0028); the
        # label shows a placeholder rather than going blank (a User-
        # requested change), even though the underlying value is None.
        assert step._est_remaining_seconds is None
        assert step._eta_label.text() == "Est. remaining: Calculating…"

    def test_cold_start_uses_the_default_multiplier(self, step: RenderStep) -> None:
        # 1,800,000ms of audio at the 80x default -> 22.5s.
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()

        step._on_progress("Encoding and muxing output…", 0.08)

        assert step._est_remaining_seconds == pytest.approx(22.5)
        assert "Est. remaining" in step._eta_label.text()

    def test_refines_from_real_measured_rate_during_encode(
        self, step: RenderStep
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()

        step._on_progress("Encoding and muxing output…", 0.08)  # cold start
        # Pretend encode+mux has been running for 10 real seconds.
        step._encode_stage_started_at = time.monotonic() - 10.0

        # sub_fraction 0.5 of encode's own (1.0 - 0.08) span -> 900,000ms
        # of the 1,800,000ms total encoded so far; rate = 900,000/10,000
        # = 90x, not the 80x default.
        step._on_progress("Encoding and muxing output…", 0.08 + 0.5 * 0.92)

        assert step._est_remaining_seconds == pytest.approx(10.0, abs=0.5)

    def test_validating_with_no_intervals_resets_to_the_placeholder(
        self, step: RenderStep
    ) -> None:
        # ADR-0044: with real intervals, _on_validating() now seeds a
        # fresh estimate for the validate phase instead of clearing it
        # (see TestProgressAndValidating) -- this covers the zero-
        # interval case, where encode+mux's own now-stale estimate must
        # still be cleared, not left showing a frozen number from the
        # stage that just ended. The label falls back to the
        # placeholder rather than going blank (a User-requested change).
        step.set_inputs(_manifest(), _empty_plan())
        with patch("m4bmaker.gui.filter.wizard.render_step.RenderWorker"):
            _find_button(step, "Start Render").click()
        step._on_progress("Encoding and muxing output…", 0.9)
        assert step._est_remaining_seconds is not None

        step._on_validating()

        assert step._est_remaining_seconds is None
        assert step._eta_label.text() == "Est. remaining: Calculating…"


class TestResultReady:
    def test_passing_validation_completes(
        self, step: RenderStep, tmp_path: Path
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        output_path = tmp_path / "out.m4b"
        result = RenderResult(output_path=output_path, duration_ms=1_800_000)
        validation = ValidationReport(issues=())

        step._on_result_ready(result, validation)

        assert step.can_advance() is True
        text = _body_text(step)
        assert "Passed validation" in text
        assert str(output_path) in text
        assert step.report_path is not None
        assert str(step.report_path) in text

    def test_public_properties_expose_the_real_result(
        self, step: RenderStep, tmp_path: Path
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        result = RenderResult(output_path=tmp_path / "out.m4b", duration_ms=1_800_000)
        validation = ValidationReport(issues=())

        step._on_result_ready(result, validation)

        assert step.result is result
        assert step.validation is validation
        assert step.report_path == report_path_for(result.output_path)

    def test_public_properties_none_before_any_result(self, step: RenderStep) -> None:
        assert step.result is None
        assert step.validation is None
        assert step.report_path is None

    def test_passing_with_warnings_shows_them(
        self, step: RenderStep, tmp_path: Path
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        result = RenderResult(output_path=tmp_path / "out.m4b", duration_ms=1_800_000)
        validation = ValidationReport(
            issues=(
                ValidationIssue(
                    check="metadata",
                    severity=Severity.WARNING,
                    message="Best-effort field 'series' not preserved.",
                ),
            )
        )

        step._on_result_ready(result, validation)

        assert step.can_advance() is True
        assert "series" in _body_text(step)

    def test_failed_validation_shows_needs_attention_not_complete(
        self, step: RenderStep, tmp_path: Path
    ) -> None:
        step.set_inputs(_manifest(), _empty_plan())
        result = RenderResult(output_path=tmp_path / "out.m4b", duration_ms=1_800_000)
        validation = ValidationReport(
            issues=(
                ValidationIssue(
                    check="duration",
                    severity=Severity.ERROR,
                    message="Output duration differs by 500ms.",
                ),
            )
        )

        step._on_result_ready(result, validation)

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
    def test_same_inputs_while_completed_is_a_no_op(
        self, step: RenderStep, tmp_path: Path
    ) -> None:
        manifest = _manifest()
        plan = _empty_plan()
        step.set_inputs(manifest, plan)
        result = RenderResult(output_path=tmp_path / "out.m4b", duration_ms=1)
        validation = ValidationReport(issues=())
        step._on_result_ready(result, validation)

        step.set_inputs(_manifest(), _empty_plan())

        assert step.can_advance() is True

    def test_different_plan_resets_to_ready(
        self, step: RenderStep, tmp_path: Path
    ) -> None:
        manifest = _manifest()
        step.set_inputs(manifest, _empty_plan())
        result = RenderResult(output_path=tmp_path / "out.m4b", duration_ms=1)
        validation = ValidationReport(issues=())
        step._on_result_ready(result, validation)

        step.set_inputs(manifest, _plan_with_one_interval())

        assert step.can_advance() is False
        assert "Start Render" in _body_text(step)

    def test_different_manifest_resets_to_ready(
        self, step: RenderStep, tmp_path: Path
    ) -> None:
        step.set_inputs(_manifest(fingerprint="sha256:one"), _empty_plan())
        result = RenderResult(output_path=tmp_path / "out.m4b", duration_ms=1)
        validation = ValidationReport(issues=())
        step._on_result_ready(result, validation)

        step.set_inputs(_manifest(fingerprint="sha256:two"), _empty_plan())

        assert step.can_advance() is False
