"""Word-span transcript rendering and selection (ADR-0053, Option 1).

Renders one chapter/segment's real ``TranscriptWord`` list as flowing,
read-only text, with words already covered by a real ``ScanHit`` struck
through and excluded from selection. Selection is word-granular (click one
word, or drag across several) and can never include or cross a hit — a
drag that reaches one stops at its edge, same reasoning ADR-0052's own
padded-vs-context toggle already used: what's about to be acted on should
never be ambiguous.

The two pieces of logic that matter most are kept as plain functions
(:func:`hit_word_flags`, :func:`snap_selection`) rather than buried inside
Qt event handlers, so they're directly testable without a running event
loop or simulated mouse events.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Sequence

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QMouseEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QMenu, QTextEdit, QWidget

from m4bmaker.filter.models import ScanHit
from m4bmaker.filter.transcript import TranscriptWord

#: Arbitrary placeholder, not a validated default -- ADR-0053 explicitly
#: leaves the real "low confidence" threshold undecided (it rejected
#: confidence as an automated *filter*, Option 4, but never picked a
#: number for this tab's optional, human-facing skim hint either). This
#: constant only needs to make the "Highlight uncertain words" toggle
#: visibly do something for evaluating the design, not be the shipped
#: default.
_LOW_CONFIDENCE_THRESHOLD = 0.75


def hit_word_flags(
    words: Sequence[TranscriptWord], hits: Sequence[ScanHit]
) -> list[bool]:
    """``True`` for each word index whose span falls inside any of *hits*'
    own ``[start_ms, end_ms)`` spans.

    Hits are merged first (sorted, overlapping spans coalesced) so this is
    one linear sweep over *words*, not an O(words × hits) scan -- the same
    shape the ADR-0053 real-data validation scripts already used and
    verified across eleven real books, not new-and-unverified here.
    """
    if not words or not hits:
        return [False] * len(words)
    merged: list[list[int]] = []
    for h in sorted(hits, key=lambda h: h.start_ms):
        if merged and h.start_ms <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], h.end_ms)
        else:
            merged.append([h.start_ms, h.end_ms])
    flags = [False] * len(words)
    mi = 0
    for i, w in enumerate(words):
        while mi < len(merged) and merged[mi][1] <= w.start_ms:
            mi += 1
        if mi < len(merged) and w.start_ms < merged[mi][1] and w.end_ms > merged[mi][0]:
            flags[i] = True
    return flags


def snap_selection(
    anchor_idx: int, position_idx: int, hit_flags: Sequence[bool]
) -> tuple[int, int] | None:
    """Normalize a raw (anchor, position) word-index pair from a drag into
    the actual ``(lo, hi)`` inclusive range to select.

    Always normalized so ``lo <= hi`` regardless of drag direction, then
    truncated at the first hit word encountered scanning from *lo* --
    a selection can never include or cross a hit. Returns ``None`` if
    that leaves nothing selectable (e.g. *lo* itself is a hit).
    """
    lo, hi = min(anchor_idx, position_idx), max(anchor_idx, position_idx)
    for i in range(lo, hi + 1):
        if hit_flags[i]:
            hi = i - 1
            break
    if hi < lo:
        return None
    return lo, hi


@dataclass(frozen=True)
class _WordSpan:
    word: TranscriptWord
    char_start: int
    char_end: int
    is_hit: bool


class TranscriptView(QTextEdit):
    """Read-only chapter text with word-granular, hit-bounded selection.

    Emits :attr:`selection_changed` once per user gesture (click, drag, or
    double-click) — not per intermediate mouse-move event, so a live drag
    renders with Qt's own default selection feedback and only "settles"
    into the actual word/hit-snapped range on release. :attr:`play_requested`
    /:attr:`add_requested` fire from the right-click context menu, acting on
    whatever :meth:`selected_words` already holds (selecting the clicked
    word first if nothing was already selected there).
    """

    selection_changed = Signal()
    play_requested = Signal()
    add_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._spans: list[_WordSpan] = []
        self._selected_range: tuple[int, int] | None = None
        self._show_low_confidence = False
        self._pending_starts_ms: set[int] = set()
        self._last_hit_jump_idx = -1

    # ── loading ──────────────────────────────────────────────────────────

    def load_words(
        self, words: Sequence[TranscriptWord], hits: Sequence[ScanHit]
    ) -> None:
        """Render *words* (one chapter/segment's worth), with the spans
        *hits* covers struck through and excluded from selection."""
        flags = hit_word_flags(words, hits)
        self._selected_range = None
        self._pending_starts_ms = set()
        self._last_hit_jump_idx = -1

        plain_fmt = QTextCharFormat()
        hit_fmt = QTextCharFormat()
        hit_fmt.setForeground(QColor("#c45a2d"))
        hit_fmt.setFontStrikeOut(True)
        hit_fmt.setFontWeight(QFont.Weight.DemiBold)

        self.clear()
        cursor = self.textCursor()
        cursor.beginEditBlock()
        spans: list[_WordSpan] = []
        for w, is_hit in zip(words, flags):
            start = cursor.position()
            cursor.insertText(w.text, hit_fmt if is_hit else plain_fmt)
            end = cursor.position()
            spans.append(
                _WordSpan(word=w, char_start=start, char_end=end, is_hit=is_hit)
            )
            cursor.insertText(" ", plain_fmt)
        cursor.endEditBlock()
        self._spans = spans

    # ── selection ────────────────────────────────────────────────────────

    def selected_words(self) -> list[TranscriptWord]:
        if self._selected_range is None:
            return []
        lo, hi = self._selected_range
        return [self._spans[i].word for i in range(lo, hi + 1)]

    def selection_text(self) -> str:
        return " ".join(w.text.strip() for w in self.selected_words())

    def _word_index_at(self, char_pos: int) -> int | None:
        if not self._spans:
            return None
        starts = [s.char_start for s in self._spans]
        idx = bisect_right(starts, char_pos) - 1
        if idx < 0:
            return None
        return idx

    def _set_selection(self, word_range: tuple[int, int] | None) -> None:
        self._selected_range = word_range
        cursor = self.textCursor()
        if word_range is None:
            cursor.clearSelection()
        else:
            lo, hi = word_range
            cursor.setPosition(self._spans[lo].char_start)
            cursor.setPosition(
                self._spans[hi].char_end, QTextCursor.MoveMode.KeepAnchor
            )
        self.setTextCursor(cursor)
        self.selection_changed.emit()

    def _snap_current_selection(self) -> None:
        if not self._spans:
            return
        cursor = self.textCursor()
        anchor_pos, pos = cursor.anchor(), cursor.position()
        if anchor_pos == pos:
            idx = self._word_index_at(pos)
            if idx is None or self._spans[idx].is_hit:
                self._set_selection(None)
            else:
                self._set_selection((idx, idx))
            return
        anchor_idx = self._word_index_at(anchor_pos)
        pos_idx = self._word_index_at(pos)
        if anchor_idx is None or pos_idx is None:
            self._set_selection(None)
            return
        flags = [s.is_hit for s in self._spans]
        self._set_selection(snap_selection(anchor_idx, pos_idx, flags))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._snap_current_selection()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        super().mouseDoubleClickEvent(event)
        self._snap_current_selection()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        char_pos = self.cursorForPosition(event.pos()).position()
        self._show_context_menu_at(char_pos, event.globalPos())

    def _show_context_menu_at(self, char_pos: int, global_pos: QPoint) -> None:
        """Split out from :meth:`contextMenuEvent` so the "which word does
        this position land on, and does a menu make sense there" logic is
        testable with a plain character offset — no real widget geometry
        or a constructed ``QContextMenuEvent`` required."""
        idx = self._word_index_at(char_pos)
        if idx is None or self._spans[idx].is_hit:
            return
        if self._selected_range is None or not (
            self._selected_range[0] <= idx <= self._selected_range[1]
        ):
            self._set_selection((idx, idx))

        menu = QMenu(self)
        play_action = menu.addAction("▶ Play")
        add_action = menu.addAction(f"＋ Add “{self.selection_text()}” to Catalog…")
        chosen = self._exec_context_menu(menu, global_pos)
        if chosen is play_action:
            self.play_requested.emit()
        elif chosen is add_action:
            self.add_requested.emit()

    def _exec_context_menu(self, menu: QMenu, global_pos: QPoint) -> object:
        """Trivial wrapper around ``menu.exec()`` — kept as a plain method
        on this class (not a bare call to the Qt/Shiboken-wrapped
        ``QMenu.exec`` itself) so tests can patch it reliably. Patching a
        C++-bound method directly via ``unittest.mock.patch.object`` on
        the real ``QMenu`` class does not reliably take effect (confirmed
        directly: the patch silently no-ops and the real modal ``exec()``
        still runs, hanging indefinitely under the offscreen test
        platform) — patching an ordinary Python method defined here does
        not have that problem."""
        return menu.exec(global_pos)

    # ── pending (added-this-session) marking ────────────────────────────

    def mark_pending(self, words: Sequence[TranscriptWord]) -> None:
        """Give *words* a dashed pending-underline — distinct from a real
        hit's solid strikethrough — signaling "added to the catalog, not
        yet reflected in this scan's own hits" rather than implying an
        already-applied fix (ADR-0053)."""
        self._pending_starts_ms |= {w.start_ms for w in words}
        pending_fmt = QTextCharFormat()
        pending_fmt.setFontUnderline(True)
        pending_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
        pending_fmt.setUnderlineColor(QColor("#d8a33d"))
        cursor = self.textCursor()
        targets = {w.start_ms for w in words}
        for span in self._spans:
            if span.word.start_ms in targets:
                cursor.setPosition(span.char_start)
                cursor.setPosition(span.char_end, QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(pending_fmt)
        self._set_selection(None)

    # ── optional low-confidence skim hint ────────────────────────────────

    def set_low_confidence_hint(self, enabled: bool) -> None:
        self._show_low_confidence = enabled
        self._apply_low_confidence_formatting()

    def _apply_low_confidence_formatting(self) -> None:
        plain_fmt = QTextCharFormat()
        lowconf_fmt = QTextCharFormat()
        lowconf_fmt.setFontUnderline(True)
        lowconf_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
        cursor = self.textCursor()
        for span in self._spans:
            if span.is_hit or span.word.start_ms in self._pending_starts_ms:
                continue
            is_low = (
                self._show_low_confidence
                and span.word.confidence is not None
                and span.word.confidence < _LOW_CONFIDENCE_THRESHOLD
            )
            cursor.setPosition(span.char_start)
            cursor.setPosition(span.char_end, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(lowconf_fmt if is_low else plain_fmt)

    # ── jump to next hit ─────────────────────────────────────────────────

    def jump_to_next_hit(self) -> None:
        hit_indices = [i for i, s in enumerate(self._spans) if s.is_hit]
        if not hit_indices:
            return
        next_idx = next(
            (i for i in hit_indices if i > self._last_hit_jump_idx), hit_indices[0]
        )
        self._last_hit_jump_idx = next_idx
        span = self._spans[next_idx]
        cursor = QTextCursor(self.document())
        cursor.setPosition(span.char_start)
        rect = self.cursorRect(cursor)
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + rect.top() - 40)
