"""The Review wizard step (PRD §7.2 stage 6, §9.4; ADR-0010).

Real, not a placeholder — the wizard shell's one fully-built step besides
its own chrome. Ports ``docs/design/wizard-shell-wireframe.html``'s
Review design: a summary stat strip, Hits/Render Plan tabs (Hits is the
default/primary tab; Render Plan is the secondary view most Users won't
need), live filters, bulk include/exclude, and a merged-interval preview
built from the real Interval Planner (``interval_planner.build_render_plan``
via ``scan.build_report``), not a reimplementation of its algorithm the
way the wireframe's demo necessarily was in plain JS.

Two deliberate departures from the wireframe, both toward this
codebase's own existing conventions rather than porting it verbatim:

- **One checkable "Included" column**, not two separate Include/Exclude
  buttons per row — mirrors ``CatalogWindow``'s already-proven
  checkable-``QTableWidgetItem`` pattern (ADR-0008) rather than inventing
  a second interaction style for the same the app.
- **Bulk actions read the table's native multi-row selection**
  (``QAbstractItemView.ExtendedSelection``), not a separate per-row
  selection checkbox column — Qt already provides this, so the wireframe's
  extra column was working around a limitation plain HTML has that Qt
  doesn't.

:meth:`set_scan` is now called for real, by the wizard shell, once the
Scan step (ADR-0018) produces a completed ``Scan`` — but this widget was
built and tested well before that existed, so it also has to render
sensibly with no scan at all, which its own tests still exercise
directly rather than only through the now-real end-to-end wizard flow.
:meth:`current_render_plan` (ADR-0019) is the one thing this step adds
purely to unblock its own successor: Render needed a public way to read
this screen's *live* include/exclude decisions as a real ``RenderPlan``,
which nothing outside this widget could reach before.

**Hit preview playback (ADR-0052)** lets a User hear a selected hit's
own audio before deciding to include or exclude it, rather than relying
only on the transcript's text. A single ``AudioPlayerWidget``
(``m4bmaker/gui/player.py`` — the base app's own audio widget, imported
directly rather than duplicated; its ``load``/``load_paused`` interface
takes only a path and millisecond offsets, no ``Book``/``Chapter``
coupling) is docked directly below the Hits table, matching where the
base app's own Chapters tab already docks this exact widget below its
own big table. Two toggleable windows per hit: the *padded* window
(what Render will actually silence) or a fixed ±2s *context* window
(for "is this really the flagged word") — see ``AudioPlayerWidget.
play_clip()`` for the auto-stop-at-a-boundary mechanism this relies on.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_store import save_catalog
from m4bmaker.filter.interval_planner import build_render_plan
from m4bmaker.filter.models import (
    AttenuationSettings,
    RenderPlan,
    ReviewStatus,
    ScanHit,
)
from m4bmaker.filter.scan import Scan, TranscriptWordIndex, build_report
from m4bmaker.filter.transcript import Transcript
from m4bmaker.gui.player import AudioPlayerWidget

from .step_base import WizardStep
from .transcript_view import TranscriptView

_COL_INCLUDED = 0
_COL_TIME = 1
_COL_CATEGORY = 2
_COL_TERM = 3
_COL_CONTEXT = 4
_COL_CONFIDENCE = 5

_ROLE_HIT_ID = Qt.ItemDataRole.UserRole

_FILTER_ALL = "__all__"

#: Preview modes (ADR-0052) — a plain ±2s either side of the hit, the
#: same fixed-seconds choice made over exact transcript word-count
#: boundaries (steady audiobook narration pace already reads as "a few
#: words" at this length; word-count boundaries don't avoid crossing a
#: chapter break any better than fixed seconds do either).
_MODE_PADDED = "padded"
_MODE_CONTEXT = "context"
_CONTEXT_MS = 2000

#: Elide budget for the preview dock's own term label — the same
#: fixed-width elide-with-tooltip convention _InfoPanel's own
#: _ROW_VALUE_ELIDE_WIDTH already established in source_step.py, sized
#: for this row's own compact layout rather than that panel's wider one.
_PREVIEW_LABEL_ELIDE_WIDTH = 170


def _mask_term(term: str) -> str:
    """First and last letter shown, everything between asterisked — e.g.
    "value" -> "v***e". Purely a display transform; whether a term is
    masked at all is ``CatalogService.is_masked()``'s call (ADR-0011)."""
    if len(term) <= 2:
        return term[0] + "*" * (len(term) - 1)
    return term[0] + "*" * (len(term) - 2) + term[-1]


def _ms_to_clock(ms: int) -> str:
    total_seconds = ms / 1000
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:04.1f}"


class ReviewStep(WizardStep):
    #: ADR-0053: lets the Transcript tab's "Go to Scan" banner button jump
    #: back to the Scan step to re-run it — the wizard shell (not this
    #: widget) owns cross-step navigation, so this only asks for it.
    go_to_scan_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_title = "Review"
        self.step_subtitle = (
            "Every hit the scan found, matched against an immutable profile "
            "snapshot. Include or exclude individually, or act on a whole "
            "category or term at once — nothing is rendered until Render."
        )

        self._scan: Scan | None = None
        self._catalog: CatalogService | None = None
        self._transcript: Transcript | None = None
        self._word_index: TranscriptWordIndex | None = None
        self._source_duration_ms = 0
        self._attenuation: AttenuationSettings | None = None
        self._source_path: Path | None = None

        # ── full-transcript review (ADR-0053, Option 1) ─────────────────
        self._transcript_added_count = 0
        #: (start_ms, end_ms) for whichever selection is currently loaded
        #: in the Transcript tab's own preview dock -- single fixed
        #: window, no padded/context toggle (nothing selected here has a
        #: catalog entry yet, so there's no padding to toggle to).
        self._transcript_preview_window: tuple[int, int] | None = None

        self._filter_category = _FILTER_ALL
        self._filter_term = _FILTER_ALL
        self._filter_confidence = _FILTER_ALL
        self._filter_state = _FILTER_ALL
        self._sort_key = "time"
        self._sort_descending = False

        # ── hit preview playback (ADR-0052) ─────────────────────────────
        self._selected_hit: ScanHit | None = None
        self._preview_mode = _MODE_PADDED
        #: {_MODE_PADDED/_MODE_CONTEXT: (start_ms, end_ms)} for whichever
        #: hit is currently selected -- computed once on selection, not
        #: recomputed per mode switch or per Play click.
        self._preview_windows: dict[str, tuple[int, int]] = {}
        self._preview_attenuation: AttenuationSettings | None = None

        self._build_ui()
        self._refresh()

    def hideEvent(self, event: QHideEvent) -> None:
        # Leaving Review (Back/Continue, or the wizard closing) must not
        # leave audio quietly playing behind the scenes -- the wizard
        # shell has no per-step lifecycle hook of its own, but switching
        # QStackedWidget pages already fires real hide events on the
        # outgoing step, so this needs no shell changes.
        self._audio_player.stop()
        self._transcript_audio_player.stop()
        super().hideEvent(event)

    def _effective_attenuation(self) -> AttenuationSettings:
        assert self._scan is not None
        return self._attenuation or self._scan.profile_snapshot.attenuation

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._stat_strip = _StatStrip()
        root.addWidget(self._stat_strip)

        tabs = QTabWidget()
        tabs.addTab(self._build_hits_tab(), "Hits")
        tabs.addTab(self._build_plan_tab(), "Render Plan")
        # Transcript goes last, not between Hits and Render Plan (ADR-0053):
        # tab order reads as priority order, and this is a supplementary
        # check most Contributors won't need, not part of the primary
        # Hits -> Render Plan -> Continue path. A tooltip states that
        # outright before anyone even clicks in; deliberately no "NEW"-
        # style badge, which would invite checking -- the opposite of the
        # goal here.
        transcript_tab_index = tabs.addTab(self._build_transcript_tab(), "Transcript")
        tabs.setTabToolTip(
            transcript_tab_index,
            "Optional — everything the scan didn't flag, in case something "
            "slipped through. Most people won't need this.",
        )
        root.addWidget(tabs, stretch=1)

    def _build_hits_tab(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)

        filter_row = QHBoxLayout()
        self._category_combo = QComboBox()
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        filter_row.addWidget(QLabel("Category:"))
        filter_row.addWidget(self._category_combo)

        self._term_combo = QComboBox()
        self._term_combo.currentIndexChanged.connect(self._on_term_changed)
        filter_row.addWidget(QLabel("Term:"))
        filter_row.addWidget(self._term_combo)

        self._confidence_combo = QComboBox()
        self._confidence_combo.addItem("All", _FILTER_ALL)
        self._confidence_combo.addItem("Below 25%", "below_25")
        self._confidence_combo.addItem("Below 75%", "below_75")
        self._confidence_combo.addItem("Below 90%", "below_90")
        self._confidence_combo.addItem("Not available", "unavailable")
        self._confidence_combo.currentIndexChanged.connect(self._on_confidence_changed)
        filter_row.addWidget(QLabel("Confidence:"))
        filter_row.addWidget(self._confidence_combo)

        self._state_combo = QComboBox()
        self._state_combo.addItem("All", _FILTER_ALL)
        self._state_combo.addItem("Included", ReviewStatus.INCLUDED.value)
        self._state_combo.addItem("Excluded", ReviewStatus.EXCLUDED.value)
        self._state_combo.currentIndexChanged.connect(self._on_state_changed)
        filter_row.addWidget(QLabel("State:"))
        filter_row.addWidget(self._state_combo)

        self._sort_combo = QComboBox()
        self._sort_combo.addItem("Time", "time")
        self._sort_combo.addItem("Category", "category")
        self._sort_combo.addItem("Term", "term")
        self._sort_combo.addItem("Confidence", "confidence")
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        filter_row.addWidget(QLabel("Sort:"))
        filter_row.addWidget(self._sort_combo)

        self._direction_btn = QPushButton("↑")
        self._direction_btn.setFixedWidth(28)
        self._direction_btn.setToolTip("Toggle sort direction")
        self._direction_btn.clicked.connect(self._on_direction_toggled)
        filter_row.addWidget(self._direction_btn)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        bulk_row = QHBoxLayout()
        self._bulk_label = QLabel("")
        bulk_row.addWidget(self._bulk_label)
        self._bulk_include_btn = QPushButton("Include")
        self._bulk_include_btn.clicked.connect(lambda: self._bulk_set(True))
        bulk_row.addWidget(self._bulk_include_btn)
        self._bulk_exclude_btn = QPushButton("Exclude")
        self._bulk_exclude_btn.clicked.connect(lambda: self._bulk_set(False))
        bulk_row.addWidget(self._bulk_exclude_btn)
        bulk_row.addStretch(1)
        layout.addLayout(bulk_row)

        self._table = QTableWidget(0, 6)
        # QTableWidget.wordWrap defaults to True in Qt -- a long enough
        # Context cell (up to ~10 words of before/after context,
        # TranscriptWordIndex's own window) can silently wrap onto 2-3
        # lines and balloon that one row's height, cutting how many
        # rows fit in the same pixel space. Every row must stay exactly
        # one line, same guarantee _InfoPanel's own row values already
        # make in source_step.py -- full text still recoverable via
        # tooltip (set per-row below), same convention.
        self._table.setWordWrap(False)
        self._table.setHorizontalHeaderLabels(
            ["Included", "Time", "Category", "Term", "Context", "Conf."]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(
            _COL_INCLUDED, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(_COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            _COL_CATEGORY, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(_COL_TERM, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_CONTEXT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            _COL_CONFIDENCE, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_bulk_bar)
        self._table.itemSelectionChanged.connect(self._update_preview_selection)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, stretch=1)

        layout.addWidget(self._build_preview_dock())

        return pane

    def _build_preview_dock(self) -> QFrame:
        """The hit preview playback dock (ADR-0052) — a single slim row
        directly below the Hits table, not a third tab: the whole point
        is hearing the audio *while* looking at the Context column and
        the Include checkbox for that same hit, and a separate tab would
        split those across two screens. Mirrors where the base app's own
        Chapters tab already docks this exact widget below its own big
        table (``gui/window.py``), including that tab's own trick of
        inserting extra widgets directly into the player's row layout —
        here, a Play button and mode toggle that drive :meth:`AudioPlayerWidget.
        play_clip`/``pause`` directly rather than that widget's own
        Play/Stop (built with ``show_controls=False``: this dock's ▶/⏸
        always resets to the clip's own start on pause, since these
        clips run a few seconds and resuming mid-clip isn't worth a
        second control the way it is for a whole book)."""
        container = QFrame()
        container.setObjectName("reviewPreviewDock")
        container.setFixedHeight(44)
        root = QHBoxLayout(container)
        root.setContentsMargins(12, 0, 12, 0)

        self._preview_idle_label = QLabel(
            "Select a hit above to preview the audio that will be muted."
        )
        self._preview_idle_label.setObjectName("statusLabel")
        root.addWidget(self._preview_idle_label)

        self._audio_player = AudioPlayerWidget(show_controls=False)
        root.addWidget(self._audio_player, stretch=1)

        self._preview_play_btn = QPushButton("▶")
        self._preview_play_btn.setFixedSize(28, 28)
        self._preview_play_btn.setObjectName("previewPlayBtn")
        self._preview_play_btn.setToolTip("Play / Pause")
        self._preview_play_btn.clicked.connect(self._on_preview_play_clicked)

        self._preview_label = QLabel("")
        self._preview_label.setToolTip("")

        self._mode_padded_btn = QPushButton("Filtered word")
        self._mode_padded_btn.setObjectName("previewModeBtn")
        self._mode_padded_btn.setCheckable(True)
        self._mode_padded_btn.setChecked(True)
        self._mode_context_btn = QPushButton("Word in context")
        self._mode_context_btn.setObjectName("previewModeBtn")
        self._mode_context_btn.setCheckable(True)
        self._mode_group = QButtonGroup(container)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._mode_padded_btn)
        self._mode_group.addButton(self._mode_context_btn)
        self._mode_padded_btn.toggled.connect(self._on_mode_toggled)
        self._mode_context_btn.toggled.connect(self._on_mode_toggled)

        # Clip-relative, not the widget's own whole-file slider/time --
        # a clip is a few seconds out of a multi-hour book, so the
        # widget's own file-absolute progress barely moves at all
        # during playback (caught by the User against the real app).
        # Driven entirely by AudioPlayerWidget.position_changed below.
        self._preview_progress = QProgressBar()
        self._preview_progress.setObjectName("previewProgress")
        self._preview_progress.setRange(0, 1000)
        self._preview_progress.setTextVisible(False)
        self._preview_progress.setFixedHeight(4)

        self._preview_time_label = QLabel("0.0s / 0.0s")
        self._preview_time_label.setObjectName("statusLabel")
        self._preview_time_label.setMinimumWidth(90)
        self._preview_time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        # Same trick the base app's Chapters tab already uses to add its
        # own prev/next-chapter buttons to this widget's row — inserted
        # in reverse order since each insertWidget(0, ...) pushes the
        # previous ones right. show_controls=False means the row starts
        # empty, so this ends up
        # [play][label][mode][mode][progress][time].
        outer_layout = self._audio_player.layout()
        assert outer_layout is not None
        row_item = outer_layout.itemAt(0)
        assert row_item is not None
        player_row = row_item.layout()
        assert isinstance(player_row, QHBoxLayout)
        player_row.insertWidget(0, self._mode_context_btn)
        player_row.insertWidget(0, self._mode_padded_btn)
        player_row.insertWidget(0, self._preview_label)
        player_row.insertWidget(0, self._preview_play_btn)
        player_row.addWidget(self._preview_progress, 1)
        player_row.addWidget(self._preview_time_label)

        self._audio_player.playback_state_changed.connect(
            self._on_audio_playback_state_changed
        )
        self._audio_player.position_changed.connect(self._on_audio_position_changed)

        self._show_preview_idle()
        return container

    def _build_plan_tab(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)

        note = QLabel(
            "This is what actually gets silenced when you render. When "
            "hits are close together or overlap — like “darn” "
            "and “darn it” appearing at the same spot — they're "
            "combined into one silenced section instead of being treated "
            "separately. Most people won't need to check this before "
            "continuing."
        )
        note.setObjectName("statusLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._plan_summary_label = QLabel("")
        layout.addWidget(self._plan_summary_label)

        self._plan_group = QGroupBox("Merged intervals")
        self._plan_layout = QVBoxLayout(self._plan_group)

        # A real scan can produce hundreds of merged intervals — without a
        # scroll area, the group box just grows to fit all of them (well
        # past any real window height) and, depending on how the wizard
        # shell constrains this tab's actual allocated space, the whole
        # list can end up clipped to nothing visible at all rather than
        # merely "cut off after a screenful." Scrolling here is not a
        # nicety, it's what makes this tab render at all past a handful of
        # hits.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._plan_group)
        layout.addWidget(scroll, stretch=1)

        return pane

    def _build_transcript_tab(self) -> QWidget:
        """The full-transcript review tab (ADR-0053, Option 1): every word
        in the current chapter, not just what the scan flagged — hits
        already struck through, everything else there to actually read.
        Selecting any other word or phrase lets a Contributor hear it
        (reusing ADR-0052's own preview mechanism) or add it straight to
        the catalog, closing the gap no automated technique could (real-
        data testing rejected both a bare confidence threshold and
        spelling/phonetic similarity to the catalog, see ADR-0053).

        Signaled as optional, not just described as optional: this tab's
        own tab-bar entry carries a tooltip and sits last in the row (see
        ``_build_ui``), and the note below states outright that most
        people won't need it — the same wording pattern ``_build_plan_tab``
        already uses for the Render Plan tab's own "most people won't need
        to check this" note, not a new convention.
        """
        pane = QWidget()
        layout = QVBoxLayout(pane)

        note = QLabel(
            "Optional — the scan already caught every catalog match on "
            "the Hits tab; most people won't need to look here before "
            "continuing. This shows every word in the chapter, not just "
            "what the scan flagged, in case something slipped through. "
            "Select any other word or phrase to hear it or add it to the "
            "catalog. Adding here updates the catalog only, not this "
            "book's hits — re-scan from the Scan step to apply it."
        )
        note.setObjectName("statusLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._rescan_banner = QFrame()
        self._rescan_banner.setObjectName("rescanBanner")
        self._rescan_banner.setVisible(False)
        banner_layout = QHBoxLayout(self._rescan_banner)
        self._rescan_banner_label = QLabel("")
        self._rescan_banner_label.setWordWrap(True)
        banner_layout.addWidget(self._rescan_banner_label, stretch=1)
        rescan_go_btn = QPushButton("Go to Scan →")
        rescan_go_btn.setObjectName("rescanGoBtn")
        rescan_go_btn.clicked.connect(self.go_to_scan_requested.emit)
        banner_layout.addWidget(rescan_go_btn)
        layout.addWidget(self._rescan_banner)

        nav_row = QHBoxLayout()
        nav_row.addWidget(QLabel("Chapter:"))
        self._chapter_combo = QComboBox()
        self._chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        nav_row.addWidget(self._chapter_combo)
        prev_btn = QPushButton("‹ Prev")
        prev_btn.clicked.connect(lambda: self._step_chapter(-1))
        nav_row.addWidget(prev_btn)
        next_btn = QPushButton("Next ›")
        next_btn.clicked.connect(lambda: self._step_chapter(1))
        nav_row.addWidget(next_btn)

        # Off by default (ADR-0053): this toggle is an explicitly
        # unvalidated skim aid, not a shipped, tested default — Option 4's
        # real-data validation rejected confidence as an automated
        # *filter*, and this is a different, much smaller claim (a visual
        # hint on top of text that's shown either way), but it's never
        # actually been tested either.
        self._lowconf_checkbox = QCheckBox("Highlight uncertain words")
        self._lowconf_checkbox.setChecked(False)
        self._lowconf_checkbox.toggled.connect(self._on_lowconf_toggled)
        nav_row.addWidget(self._lowconf_checkbox)

        nav_row.addStretch(1)
        jump_btn = QPushButton("Jump to next hit ↓")
        jump_btn.clicked.connect(self._on_jump_to_next_hit)
        nav_row.addWidget(jump_btn)
        layout.addLayout(nav_row)

        self._transcript_view = TranscriptView()
        self._transcript_view.selection_changed.connect(
            self._on_transcript_selection_changed
        )
        self._transcript_view.play_requested.connect(self._on_transcript_play_clicked)
        self._transcript_view.add_requested.connect(self._on_transcript_add_clicked)
        layout.addWidget(self._transcript_view, stretch=1)

        layout.addWidget(self._build_transcript_action_row())

        return pane

    def _build_transcript_action_row(self) -> QFrame:
        """A second, independent preview dock for the Transcript tab —
        same placement/enable-disable pattern as ADR-0052's own dock on
        the Hits tab, but its own ``AudioPlayerWidget`` instance: the two
        tabs can't share one widget, since only one can actually be
        embedded in a visible layout at a time."""
        container = QFrame()
        container.setObjectName("reviewPreviewDock")
        container.setFixedHeight(44)
        root = QHBoxLayout(container)
        root.setContentsMargins(12, 0, 12, 0)

        self._transcript_idle_label = QLabel(
            "Click or drag a word above to hear it or add it to the catalog."
        )
        self._transcript_idle_label.setObjectName("statusLabel")
        root.addWidget(self._transcript_idle_label)

        self._transcript_audio_player = AudioPlayerWidget(show_controls=False)
        root.addWidget(self._transcript_audio_player, stretch=1)

        self._transcript_play_btn = QPushButton("▶")
        self._transcript_play_btn.setFixedSize(28, 28)
        self._transcript_play_btn.setObjectName("previewPlayBtn")
        self._transcript_play_btn.setToolTip("Play / Pause")
        self._transcript_play_btn.clicked.connect(self._on_transcript_play_clicked)

        self._transcript_sel_label = QLabel("")

        self._transcript_progress = QProgressBar()
        self._transcript_progress.setObjectName("previewProgress")
        self._transcript_progress.setRange(0, 1000)
        self._transcript_progress.setTextVisible(False)
        self._transcript_progress.setFixedHeight(4)

        self._transcript_time_label = QLabel("0.0s / 0.0s")
        self._transcript_time_label.setObjectName("statusLabel")
        self._transcript_time_label.setMinimumWidth(90)
        self._transcript_time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._transcript_add_btn = QPushButton("＋ Add to Catalog")
        self._transcript_add_btn.clicked.connect(self._on_transcript_add_clicked)

        outer_layout = self._transcript_audio_player.layout()
        assert outer_layout is not None
        row_item = outer_layout.itemAt(0)
        assert row_item is not None
        player_row = row_item.layout()
        assert isinstance(player_row, QHBoxLayout)
        player_row.insertWidget(0, self._transcript_sel_label)
        player_row.insertWidget(0, self._transcript_play_btn)
        player_row.addWidget(self._transcript_progress, 1)
        player_row.addWidget(self._transcript_time_label)
        player_row.addWidget(self._transcript_add_btn)

        self._transcript_audio_player.playback_state_changed.connect(
            self._on_transcript_playback_state_changed
        )
        self._transcript_audio_player.position_changed.connect(
            self._on_transcript_position_changed
        )

        self._show_transcript_idle()
        return container

    # ── data entry point ─────────────────────────────────────────────────

    def set_scan(
        self,
        scan: Scan,
        catalog: CatalogService,
        transcript: Transcript,
        source_duration_ms: int,
        source_path: Path,
        attenuation: AttenuationSettings | None = None,
    ) -> None:
        """Load a completed :class:`~m4bmaker.filter.scan.Scan` for review.
        The caller (the Scan step, ADR-0018) owns producing the scan;
        this step only displays and records decisions against it.

        *source_path* (ADR-0052) is the real source ``.m4b`` — the same
        one ``SourceStep.manifest.source_path`` already holds and Render
        already reads — needed here only for hit preview playback."""
        self._scan = scan
        self._catalog = catalog
        self._transcript = transcript
        self._word_index = TranscriptWordIndex(transcript)
        self._source_duration_ms = source_duration_ms
        self._source_path = source_path
        self._attenuation = attenuation
        self._filter_category = _FILTER_ALL
        self._filter_term = _FILTER_ALL
        self._filter_confidence = _FILTER_ALL
        self._filter_state = _FILTER_ALL
        self._selected_hit = None
        self._audio_player.stop()
        self._show_preview_idle()
        self._transcript_added_count = 0
        self._rescan_banner.setVisible(False)
        self._lowconf_checkbox.setChecked(False)
        self._transcript_audio_player.stop()
        self._show_transcript_idle()
        self._refresh_chapter_options()
        self._refresh()

    def current_render_plan(self) -> RenderPlan | None:
        """The real :class:`~m4bmaker.filter.models.RenderPlan` reflecting
        this screen's *current* include/exclude decisions — recomputed
        live from ``self._scan`` on every call, never cached, so it is
        always exactly what a Render step would need to act on right now
        (the same computation :meth:`_refresh_plan_tab` already does for
        its own on-screen preview). ``None`` before a scan is loaded —
        the first real predecessor Render is wired to (ADR-0019)."""
        if self._scan is None:
            return None
        return build_render_plan(
            self._scan.included_hits(),
            self._source_duration_ms,
            self._effective_attenuation(),
        )

    # ── derived data ─────────────────────────────────────────────────────

    def _category_name(self, category_id: str) -> str:
        if self._catalog is None:
            return category_id
        try:
            return self._catalog.get_category(category_id).name
        except KeyError:
            return category_id

    def _term_text(self, hit: ScanHit) -> str:
        return " ".join(hit.raw_tokens) or hit.entry_id

    def _display_term(self, hit: ScanHit) -> str:
        text = self._term_text(hit)
        if self._catalog is not None and self._catalog.is_masked(hit.entry_id):
            return _mask_term(text)
        return text

    def _filtered_sorted_hits(self) -> list[ScanHit]:
        if self._scan is None:
            return []
        hits = list(self._scan.hits)

        if self._filter_category != _FILTER_ALL:
            hits = [h for h in hits if h.category_id == self._filter_category]
        if self._filter_term != _FILTER_ALL:
            hits = [h for h in hits if h.entry_id == self._filter_term]
        if self._filter_confidence == "below_25":
            hits = [h for h in hits if h.confidence is not None and h.confidence < 0.25]
        elif self._filter_confidence == "below_75":
            hits = [h for h in hits if h.confidence is not None and h.confidence < 0.75]
        elif self._filter_confidence == "below_90":
            hits = [h for h in hits if h.confidence is not None and h.confidence < 0.90]
        elif self._filter_confidence == "unavailable":
            hits = [h for h in hits if h.confidence is None]
        if self._filter_state != _FILTER_ALL:
            hits = [
                h
                for h in hits
                if self._scan.status_for(h.id).value == self._filter_state
            ]

        def sort_key(h: ScanHit) -> str | float:
            if self._sort_key == "category":
                return self._category_name(h.category_id)
            if self._sort_key == "term":
                return self._term_text(h)
            if self._sort_key == "confidence":
                return h.confidence if h.confidence is not None else -1.0
            return h.start_ms

        hits.sort(key=sort_key, reverse=self._sort_descending)
        return hits

    # ── refresh ──────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._refresh_filter_options()
        self._refresh_stats_and_table()
        self._refresh_plan_tab()

    def _refresh_filter_options(self) -> None:
        self._category_combo.blockSignals(True)
        self._term_combo.blockSignals(True)
        self._category_combo.clear()
        self._term_combo.clear()
        self._category_combo.addItem("All", _FILTER_ALL)
        self._term_combo.addItem("All", _FILTER_ALL)

        if self._scan is not None:
            seen_categories: dict[str, str] = {}
            seen_terms: dict[str, str] = {}
            for hit in self._scan.hits:
                seen_categories.setdefault(
                    hit.category_id, self._category_name(hit.category_id)
                )
                seen_terms.setdefault(hit.entry_id, self._display_term(hit))
            for category_id, name in sorted(
                seen_categories.items(), key=lambda kv: kv[1]
            ):
                self._category_combo.addItem(name, category_id)
            for entry_id, label in sorted(seen_terms.items(), key=lambda kv: kv[1]):
                self._term_combo.addItem(label, entry_id)

        cat_index = self._category_combo.findData(self._filter_category)
        self._category_combo.setCurrentIndex(max(cat_index, 0))
        term_index = self._term_combo.findData(self._filter_term)
        self._term_combo.setCurrentIndex(max(term_index, 0))
        self._category_combo.blockSignals(False)
        self._term_combo.blockSignals(False)

    def _refresh_stats_and_table(self) -> None:
        if self._scan is not None:
            report = build_report(
                self._scan, self._source_duration_ms, self._attenuation
            )
            self._stat_strip.set_values(
                total=report.total_raw_hits,
                included=report.included_hits,
                excluded=report.excluded_hits,
                unique_terms=report.unique_terms_hit,
                attenuated_ms=report.total_planned_attenuated_duration_ms,
            )
        else:
            self._stat_strip.set_values(0, 0, 0, 0, 0)

        rows = self._filtered_sorted_hits()
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for hit in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)

            included_item = QTableWidgetItem()
            included_item.setFlags(
                (included_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            status = (
                self._scan.status_for(hit.id)
                if self._scan is not None
                else ReviewStatus.INCLUDED
            )
            included_item.setCheckState(
                Qt.CheckState.Checked
                if status == ReviewStatus.INCLUDED
                else Qt.CheckState.Unchecked
            )
            included_item.setData(_ROLE_HIT_ID, hit.id)
            self._table.setItem(row, _COL_INCLUDED, included_item)

            time_item = QTableWidgetItem(_ms_to_clock(hit.start_ms))
            time_item.setFlags(time_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, _COL_TIME, time_item)

            cat_item = QTableWidgetItem(self._category_name(hit.category_id))
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, _COL_CATEGORY, cat_item)

            term_item = QTableWidgetItem(self._display_term(hit))
            term_item.setFlags(term_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, _COL_TERM, term_item)

            context_text = self._context_text(hit)
            context_item = QTableWidgetItem(context_text)
            context_item.setFlags(context_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            context_item.setToolTip(context_text)
            self._table.setItem(row, _COL_CONTEXT, context_item)

            conf_text = (
                f"{round(hit.confidence * 100)}%"
                if hit.confidence is not None
                else "n/a"
            )
            conf_item = QTableWidgetItem(conf_text)
            conf_item.setFlags(conf_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, _COL_CONFIDENCE, conf_item)
        self._table.blockSignals(False)
        self._update_bulk_bar()

    def _context_text(self, hit: ScanHit) -> str:
        if self._word_index is None:
            return ""
        before, after = self._word_index.context(hit)
        term = self._display_term(hit)
        return f"{' '.join(before)} [{term}] {' '.join(after)}".strip()

    def _refresh_plan_tab(self) -> None:
        while self._plan_layout.count():
            child = self._plan_layout.takeAt(0)
            if child is None:
                continue
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

        if self._scan is None:
            self._plan_summary_label.setText("")
            self._plan_layout.addWidget(QLabel("No scan loaded yet."))
            return

        plan = build_render_plan(
            self._scan.included_hits(),
            self._source_duration_ms,
            self._effective_attenuation(),
        )
        total_ms = sum(iv.end_ms - iv.start_ms for iv in plan.intervals)
        self._plan_summary_label.setText(
            f"{len(plan.intervals)} interval"
            f"{'' if len(plan.intervals) == 1 else 's'} · "
            f"{_format_duration(total_ms)} total"
        )

        if not plan.intervals:
            self._plan_layout.addWidget(
                QLabel("Nothing included — the render plan is empty.")
            )
            return

        hits_by_id = {h.id: h for h in self._scan.hits}
        for interval in plan.intervals:
            terms = [
                self._display_term(hits_by_id[hid])
                for hid in interval.hit_ids
                if hid in hits_by_id
            ]
            text = (
                f"{_ms_to_clock(interval.start_ms)}–{_ms_to_clock(interval.end_ms)}  "
                f"({_format_duration(interval.end_ms - interval.start_ms)})  "
                f"merged from {len(interval.hit_ids)} "
                f"hit{'' if len(interval.hit_ids) == 1 else 's'}: {', '.join(terms)}"
            )
            self._plan_layout.addWidget(QLabel(text))

    # ── filter/sort handlers ────────────────────────────────────────────

    def _on_category_changed(self) -> None:
        self._filter_category = self._category_combo.currentData()
        self._refresh_stats_and_table()

    def _on_term_changed(self) -> None:
        self._filter_term = self._term_combo.currentData()
        self._refresh_stats_and_table()

    def _on_confidence_changed(self) -> None:
        self._filter_confidence = self._confidence_combo.currentData()
        self._refresh_stats_and_table()

    def _on_state_changed(self) -> None:
        self._filter_state = self._state_combo.currentData()
        self._refresh_stats_and_table()

    def _on_sort_changed(self) -> None:
        self._sort_key = self._sort_combo.currentData()
        self._refresh_stats_and_table()

    def _on_direction_toggled(self) -> None:
        self._sort_descending = not self._sort_descending
        self._direction_btn.setText("↓" if self._sort_descending else "↑")
        self._refresh_stats_and_table()

    # ── decisions ────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != _COL_INCLUDED or self._scan is None:
            return
        hit_id = item.data(_ROLE_HIT_ID)
        if hit_id is None:
            return
        status = (
            ReviewStatus.INCLUDED
            if item.checkState() == Qt.CheckState.Checked
            else ReviewStatus.EXCLUDED
        )
        self._scan.decide(hit_id, status)
        self._refresh_stats_and_table()
        self._refresh_plan_tab()

    def _update_bulk_bar(self) -> None:
        count = len(self._table.selectionModel().selectedRows())
        self._bulk_label.setText(f"{count} selected" if count else "")
        self._bulk_include_btn.setEnabled(count > 0)
        self._bulk_exclude_btn.setEnabled(count > 0)

    def _bulk_set(self, included: bool) -> None:
        if self._scan is None:
            return
        status = ReviewStatus.INCLUDED if included else ReviewStatus.EXCLUDED
        rows = self._table.selectionModel().selectedRows()
        for index in rows:
            item = self._table.item(index.row(), _COL_INCLUDED)
            if item is None:
                continue
            hit_id = item.data(_ROLE_HIT_ID)
            if hit_id is not None:
                self._scan.decide(hit_id, status)
        self._table.clearSelection()
        self._refresh_stats_and_table()
        self._refresh_plan_tab()

    # ── hit preview playback (ADR-0052) ─────────────────────────────────

    def _update_preview_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if len(rows) != 1 or self._scan is None or self._source_path is None:
            self._selected_hit = None
            self._audio_player.stop()
            self._show_preview_idle()
            return

        item = self._table.item(rows[0].row(), _COL_INCLUDED)
        hit_id = item.data(_ROLE_HIT_ID) if item is not None else None
        hit = next((h for h in self._scan.hits if h.id == hit_id), None)
        if hit is None:
            self._selected_hit = None
            self._audio_player.stop()
            self._show_preview_idle()
            return

        self._selected_hit = hit
        self._load_preview_windows()
        self._show_preview_loaded()

    def _load_preview_windows(self) -> None:
        """Compute both preview windows for the newly-selected hit and
        load-paused at the (now default) padded window's start —
        mirrors the base app's own chapter-preview click behavior
        exactly (seek position, don't auto-play) rather than a new
        pattern for this screen."""
        hit = self._selected_hit
        assert hit is not None
        assert self._source_path is not None
        attenuation = self._effective_attenuation()
        self._preview_attenuation = attenuation
        self._preview_windows = {
            _MODE_PADDED: (
                max(0, hit.start_ms - attenuation.lead_padding_ms),
                min(self._source_duration_ms, hit.end_ms + attenuation.tail_padding_ms),
            ),
            _MODE_CONTEXT: (
                max(0, hit.start_ms - _CONTEXT_MS),
                min(self._source_duration_ms, hit.end_ms + _CONTEXT_MS),
            ),
        }
        self._preview_mode = _MODE_PADDED
        self._mode_padded_btn.setChecked(True)
        self._render_preview_label()
        start_ms, _ = self._preview_windows[self._preview_mode]
        self._audio_player.load_paused(self._source_path, start_ms)

    def _render_preview_label(self) -> None:
        hit = self._selected_hit
        attenuation = self._preview_attenuation
        assert hit is not None
        assert attenuation is not None
        start_ms, end_ms = self._preview_windows[self._preview_mode]

        full_text = f'Previewing "{self._display_term(hit)}"'
        metrics = self._preview_label.fontMetrics()
        self._preview_label.setText(
            metrics.elidedText(
                full_text, Qt.TextElideMode.ElideRight, _PREVIEW_LABEL_ELIDE_WIDTH
            )
        )
        if self._preview_mode == _MODE_CONTEXT:
            detail = (
                f"{_ms_to_clock(start_ms)}–{_ms_to_clock(end_ms)} "
                f"({_CONTEXT_MS // 1000}s before / after)"
            )
        else:
            detail = (
                f"{_ms_to_clock(start_ms)}–{_ms_to_clock(end_ms)} (incl. "
                f"{attenuation.lead_padding_ms / 1000:.1f}s lead-in / "
                f"{attenuation.tail_padding_ms / 1000:.1f}s tail)"
            )
        self._preview_label.setToolTip(f"{full_text} — {detail}")

        # Immediate reset, not waiting on the next position_changed
        # signal to arrive (load_paused() defers the seek by up to
        # AudioPlayerWidget._SEEK_DELAY_MS on a brand new source) --
        # otherwise the bar/time could flash the *previous* clip's
        # last-known progress for a moment after switching hits or mode.
        clip_len_ms = max(1, end_ms - start_ms)
        self._preview_progress.setValue(0)
        self._preview_time_label.setText(f"0.0s / {clip_len_ms / 1000:.1f}s")

    def _on_audio_position_changed(self, position_ms: int) -> None:
        if self._selected_hit is None:
            return
        start_ms, end_ms = self._preview_windows[self._preview_mode]
        clip_len_ms = max(1, end_ms - start_ms)
        elapsed_ms = max(0, min(clip_len_ms, position_ms - start_ms))
        self._preview_progress.setValue(round(elapsed_ms / clip_len_ms * 1000))
        self._preview_time_label.setText(
            f"{elapsed_ms / 1000:.1f}s / {clip_len_ms / 1000:.1f}s"
        )

    def _on_mode_toggled(self, checked: bool) -> None:
        if not checked or self._selected_hit is None or self._source_path is None:
            return
        mode = (
            _MODE_CONTEXT if self.sender() is self._mode_context_btn else _MODE_PADDED
        )
        if mode == self._preview_mode:
            return
        self._preview_mode = mode
        self._audio_player.pause()
        self._render_preview_label()
        start_ms, _ = self._preview_windows[mode]
        self._audio_player.load_paused(self._source_path, start_ms)

    def _on_preview_play_clicked(self) -> None:
        if self._selected_hit is None or self._source_path is None:
            return
        start_ms, end_ms = self._preview_windows[self._preview_mode]
        if self._audio_player.is_playing:
            # No separate Stop control (ADR-0052): these clips run a few
            # seconds, so resuming mid-clip isn't worth a second button
            # the way it is for a whole book -- ▶/⏸ mid-playback always
            # halts *and* resets to this clip's own start, same as
            # letting it finish naturally already does.
            self._audio_player.pause()
            self._audio_player.load_paused(self._source_path, start_ms)
        else:
            self._audio_player.play_clip(self._source_path, start_ms, end_ms)

    def _on_audio_playback_state_changed(self, playing: bool) -> None:
        # Single source of truth for this button's icon -- also fires
        # for play_clip()'s own automatic pause at the clip's end, not
        # just for clicks on this button itself.
        self._preview_play_btn.setText("⏸" if playing else "▶")

    def _show_preview_idle(self) -> None:
        self._preview_idle_label.setVisible(True)
        self._audio_player.setVisible(False)

    def _show_preview_loaded(self) -> None:
        self._preview_idle_label.setVisible(False)
        self._audio_player.setVisible(True)

    # ── full-transcript review (ADR-0053, Option 1) ─────────────────────

    def _refresh_chapter_options(self) -> None:
        self._chapter_combo.blockSignals(True)
        self._chapter_combo.clear()
        if self._transcript is not None:
            for i, segment in enumerate(self._transcript.segments):
                label = (
                    f"{i + 1} — {_ms_to_clock(segment.start_ms)}"
                    f"–{_ms_to_clock(segment.end_ms)}"
                )
                self._chapter_combo.addItem(label)
        self._chapter_combo.blockSignals(False)
        self._load_chapter(0)

    def _step_chapter(self, delta: int) -> None:
        count = self._chapter_combo.count()
        if count == 0:
            return
        new_index = max(0, min(count - 1, self._chapter_combo.currentIndex() + delta))
        self._chapter_combo.setCurrentIndex(new_index)

    def _on_chapter_changed(self) -> None:
        self._load_chapter(self._chapter_combo.currentIndex())

    def _load_chapter(self, index: int) -> None:
        if (
            self._transcript is None
            or self._scan is None
            or not (0 <= index < len(self._transcript.segments))
        ):
            self._transcript_view.load_words([], [])
            return
        segment = self._transcript.segments[index]
        self._transcript_view.load_words(list(segment.words), list(self._scan.hits))
        self._transcript_view.set_low_confidence_hint(
            self._lowconf_checkbox.isChecked()
        )

    def _on_lowconf_toggled(self, checked: bool) -> None:
        self._transcript_view.set_low_confidence_hint(checked)

    def _on_jump_to_next_hit(self) -> None:
        self._transcript_view.jump_to_next_hit()

    def _on_transcript_selection_changed(self) -> None:
        words = self._transcript_view.selected_words()
        if not words:
            self._transcript_preview_window = None
            self._transcript_audio_player.stop()
            self._show_transcript_idle()
            return

        start_ms = max(0, min(w.start_ms for w in words) - _CONTEXT_MS)
        end_ms = min(
            self._source_duration_ms, max(w.end_ms for w in words) + _CONTEXT_MS
        )
        self._transcript_preview_window = (start_ms, end_ms)

        full_text = f'Selected: "{self._transcript_view.selection_text()}"'
        metrics = self._transcript_sel_label.fontMetrics()
        self._transcript_sel_label.setText(
            metrics.elidedText(
                full_text, Qt.TextElideMode.ElideRight, _PREVIEW_LABEL_ELIDE_WIDTH
            )
        )
        self._transcript_sel_label.setToolTip(full_text)

        clip_len_ms = max(1, end_ms - start_ms)
        self._transcript_progress.setValue(0)
        self._transcript_time_label.setText(f"0.0s / {clip_len_ms / 1000:.1f}s")
        self._show_transcript_loaded()
        if self._source_path is not None:
            self._transcript_audio_player.load_paused(self._source_path, start_ms)

    def _on_transcript_play_clicked(self) -> None:
        if self._transcript_preview_window is None or self._source_path is None:
            return
        start_ms, end_ms = self._transcript_preview_window
        if self._transcript_audio_player.is_playing:
            # No separate Stop control, same reasoning as ADR-0052's Hits
            # tab dock: these clips run a few seconds, so ▶/⏸ mid-playback
            # halts and resets to the clip's own start rather than
            # exposing a true pause/resume.
            self._transcript_audio_player.pause()
            self._transcript_audio_player.load_paused(self._source_path, start_ms)
        else:
            self._transcript_audio_player.play_clip(self._source_path, start_ms, end_ms)

    def _on_transcript_playback_state_changed(self, playing: bool) -> None:
        self._transcript_play_btn.setText("⏸" if playing else "▶")

    def _on_transcript_position_changed(self, position_ms: int) -> None:
        if self._transcript_preview_window is None:
            return
        start_ms, end_ms = self._transcript_preview_window
        clip_len_ms = max(1, end_ms - start_ms)
        elapsed_ms = max(0, min(clip_len_ms, position_ms - start_ms))
        self._transcript_progress.setValue(round(elapsed_ms / clip_len_ms * 1000))
        self._transcript_time_label.setText(
            f"{elapsed_ms / 1000:.1f}s / {clip_len_ms / 1000:.1f}s"
        )

    def _on_transcript_add_clicked(self) -> None:
        words = self._transcript_view.selected_words()
        if not words or self._catalog is None:
            return
        phrase = self._transcript_view.selection_text()
        dialog = _AddToCatalogDialog(self._catalog, phrase, self)
        if self._run_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        self._transcript_view.mark_pending(words)
        self._transcript_audio_player.stop()
        self._show_transcript_idle()
        self._transcript_added_count += 1
        self._update_rescan_banner()

    def _run_dialog(self, dialog: QDialog) -> int:
        """Trivial wrapper around ``dialog.exec()`` — kept as a plain
        method on this class, not a bare call to the Qt-wrapped
        ``QDialog.exec()`` itself, so tests can patch it reliably (the
        same reasoning as ``TranscriptView._exec_context_menu``: patching
        a C++-bound method directly via ``unittest.mock.patch.object``
        does not reliably take effect and can hang a headless test on the
        real modal call instead)."""
        return dialog.exec()

    def _update_rescan_banner(self) -> None:
        count = self._transcript_added_count
        noun = "new catalog entry" if count == 1 else "new catalog entries"
        pronoun = "it" if count == 1 else "them"
        self._rescan_banner_label.setText(
            f"{count} {noun} added this session — re-scan to apply "
            f"{pronoun} to this book's hits."
        )
        self._rescan_banner.setVisible(count > 0)

    def _show_transcript_idle(self) -> None:
        self._transcript_idle_label.setVisible(True)
        self._transcript_audio_player.setVisible(False)
        self._transcript_add_btn.setEnabled(False)

    def _show_transcript_loaded(self) -> None:
        self._transcript_idle_label.setVisible(False)
        self._transcript_audio_player.setVisible(True)
        self._transcript_add_btn.setEnabled(True)


class _AddToCatalogDialog(QDialog):
    """Add arbitrary selected transcript text to the catalog, picking a
    category (ADR-0053) — the manual, arbitrary-text sibling of
    ``WordVariationDialog``'s one-click add, which only ever adds a
    *suggested* variation that already inherits its category from the
    catalog word it resembles. There's no such inherited category here
    (the Contributor is selecting free text from the transcript, not
    accepting a suggestion), so unlike that dialog, this one needs a real
    category picker — mirrors ``CatalogWindow``'s own manual "+ Word" flow
    (create, duplicate-check, save) instead.
    """

    def __init__(
        self, catalog: CatalogService, phrase: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._phrase = phrase
        self.setWindowTitle("Add to Catalog")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Add "{phrase}" to the catalog:'))

        layout.addWidget(QLabel("Category"))
        self._category_combo = QComboBox()
        for category in catalog.list_categories():
            self._category_combo.addItem(category.name, category.id)
        layout.addWidget(self._category_combo)

        self._status_label = QLabel("")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        add_btn = QPushButton("Add")
        add_btn.setDefault(True)
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        layout.addLayout(btn_row)

    def _on_add(self) -> None:
        category_id = self._category_combo.currentData()
        duplicate = self._catalog.find_duplicate_entry(category_id, self._phrase)
        if duplicate is not None:
            self._status_label.setText(
                f'"{self._phrase}" is already in this category, as '
                f'"{duplicate.canonical_phrase}" — not added again.'
            )
            return
        self._catalog.create_entry(category_id, self._phrase)
        save_catalog(self._catalog)
        self.accept()


def _format_duration(ms: int) -> str:
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.2f}s"


class _StatStrip(QWidget):
    """The summary numbers row (PRD §9.4): total/included/excluded/unique
    terms/attenuated duration — matches ``ScanReport``'s fields exactly."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        self._total = self._make_stat(layout, "Total hits")
        self._included = self._make_stat(layout, "Included")
        self._excluded = self._make_stat(layout, "Excluded")
        self._unique_terms = self._make_stat(layout, "Unique terms hit")
        self._attenuated = self._make_stat(
            layout,
            "Attenuated total",
            tooltip=(
                "How much audio will actually go quiet. If two flagged "
                "hits overlap, that time is only counted once."
            ),
        )
        layout.addStretch(1)

    def _make_stat(
        self, layout: QHBoxLayout, label: str, tooltip: str | None = None
    ) -> QLabel:
        box = QVBoxLayout()
        caption = QLabel(label)
        caption.setObjectName("statusLabel")
        value = QLabel("0")
        value.setStyleSheet("font-size: 15px; font-weight: 600;")
        if tooltip is not None:
            caption.setToolTip(tooltip)
            value.setToolTip(tooltip)
        box.addWidget(caption)
        box.addWidget(value)
        wrapper = QWidget()
        wrapper.setLayout(box)
        layout.addWidget(wrapper)
        return value

    def set_values(
        self,
        total: int,
        included: int,
        excluded: int,
        unique_terms: int,
        attenuated_ms: int,
    ) -> None:
        self._total.setText(str(total))
        self._included.setText(str(included))
        self._excluded.setText(str(excluded))
        self._unique_terms.setText(str(unique_terms))
        self._attenuated.setText(_format_duration(attenuated_ms))
