"""Persistent file card (ADR-0046): keeps the loaded book's filename,
duration, bitrate, and eligibility visible below the stepper across
every step from Source through Render, not just on Source itself.

Reads ``SourceStep``'s own ``manifest``/``cover_path`` — no new state
of its own beyond what to display, and no new source of truth (this
widget never mutates or independently re-inspects anything).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from m4bmaker.filter.models import MediaManifest

_THUMB_SIZE = 36


def _repolish(widget: QWidget) -> None:
    """Force Qt to re-evaluate attribute-selector QSS after setProperty()
    -- same helper ``stepper.py`` already uses for its own step-state
    styling, duplicated rather than imported since it's a private,
    per-module convention there."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def set_cover_pixmap(
    label: QLabel, cover_path: Path | None, size: int, fallback_text: str = ""
) -> None:
    """Show *cover_path*'s image in *label* (square, scaled, center-
    cropped to fill — real cover art is rarely already square), or
    *fallback_text* when there's no real image to show. Shared between
    this card and the Source step's own eligibility panel so both
    render a cover exactly the same way."""
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if cover_path is not None and cover_path.exists():
        pix = QPixmap(str(cover_path))
        if not pix.isNull():
            scaled = pix.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            # KeepAspectRatioByExpanding fills at least size x size but
            # can overshoot on the other axis for a non-square source --
            # centered so the crop trims evenly on both sides.
            x = max(0, (scaled.width() - size) // 2)
            y = max(0, (scaled.height() - size) // 2)
            label.setPixmap(scaled.copy(x, y, size, size))
            label.setText("")
            return
    label.setPixmap(QPixmap())
    label.setText(fallback_text)


def _format_duration(ms: int) -> str:
    total_s = ms // 1000
    h, rem = divmod(total_s, 3600)
    m, _s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


class FileCard(QWidget):
    """One-line strip: cover thumbnail, filename, duration/bitrate, and
    an eligibility badge.

    Always visible, from before any source file has even been chosen —
    matching the base app's own ``CoverWidget``/metadata-field pattern
    (a "Cover" placeholder box and empty fields, filled in once a file
    is loaded, never hidden outright) rather than this widget's earlier
    "hidden until populated" behavior."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Explicit, not relying on Qt's own default -- this widget wants
        # to be shown as soon as whatever embeds it is shown, unlike its
        # earlier "hidden until populated" behavior.
        self.setVisible(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(26, 8, 26, 8)
        layout.setSpacing(10)

        self._thumb = QLabel()
        self._thumb.setObjectName("coverThumb")
        layout.addWidget(self._thumb)

        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-weight: 500;")
        layout.addWidget(self._name_label)

        self._meta_label = QLabel()
        self._meta_label.setObjectName("statusLabel")
        layout.addWidget(self._meta_label)

        layout.addStretch(1)

        self._badge = QLabel()
        self._badge.setObjectName("eligibleBadge")
        layout.addWidget(self._badge)

        self._show_placeholder()

    def _show_placeholder(self) -> None:
        set_cover_pixmap(self._thumb, None, _THUMB_SIZE, fallback_text="Cover")
        # Muted, not bold -- reads as an empty slot rather than a real
        # (just oddly-worded) filename, mirroring how the base app's own
        # "Cover" placeholder box reads visually distinct from a loaded
        # cover, not just textually different.
        self._name_label.setObjectName("statusLabel")
        self._name_label.setStyleSheet("")
        _repolish(self._name_label)
        self._name_label.setText("No file selected yet")
        self._name_label.setToolTip("")
        self._meta_label.setText("")
        self._badge.setVisible(False)

    def set_manifest(
        self, manifest: MediaManifest | None, cover_path: Path | None
    ) -> None:
        if manifest is None:
            self._show_placeholder()
            return
        self._badge.setVisible(True)
        set_cover_pixmap(self._thumb, cover_path, _THUMB_SIZE, fallback_text="Cover")

        self._name_label.setObjectName("")
        self._name_label.setStyleSheet("font-weight: 500;")
        _repolish(self._name_label)
        name = Path(manifest.source_path).name
        metrics = self._name_label.fontMetrics()
        self._name_label.setText(
            metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, 360)
        )
        self._name_label.setToolTip(name)

        track = next(
            (t for t in manifest.tracks if t.index == manifest.selected_track_index),
            None,
        )
        parts = [_format_duration(manifest.duration_ms)]
        if track is not None and track.bit_rate:
            parts.append(f"~{round(track.bit_rate / 1000)} kbps")
        self._meta_label.setText(" · ".join(parts))

        if manifest.eligible:
            # "Verified" rather than "Eligible" -- this badge is visible
            # on every step, not just Source, and "Eligible" reads as
            # stale once you've moved past the check that produced it
            # (the User's own observation). "Verified" stays accurate
            # regardless of which step is active, and — unlike
            # alternatives considered ("Ready") — doesn't collide with
            # any step's own notion of being "ready" (e.g. Render's).
            # The ineligible case is untouched: can_advance() already
            # blocks leaving Source with an ineligible file, so that
            # badge is only ever seen on Source itself, where "Not
            # eligible" already stays fully accurate.
            self._badge.setText("✓ Verified")
            self._badge.setProperty("state", "eligible")
        else:
            self._badge.setText("✕ Not eligible")
            self._badge.setProperty("state", "ineligible")
        _repolish(self._badge)
