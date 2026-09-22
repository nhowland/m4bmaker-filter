# ADR-0023: GUI test suite segfault — cyclic GC racing Qt native teardown

**Status:** Implemented and verified. Root-causes and fixes the
combined-run segfault flagged as an open follow-up at the end of
ADR-0022 ("running a large enough combination of `tests/gui/` files
together in one process ... segfaults in native Qt/Shiboken teardown
code").

## The bug

`pytest tests/gui/filter/ tests/gui/test_window.py` (or the larger
`pytest tests/gui/`) crashes with `Fatal Python error: Segmentation
fault`, always inside `tests/gui/test_window.py`'s own `win` fixture
teardown, always at the same line:

```
File "tests/gui/test_window.py", line 98 in win
File ".../_pytest/fixtures.py", line 1014 in _teardown_yield_fixture
```

Line 98 is `QCoreApplication.sendPostedEvents(None,
QEvent.Type.DeferredDelete.value)`. The native frame at the point of
the crash is Shiboken/Qt object-teardown code only — `shiboken6.Shiboken`,
`PySide6.QtCore`, `QtGui`, `QtWidgets` — no Python frames from this
project. It does **not** reproduce running either file alone
(`test_window.py`: 180/180 pass; `tests/gui/filter/`: 287/287 pass),
only once enough total widget churn from *other* files has piled up in
the same process before `test_window.py`'s fixtures run.

## Root cause

Nearly every `win`-style fixture outside `test_window.py` just
constructs and returns a window with no teardown at all:

```python
# tests/gui/filter/test_catalog_window.py
@pytest.fixture()
def win(service: CatalogService, mock_save: MagicMock) -> CatalogWindow:
    return CatalogWindow(service)
```

Same shape in `test_model_manager_window.py` and every
`tests/gui/filter/wizard/*.py` file — `return WizardWindow(...)`, no
`yield`, no `.close()`, no `.deleteLater()`. `test_window.py`'s own
`win` fixture is the one exception in the whole tree, and its docstring
explains why it bothers (`tests/gui/test_window.py:20-35`): it already
knew accumulated live windows corrupt later tests' app-level event
delivery, so it goes out of its way to force real Qt-side destruction —
`deleteLater()`, `processEvents()`, then the explicit
`sendPostedEvents(None, DeferredDelete)` flush, since plain
`processEvents()` alone never delivers `DeferredDelete`.

None of the other ~15 GUI test files parent their windows to anything,
so each one is a top-level `QObject` Python-owns: when the local
variable goes out of scope at the end of the test function, nothing
Qt-side destroys the C++ object — it just waits for CPython's garbage
collector to notice it's unreachable. Reference counting alone would
free it immediately, but a `QWidget` tree is full of reference cycles
(parent↔child pointers, signal/slot connections holding both ends),
so these are cyclic garbage: they only get collected whenever CPython's
generational GC happens to run, at whatever point in the program that
lands.

Running `tests/gui/filter/` (and further, the wizard step files) before
`test_window.py` leaves several hundred such orphaned top-level widget
trees alive and uncollected. By the time `test_window.py`'s `win`
fixture reaches its own explicit `sendPostedEvents(None,
DeferredDelete)` call — itself a heavy native call into Qt's object and
event-queue bookkeeping — enough Python allocations have happened that
CPython's GC threshold trips *during* that call. The GC then destroys a
batch of the accumulated orphaned `QWidget` trees via Shiboken while
Qt's own native teardown code is mid-traversal on the call stack above
it. Two independent native object-teardown paths (CPython's collector
tearing down `QWidget` C++ objects it decided are garbage, and Qt's own
event/object bookkeeping mid-flush for an unrelated widget) end up
reentrant on the same thread, and that reentrancy is what segfaults —
not any single widget being wrong.

This explains every observed fact:
- **Only test_window.py's teardown crashes**: it's the only fixture
  that calls `sendPostedEvents(None, ...)` — the specific call whose
  own native work is heavy enough, and frequent enough across all of
  `test_window.py`'s many tests, to be the one reliably caught mid-GC.
- **Doesn't reproduce with either file alone**: not enough orphaned
  widgets accumulate pre-crash to trip CPython's GC threshold inside
  that call.
- **Reproduces at both the current tree and the pre-round stash
  (20f05c9)**: the leak pattern (`return Window(...)`, no teardown) was
  already present everywhere outside `test_window.py`; the wizard
  round just added enough additional widget-heavy test files (Profile/
  Scan/Render steps) to push total per-process churn over the
  threshold.
- **No project code in the crashing frames**: correct — the defect is
  in test fixture lifecycle, not in `m4bmaker` or `m4bmaker.gui`
  itself.

**Confirmed experimentally**: `gc.disable()` before either the
combined repro (`tests/gui/filter/` + `tests/gui/test_window.py`) or
the full `tests/gui/` suite makes the crash disappear completely and
deterministically — verified across multiple repeated runs, in both
cases with the exact same tests, unmodified. Reference counting still
frees everything that isn't a cycle; only the cyclic collector actually
matters here.

## Decision

Add a session-scoped, autouse `_disable_cyclic_gc` fixture to
`tests/gui/conftest.py` that disables CPython's cyclic garbage
collector for the lifetime of the GUI test session and re-enables it on
teardown:

```python
@pytest.fixture(scope="session", autouse=True)
def _disable_cyclic_gc():
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()
```

This was chosen over the alternative of retrofitting explicit
`.deleteLater()` + `sendPostedEvents(None, DeferredDelete)` teardown
(matching `test_window.py`'s own pattern) into every other `win`
fixture across `tests/gui/filter/` and `tests/gui/filter/wizard/`:
that fix is more surgical, but it means touching ~15 files' worth of
fixtures for what is purely a test-process object-lifetime artifact,
not a real defect a User can hit — the real application creates one
`MainWindow`/one `WizardWindow` per run and shuts down normally,
never approaching the volume of orphaned top-level widgets a single
pytest process accumulates across 700+ GUI tests. Disabling the cyclic
collector for a short-lived test process is a standard, low-risk
mitigation for exactly this class of GC/native-teardown reentrancy in
PySide/PyQt test suites — reference counting still reclaims everything
that isn't a cycle, and the process exits shortly after, so the
uncollected cyclic garbage never has a chance to matter.

## What this ADR does not change

No production code changes — `m4bmaker/` is untouched. This is a
test-only fix, scoped to `tests/gui/conftest.py`. It does not retrofit
teardown into the individual `win` fixtures that leak windows; if a
future change wants deterministic per-test widget destruction (e.g. to
catch a real crash-on-close bug rather than a test-lifecycle one),
that's a separate, larger change than this ADR takes on.

## Verification

`pytest tests/gui/filter/ tests/gui/test_window.py` (the original
repro): 467/467 pass, stable across repeated runs. Full `pytest
tests/gui/`: 784/784 pass, stable across repeated runs (previously
segfaulted, `Fatal Python error: Segmentation fault`, exit code 139).
This project's real CI command, `pytest tests/ --ignore=tests/gui`
(unaffected either way, since it never runs `tests/gui/`), remains
clean: 927 passed, 2 skipped.
