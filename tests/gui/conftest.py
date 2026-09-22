"""Shared pytest fixtures for GUI tests.

Sets QT_QPA_PLATFORM=offscreen before any Qt import so the test
suite can run headlessly in CI with no display.
"""

from __future__ import annotations

import gc
import os
import sys
from unittest.mock import patch

import pytest

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _disable_cyclic_gc():
    """See ADR-0023: most `win`-style fixtures across tests/gui/ hand back a
    bare top-level widget with no `deleteLater()`/`close()` teardown, so
    hundreds of them across a combined run stay alive as pure-Python objects
    until CPython's cyclic GC happens to collect them. When that collection
    lands inside Qt/Shiboken's own native teardown call stack (observed at
    `QCoreApplication.sendPostedEvents(None, DeferredDelete)` in
    tests/gui/test_window.py's `win` fixture), destroying a batch of orphaned
    QWidget trees mid-traversal segfaults. Reference counting alone still
    frees everything that isn't a cycle, so disabling only the *cyclic*
    collector for the test session is sufficient and was verified to make
    `pytest tests/gui/` fully deterministic (784/784) with no test changes."""
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication shared across all GUI tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    # Do NOT call app.quit() — pytest-qt style; let the process clean up.


@pytest.fixture(autouse=True)
def _no_update_thread():
    """Prevent UpdateChecker from spawning a real network thread in tests.

    Tests that specifically test UpdateChecker behaviour patch the class or
    its methods themselves; this fixture only guards MainWindow instantiations
    that don't care about the update check and would otherwise start a live
    network request and a background QThread.
    """
    with patch("m4bmaker.gui.updater.UpdateChecker.start"):
        yield


@pytest.fixture(autouse=True)
def _reset_download_coordinator():
    """``download_coordinator`` (ADR-0014) is one process-wide instance,
    not per-window state — without a reset, a test that acquires it and
    doesn't reach its own teardown (e.g. asserting mid-download) would
    leave it held, incorrectly blocking an unrelated download in a later
    test that happens to run afterward in the same process."""
    from m4bmaker.gui.filter.workers import download_coordinator

    download_coordinator.release()
    yield
    download_coordinator.release()
