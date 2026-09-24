"""Fixtures shared by the wizard step tests."""

from __future__ import annotations

from typing import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _whisper_cli_present() -> Iterator[None]:
    """TranscriptStep asks find_whisper_cli() whether it may advance
    (ADR-0056). Default every wizard test to "installed" so none depends on
    whether the machine running the suite has whisper-cli; the banner tests
    patch it to None themselves."""
    with patch(
        "m4bmaker.gui.filter.wizard.transcript_step.find_whisper_cli",
        return_value="/fake/bin/whisper-cli",
    ):
        yield
