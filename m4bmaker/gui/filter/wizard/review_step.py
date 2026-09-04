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
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.interval_planner import build_render_plan
from m4bmaker.filter.models import (
    AttenuationSettings,
    RenderPlan,
    ReviewStatus,
    ScanHit,
)
from m4bmaker.filter.scan import Scan, TranscriptWordIndex, build_report
from m4bmaker.filter.transcript import Transcript

from .step_base import WizardStep

_COL_INCLUDED = 0
_COL_TIME = 1
_COL_CATEGORY = 2
_COL_TERM = 3
_COL_CONTEXT = 4
_COL_CONFIDENCE = 5

_ROLE_HIT_ID = Qt.ItemDataRole.UserRole

_FILTER_ALL = "__all__"


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
        self._word_index: TranscriptWordIndex | None = None
        self._source_duration_ms = 0
        self._attenuation: AttenuationSettings | None = None

        self._filter_category = _FILTER_ALL
        self._filter_term = _FILTER_ALL
        self._filter_confidence = _FILTER_ALL
        self._filter_state = _FILTER_ALL
        self._sort_key = "time"
        self._sort_descending = False

        self._build_ui()
        self._refresh()

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._stat_strip = _StatStrip()
        root.addWidget(self._stat_strip)

        tabs = QTabWidget()
        tabs.addTab(self._build_hits_tab(), "Hits")
        tabs.addTab(self._build_plan_tab(), "Render Plan")
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
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, stretch=1)

        return pane

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

    # ── data entry point ─────────────────────────────────────────────────

    def set_scan(
        self,
        scan: Scan,
        catalog: CatalogService,
        transcript: Transcript,
        source_duration_ms: int,
        attenuation: AttenuationSettings | None = None,
    ) -> None:
        """Load a completed :class:`~m4bmaker.filter.scan.Scan` for review.
        The caller (the Scan step, ADR-0018) owns producing the scan;
        this step only displays and records decisions against it."""
        self._scan = scan
        self._catalog = catalog
        self._word_index = TranscriptWordIndex(transcript)
        self._source_duration_ms = source_duration_ms
        self._attenuation = attenuation
        self._filter_category = _FILTER_ALL
        self._filter_term = _FILTER_ALL
        self._filter_confidence = _FILTER_ALL
        self._filter_state = _FILTER_ALL
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
        effective_attenuation = (
            self._attenuation or self._scan.profile_snapshot.attenuation
        )
        return build_render_plan(
            self._scan.included_hits(), self._source_duration_ms, effective_attenuation
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

            context_item = QTableWidgetItem(self._context_text(hit))
            context_item.setFlags(context_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
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

        effective_attenuation = (
            self._attenuation or self._scan.profile_snapshot.attenuation
        )
        plan = build_render_plan(
            self._scan.included_hits(), self._source_duration_ms, effective_attenuation
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
