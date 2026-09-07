"""Tests for m4bmaker.gui.filter.wizard.wizard_window.WizardWindow
(ADR-0010, PRD §7.2).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QMessageBox

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.model_manager import KNOWN_MODELS
from m4bmaker.filter.models import (
    NORMALIZATION_VERSION,
    AttenuationSettings,
    MediaManifest,
    RenderPlan,
)
from m4bmaker.filter.renderer import RenderResult
from m4bmaker.filter.scan import run_scan
from m4bmaker.filter.transcript import (
    Transcript,
    TranscriptEngine,
    TranscriptSource,
    TranscriptStatus,
)
from m4bmaker.filter.validator import ValidationReport
from m4bmaker.gui.filter.wizard.profile_step import ProfileStep
from m4bmaker.gui.filter.wizard.render_step import RenderStep
from m4bmaker.gui.filter.wizard.review_step import ReviewStep
from m4bmaker.gui.filter.wizard.scan_step import ScanStep
from m4bmaker.gui.filter.wizard.source_step import SourceStep
from m4bmaker.gui.filter.wizard.stepper import STEP_LABELS
from m4bmaker.gui.filter.wizard.transcribe_step import TranscribeStep
from m4bmaker.gui.filter.wizard.transcript_step import TranscriptStep
from m4bmaker.gui.filter.wizard.wizard_window import WizardWindow

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(autouse=True)
def _fast_transcript_lookup():
    """find_compatible_transcript() (ADR-0013) falls back to scanning the
    real, user-wide transcripts directory when given no override --
    same cost test_source_step.py's own `_fast_transcript_lookup` fixture
    exists to avoid. Nearly every test in this file drives the wizard
    shell past Source (which now runs the lookup on a background
    TranscriptLookupWorker via source_step.py, ADR-0050) and/or into
    Transcript (which calls find_compatible_transcript directly, via
    transcript_step.py's own set_manifest()), so left unpatched this
    real disk scan runs on almost every test here, not just a handful.
    Both call sites are defaulted to a cheap no-op; every test in this
    file already treats "not found" (None) as the expected outcome for
    its fake fingerprints, so this changes no test's behavior. Tests
    that need a specific *found* transcript (e.g.
    TestTranscriptToProfileWiring's reuse test) set
    `TranscriptStep._compatible_transcript` directly rather than relying
    on find_compatible_transcript's return value, so they're unaffected
    too.

    source_step's TranscriptLookupWorker is patched with a
    side_effect, not a bare patch -- a bare patch's mock class returns
    the *same* .return_value from every call, so a test that advances
    Source more than once (e.g. TestSourceToTranscriptWiring's "second
    pass with a different source") would get back the same worker
    "instance" every time, unlike a real TranscriptLookupWorker() call
    which constructs a distinct object per selection (see
    test_source_step.py's own identical fixture for the same reasoning).
    """
    with (
        patch(
            "m4bmaker.gui.filter.wizard.source_step.TranscriptLookupWorker",
            side_effect=lambda *_a, **_k: MagicMock(),
        ),
        patch(
            "m4bmaker.gui.filter.wizard.transcript_step.find_compatible_transcript",
            return_value=None,
        ),
    ):
        yield


@pytest.fixture()
def win(tmp_path: Path) -> WizardWindow:
    # models_dest_dir points TranscriptStep at tmp_path rather than the
    # real, user-wide models directory — see WizardWindow's own
    # docstring for that param. catalog_service is likewise a fresh,
    # empty in-memory instance, not the real per-user catalog.json —
    # without this, WizardWindow.__init__'s own "catalog_service or
    # load_catalog()" fallback reads whatever profiles genuinely exist
    # on the machine running the tests, which is exactly what let one
    # real, pre-existing profile's own persisted (and possibly stale)
    # attenuation settings leak into these tests undetected until
    # ADR-0023 changed the in-code default and broke the coincidental
    # match.
    return WizardWindow(models_dest_dir=tmp_path, catalog_service=CatalogService())


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
    # CoverArtWorker (ADR-0046) now starts from _on_file_selected, not
    # _on_inspect_finished (ADR-0050) -- calling _on_inspect_finished
    # directly, as this helper does, never touches it, so no patch is
    # needed here for that. TranscriptLookupWorker (also started from
    # _on_inspect_finished, ADR-0050) is patched process-wide by the
    # module's own autouse _fast_transcript_lookup fixture instead.
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


def _make_profile_ready(win: WizardWindow) -> None:
    """Create one real profile directly through the shared
    ``CatalogService`` and refresh ProfileStep's list — same "test the
    shell, not the step" split as the helpers above; ProfileStep's own
    picker/editor/archive logic has its own dedicated tests. A real
    ``create_profile`` call (not a synthetic step-internal attribute) so
    ProfileStep picks it up exactly the way it would after the editor
    dialog saves one.
    """
    profile_step = win._steps[STEP_LABELS.index("Profile")]
    assert isinstance(profile_step, ProfileStep)
    win._catalog_service.create_profile("Family Friendly")
    profile_step._refresh_profiles()


def _make_scan_ready(win: WizardWindow) -> None:
    """Deliver one real, freshly-run Scan directly to ScanStep's own
    result handler, bypassing the real ScanWorker/matcher thread entirely
    — same "test the shell, not the step" split as the helpers above;
    ScanStep's own worker/state logic has its own dedicated tests. Still a
    real ``run_scan()`` call against a real snapshot of whatever profile
    ``_make_profile_ready`` created, matching the exact fingerprint
    ``_make_transcribe_ready`` set up, so ScanStep's own re-entry guard
    (same inputs -> no-op) behaves correctly against later real
    navigation.
    """
    scan_step = win._steps[STEP_LABELS.index("Scan")]
    profile_step = win._steps[STEP_LABELS.index("Profile")]
    assert isinstance(scan_step, ScanStep)
    assert isinstance(profile_step, ProfileStep)
    profile_id = profile_step.selected_profile_id
    assert profile_id is not None
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
    scan_step.set_inputs(transcript, win._catalog_service, profile_id)
    snapshot = win._catalog_service.create_snapshot(profile_id)
    scan = run_scan(transcript, snapshot, NORMALIZATION_VERSION)
    scan_step._on_result_ready(scan)


def _make_render_ready(win: WizardWindow, tmp_path: Path) -> None:
    """Deliver a fake completed render directly to RenderStep's own
    result handler, bypassing the real RenderWorker/ffmpeg entirely —
    same "test the shell, not the step" split as the helpers above;
    RenderStep's own worker/state logic has its own dedicated tests. Uses
    Source's own real manifest (set by ``_make_source_eligible``) rather
    than a second, possibly-inconsistent one, and a real (if empty)
    ``RenderPlan`` — an empty plan is a legitimate real case (nothing
    included), not a shortcut. *tmp_path* must be the same fixture
    instance ``win`` itself was built with (pytest caches it per test, so
    just requesting it in the calling test method is enough) — Render's
    own result handler now writes a real filter-report.json next to the
    output path (ADR-0029), so a fictional ``/tmp/out.m4b`` would fail.
    """
    render_step = win._steps[STEP_LABELS.index("Render")]
    source_step = win._steps[STEP_LABELS.index("Source")]
    assert isinstance(render_step, RenderStep)
    assert isinstance(source_step, SourceStep)
    manifest = source_step.manifest
    assert manifest is not None
    plan = RenderPlan(
        intervals=(),
        attenuation=AttenuationSettings(),
        source_duration_ms=manifest.duration_ms,
    )
    render_step.set_inputs(manifest, plan)
    result = RenderResult(
        output_path=tmp_path / "out.m4b", duration_ms=manifest.duration_ms
    )
    validation = ValidationReport(issues=())
    render_step._on_result_ready(result, validation)


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

    def test_profile_step_is_the_real_widget(self, win: WizardWindow) -> None:
        profile_index = STEP_LABELS.index("Profile")
        assert isinstance(win._steps[profile_index], ProfileStep)

    def test_scan_step_is_the_real_widget(self, win: WizardWindow) -> None:
        scan_index = STEP_LABELS.index("Scan")
        assert isinstance(win._steps[scan_index], ScanStep)

    def test_render_step_is_the_real_widget(self, win: WizardWindow) -> None:
        render_index = STEP_LABELS.index("Render")
        assert isinstance(win._steps[render_index], RenderStep)

    def test_starts_on_first_step(self, win: WizardWindow) -> None:
        assert win._active == 0
        assert win._title_label.text() == "Select Source"
        assert win._back_btn.isEnabled() is False

    def test_apply_stylesheet_does_not_raise(self, win: WizardWindow) -> None:
        win.apply_stylesheet(True)
        win.apply_stylesheet(False)


class TestOpenSettingsForwarding:
    """SourceStep's "Change location…" link has no reach into MainWindow's
    Settings window itself -- WizardWindow just forwards the request up
    to whoever constructed it (window.py's MainWindow)."""

    def test_source_step_signal_forwarded_to_wizard_window(
        self, win: WizardWindow
    ) -> None:
        source_index = STEP_LABELS.index("Source")
        source_step = win._steps[source_index]
        assert isinstance(source_step, SourceStep)

        received: list[None] = []
        win.open_settings_requested.connect(lambda: received.append(None))
        source_step.open_settings_requested.emit()

        assert received == [None]


class TestNavigation:
    def test_continue_advances_one_step(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        win._on_continue()
        assert win._active == 1
        assert win._title_label.text() == "Choose Transcript Path"

    def test_continue_tracks_furthest_reached(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        win._on_continue()
        win._on_continue()
        assert win._furthest == 2

    def test_continue_stops_at_last_step(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        for _ in range(len(STEP_LABELS) + 2):
            win._on_continue()
        assert win._active == len(STEP_LABELS) - 1
        assert win._continue_btn.text() == "Done"

    def test_back_disabled_on_first_step(self, win: WizardWindow) -> None:
        win._on_back()
        assert win._active == 0

    def test_back_returns_one_step(self, win: WizardWindow, tmp_path: Path) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        win._on_continue()
        win._on_continue()
        win._on_back()
        assert win._active == 1

    def test_stepper_click_beyond_furthest_is_rejected(self, win: WizardWindow) -> None:
        win._go_to_step(3)
        assert win._active == 0

    def test_stepper_click_within_furthest_navigates(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        win._on_continue()
        win._on_continue()
        win._go_to_step(0)
        assert win._active == 0
        win._go_to_step(2)
        assert win._active == 2

    def test_stepper_reflects_current_progress(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        win._on_continue()
        assert win._stepper._cells[0]._badge.property("stepState") == "done"
        assert win._stepper._cells[1]._badge.property("stepState") == "current"


class TestNavButtonsFollowStepCanAdvance:
    def test_continue_disabled_when_step_cannot_advance(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        review_index = STEP_LABELS.index("Review")
        for _ in range(review_index):
            win._on_continue()
        assert win._active == review_index
        review = win._steps[review_index]
        assert isinstance(review, ReviewStep)

        # ReviewStep never blocks Continue (base default) — flip its own
        # advance-ability via monkeypatch to prove the shell actually
        # listens to can_advance_changed rather than ignoring it.
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
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
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


class TestFileCardRefresh:
    def test_file_card_updates_after_inspect_even_when_cover_already_arrived(
        self, win: WizardWindow
    ) -> None:
        # Regression: CoverArtWorker and MediaInspectWorker now run
        # concurrently (ADR-0050 addendum), so cover art can finish
        # before inspection does. The persistent file card reads
        # manifest and cover_path together in one call
        # (WizardWindow._refresh_file_card), but was only ever told to
        # re-read them via SourceStep.cover_ready's *cover* emission
        # point -- so a manifest that arrived *after* cover art (the
        # common case now, since mutagen-first cover extraction is
        # faster than the ffprobe inspect subprocess, ADR-0050
        # addendum) was never shown on the card at all, leaving it
        # stuck on its placeholder even though the Source step's own
        # info panel below had already fully populated.
        source = win._steps[STEP_LABELS.index("Source")]
        assert isinstance(source, SourceStep)

        # Simulate cover art finishing first: set the cover path
        # directly, the same state _on_cover_ready() would leave behind,
        # without ever going through _on_inspect_finished().
        source._cover_path = Path("/fake/cover.jpg")

        manifest = MediaManifest(
            schema_version=1,
            source_path="/books/regression.m4b",
            fingerprint="sha256:regression",
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

        assert "regression.m4b" in win._file_card._name_label.text()


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
        self, win: WizardWindow, tmp_path: Path
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
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        win._on_continue()  # Transcript -> Transcribe, for real this time

        assert win._active == transcribe_index
        assert transcribe_index not in win._skipped

    def test_transcribe_step_itself_is_told_at_skip_time(
        self, win: WizardWindow
    ) -> None:
        """ADR-0050 follow-up regression guard: before this fix,
        TranscribeStep had no idea it had been skipped — it just sat in
        its "not ready" state forever, since nothing had ever called
        either of its two entry points for this source. Checked here
        directly on TranscribeStep, not just on the shell's own
        `_skipped` bookkeeping, since that's the actual bug: the shell
        knew Transcribe was skipped the whole time, but the step itself
        didn't."""
        _make_source_eligible(win)
        win._on_continue()  # Source -> Transcript
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        transcribe_step = win._steps[STEP_LABELS.index("Transcribe")]
        assert isinstance(transcript_step, TranscriptStep)
        assert isinstance(transcribe_step, TranscribeStep)
        reused = Transcript(
            schema_version=1,
            status=TranscriptStatus.COMPLETE,
            source=TranscriptSource(
                fingerprint="sha256:x", duration_ms=10_000, selected_audio_stream=0
            ),
            engine=TranscriptEngine(
                name="whisper.cpp", version="1.9.2", model="base.en", model_checksum="x"
            ),
        )
        transcript_step._compatible_transcript = reused

        transcript_step.reuse_requested.emit()

        assert transcribe_step.can_advance() is True
        assert transcribe_step.transcript is reused

    def test_back_from_profile_into_skipped_transcribe_enables_continue(
        self, win: WizardWindow
    ) -> None:
        """The exact bug the User reported: reuse a transcript, then hit
        Back from Profile — landing on Transcribe with Continue
        permanently disabled and no way forward except leaving the
        step entirely."""
        _make_source_eligible(win)
        win._on_continue()  # Source -> Transcript
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        assert isinstance(transcript_step, TranscriptStep)
        transcript_step._compatible_transcript = Transcript(
            schema_version=1,
            status=TranscriptStatus.COMPLETE,
            source=TranscriptSource(
                fingerprint="sha256:x", duration_ms=10_000, selected_audio_stream=0
            ),
            engine=TranscriptEngine(
                name="whisper.cpp", version="1.9.2", model="base.en", model_checksum="x"
            ),
        )
        transcript_step.reuse_requested.emit()  # skips straight to Profile
        assert win._active == STEP_LABELS.index("Profile")

        win._on_back()  # Profile -> Transcribe

        assert win._active == STEP_LABELS.index("Transcribe")
        assert win._continue_btn.isEnabled() is True

    def test_direct_stepper_click_into_skipped_transcribe_enables_continue(
        self, win: WizardWindow
    ) -> None:
        """Same bug, reached the other way: the stepper's own step
        circles let a User jump straight to any previously-visited step
        (`_go_to_step`), not just via the Back button — that path has
        to land in the same working state, not just Back."""
        _make_source_eligible(win)
        win._on_continue()  # Source -> Transcript
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        assert isinstance(transcript_step, TranscriptStep)
        transcript_step._compatible_transcript = Transcript(
            schema_version=1,
            status=TranscriptStatus.COMPLETE,
            source=TranscriptSource(
                fingerprint="sha256:x", duration_ms=10_000, selected_audio_stream=0
            ),
            engine=TranscriptEngine(
                name="whisper.cpp", version="1.9.2", model="base.en", model_checksum="x"
            ),
        )
        transcript_step.reuse_requested.emit()  # skips straight to Profile

        win._go_to_step(STEP_LABELS.index("Transcribe"))

        assert win._active == STEP_LABELS.index("Transcribe")
        assert win._continue_btn.isEnabled() is True


class TestTranscriptToProfileWiring:
    """ADR-0022: Profile only needs the real Transcript to offer "View
    Transcript" — it never affects profile selection — but it has to
    arrive on both paths a Transcript can reach Profile by."""

    def test_advancing_past_transcribe_hands_the_transcript_to_profile(
        self, win: WizardWindow
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        win._on_continue()  # Source -> Transcript
        win._on_continue()  # Transcript -> Transcribe
        _make_transcribe_ready(win)

        transcribe_step = win._steps[STEP_LABELS.index("Transcribe")]
        profile_step = win._steps[STEP_LABELS.index("Profile")]
        assert isinstance(transcribe_step, TranscribeStep)
        assert isinstance(profile_step, ProfileStep)

        win._on_continue()  # Transcribe -> Profile

        assert profile_step._transcript is transcribe_step.transcript

    def test_reuse_path_hands_the_reused_transcript_to_profile(
        self, win: WizardWindow
    ) -> None:
        _make_source_eligible(win)
        win._on_continue()  # Source -> Transcript
        transcript_step = win._steps[STEP_LABELS.index("Transcript")]
        profile_step = win._steps[STEP_LABELS.index("Profile")]
        assert isinstance(transcript_step, TranscriptStep)
        assert isinstance(profile_step, ProfileStep)
        reused = Transcript(
            schema_version=1,
            status=TranscriptStatus.COMPLETE,
            source=TranscriptSource(
                fingerprint="sha256:x", duration_ms=10_000, selected_audio_stream=0
            ),
            engine=TranscriptEngine(
                name="whisper.cpp", version="1.9.2", model="base.en", model_checksum="x"
            ),
        )
        transcript_step._compatible_transcript = reused

        transcript_step.reuse_requested.emit()

        assert profile_step._transcript is reused


class TestDoneClosesWizard:
    """ADR-0022 merged the former Complete step into Render's own
    Completed state — Render is now the wizard's last step, and
    ``_make_render_ready`` already drives it to ``_STATE_COMPLETED`` (so
    ``can_advance()`` is already true once every earlier step is ready),
    matching what used to require one extra Render -> Complete hop."""

    def test_clicking_done_on_the_last_step_closes_the_window(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        for _ in range(len(STEP_LABELS) - 1):
            win._on_continue()
        assert win._active == STEP_LABELS.index("Render")
        assert win._continue_btn.text() == "Done"

        with patch.object(win, "close") as mock_close:
            win._on_continue()  # click "Done"

        mock_close.assert_called_once()

    def test_done_does_not_advance_past_the_last_step(
        self, win: WizardWindow, tmp_path: Path
    ) -> None:
        _make_source_eligible(win)
        _make_transcript_ready(win)
        _make_transcribe_ready(win)
        _make_profile_ready(win)
        _make_scan_ready(win)
        _make_render_ready(win, tmp_path)
        for _ in range(len(STEP_LABELS) - 1):
            win._on_continue()

        win._on_continue()

        assert win._active == STEP_LABELS.index("Render")


class TestCloseEventConfirmation:
    """ADR-0021: ADR-0010's original wireframe review already decided
    closing mid-Transcribe/mid-Render should confirm first — no code
    ever actually did it until now.

    Workers are stand-in ``MagicMock``s, not real ``QThread``s — same
    "test the shell, not the step" split every other shell-level test in
    this file already uses; TranscribeWorker/RenderWorker have their own
    dedicated tests for their own real behavior.
    """

    def _running_worker(self) -> MagicMock:
        worker = MagicMock()
        worker.isRunning.return_value = True
        return worker

    def test_nothing_running_closes_without_any_prompt(self, win: WizardWindow) -> None:
        with patch(
            "m4bmaker.gui.filter.wizard.wizard_window.QMessageBox.question"
        ) as mock_question:
            result = win.close()

        mock_question.assert_not_called()
        assert result is True

    def test_declining_mid_transcribe_keeps_the_window_open(
        self, win: WizardWindow
    ) -> None:
        transcribe_step = win._steps[STEP_LABELS.index("Transcribe")]
        assert isinstance(transcribe_step, TranscribeStep)
        worker = self._running_worker()
        transcribe_step._worker = worker

        with patch(
            "m4bmaker.gui.filter.wizard.wizard_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            result = win.close()

        assert result is False
        worker.request_pause.assert_not_called()

    def test_confirming_mid_transcribe_pauses_and_waits_then_closes(
        self, win: WizardWindow
    ) -> None:
        transcribe_step = win._steps[STEP_LABELS.index("Transcribe")]
        assert isinstance(transcribe_step, TranscribeStep)
        worker = self._running_worker()
        transcribe_step._worker = worker

        with patch(
            "m4bmaker.gui.filter.wizard.wizard_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            result = win.close()

        assert result is True
        worker.request_pause.assert_called_once()
        worker.wait.assert_called_once_with(5000)

    def test_declining_mid_render_keeps_the_window_open(
        self, win: WizardWindow
    ) -> None:
        render_step = win._steps[STEP_LABELS.index("Render")]
        assert isinstance(render_step, RenderStep)
        render_step._worker = self._running_worker()

        with patch(
            "m4bmaker.gui.filter.wizard.wizard_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            result = win.close()

        assert result is False

    def test_confirming_mid_render_closes_without_calling_anything_on_it(
        self, win: WizardWindow
    ) -> None:
        # RenderWorker has no request_pause()/request_cancel() at all
        # (ADR-0019) — confirming just lets the close proceed, with
        # nothing to ask the worker to do.
        render_step = win._steps[STEP_LABELS.index("Render")]
        assert isinstance(render_step, RenderStep)
        worker = self._running_worker()
        render_step._worker = worker

        with patch(
            "m4bmaker.gui.filter.wizard.wizard_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            result = win.close()

        assert result is True
        worker.request_pause.assert_not_called()
        worker.wait.assert_not_called()

    def test_both_running_confirms_both_in_order(self, win: WizardWindow) -> None:
        transcribe_step = win._steps[STEP_LABELS.index("Transcribe")]
        render_step = win._steps[STEP_LABELS.index("Render")]
        assert isinstance(transcribe_step, TranscribeStep)
        assert isinstance(render_step, RenderStep)
        transcribe_worker = self._running_worker()
        transcribe_step._worker = transcribe_worker
        render_step._worker = self._running_worker()

        with patch(
            "m4bmaker.gui.filter.wizard.wizard_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as mock_question:
            result = win.close()

        assert result is True
        assert mock_question.call_count == 2
        transcribe_worker.request_pause.assert_called_once()
