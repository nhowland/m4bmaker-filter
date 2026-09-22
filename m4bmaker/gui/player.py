"""Compact audio playback widget using PySide6 QMediaPlayer.

Provides a play/pause button, stop button, seek slider, and a time
readout.  Used in the Chapters tab to preview source audio so the user
can identify and edit chapter titles.

Call :meth:`AudioPlayerWidget.load` to open a file and start playback.
Call :meth:`AudioPlayerWidget.seek_chapter` to jump to a timestamp
(milliseconds) without reloading the file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

_ICON_PLAY = "\u25b6"
_ICON_PAUSE = "\u23f8"
_ICON_STOP = "\u23f9"


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as M:SS or H:MM:SS."""
    s = max(0, ms) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


class AudioPlayerWidget(QWidget):
    """Play/Pause + seek slider + time readout for audio preview.

    Selecting a row in the :class:`ChapterTable` should call
    :meth:`load` (new file) or :meth:`seek_chapter` (same file, e.g.
    when editing an existing .m4b).

    *show_controls* (ADR-0052): when ``False``, every one of this
    widget's own row widgets — Play, Stop, the seek slider, the time
    label — are still built (so every other method here still has
    something to update) but none are added to the visible row, leaving
    it empty for an embedder that wants only the QMediaPlayer/
    QAudioOutput plumbing and drives 100% of its own UI instead (the
    Review step's per-hit preview dock: its Play button always restarts
    a short clip from its own start rather than exposing this widget's
    whole-file pause/resume semantics and has no Stop at all — see
    :meth:`play_clip`/:meth:`pause` — and its progress display is
    clip-relative, not this widget's own whole-file slider/time, which
    barely moves at all over a clip that's a few seconds out of a
    multi-hour book — see :attr:`position_changed`).
    """

    # delay (ms) before seeking after a new source is set, to allow
    # the media backend to buffer enough to accept a seek command.
    _SEEK_DELAY_MS = 250

    #: Fires on every real playback-state transition, including the
    #: automatic pause :meth:`play_clip` triggers at its own *end_ms* —
    #: an embedder driving its own transport button (rather than this
    #: widget's own, e.g. with ``show_controls=False``) needs this to
    #: know when that auto-pause happens, not just when its own calls
    #: change state.
    playback_state_changed = Signal(bool)  # True while actually playing

    #: Fires on every real position update from the underlying player —
    #: an embedder building its own clip-relative progress display
    #: (``show_controls=False``) needs live position, not just this
    #: widget's own file-absolute slider/time label (which it isn't
    #: showing at all in that case).
    position_changed = Signal(int)  # position_ms

    def __init__(
        self, parent: QWidget | None = None, *, show_controls: bool = True
    ) -> None:
        super().__init__(parent)

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._audio_out.setVolume(1.0)

        self._seeking = False  # guard re-entrant slider/position updates

        # M7: a single reusable deferred-seek timer, cancelled and restarted
        # on every load/seek so a quick second click cannot fire a stale
        # target position from an earlier request (or the previous file).
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._apply_pending_seek)
        self._pending_seek_ms: int | None = None

        #: ADR-0052: the position (if any) at which the *current*
        #: play_clip() call should auto-pause. Cleared by every other
        #: load/seek entry point so a stale boundary from a previous
        #: clip preview can never fire during unrelated, later
        #: whole-file playback.
        self._clip_end_ms: int | None = None

        # ── buttons ──────────────────────────────────────────────────────────
        self._play_btn = QPushButton(_ICON_PLAY)
        self._play_btn.setObjectName("playerPlayBtn")
        self._play_btn.setFixedSize(36, 32)
        self._play_btn.setToolTip("Play / Pause")
        self._play_btn.clicked.connect(self._toggle_play)

        self._stop_btn = QPushButton(_ICON_STOP)
        self._stop_btn.setObjectName("playerStopBtn")
        self._stop_btn.setFixedSize(36, 32)
        self._stop_btn.setToolTip("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)

        # ── timeline slider ───────────────────────────────────────────────────
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._player.setPosition)

        # ── time label ────────────────────────────────────────────────────────
        self._time_lbl = QLabel("—:—— / —:——")
        self._time_lbl.setStyleSheet(
            "font-size: 11px; color: #7a7a7a; background: transparent;"
        )
        self._time_lbl.setMinimumWidth(110)
        self._time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        # ── layout ────────────────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        if show_controls:
            row.addWidget(self._play_btn)
            row.addWidget(self._stop_btn)
            row.addWidget(self._slider, stretch=1)
            row.addWidget(self._time_lbl)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.addLayout(row)

        # ── player signals ────────────────────────────────────────────────────
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)

        self._update_buttons(QMediaPlayer.PlaybackState.StoppedState)

    # ── public interface ──────────────────────────────────────────────────────

    def load(self, path: Path, start_ms: int = 0) -> None:
        """Load *path* and start playback, optionally seeking to *start_ms*.

        If *path* is already the current source, calls :meth:`seek_chapter`
        instead (avoids unnecessary reloading when navigating chapters inside
        a single .m4b file).
        """
        self._clip_end_ms = None
        new_url = QUrl.fromLocalFile(str(path))
        if self._player.source() == new_url:
            self.seek_chapter(start_ms)
            return

        self._player.setSource(new_url)
        self._player.play()
        self._defer_seek(start_ms)

    def load_paused(self, path: Path, start_ms: int = 0) -> None:
        """Load *path* and seek to *start_ms* without starting playback.

        Use this when selecting a chapter row should preview position
        but not auto-start audio.
        """
        self._clip_end_ms = None
        new_url = QUrl.fromLocalFile(str(path))
        if self._player.source() == new_url:
            self._cancel_pending_seek()
            self._player.setPosition(start_ms)
            return

        self._player.setSource(new_url)
        self._defer_seek(start_ms)

    def seek_chapter(self, start_ms: int) -> None:
        """Seek to *start_ms* in the currently loaded file and resume play."""
        self._clip_end_ms = None
        if self._player.source().isEmpty():
            return
        self._cancel_pending_seek()
        self._player.setPosition(start_ms)
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.play()

    def play_clip(self, path: Path, start_ms: int, end_ms: int) -> None:
        """Play *path* from *start_ms*, auto-pausing once playback reaches
        *end_ms* (ADR-0052) — a short bounded preview, not a full-file
        playthrough. Always (re)starts from *start_ms*, even if this
        exact clip was already playing or paused partway through —
        callers that want true resume-from-pause should use
        :meth:`pause` plus this widget's own Play button instead, the
        same way a whole-file playthrough already works.
        """
        self._clip_end_ms = end_ms
        new_url = QUrl.fromLocalFile(str(path))
        if self._player.source() == new_url:
            self._cancel_pending_seek()
            self._player.setPosition(start_ms)
            self._player.play()
            return

        self._player.setSource(new_url)
        self._player.play()
        self._defer_seek(start_ms)

    def pause(self) -> None:
        """Pause in place, keeping the current position — unlike
        :meth:`stop`, which resets to the start of the file. Exists for
        an embedder driving its own transport button (``show_controls=
        False``) that needs a bare pause without also resetting position
        the way :meth:`stop` does."""
        self._player.pause()

    def stop(self) -> None:
        """Stop playback and reset the slider."""
        self._clip_end_ms = None
        self._cancel_pending_seek()
        self._player.stop()

    def release(self) -> None:
        """Stop playback and clear the loaded source.

        Call before an external process rewrites the currently-open file
        (M6) — QMediaPlayer can hold a file handle/lock on the source even
        while stopped, which fails an in-place save on Windows.
        """
        self._clip_end_ms = None
        self._cancel_pending_seek()
        self._player.stop()
        self._player.setSource(QUrl())

    # ── deferred seek (M7) ────────────────────────────────────────────────────

    def _defer_seek(self, start_ms: int) -> None:
        """Schedule a seek to *start_ms* after the backend has buffered.

        Cancels any seek already pending so a rapid second load/seek cannot
        later apply a stale target — e.g. a quick second click landing on
        the *previous* chapter's position, or on the wrong file entirely.
        """
        self._cancel_pending_seek()
        if start_ms <= 0:
            return
        self._pending_seek_ms = start_ms
        self._seek_timer.start(self._SEEK_DELAY_MS)

    def _cancel_pending_seek(self) -> None:
        self._seek_timer.stop()
        self._pending_seek_ms = None

    def _apply_pending_seek(self) -> None:
        """Bound-method timer callback — reads the target from state, not a closure."""
        if self._pending_seek_ms is not None:
            self._player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = None

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def current_position_ms(self) -> int:
        """Current playback position in milliseconds."""
        return self._player.position()

    @property
    def has_source(self) -> bool:
        """True if a file is loaded."""
        return not self._player.source().isEmpty()

    # ── internal slots ────────────────────────────────────────────────────────

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_stop(self) -> None:
        self._player.stop()

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self._seeking = False
        # A manual scrub is the User taking over -- a stale clip-end
        # boundary from a play_clip() call must not auto-pause partway
        # through wherever they just dragged to.
        self._clip_end_ms = None
        self._player.setPosition(self._slider.value())

    def _on_position_changed(self, position_ms: int) -> None:
        if not self._seeking:
            self._slider.setValue(position_ms)
        duration = self._player.duration()
        self._time_lbl.setText(f"{_fmt_ms(position_ms)} / {_fmt_ms(duration)}")
        self.position_changed.emit(position_ms)
        if (
            self._clip_end_ms is not None
            and position_ms >= self._clip_end_ms
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._player.pause()

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._slider.setMaximum(duration_ms)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._update_buttons(state)
        self.playback_state_changed.emit(
            state == QMediaPlayer.PlaybackState.PlayingState
        )

    def _update_buttons(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        stopped = state == QMediaPlayer.PlaybackState.StoppedState
        self._play_btn.setText(_ICON_PAUSE if playing else _ICON_PLAY)
        self._stop_btn.setEnabled(not stopped)
