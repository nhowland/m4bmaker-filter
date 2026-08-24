# Testing the filtering feature

This supplements the base project's `CONTRIBUTING.md` (still accurate for
setup/run commands) with conventions specific to `m4bmaker/filter/`.

## Running just the filtering tests

```bash
pytest tests/filter/ -v
pytest tests/filter/ --cov=m4bmaker.filter --cov-report=term-missing
```

## Conventions carried over from the base project

- `unittest.mock.patch("subprocess.run", return_value=<MagicMock>)` for
  every ffprobe/ffmpeg call — never invoke the real binaries in unit tests.
  See `tests/filter/test_media_inspector.py` for the pattern, copied
  directly from `tests/test_preflight.py`.
- `pytest`'s `tmp_path` fixture for any filesystem interaction — never
  write to a fixed path.
- Class-per-behavior grouping (`class TestX: def test_y(self): ...`),
  matching every existing test file in `tests/`.
- `black` / `flake8` / `mypy` must pass on new code with zero exceptions
  added to `setup.cfg`. Run before every commit:

  ```bash
  black m4bmaker/filter tests/filter
  flake8 m4bmaker/filter tests/filter
  mypy m4bmaker/filter
  ```

- Coverage target: >90%, matching the project-wide target in
  `CONTRIBUTING.md`. As of G1, `m4bmaker/filter/` is at 98% (7 uncovered
  lines, all defensive `except (TypeError, ValueError)` branches around
  malformed upstream ffprobe fields — the same style of intentionally
  hard-to-hit defensive branch the base `preflight.py` already has).

## New conventions for this feature

- **No live STT/model/network calls in any automated test through at least
  G2.** G3 introduces the first tests that touch a real (but tiny, pinned)
  model — those must be marked and isolated so `pytest tests/` stays fast
  and offline-safe by default; the exact marker/opt-in mechanism is a G3
  decision, not defined yet.
- **Schema tests assert both the happy path and every documented boundary**
  from the PRD's own tables — e.g. `AttenuationSettings`' range tests in
  `tests/filter/test_models.py` cover every min/max in PRD §8.3's table,
  not just "some invalid value raises."
- **Every job-state test is written against the PRD's own transition
  rationale, not just current code behavior** — `tests/filter/test_jobs.py`
  has one test per bullet in `jobs.py`'s `ALLOWED_TRANSITIONS` docstring
  (e.g. "pausing cannot go directly back to running"), so a future edit
  that silently reintroduces auto-resume fails a test with a name that
  explains *why* it's wrong, not just that an assertion failed.
- **`inspect()` never raises for a bad/ineligible source file** — it
  reports ineligibility on the returned `MediaManifest` instead (PRD §6.1).
  Tests must assert on `.eligible`/`.ineligibility_reasons`, not on
  exceptions, for every unsupported-condition case in PRD §6.4. Each PRD
  §6.4 bullet has a corresponding test in `TestUnsupportedConditions`.

## What G1's tests deliberately do *not* cover

- Real whisper.cpp output (G3).
- Real ffmpeg gain-envelope rendering (G4).
- End-to-end GUI flows (G5) — no `gui/filter/` exists yet.
- Multi-hour fixture performance (needs the named reference-hardware
  benchmark harness from PRD §13.1, not yet built).

These are called out explicitly so a reviewer doesn't mistake "98%
coverage of the G1 modules" for "the feature is tested" — it's schema and
eligibility-logic coverage only, exactly matching G1's PRD-defined scope.
