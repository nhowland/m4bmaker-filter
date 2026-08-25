"""Stand-in widget for the six wizard steps not yet designed to the
level of detail ADR-0010 requires before writing real code for them
(Source, Transcript, Transcribe, Profile, Scan, Render, Complete — only
the shell itself and Review have been through a wireframe review).

Deliberately inert: it does not pretend to implement PRD §7.2's stage
behavior, it says plainly that it isn't built yet. This keeps the wizard
shell fully navigable end-to-end today without inventing screen designs
that haven't been reviewed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .step_base import WizardStep


class PlaceholderStep(WizardStep):
    def __init__(
        self, title: str, subtitle: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.step_title = title
        self.step_subtitle = subtitle

        layout = QVBoxLayout(self)
        layout.addStretch(1)
        label = QLabel(f"“{title}” isn't built yet.")
        label.setObjectName("statusLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addStretch(2)
