"""The Render wizard step (PRD §7.2 stage 7, §8; wireframe pass
2026-08-25, ADR-0019; merged with the former Complete step per
ADR-0022).

Real, not a placeholder — and, as of ADR-0022, the wizard's last step:
Complete (formerly PRD §7.2 stage 8, ADR-0020) was folded into this
step's own Completed state once the two became near-duplicates of each
other (Complete's only remaining distinct content was its Open Folder
button). ``renderer.render()``/``validator.validate()``
(ADR-0006/0007) needed no changes at all to be wired up here. Wired to
its two real predecessors: Source (the original ``MediaManifest``, via
:class:`~.source_step.SourceStep`) and Review (the live include/exclude
render plan, via :meth:`~.review_step.ReviewStep.current_render_plan`,
which the original Render round added since nothing outside Review
could reach that before).

Four states — no Paused state, matching Scan's own reasoning even though
Render's job is a much longer, genuinely staged one: ``render()`` is one
synchronous, all-or-nothing call, and ADR-0007 already explicitly decided
(with real evidence: 615s/10.25min measured for a full 13.5-hour book)
that full-restart-on-failure is the right MVP position, not a gap to
close here.

- **Ready to render** — the output path and bitrate are both real,
  editable defaults (``renderer.default_output_path()``/
  ``renderer.pick_default_bitrate()``, ADR-0019 — the latter ports
  ``gui/window.py``'s own real bitrate-auto-selection logic rather than
  reinventing it), not fixed choices. Still no upfront "Est. render
  time" here — ADR-0007's own real measurement is for exactly one
  fixture, and unlike Running's own estimate (below), there's no live
  data yet at this point to correct an initial guess against.
- **Running** — real, determinate progress from ``render()``'s own four
  stages, reweighted by real relative cost rather than four equal 25%
  steps (ADR-0028 — ADR-0007's own measurement: encode+mux is ~93% of
  total render time on a real 13.5-hour book), then a *second*,
  separately-determinate "Validating output…" phase once ``validate()``
  takes over — real per-interval progress from its own attenuation check
  (ADR-0044; earlier versions of this step showed an indeterminate tail
  here, before real data — a real ~11.5-hour book's own filter-report
  timing — showed validation can itself cost minutes, not the negligible
  tail originally assumed). Alongside Elapsed, an estimated-remaining-
  time label spans both phases: encode+mux's own rate (ADR-0028) while
  render() runs, then validate()'s own interval-count-based rate once
  it takes over — both seeded from a rough hardcoded default and
  corrected by this run's own measured data the moment it exists, same
  "re-anchor the countdown" pattern Transcribe's own "Est. remaining"
  already uses (ADR-0027). No Cancel button — there is no handle back to
  the underlying ffmpeg subprocess to actually stop; the wizard window's
  own confirm-before-close is the real, honest escape hatch, not a
  button that can't do what it claims.
- **Needs attention** — either a real ``RenderError`` or a failed
  ``ValidationReport`` (PRD §8.1: never present a failed validation as
  success) lands here. The output file is never auto-deleted — Retry
  safely overwrites it via ``encode_and_mux()``'s own atomic staging.
- **Completed** — real validation status and warnings, the real output
  path, and a real, newly-persisted ``filter-report.json``
  (``filter_report.write_filter_report()``, ADR-0019 — ADR-0007 itself
  flagged this as deferred "UI/orchestration-layer work," not blocked by
  anything in that ADR). Called from *this step* now, not
  ``RenderWorker`` (ADR-0029) — the report widened to cover the whole
  pipeline (transcript paths, filter stats, every stage's timing), and
  this step is where the Transcript/Scan/CatalogService/earlier-stage
  timings the report needs already arrive from the wizard shell; the
  worker only ever held a bare manifest/plan. Plus an Open Folder button
  (ported from the
  former Complete step, ADR-0022) that opens the output's parent
  directory — the report always lives in that same directory
  (``filter_report.report_path_for()`` derives it from the output
  path's own name), so one button correctly covers both files rather
  than two redundant ones opening the same folder. Deliberately not
  showing a numeric duration/chapter delta on success: ``validator.py``'s
  checks only ever produce an *issue* when something fails — there is no
  structured "it matched by Xms" value recorded on the passing path to
  display honestly. This is now the wizard's terminal state — the
  shell's footer button reads "Done" here and closes the window (same
  behavior Complete used to own; see ``wizard_window.py``).
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.filter_report import write_filter_report
from m4bmaker.filter.models import MediaManifest, RenderPlan
from m4bmaker.filter.renderer import (
    SUPPORTED_BITRATES,
    RenderResult,
    _STAGE_ENCODE_START,
    default_output_path,
    estimate_storage_bytes,
    pick_default_bitrate,
)
from m4bmaker.filter.scan import Scan
from m4bmaker.filter.transcript import Transcript
from m4bmaker.filter.validator import ValidationReport

from ..workers import RenderWorker
from .source_step import _format_duration
from .step_base import WizardStep
from .transcribe_step import _format_elapsed

#: Rough, deliberately-approximate "audio-ms encoded per wall-clock-ms"
#: guess used only until this run's own encode+mux stage has real
#: measured data (ADR-0028) — derived from ADR-0007's one real
#: measurement (13.5h encoded in 587.7s, ~82.7x realtime), rounded down
#: slightly for a touch of headroom across different hardware/bitrates,
#: not a claim of accuracy for an arbitrary machine.
_DEFAULT_ENCODE_REALTIME_MULTIPLIER = 80.0

#: Rough, deliberately-approximate "wall-clock seconds per render
#: interval" guess used only until this run's own validate() stage has
#: real measured data (ADR-0044) — derived from a real ~11.5-hour book's
#: own recorded filter-report timing (108.7s across 293 intervals,
#: ~0.37s/interval; validate_attenuation() spawns one ffmpeg subprocess
#: per interval), rounded up slightly for headroom rather than claiming
#: that exact figure holds on every machine.
_DEFAULT_VALIDATE_SECONDS_PER_INTERVAL = 0.4

_STATE_NOT_READY = "not_ready"
_STATE_READY = "ready"
_STATE_RUNNING = "running"
_STATE_NEEDS_ATTENTION = "needs_attention"
_STATE_COMPLETED = "completed"


def _info_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("statusLabel")
    label.setWordWrap(True)
    return label


def _format_bytes(n: int) -> str:
    gb = n / (1024**3)
    if gb >= 1:
        return f"~{gb:.1f} GB"
    mb = n / (1024**2)
    return f"~{mb:.0f} MB"


class RenderStep(WizardStep):
    """PRD §7.2 stage 7: "confirm output plan, run attenuate/encode/mux/
    validation"."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_title = "Render"
        self.step_subtitle = (
            "Confirm the output plan, then attenuate, encode, mux, and " "validate."
        )
        self._manifest: MediaManifest | None = None
        self._render_plan: RenderPlan | None = None
        self._output_path: Path | None = None
        self._bitrate: str = ""
        # The bitrate pick_default_bitrate() itself chose, kept separate
        # from _bitrate (which _on_start() overwrites with whatever the
        # combo currently shows) so the "matches your source" hint can
        # tell "still the auto-pick" from "the User changed it" even
        # after Start is clicked.
        self._auto_bitrate: str = ""
        self._worker: RenderWorker | None = None
        self._result: RenderResult | None = None
        self._validation: ValidationReport | None = None
        self._report_path: Path | None = None
        self._state = _STATE_NOT_READY
        self._error_message: str | None = None
        self._start_time: float | None = None

        # Context this step doesn't otherwise need to run a render, but
        # the filter report does (ADR-0029) — sourced from earlier steps
        # via the wizard shell, not re-derived here.
        self._transcript: Transcript | None = None
        self._scan: Scan | None = None
        self._catalog: CatalogService | None = None
        self._transcribe_elapsed_seconds: float | None = None
        self._scan_elapsed_seconds: float | None = None
        self._render_elapsed_seconds: float | None = None
        self._validation_elapsed_seconds: float | None = None
        self._validating_at: float | None = None

        # Estimated-remaining-time bookkeeping (ADR-0028) -- only for the
        # encode+mux stage, the one real measurement (ADR-0007) showed
        # dominates total render time; extract/envelope/attenuate are
        # fast enough not to be worth estimating.
        self._encode_stage_started_at: float | None = None
        self._est_remaining_seconds: float | None = None
        self._est_anchor_time: float | None = None

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._build_ui()
        self._render_body()

    # ── public state ─────────────────────────────────────────────────────

    def can_advance(self) -> bool:
        return self._state == _STATE_COMPLETED

    @property
    def result(self) -> RenderResult | None:
        return self._result

    @property
    def validation(self) -> ValidationReport | None:
        return self._validation

    @property
    def report_path(self) -> Path | None:
        return self._report_path

    def set_inputs(
        self,
        manifest: MediaManifest,
        render_plan: RenderPlan,
        *,
        transcript: Transcript | None = None,
        scan: Scan | None = None,
        catalog: CatalogService | None = None,
        transcribe_elapsed_seconds: float | None = None,
        scan_elapsed_seconds: float | None = None,
    ) -> None:
        """Entry point, called by the wizard shell with Source's real
        ``MediaManifest`` and Review's *live* ``RenderPlan`` every time the
        User continues past Review — including re-entering after Back
        with nothing changed, while this step is already running or has
        already completed. A no-op in that case (same source, same exact
        plan — ``RenderPlan``/``AttenuationSettings``/``RenderInterval``
        are all frozen dataclasses, so structural ``==`` is exactly the
        right comparison), matching every other real step's own re-entry
        guard and for the same reason. If the plan genuinely changed
        (the User went back to Review and changed a decision), this
        resets to "ready to render" with a freshly recomputed default
        output path/bitrate — an old confirmed plan is stale by
        construction once what it would render has changed.

        The keyword-only params carry no rendering decision at all —
        they exist purely so the filter report (ADR-0029), written once
        this step's own render actually completes, can describe the
        whole pipeline (transcript paths, filter stats, earlier steps'
        timings) instead of just this step's own output/validation.
        """
        already_in_flight = self._state in (_STATE_RUNNING, _STATE_COMPLETED)
        same_inputs = (
            self._manifest is not None
            and self._manifest.fingerprint == manifest.fingerprint
            and self._render_plan == render_plan
        )
        if already_in_flight and same_inputs:
            return

        self._manifest = manifest
        self._render_plan = render_plan
        self._transcript = transcript
        self._scan = scan
        self._catalog = catalog
        self._transcribe_elapsed_seconds = transcribe_elapsed_seconds
        self._scan_elapsed_seconds = scan_elapsed_seconds
        self._result = None
        self._validation = None
        self._report_path = None
        self._error_message = None
        self._output_path = default_output_path(Path(manifest.source_path))
        track = next(
            (t for t in manifest.tracks if t.index == manifest.selected_track_index),
            None,
        )
        source_bit_rate = track.bit_rate if track else None
        self._bitrate = pick_default_bitrate(
            source_bit_rate, track.codec_name if track else None
        )
        # Only a real match to a known source bitrate counts as "auto-
        # picked" for the hint's purposes -- pick_default_bitrate()
        # falling back to its own hardcoded DEFAULT_BITRATE because the
        # source's bitrate is unknown isn't a match to anything real,
        # and the hint would be false if shown for it.
        self._auto_bitrate = self._bitrate if source_bit_rate is not None else ""
        self._state = _STATE_READY
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._body)
        root.addStretch(1)

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_body(self) -> None:
        self._clear_body()
        if self._state == _STATE_NOT_READY:
            self._body_layout.addWidget(_info_label("Review a scan first."))
        elif self._state == _STATE_READY:
            self._body_layout.addWidget(self._build_ready_panel())
        elif self._state == _STATE_RUNNING:
            self._body_layout.addWidget(self._build_running_panel())
        elif self._state == _STATE_NEEDS_ATTENTION:
            self._body_layout.addWidget(self._build_needs_attention_panel())
        elif self._state == _STATE_COMPLETED:
            self._body_layout.addWidget(self._build_completed_panel())

    # ── "ready to render" ────────────────────────────────────────────────

    def _build_ready_panel(self) -> QFrame:
        assert self._manifest is not None
        assert self._render_plan is not None
        assert self._output_path is not None
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Ready to render")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(
            _info_label(
                "Nothing runs until this is confirmed — Start is its own "
                "explicit action."
            )
        )

        if self._error_message:
            error_label = QLabel(self._error_message)
            error_label.setWordWrap(True)
            error_label.setStyleSheet("color: #c45a2d;")
            layout.addWidget(error_label)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output:"))
        self._output_input = QLineEdit(str(self._output_path))
        self._output_input.setReadOnly(True)
        output_row.addWidget(self._output_input, stretch=1)
        browse_btn = QPushButton("Change…")
        browse_btn.clicked.connect(self._on_browse_output)
        output_row.addWidget(browse_btn)
        layout.addLayout(output_row)

        bitrate_row = QHBoxLayout()
        bitrate_row.addWidget(QLabel("Bitrate:"))
        self._bitrate_combo = QComboBox()
        self._bitrate_combo.addItems(SUPPORTED_BITRATES)
        self._bitrate_combo.setCurrentText(self._bitrate)
        self._bitrate_combo.currentTextChanged.connect(self._on_bitrate_changed)
        bitrate_row.addWidget(self._bitrate_combo)
        self._bitrate_hint_label = _info_label("")
        bitrate_row.addWidget(self._bitrate_hint_label)
        bitrate_row.addStretch(1)
        layout.addLayout(bitrate_row)
        self._update_bitrate_hint(self._bitrate_combo.currentText())

        storage_bytes = estimate_storage_bytes(self._manifest)
        interval_count = len(self._render_plan.intervals)
        hit_count = sum(len(iv.hit_ids) for iv in self._render_plan.intervals)
        layout.addWidget(
            _info_label(
                f"Temp storage needed: {_format_bytes(storage_bytes)} "
                "(temporary working files + final output)"
            )
        )
        layout.addWidget(
            _info_label(
                f"Intervals to attenuate: {interval_count} "
                f"(merged from {hit_count} hit{'' if hit_count == 1 else 's'})"
            )
        )

        start_btn = QPushButton("▶ Start Render")
        start_btn.clicked.connect(self._on_start)
        row = QHBoxLayout()
        row.addWidget(start_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    def _on_browse_output(self) -> None:
        assert self._output_path is not None
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose output location",
            str(self._output_path),
            "M4B Audiobooks (*.m4b)",
        )
        if path:
            self._output_path = Path(path)
            self._output_input.setText(str(self._output_path))

    def _on_bitrate_changed(self, text: str) -> None:
        self._update_bitrate_hint(text)

    def _update_bitrate_hint(self, current_text: str) -> None:
        matches_auto = bool(self._auto_bitrate) and current_text == self._auto_bitrate
        self._bitrate_hint_label.setText(
            "Matches your source file's bitrate" if matches_auto else ""
        )

    def _on_start(self) -> None:
        assert self._manifest is not None
        assert self._render_plan is not None
        assert self._output_path is not None
        self._bitrate = self._bitrate_combo.currentText()

        self._state = _STATE_RUNNING
        self._render_body()

        self._worker = RenderWorker(
            Path(self._manifest.source_path),
            self._manifest,
            self._render_plan,
            self._output_path,
            self._bitrate,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.validating.connect(self._on_validating)
        self._worker.validating_progress.connect(self._on_validating_progress)
        self._worker.result_ready.connect(self._on_result_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

        self._start_time = time.monotonic()
        self._encode_stage_started_at = None
        self._est_remaining_seconds = None
        self._est_anchor_time = None
        self._render_elapsed_seconds = None
        self._validation_elapsed_seconds = None
        self._validating_at = None
        self._elapsed_timer.start()

    # ── "running" ────────────────────────────────────────────────────────

    def _build_running_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Rendering…")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        self._progress_label = _info_label("Starting…")
        layout.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("jobProgress")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        time_row = QHBoxLayout()
        time_row.setSpacing(12)
        self._elapsed_label = QLabel(self._elapsed_display_text())
        self._elapsed_label.setObjectName("statusLabel")
        time_row.addWidget(self._elapsed_label)
        separator = QLabel("·")
        separator.setObjectName("statusLabel")
        time_row.addWidget(separator)
        self._eta_label = QLabel(self._est_remaining_display_text())
        self._eta_label.setObjectName("statusLabel")
        time_row.addWidget(self._eta_label)
        time_row.addStretch(1)
        layout.addLayout(time_row)

        layout.addWidget(
            _info_label(
                "This step can't be paused. If you need to stop it, close "
                "the wizard — but you'll have to start the render over "
                "from the beginning."
            )
        )
        return panel

    def _elapsed_display_text(self) -> str:
        if self._start_time is None:
            return ""
        return f"Elapsed: {_format_elapsed(time.monotonic() - self._start_time)}"

    def _recompute_encode_eta(self, fraction: float) -> None:
        """Re-anchor the countdown (ADR-0028, same pattern as Transcribe's
        ADR-0027): called once when encode+mux's own progress is first
        seen (seeded from the hardcoded default) and again on every
        subsequent progress update (seeded from this run's own real
        measured rate). Only ever called once *fraction* has reached
        encode+mux's own slice of the bar — extract/envelope/attenuate
        are fast enough real relative-cost measurement (ADR-0007) says
        they're not worth estimating."""
        assert self._manifest is not None
        assert self._encode_stage_started_at is not None

        encode_span = 1.0 - _STAGE_ENCODE_START
        encode_sub_fraction = max(0.0, (fraction - _STAGE_ENCODE_START) / encode_span)
        audio_encoded_ms = encode_sub_fraction * self._manifest.duration_ms
        remaining_audio_ms = max(0.0, self._manifest.duration_ms - audio_encoded_ms)

        elapsed_encode_ms = (time.monotonic() - self._encode_stage_started_at) * 1000
        if audio_encoded_ms > 0 and elapsed_encode_ms > 0:
            rate = audio_encoded_ms / elapsed_encode_ms
        else:
            rate = _DEFAULT_ENCODE_REALTIME_MULTIPLIER

        self._est_remaining_seconds = (
            (remaining_audio_ms / rate) / 1000 if rate > 0 else None
        )
        self._est_anchor_time = time.monotonic()

    def _est_remaining_display_text(self) -> str:
        if self._est_remaining_seconds is None or self._est_anchor_time is None:
            # Shown while running but before any estimate exists yet
            # (the fast pre-encode stages, or a zero-interval validate
            # phase with nothing to plan against) — a User-requested
            # change from leaving the label blank, which read as the
            # feature being silently absent rather than still working on
            # an answer. Both of those windows are short in practice
            # (extract/envelope/attenuate are fast; a zero-interval
            # validate() returns almost immediately), so this doesn't
            # sit unresolved for long even where no real estimate will
            # ever land for that specific stage.
            return "Est. remaining: Calculating…"
        countdown = self._est_remaining_seconds - (
            time.monotonic() - self._est_anchor_time
        )
        return f"Est. remaining: ~{_format_elapsed(max(0.0, countdown))}"

    def _tick_elapsed(self) -> None:
        if hasattr(self, "_elapsed_label"):
            self._elapsed_label.setText(self._elapsed_display_text())
        if hasattr(self, "_eta_label"):
            self._eta_label.setText(self._est_remaining_display_text())

    def _on_progress(self, message: str, fraction: float) -> None:
        if self._state != _STATE_RUNNING:
            return
        self._progress_label.setText(message)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(int(fraction * 100))

        if fraction >= _STAGE_ENCODE_START and self._manifest is not None:
            if self._encode_stage_started_at is None:
                self._encode_stage_started_at = time.monotonic()
            self._recompute_encode_eta(fraction)
            if hasattr(self, "_eta_label"):
                self._eta_label.setText(self._est_remaining_display_text())

    def _on_validating(self) -> None:
        if self._state != _STATE_RUNNING:
            return
        self._progress_label.setText("Validating output…")
        interval_count = (
            len(self._render_plan.intervals) if self._render_plan is not None else 0
        )
        if interval_count:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
            # Seed an upfront guess from interval count alone (ADR-0044)
            # -- real per-run data (validate_attenuation()'s own
            # progress callback) replaces this the moment the first
            # interval's progress arrives, the same "hardcoded default,
            # then corrected" pattern encode+mux's own ETA already uses.
            self._est_remaining_seconds = (
                interval_count * _DEFAULT_VALIDATE_SECONDS_PER_INTERVAL
            )
            self._est_anchor_time = time.monotonic()
        else:
            # No intervals at all (e.g. every hit excluded) -- validate()
            # still runs its three near-instant metadata checks, but
            # there's nothing to estimate or show progress against.
            self._progress_bar.setRange(0, 0)
            self._est_remaining_seconds = None
            self._est_anchor_time = None
        if hasattr(self, "_eta_label"):
            self._eta_label.setText(self._est_remaining_display_text())
        # render()'s own real elapsed time, measured from this step's
        # already-existing start timestamp -- validate() takes over from
        # here, so this is exactly the render/validate boundary.
        if self._start_time is not None:
            self._render_elapsed_seconds = time.monotonic() - self._start_time
        self._validating_at = time.monotonic()

    def _on_validating_progress(self, message: str, fraction: float) -> None:
        """Real per-interval progress from validate()'s own attenuation
        check (ADR-0044) — replaces ``_on_validating``'s upfront guess
        with this run's own measured rate the moment real data exists,
        the same re-anchor-the-countdown pattern
        :meth:`_recompute_encode_eta` uses. Estimated directly against
        *fraction* (intervals done / total intervals) rather than an
        audio-duration/rate split like the encode stage — validate's
        real cost (one ffmpeg subprocess per interval) scales with
        interval count, not audio length."""
        if self._state != _STATE_RUNNING:
            return
        self._progress_label.setText(message)
        self._progress_bar.setValue(int(fraction * 100))
        if self._validating_at is not None and fraction > 0:
            elapsed = time.monotonic() - self._validating_at
            self._est_remaining_seconds = elapsed * (1.0 - fraction) / fraction
            self._est_anchor_time = time.monotonic()
        if hasattr(self, "_eta_label"):
            self._eta_label.setText(self._est_remaining_display_text())

    def _on_result_ready(
        self,
        result: RenderResult,
        validation: ValidationReport,
    ) -> None:
        self._worker = None
        self._elapsed_timer.stop()
        self._start_time = None
        self._est_remaining_seconds = None
        self._est_anchor_time = None
        if self._validating_at is not None:
            self._validation_elapsed_seconds = time.monotonic() - self._validating_at
        self._validating_at = None

        assert self._manifest is not None
        report_path = write_filter_report(
            result.output_path,
            result,
            validation,
            self._bitrate,
            source_path=Path(self._manifest.source_path),
            transcript=self._transcript,
            scan=self._scan,
            render_plan=self._render_plan,
            catalog=self._catalog,
            transcribe_elapsed_seconds=self._transcribe_elapsed_seconds,
            scan_elapsed_seconds=self._scan_elapsed_seconds,
            render_elapsed_seconds=self._render_elapsed_seconds,
            validation_elapsed_seconds=self._validation_elapsed_seconds,
        )

        if not validation.passed:
            self._result = result
            self._validation = validation
            self._report_path = report_path
            self._error_message = "; ".join(
                issue.message for issue in validation.errors
            )
            self._state = _STATE_NEEDS_ATTENTION
            self._render_body()
            return

        self._result = result
        self._validation = validation
        self._report_path = report_path
        self._state = _STATE_COMPLETED
        self._render_body()
        self.can_advance_changed.emit(self.can_advance())

    def _on_error(self, message: str) -> None:
        self._worker = None
        self._elapsed_timer.stop()
        self._start_time = None
        self._est_remaining_seconds = None
        self._est_anchor_time = None
        self._validating_at = None
        self._error_message = message
        self._state = _STATE_NEEDS_ATTENTION
        self._render_body()

    # ── "needs attention" ────────────────────────────────────────────────

    def _build_needs_attention_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("Needs attention")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        error_label = QLabel(self._error_message or "Render failed.")
        error_label.setWordWrap(True)
        error_label.setStyleSheet("color: #c45a2d;")
        layout.addWidget(error_label)
        layout.addWidget(
            _info_label(
                "The output file, if one exists, is left on disk — Retry "
                "overwrites it safely, nothing to clean up by hand."
            )
        )

        retry_btn = QPushButton("↻ Retry")
        retry_btn.clicked.connect(self._on_start)
        row = QHBoxLayout()
        row.addWidget(retry_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    # ── "complete" ───────────────────────────────────────────────────────

    def _build_completed_panel(self) -> QFrame:
        assert self._result is not None
        assert self._validation is not None
        assert self._report_path is not None
        panel = QFrame()
        layout = QVBoxLayout(panel)

        if self._validation.warnings:
            heading = QLabel("✓ Passed, with warnings")
        else:
            heading = QLabel("✓ Passed validation")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        for warning in self._validation.warnings:
            layout.addWidget(_info_label(f"⚠ {warning.message}"))

        layout.addWidget(_info_label(f"Output: {self._result.output_path}"))
        layout.addWidget(
            _info_label(
                f"Audiobook length: {_format_duration(self._result.duration_ms)}"
            )
        )
        layout.addWidget(_info_label(f"Report: {self._report_path}"))

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self._on_open_folder)
        row = QHBoxLayout()
        row.addWidget(open_folder_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    def _on_open_folder(self) -> None:
        assert self._result is not None
        folder = self._result.output_path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
