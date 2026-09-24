"""Tests for m4bmaker.gui.filter.wizard.source_step.SourceStep (PRD §7.2
stage 1; ADR-0013).

The MediaInspectWorker's ffprobe subprocess call is mocked (same
convention test_workers.py already uses for it) — but everything
downstream of a returned MediaManifest (eligibility display, storage
estimate, transcript lookup) runs for real against real
MediaManifest/Transcript objects, not fakes.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

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
        source_path="/books/test-audiobook.m4b",
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
        required_metadata={"title": "Test Audiobook", "author": "Test Author"},
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


@pytest.fixture(autouse=True)
def _fast_transcript_lookup():
    """TranscriptLookupWorker (ADR-0050) runs find_compatible_transcript()
    on a background thread -- that lookup falls back to scanning the
    real, user-wide transcripts directory when given no override, and
    glob-scanning/JSON-parsing dozens of real *.m4bt.json files left
    over from real-book runs takes seconds, not milliseconds. Patched
    out here so _on_inspect_finished() never starts a real thread; the
    tests below that specifically exercise transcript lookup instead
    deliver a result manually via _on_transcript_ready(), the same way
    TestCoverArt delivers cover results via _on_cover_ready().
    """
    # side_effect=lambda: MagicMock(), not a bare patch() -- a bare
    # patch's mock class returns the *same* .return_value from every
    # call, so two selections in one test (as the staleness test below
    # does) would get back the same worker "instance" and could never
    # tell them apart, unlike a real TranscriptLookupWorker() call which
    # constructs a distinct object each time.
    with patch(
        "m4bmaker.gui.filter.wizard.source_step.TranscriptLookupWorker",
        side_effect=lambda *_a, **_k: MagicMock(),
    ):
        yield


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

    _on_file_selected() also kicks off a real CoverArtWorker (ADR-0046),
    now started alongside inspection rather than after it (ADR-0050) —
    patched out here too, same reasoning; the cover-art path has its own
    dedicated coverage below (TestCoverArt) rather than firing unmocked
    on every other test in this file. TranscriptLookupWorker (also
    ADR-0050, started from _on_inspect_finished) is patched process-wide
    by the autouse _fast_transcript_lookup fixture instead, so it needs
    no patch here.
    """
    with (
        patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"),
        patch("m4bmaker.gui.filter.wizard.source_step.CoverArtWorker"),
    ):
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

    def test_placeholder_panel_shown_before_any_file(self, step: SourceStep) -> None:
        # ADR-0046: matches the base app's own empty-state pattern (a
        # "Cover" placeholder box and blank fields, not nothing at all)
        # rather than leaving this whole area blank until the first
        # real inspection result arrives.
        panel = _first_result_widget(step)
        text = _all_text(panel)
        for row_label in (
            "Audio track",
            "Duration",
            "Chapters",
            "Metadata",
            "Saved transcript",
            "Temp storage needed",
        ):
            assert row_label in text
        cover = panel.findChild(QLabel, "coverThumb")
        assert cover is not None
        assert cover.text() == "Cover"

    def test_placeholder_stays_up_during_inspection_not_cleared(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # ADR-0046 addendum: the placeholder used to be torn down the
        # instant a file was picked, leaving a visible gap (nothing
        # shown at all) until the real result arrived a few seconds
        # later. It now stays visible, still in its placeholder state,
        # for the whole "Inspecting…" window -- the info panel is
        # never removed from the layout at all, just updated in place.
        with (
            patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"),
            patch("m4bmaker.gui.filter.wizard.source_step.CoverArtWorker"),
        ):
            step._picker.set_path(tmp_path / "book.m4b")
        # isHidden(), not isVisible() -- step itself is a bare,
        # never-.show()-called widget in this test, which makes
        # isVisible() report False for the whole tree regardless of any
        # setVisible() call (the same Qt quirk file_card.py's own tests
        # hit earlier). isHidden() reflects this widget's own explicit
        # flag, which is what "was it ever hidden" actually means here.
        assert step._info_panel.isHidden() is False
        assert step._secondary_widget is None
        assert "Inspecting" in step._status_label.text()


class TestFileSelection:
    def test_selecting_file_starts_inspection(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        path = tmp_path / "book.m4b"
        # Patch the worker classes themselves so no real QThread actually
        # starts — a real one would race the next test's
        # qapp.processEvents(). CoverArtWorker also needs patching here
        # now (ADR-0050): it starts alongside MediaInspectWorker, not
        # after it, so selecting a file kicks off both at once.
        with (
            patch(
                "m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"
            ) as mock_worker_cls,
            patch("m4bmaker.gui.filter.wizard.source_step.CoverArtWorker"),
        ):
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

    def test_panel_height_unchanged_even_with_a_long_metadata_value(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # The actual root cause of the reported size mismatch: real
        # values (unlike the placeholder's own "—") could be long enough
        # to word-wrap onto a second line, growing the row taller than
        # what the cover thumbnail's own (separately measured) size
        # accounted for. Row values are elided to a single line now
        # instead, specifically so this can't happen -- verified here by
        # comparing the panel's own height before/after a real value
        # long enough that it *would* have wrapped under the old design.
        placeholder_height = step._info_panel.sizeHint().height()
        long_manifest = dataclasses.replace(
            _eligible_manifest(),
            required_metadata={f"field_{i}": "x" * 20 for i in range(12)},
        )
        _select_and_finish(step, tmp_path / "book.m4b", long_manifest)
        assert step._info_panel.sizeHint().height() == placeholder_height

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

    def test_saved_transcript_shows_checking_before_lookup_resolves(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # ADR-0050: the lookup runs on a background thread now, so the
        # row must read as "still working" rather than blank or stale
        # for the (possibly several-second) window before it resolves.
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        assert "Checking" in _all_text(panel)

    def test_no_saved_transcript_shows_none_found(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        step._on_transcript_ready(None, step._transcript_worker)
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
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        step._on_transcript_ready(transcript, step._transcript_worker)
        panel = _first_result_widget(step)
        text = _all_text(panel)
        assert "whisper.cpp" in text
        assert "1.9.2" in text
        assert "base.en" in text

    def test_stale_transcript_workers_result_is_ignored(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # Mirrors TestCoverArt.test_stale_workers_result_is_ignored --
        # an older TranscriptLookupWorker's late result must not
        # overwrite a newer selection's own state.
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        stale_worker = step._transcript_worker
        assert stale_worker is not None

        _select_and_finish(
            step, tmp_path / "other.m4b", _eligible_manifest(fingerprint="sha256:other")
        )
        current_worker = step._transcript_worker
        assert current_worker is not None
        assert current_worker is not stale_worker

        step._on_transcript_ready(None, stale_worker)
        panel = _first_result_widget(step)
        assert "Checking" in _all_text(panel)  # stale result had no effect

    def test_metadata_fields_listed(self, step: SourceStep, tmp_path: Path) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        text = _all_text(panel)
        assert "author" in text
        assert "title" in text

    def test_heading_reads_eligible_for_filtering(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # ADR-0046: a plain "✓ Eligible" heading was replaced with a
        # more explicit, friendlier pill.
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        assert "Eligible for filtering" in _all_text(panel)

    def test_cover_thumbnail_sized_to_match_the_info_panels_real_height(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # Real use found the old fixed 108px thumbnail visibly shorter
        # than the six rows of info text beside it -- sized to that
        # panel's own measured sizeHint() height instead of a fixed
        # guess, so it grows or shrinks to match whatever that panel's
        # real content turns out to need.
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        cover = panel.findChild(QLabel, "coverThumb")
        assert cover is not None
        assert cover.width() == cover.height()
        assert cover.height() > 108  # taller than the old fixed size

    def test_cover_art_row_no_longer_shown_as_text(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # The old "Cover art: Present/Not present" text row is redundant
        # now that a real thumbnail is shown instead (ADR-0046) —
        # regression guard against it silently coming back.
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        assert "Cover art" not in _all_text(panel)

    def test_storage_label_renamed_with_explanatory_tooltip(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel = _first_result_widget(step)
        labels = panel.findChildren(QLabel)
        matches = [lbl for lbl in labels if lbl.text() == "Temp storage needed"]
        assert len(matches) == 1
        assert "kept afterward" in matches[0].toolTip()


class TestCoverArt:
    def test_worker_started_with_the_selected_path_alongside_inspection(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # ADR-0050: CoverArtWorker now starts from _on_file_selected,
        # concurrently with MediaInspectWorker, rather than waiting for
        # _on_inspect_finished -- cover extraction doesn't depend on
        # anything ffprobe reports, so there's no reason for it to wait.
        # It's built from the path the User picked directly, not
        # manifest.source_path (no manifest exists yet at this point).
        path = tmp_path / "book.m4b"
        with (
            patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"),
            patch(
                "m4bmaker.gui.filter.wizard.source_step.CoverArtWorker"
            ) as mock_worker_cls,
        ):
            step._picker.set_path(path)
            mock_worker_cls.assert_called_once_with(path)
            mock_worker_cls.return_value.start.assert_called_once()

    def test_cover_ready_signal_fires_with_real_result(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        received: list[None] = []
        step.cover_ready.connect(lambda: received.append(None))
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"\xff\xd8\xff")  # not decodable -- the path is what matters
        step._on_cover_ready(cover, step._cover_worker)
        assert step.cover_path == cover
        assert len(received) >= 1

    def test_panel_updated_in_place_once_art_arrives_not_rebuilt(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # ADR-0046 addendum: the panel used to be torn down and rebuilt
        # from scratch every time late-arriving cover art showed up --
        # now it's the same persistent _InfoPanel instance the whole
        # time, just updated in place.
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        panel_before = _first_result_widget(step)
        step._on_cover_ready(tmp_path / "cover.jpg", step._cover_worker)
        panel_after = _first_result_widget(step)
        assert panel_after is panel_before
        assert panel_after is step._info_panel

    def test_stale_workers_result_is_ignored(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        # The User picked a different file, and its own inspection+cover
        # pair completed too, all before an *older* CoverArtWorker's own
        # late result arrives -- that late result must not overwrite the
        # newer selection's own state.
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        stale_worker = step._cover_worker
        assert stale_worker is not None

        _select_and_finish(step, tmp_path / "other.m4b", _eligible_manifest())
        current_worker = step._cover_worker
        assert current_worker is not None
        assert current_worker is not stale_worker
        step._on_cover_ready(tmp_path / "current_cover.jpg", current_worker)
        assert step.cover_path == tmp_path / "current_cover.jpg"

        step._on_cover_ready(tmp_path / "stale_cover.jpg", stale_worker)
        assert step.cover_path == tmp_path / "current_cover.jpg"  # unchanged

    def test_no_cover_result_is_a_no_op_not_an_error(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _eligible_manifest())
        step._on_cover_ready(None, step._cover_worker)
        assert step.cover_path is None


class TestIneligibleResult:
    def test_can_advance_stays_false(self, step: SourceStep, tmp_path: Path) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _ineligible_manifest())
        assert step.can_advance() is False

    def test_persistent_info_panel_hidden_in_favor_of_the_secondary_widget(
        self, step: SourceStep, tmp_path: Path
    ) -> None:
        _select_and_finish(step, tmp_path / "book.m4b", _ineligible_manifest())
        assert step._info_panel.isHidden() is True
        assert step._secondary_widget is not None
        assert step._secondary_widget.isHidden() is False

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
        with (
            patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"),
            patch("m4bmaker.gui.filter.wizard.source_step.CoverArtWorker"),
        ):
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
        # would also kick off real MediaInspectWorker and CoverArtWorker
        # instances via _on_file_selected — patched out for the same
        # reason _select_and_finish() patches them.
        with (
            patch("m4bmaker.gui.filter.wizard.source_step.MediaInspectWorker"),
            patch("m4bmaker.gui.filter.wizard.source_step.CoverArtWorker"),
        ):
            step._picker.dropEvent(event)
        assert received == [path]


class TestOpenSettingsShortcut:
    """The "Change temp storage location…" link on the info panel's
    "Temp storage needed" row -- lets a User who's just seen the storage
    estimate jump straight to Settings to redirect it."""

    def test_present_in_placeholder_state(self, step: SourceStep) -> None:
        # Not tied to a loaded manifest -- the temp-folder setting is
        # global, so the link is available before any file is chosen too.
        buttons = step._info_panel.findChildren(QPushButton)
        assert any(b.text() == "Change temp storage location…" for b in buttons)

    def test_clicking_link_fires_source_step_signal(self, step: SourceStep) -> None:
        received: list[None] = []
        step.open_settings_requested.connect(lambda: received.append(None))

        buttons = step._info_panel.findChildren(QPushButton)
        link = next(b for b in buttons if b.text() == "Change temp storage location…")
        link.click()

        assert received == [None]


def _first_result_widget(step: SourceStep) -> QWidget:
    """The currently *visible* result widget -- ADR-0046's persistent
    _InfoPanel stays in step._result_layout permanently (just hidden,
    not removed) whenever a secondary ineligible/error widget is shown
    in its place, so "the first item in the layout" is no longer
    reliably "the one actually being shown" the way it was before."""
    if step._secondary_widget is not None:
        return step._secondary_widget
    return step._info_panel


def _all_text(widget: QWidget) -> str:
    """All QLabel text under *widget*, including *widget* itself if it
    is one — the inspection-error panel is a single bare QLabel, not a
    container, so restricting to findChildren() alone would miss it."""
    labels = list(widget.findChildren(QLabel))
    if isinstance(widget, QLabel):
        labels.append(widget)
    return " ".join(label.text() for label in labels)
