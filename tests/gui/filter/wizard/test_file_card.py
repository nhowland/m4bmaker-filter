"""Tests for m4bmaker.gui.filter.wizard.file_card.FileCard/set_cover_pixmap
(ADR-0046)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel

from m4bmaker.filter.models import AudioTrack, MediaManifest
from m4bmaker.gui.filter.wizard.file_card import FileCard, set_cover_pixmap

pytestmark = pytest.mark.usefixtures("qapp")


def _manifest(
    source_path: str = "/books/dcc.m4b",
    duration_ms: int = 48_693_108,
    bit_rate: int | None = 126_000,
    eligible: bool = True,
) -> MediaManifest:
    return MediaManifest(
        schema_version=1,
        source_path=source_path,
        fingerprint="sha256:x",
        duration_ms=duration_ms,
        tracks=(
            AudioTrack(
                index=0,
                codec_name="aac",
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
        cover_present=False,
        eligible=eligible,
    )


@pytest.fixture()
def card() -> FileCard:
    return FileCard()


class TestPlaceholder:
    """Always visible, matching the base app's own CoverWidget/metadata-
    field pattern -- a placeholder shown before a file is chosen, filled
    in once one is, never hidden outright (a deliberate change from this
    widget's earlier "hidden until populated" behavior)."""

    def test_visible_before_any_manifest_is_set(self, card: FileCard) -> None:
        assert card.isVisible() is True

    def test_still_visible_once_a_manifest_is_set(self, card: FileCard) -> None:
        card.set_manifest(_manifest(), None)
        assert card.isVisible() is True

    def test_still_visible_when_manifest_is_cleared_again(self, card: FileCard) -> None:
        card.set_manifest(_manifest(), None)
        card.set_manifest(None, None)
        assert card.isVisible() is True

    def test_placeholder_text_and_cover_shown_before_any_manifest(
        self, card: FileCard
    ) -> None:
        assert card._name_label.text() == "No file selected yet"
        assert card._thumb.text() == "Cover"
        assert card._meta_label.text() == ""

    def test_badge_hidden_until_a_manifest_is_set(self, card: FileCard) -> None:
        assert card._badge.isVisible() is False
        card.set_manifest(_manifest(), None)
        assert card._badge.isVisible() is True

    def test_placeholder_restored_when_manifest_is_cleared_again(
        self, card: FileCard
    ) -> None:
        card.set_manifest(_manifest(), None)
        card.set_manifest(None, None)
        assert card._name_label.text() == "No file selected yet"
        assert card._thumb.text() == "Cover"
        assert card._meta_label.text() == ""
        assert card._badge.isVisible() is False


class TestContent:
    def test_filename_shown(self, card: FileCard) -> None:
        card.set_manifest(_manifest(source_path="/books/dcc.m4b"), None)
        assert card._name_label.toolTip() == "dcc.m4b"

    def test_duration_and_bitrate_shown(self, card: FileCard) -> None:
        card.set_manifest(_manifest(duration_ms=48_693_108, bit_rate=126_000), None)
        text = card._meta_label.text()
        assert "13h 31m" in text
        assert "126 kbps" in text

    def test_missing_bitrate_shows_duration_only(self, card: FileCard) -> None:
        card.set_manifest(_manifest(bit_rate=None), None)
        assert "kbps" not in card._meta_label.text()

    def test_eligible_badge(self, card: FileCard) -> None:
        # "Verified," not "Eligible" -- this badge is visible on every
        # step, not just Source, and "Eligible" reads as stale once
        # you've moved past the check itself.
        card.set_manifest(_manifest(eligible=True), None)
        assert card._badge.text() == "✓ Verified"
        assert card._badge.property("state") == "eligible"

    def test_ineligible_badge(self, card: FileCard) -> None:
        card.set_manifest(_manifest(eligible=False), None)
        assert card._badge.text() == "✕ Not eligible"
        assert card._badge.property("state") == "ineligible"


class TestSetCoverPixmap:
    def test_no_path_shows_fallback_text(self) -> None:
        label = QLabel()
        set_cover_pixmap(label, None, 40, fallback_text="No cover")
        assert label.text() == "No cover"
        assert label.pixmap().isNull()

    def test_nonexistent_path_shows_fallback_text(self, tmp_path: Path) -> None:
        label = QLabel()
        set_cover_pixmap(label, tmp_path / "missing.jpg", 40, fallback_text="No cover")
        assert label.text() == "No cover"

    def test_real_image_is_shown_scaled_to_size(self, tmp_path: Path) -> None:
        # A real, decodable (if tiny) image -- not just a file with
        # image-looking magic bytes -- so QPixmap actually loads it and
        # this test exercises the real scale/crop path, not the
        # fallback one.
        image_path = tmp_path / "cover.png"
        img = QImage(200, 100, QImage.Format.Format_RGB32)
        img.fill(0xFF8800)
        img.save(str(image_path))

        label = QLabel()
        set_cover_pixmap(label, image_path, 40, fallback_text="No cover")
        assert label.text() == ""
        pix = label.pixmap()
        assert pix.isNull() is False
        assert pix.width() == 40
        assert pix.height() == 40
