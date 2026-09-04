"""About Language Filter dialog (ADR-0048).

A read-only overview of what the Language Filter feature does and the
capabilities it offers — informational only, no controls beyond
closing it. Built fresh on every open (matching the base app's own
``MainWindow._show_about()``) rather than lazily created and reused
like the feature's other secondary windows — there is no state here to
preserve between opens.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_ACCENT = "#c45a2d"

#: Feature cards lay out two-across rather than one long column — with
#: 7 real cards, a single column needs real scrolling to see the last
#: few (verified against the real running app at this dialog's original
#: 520px width); two columns cuts that to 4 rows, short enough to fit
#: without scrolling at a wider default size instead.
_FEATURE_COLUMNS = 2

#: (title, description) for each real capability — in the same order a
#: User encounters them while actually using the feature (build a word
#: list, save it as a profile, transcribe, scan, review, render, plus
#: the two supporting/management windows), so this doubles as an
#: implicit walkthrough of the wizard flow without a separate numbered
#: list competing with it for space. Kept at an even number (currently
#: 8) so the two-column grid below never leaves an orphan card alone in
#: the last row.
_FEATURES: tuple[tuple[str, str], ...] = (
    (
        "Word List",
        "Build a catalog of words and phrases to filter, organized into "
        "categories you define. Mask sensitive terms so they show as "
        "asterisks instead of plain text wherever they're reviewed.",
    ),
    (
        "Filter Profiles",
        "Save a reusable combination of catalog selections and "
        "attenuation settings — how much of each word gets silenced, "
        "and how abruptly — so the same profile applies consistently "
        "across every book.",
    ),
    (
        "Automatic Transcription",
        "Powered by whisper.cpp, running entirely on this machine — "
        "nothing is uploaded anywhere. Long books are split into "
        "chapter-aligned chunks, so a transcription job can be paused "
        "and resumed later without losing progress.",
    ),
    (
        "Smart Scanning",
        "Matches your word list against the real transcript and flags "
        "every occurrence, including some near-miss variations "
        "whisper's own recognition might otherwise let slip through.",
    ),
    (
        "Review Before You Commit",
        "See every flagged word in context, include or exclude each "
        "one individually, and preview exactly what will be silenced "
        "before any audio is touched.",
    ),
    (
        "Filtered Render",
        "Produces a new M4B with the approved sections silenced — "
        "chapters, metadata, and cover art are all preserved from the "
        "original file.",
    ),
    (
        "Manage Transcription Models",
        "Download, verify, and remove the whisper.cpp models used for "
        "transcription — right from the app, with no separate setup "
        "required.",
    ),
    (
        "Settings",
        "Choose where downloaded models, saved transcripts, filtered "
        "output, and temporary working files are stored on this "
        "machine.",
    ),
)


def _feature_card(title: str, body: str) -> QFrame:
    card = QFrame()
    card.setObjectName("aboutFeatureCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(4)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"font-weight: 600; font-size: 13px; color: {_ACCENT};")
    layout.addWidget(title_lbl)

    body_lbl = QLabel(body)
    body_lbl.setWordWrap(True)
    body_lbl.setObjectName("statusLabel")
    layout.addWidget(body_lbl)

    return card


class AboutLanguageFilterDialog(QDialog):
    """Read-only overview of the Language Filter feature."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Language Filter")
        self.setMinimumWidth(760)
        self._build_ui()
        self._size_to_content()

    def _size_to_content(self) -> None:
        """Open tall enough to show every card without scrolling,
        measured from the real built content rather than a guessed
        constant — the same fix already applied to the Settings window
        (ADR-0047 addendum) after a hardcoded height there clipped its
        own last row on a real screen.

        Two separate reasons a plain ``sizeHint()`` undercounts this
        dialog's real height, both worth spelling out since either one
        alone reproduces the clipped-last-row bug: a `QScrollArea`'s own
        ``sizeHint()`` doesn't reflect how tall its *content* actually
        is (that's the entire point of a scroll area — measured from
        ``self._body`` directly instead); and a word-wrapped
        `QLabel` (every description here, plus the header's own title/
        tagline/workflow text) reports its *unwrapped*, single-line
        height from a plain ``sizeHint()`` — only ``heightForWidth()``
        at the width this dialog will actually open at reflects how
        many lines it really wraps to. Capped against the real
        available screen height so this doesn't grow past what actually
        fits — the `QScrollArea` stays in place as the fallback for
        whatever doesn't.
        """
        dialog_width = 860
        header_layout = self._header.layout()
        body_layout = self._body.layout()
        assert header_layout is not None  # set in _build_ui() just above
        assert body_layout is not None  # set in _build_ui() just above
        header_height = header_layout.heightForWidth(dialog_width)
        body_height = body_layout.heightForWidth(dialog_width)
        footer_height = self._footer.sizeHint().height()
        content_height = header_height + body_height + footer_height

        screen = QApplication.primaryScreen()
        max_height = (
            int(screen.availableGeometry().height() * 0.9)
            if screen is not None
            else content_height
        )
        self.resize(dialog_width, min(content_height, max_height))

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(32, 28, 32, 20)
        header_layout.setSpacing(8)

        title = QLabel("Filter Audiobook Language")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header_layout.addWidget(title)

        tagline = QLabel(
            "Automatically finds and silences the words you choose in "
            "your own audiobooks, using on-device transcription — you "
            "review every change before anything is rendered."
        )
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setWordWrap(True)
        tagline.setObjectName("statusLabel")
        header_layout.addWidget(tagline)

        workflow = QLabel(
            "It works as a guided, step-by-step process: pick a book, "
            "transcribe it, review what would be filtered, then render "
            "a new copy with those words silenced."
        )
        workflow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        workflow.setWordWrap(True)
        workflow.setObjectName("statusLabel")
        header_layout.addWidget(workflow)

        self._header = header
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 0, 24, 16)
        body_layout.setSpacing(10)

        section_label = QLabel("WHAT YOU CAN DO")
        section_label.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #7a7a7a; "
            "letter-spacing: 0.06em;"
        )
        body_layout.addWidget(section_label)

        grid = QGridLayout()
        grid.setSpacing(10)
        for column in range(_FEATURE_COLUMNS):
            grid.setColumnStretch(column, 1)
        for index, (feature_title, feature_body) in enumerate(_FEATURES):
            row, column = divmod(index, _FEATURE_COLUMNS)
            grid.addWidget(_feature_card(feature_title, feature_body), row, column)
        body_layout.addLayout(grid)

        body_layout.addStretch(1)
        self._body = body
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 20)
        footer_layout.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("primaryBtn")
        close_btn.setFixedWidth(96)
        close_btn.clicked.connect(self.accept)
        footer_layout.addWidget(close_btn)
        self._footer = footer
        root.addWidget(footer)

    def apply_stylesheet(self, dark: bool) -> None:
        from m4bmaker.gui.styles import get_stylesheet

        self.setStyleSheet(get_stylesheet(dark))
