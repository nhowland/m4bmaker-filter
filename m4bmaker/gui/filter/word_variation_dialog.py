"""Word Variation Scanner dialog (ADR-0036).

Reviews catalog-entry variations found in a real transcript (stemmed
forms, whisper.cpp tokenization splits) that the exact-match Matcher
wouldn't catch on its own, and lets the User add any of them to the
catalog with one click — see ``filter/variation_scan.py`` for the
detection logic and the "curation aid, not a Matcher change" framing.

Opened fresh from ``ProfileStep`` each time, unlike ``CatalogWindow``/
``ModelManagerWindow``'s lazy-create-and-reuse pattern — there is
nothing to keep alive between opens; each scan is tied to a specific
transcript + profile snapshot taken when it's opened.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_store import save_catalog
from m4bmaker.filter.models import FilterProfileSnapshot
from m4bmaker.filter.transcript import Transcript
from m4bmaker.filter.variation_scan import (
    KIND_SPLIT_TOKEN,
    KIND_STEM_MATCH,
    WordVariationSuggestion,
    find_word_variations,
)

_COL_WORD = 0
_COL_RELATED = 1
_COL_WHY = 2
_COL_SEEN = 3
_COL_CONTEXT = 4
_COL_ACTION = 5

#: Short, jargon-free labels for the "Why" column — the fuller
#: explanation lives in each label's tooltip (below), not the label
#: itself, so the table stays scannable at a glance.
_KIND_LABELS = {
    KIND_STEM_MATCH: "Similar word",
    KIND_SPLIT_TOKEN: "Split by transcription",
}
_KIND_TOOLTIPS = {
    KIND_STEM_MATCH: (
        "A different form of a word on your list — like an added "
        "-ing, -ed, or -er ending (e.g. “shuck” → “shucking”)."
    ),
    KIND_SPLIT_TOKEN: (
        "Whisper sometimes breaks one word into several pieces while "
        "transcribing. This looks like a word on your list split apart "
        "(e.g. “shuck” → “sh uck”)."
    ),
}


class WordVariationDialog(QDialog):
    """Modal dialog: scan a transcript for likely catalog-entry
    variations and let the User add any of them, one click each."""

    def __init__(
        self,
        service: CatalogService,
        transcript: Transcript,
        snapshot: FilterProfileSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._transcript = transcript
        self._snapshot = snapshot

        self.setWindowTitle("Find More Words")
        self.setMinimumSize(760, 420)
        self.resize(860, 480)

        self._build_ui()
        self._run_scan()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        explainer_label = QLabel(
            "Looks for words in your transcript that resemble ones "
            "already on your list — different endings, or a word split "
            "apart by the transcription — so you can review them and "
            "add any you want with one click.\n"
            f"Adding a word here adds it to your Word List, not "
            f"automatically to “{self._snapshot.name}” — to include it in "
            f"this profile's filtering, add it there too from Edit "
            f"Profile."
        )
        explainer_label.setObjectName("statusLabel")
        explainer_label.setWordWrap(True)
        root.addWidget(explainer_label)

        self._status_label = QLabel("Scanning the transcript…")
        root.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setObjectName("jobProgress")
        self._progress.setRange(0, 0)
        root.addWidget(self._progress)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Suggested Word", "Related To", "Why", "Seen", "Context", ""]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_CONTEXT, QHeaderView.ResizeMode.Stretch
        )
        # Neither a one-time resizeColumnsToContents() call nor a
        # persistent ResizeToContents mode measured this column's real
        # cell widgets correctly in practice (both clipped "+ Add" to
        # "Ad") — QTableWidget's column-content sizing isn't reliable
        # for embedded widgets, so this is sized explicitly instead.
        #
        # QFontMetrics(self._table.font()) was also wrong: the app's
        # stylesheet sets QPushButton's own font/padding independently
        # of the table's font, so that measured the wrong font entirely
        # and produced a column barely as wide as the button's bare
        # sizeHint — a real button/label's own sizeHint *does* correctly
        # reflect the applied app-wide stylesheet (QApplication-level
        # styling applies before a widget is even parented), so building
        # one of each here and asking Qt directly is the reliable way to
        # size this, with a real margin on top rather than a razor edge.
        sample_button = QPushButton("+ Add")
        sample_added_label = QLabel("✓ Added")
        sample_existing_label = QLabel("Already in catalog")
        self._action_column_width = (
            max(
                sample_button.sizeHint().width(),
                sample_added_label.sizeHint().width(),
                sample_existing_label.sizeHint().width(),
            )
            + 16
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_ACTION, QHeaderView.ResizeMode.Fixed
        )
        self._table.setColumnWidth(_COL_ACTION, self._action_column_width)
        self._table.setVisible(False)
        root.addWidget(self._table, stretch=1)

        self._empty_label = QLabel("No additional variations found.")
        self._empty_label.setObjectName("statusLabel")
        self._empty_label.setVisible(False)
        root.addWidget(self._empty_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── scan ────────────────────────────────────────────────────────────

    def _run_scan(self) -> None:
        # Synchronous — see variation_scan.py's own performance note. If
        # this proves slow on a real long book, move it to a QThread the
        # same way ScanWorker already does for the real Matcher pass,
        # rather than pre-emptively guessing it needs one.
        suggestions = find_word_variations(self._transcript, self._snapshot)
        self._progress.setVisible(False)
        if not suggestions:
            self._status_label.setVisible(False)
            self._empty_label.setVisible(True)
            return
        count = len(suggestions)
        self._status_label.setText(
            f"{count} possible variation{'s' if count != 1 else ''} found:"
        )
        self._populate_table(suggestions)

    def _populate_table(self, suggestions: list[WordVariationSuggestion]) -> None:
        self._table.setVisible(True)
        self._table.setRowCount(len(suggestions))
        for row, suggestion in enumerate(suggestions):
            self._table.setItem(
                row, _COL_WORD, QTableWidgetItem(suggestion.surface_text)
            )
            self._table.setItem(
                row, _COL_RELATED, QTableWidgetItem(suggestion.related_phrase)
            )
            why_item = QTableWidgetItem(_KIND_LABELS[suggestion.kind])
            why_item.setToolTip(_KIND_TOOLTIPS[suggestion.kind])
            self._table.setItem(row, _COL_WHY, why_item)
            self._table.setItem(
                row,
                _COL_SEEN,
                QTableWidgetItem(f"{suggestion.occurrence_count}×"),
            )
            context_item = QTableWidgetItem(suggestion.context)
            context_item.setToolTip(suggestion.context)
            self._table.setItem(row, _COL_CONTEXT, context_item)

            # A suggestion is only checked against the *selected profile's*
            # entries (variation_scan.py's own documented scope) — a word
            # already in the catalog under the same category, just not
            # yet part of this profile, wouldn't be caught by that. Check
            # directly here too so "+ Add" never offers to create a real
            # duplicate, same guard CatalogWindow's own manual add already
            # applies (ADR-0040).
            existing = self._service.find_duplicate_entry(
                suggestion.related_category_id, suggestion.surface_text
            )
            if existing is not None:
                already_label = QLabel("Already in catalog")
                already_label.setObjectName("statusLabel")
                already_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setCellWidget(row, _COL_ACTION, already_label)
                continue

            add_btn = QPushButton("+ Add")
            add_btn.clicked.connect(
                lambda _checked=False, r=row, s=suggestion: self._on_add(r, s)
            )
            self._table.setCellWidget(row, _COL_ACTION, add_btn)

        # Column widths default to a fixed initial size too narrow for
        # some headers (e.g. "Suggested Word" got clipped to "GGESTED
        # WOI"); row heights default to fit plain text, shorter than a
        # normal QPushButton's own natural height, clipping the "+ Add"
        # button. Both need the real content/widgets already in place to
        # size against, so this runs once after the loop above, not per
        # row — resizeColumnsToContents would undersize _COL_CONTEXT if
        # done first, since Stretch mode overrides it anyway.
        self._table.resizeColumnsToContents()
        self._table.resizeRowsToContents()
        # resizeColumnsToContents() above ignores _COL_ACTION's Fixed
        # mode and re-measures it from the cell widget anyway (the same
        # unreliable measurement that clipped "+ Add" to "Ad" in the
        # first place), undoing _build_ui()'s explicit width — restore
        # it every time this repopulates the table.
        self._table.setColumnWidth(_COL_ACTION, self._action_column_width)

    def _on_add(self, row: int, suggestion: WordVariationSuggestion) -> None:
        self._service.create_entry(
            suggestion.related_category_id, suggestion.surface_text
        )
        save_catalog(self._service)
        added_label = QLabel("✓ Added")
        added_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(row, _COL_ACTION, added_label)
