"""Horizontal step indicator for the filter wizard (ADR-0010, PRD §7.2).

Ports ``docs/design/wizard-shell-wireframe.html``'s stepper design
directly, since that wireframe went through several review rounds before
being approved: equal-width/height cells regardless of label length or
state, a solid connecting line running through every circle, and state
carried by color/weight alone — an earlier wireframe draft added a status
caption ("Viewing now" / "Complete") under each label and it was removed
as redundant clutter once the color/weight distinction was in place, so
this port never adds one back.

State per cell mirrors the wireframe's exact logic, including one
detail worth calling out: the furthest-reached step, when it is not the
currently active one (e.g. the User clicked Back to revisit an earlier
step), is deliberately *not* styled as "locked" — it's still reachable
(clickable), just not focused — so it renders with the plain base badge
style rather than the grayed-out locked one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

STEP_LABELS: tuple[str, ...] = (
    "Source",
    "Transcript",
    "Transcribe",
    "Profile",
    "Scan",
    "Review",
    "Render",
)

_DONE_GLYPH = "✓"  # ✓
_SKIPPED_GLYPH = "»"  # »


def _repolish(widget: QWidget) -> None:
    """Force Qt to re-evaluate attribute-selector QSS after setProperty()."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class _StepCell(QWidget):
    """One stepper cell: a connecting-line/badge row, and a name label
    below it. Two separate line segments (before/after the badge) so
    adjacent cells' segments can be colored independently yet still read
    as one continuous line — each fills exactly half its cell."""

    clicked = Signal(int)

    def __init__(self, index: int, name: str, is_first: bool, is_last: bool) -> None:
        super().__init__()
        self._index = index
        self._clickable = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        node_row = QHBoxLayout()
        node_row.setContentsMargins(0, 0, 0, 0)
        node_row.setSpacing(0)
        root.addLayout(node_row)

        self._line_before = QLabel()
        self._line_before.setObjectName("stepLine")
        self._line_before.setFixedHeight(2)
        self._line_before.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        if is_first:
            self._line_before.setVisible(False)
        node_row.addWidget(self._line_before)

        self._badge = QLabel(str(index + 1))
        self._badge.setObjectName("stepBadge")
        self._badge.setFixedSize(28, 28)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        node_row.addWidget(self._badge)

        self._line_after = QLabel()
        self._line_after.setObjectName("stepLine")
        self._line_after.setFixedHeight(2)
        self._line_after.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        if is_last:
            self._line_after.setVisible(False)
        node_row.addWidget(self._line_after)

        self._name_label = QLabel(name)
        self._name_label.setObjectName("stepName")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._name_label)

    def set_state(self, state: str | None) -> None:
        """*state* is ``"current"``, ``"done"``, ``"locked"``, or ``None``
        (reachable but not the active step — the base/unstyled look)."""
        prop_value = state or ""
        for widget in (self._badge, self._name_label):
            widget.setProperty("stepState", prop_value)
            _repolish(widget)

    def set_badge_text(self, skipped: bool) -> None:
        if skipped:
            self._badge.setText(_SKIPPED_GLYPH)
        elif self._badge.property("stepState") == "done":
            self._badge.setText(_DONE_GLYPH)
        else:
            self._badge.setText(str(self._index + 1))

    def set_line_fill(self, before_filled: bool, after_filled: bool) -> None:
        self._line_before.setProperty("filled", "true" if before_filled else "")
        self._line_after.setProperty("filled", "true" if after_filled else "")
        _repolish(self._line_before)
        _repolish(self._line_after)

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = clickable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if clickable
            else Qt.CursorShape.ArrowCursor
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._clickable:
            self.clicked.emit(self._index)
        super().mousePressEvent(event)


class StepperWidget(QWidget):
    """The wizard's horizontal step indicator. Emits ``step_clicked(index)``
    when a reachable cell is clicked; the wizard window decides what
    "reachable" means and calls :meth:`set_progress` accordingly — this
    widget only renders whatever progress it's told."""

    step_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cells: list[_StepCell] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(0)
        last = len(STEP_LABELS) - 1
        for i, name in enumerate(STEP_LABELS):
            cell = _StepCell(i, name, is_first=(i == 0), is_last=(i == last))
            cell.clicked.connect(self.step_clicked.emit)
            layout.addWidget(cell, stretch=1)
            self._cells.append(cell)

    def set_progress(
        self, current: int, furthest: int, skipped: set[int] | None = None
    ) -> None:
        furthest = max(furthest, current)
        skipped = set(skipped or ())

        done_flags = [i < furthest and i != current for i in range(len(self._cells))]

        for i, cell in enumerate(self._cells):
            is_current = i == current
            is_done = done_flags[i]
            is_locked = i > furthest
            state = (
                "current"
                if is_current
                else "done" if is_done else "locked" if is_locked else None
            )
            cell.set_state(state)
            cell.set_badge_text(skipped=is_done and i in skipped)
            before_filled = i > 0 and done_flags[i - 1]
            after_filled = is_done
            cell.set_line_fill(before_filled, after_filled)
            cell.set_clickable(i <= furthest)
