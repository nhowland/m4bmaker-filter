# G0 — Repository Discovery

**Status:** Complete
**Gate:** G0 (PRD §17.3/§17.4)
**Date:** 2026-08-24
**Performed by:** AI coding agent (Claude), read-only inspection + baseline test run, no feature code

This is the mandatory G0 deliverable: pin the upstream state, inventory the
existing stack, and identify integration points before any filtering feature
code is written. Everything below was verified against the actual cloned
repository, not assumed from the README.

---

## 1. Pinned baseline

| Item | Value |
|---|---|
| Upstream repo | `https://github.com/sageframe-no-kaji/m4bmaker` |
| Branch | `main` |
| Pinned commit | `3f35b544bd7f5ebe60b42ea389a3231d6034eb27` |
| Commit date | 2026-08-24 |
| Package version | `1.1.1` (`pyproject.toml`) |
| License | GPL-3.0-or-later (SPDX), full GPLv3 text in `LICENSE` |
| Fork remote setup | `upstream` → the URL above (fetch-only, no `origin` configured yet — this fork is local-only pending a decision on where/whether to host it) |

## 2. Runtime and toolchain

| Item | Value |
|---|---|
| Python | `>=3.11` required (`pyproject.toml`); verified against 3.12 (Homebrew `python@3.12`) |
| GUI toolkit | PySide6 `>=6.6`, optional extra (`pip install m4bmaker[gui]`) |
| Runtime deps | `mutagen>=1.47`, `natsort>=8.4`, `platformdirs>=4.0` |
| Dev/test deps | `pytest>=8.0`, `pytest-cov>=5.0`, `mypy>=1.9`, `black>=24.0`, `flake8>=7.0`, `Pillow>=10.0`, `pyinstaller>=6.0`, `static-ffmpeg>=2.5` |
| External binary dependency | System `ffmpeg`/`ffprobe` `>=6.0`, located via `m4bmaker/utils.py:_which()` — checks PyInstaller `_MEIPASS`/frozen bundle dirs first, then `shutil.which`, then a fixed list of common Homebrew/MacPorts paths. This exact lookup pattern is the template a bundled STT binary should reuse. |
| Local ffmpeg used for this discovery | Homebrew `ffmpeg` 9.0.1 (installed during G0; none was present on this machine beforehand) |

## 3. Baseline test run (verified, not assumed)

```
python -m pytest tests/ -q --no-header
============================ 1013 passed in 12.90s =============================
```

Run from a clean `python3.12 -m venv .venv` + `pip install -e ".[gui]" -r requirements-dev.txt`, no modifications to the source tree. All 1013 tests pass, including the PySide6 GUI test suite (which ran natively; no `QT_QPA_PLATFORM=offscreen` was needed on this machine — the CI workflow sets it explicitly for the Windows runner instead).

**Discrepancy found:** `CONTRIBUTING.md` states "616 tests, March 2026, 93% overall" coverage. The actual current count is 1013. This is a stale doc comment upstream, not a discovery-gate blocker — noted here so nobody re-derives the wrong baseline later.

## 4. CI

Single GitHub Actions workflow, `.github/workflows/build.yml`:
- Triggers on `v*` tags or manual dispatch.
- **Windows only.** Installs from a hash-pinned lockfile (`requirements-build.lock`), runs `pytest tests/ --ignore=tests/gui` (GUI tests skipped in CI — no display), builds via PyInstaller (`m4bmaker-windows.spec`), packages with Inno Setup (`installer.iss`), uploads a private artifact (explicitly *not* a public GitHub Release — the workflow comment says the .exe is uploaded to Payhip manually).
- **No macOS CI job exists.** macOS signing/notarization is a fully manual process, documented step-by-step in `RELEASING.md` (references a named notarization keychain profile).

## 5. Module inventory relevant to this feature

| Module | Role | Relevance to filtering feature |
|---|---|---|
| `m4bmaker/models.py` | `Book`, `BookMetadata`, `Chapter`, `PipelineResult` dataclasses | Reused read-only for existing conversion; **not** to be extended with filtering-specific fields (O-08) |
| `m4bmaker/pipeline.py` | `load_audiobook()`, `run_pipeline()` — shared orchestration used by both CLI and GUI | Precedent for how a new filtering pipeline module should be structured (progress callback shape, `_tmp_dir` injection point for tests, cancel_event pattern) |
| `m4bmaker/encoder.py` | `encode()`, `write_concat_list()` — ffmpeg invocation, atomic `.partial` → `os.replace` output, `-progress pipe:1` parsing | Direct precedent for the render engine's AAC re-encode step and its progress/cancellation contract |
| `m4bmaker/m4b_editor.py` | `load_m4b_chapters()`, `save_m4b_chapters()` — read/rewrite an *existing* M4B's chapters/metadata via `ffprobe -show_chapters` / `ffmpeg -c copy` without re-encoding audio | Directly relevant: this is the only existing code path that opens an already-built M4B rather than a source folder. The Media Inspector (PRD §14.2) extends this pattern; note it currently assumes a single audio stream and would need extension for explicit primary-track detection (PRD D-09) |
| `m4bmaker/preflight.py` | `probe_file()`, `run_preflight()` — ffprobe-based per-file stream analysis (sample rate/channels/bitrate/codec), `lru_cache`d | Template for the eligibility preflight in PRD §6.1 |
| `m4bmaker/utils.py` | `find_ffmpeg()`/`find_ffprobe()`/`_which()` binary discovery, `subprocess_flags()` (Windows `CREATE_NO_WINDOW`), `get_temp_root()` (atexit-registered process temp dir), `sanitize_filename_component()` | `find_ffmpeg`/`_which` pattern is the direct template for a new `find_whisper()`/bundled-model discovery function (O-02); `subprocess_flags()` must be reused for any new subprocess call for consistent Windows behavior |
| `m4bmaker/gui/worker.py`, `m4bmaker/gui/queue_manager.py` | `QThread` subclasses emitting typed `Signal`s; careful documented lifecycle discipline around superseded workers and native `finished` vs. custom completion signals | Template for UI-thread-safe job execution shape. **Confirmed in-memory/session-only — no persistence.** New durable job orchestration (PRD §14.2, §11) must be built as a new layer; it can reuse this signal/thread shape for the UI-facing side but cannot reuse any existing persistence, because there isn't any (see §6 below) |
| `m4bmaker/gui/prefs.py` | JSON preferences at `platformdirs.user_config_dir()` | Only existing persistence mechanism in the app; too lightweight for job/catalog state but confirms the platform-appropriate storage root convention (PRD §12.4) |
| `m4bmaker/metadata.py` | `extract_metadata()` via `mutagen`, including MP4-specific `©nrt`/`©wrt` narrator atom handling | Template for reading required-metadata fields (PRD §6.3); narrator-atom quirk is directly relevant since output preservation must handle the same atom |
| `m4bmaker/cli.py`, `m4bmaker/__main__.py` | `argparse`-based CLI, `m4bmaker` entry point | Relevant to O-07 (CLI parity decision) — confirms the project's existing CLI is scriptable end-to-end, which is the bar O-07 must explicitly accept or decline meeting for the filtering feature |

## 6. Persistence — confirmed gap

The only persistence in the existing app is `gui/prefs.py`'s JSON blob (dark
mode, update-check toggle — two scalar keys). There is:
- No database.
- No durable job/task table.
- No resumable-across-restart state of any kind.

This confirms the PRD's own `Job Orchestrator` module (§14.2) and the
SQLite-backed persistence contract (§14.3) are **wholly new** subsystems,
not extensions of anything that exists today. §11.1 of the PRD has been
corrected accordingly (see PRD diff notes).

## 7. Packaging precedent (relevant to O-06)

- `m4bmaker.spec` (macOS) and `m4bmaker-windows.spec` (Windows) are PyInstaller specs that already bundle `ffmpeg`/`ffprobe` via `static-ffmpeg`, so end users never install ffmpeg themselves in the packaged app.
- `installer.iss` — Inno Setup script for the Windows installer.
- `RELEASING.md` documents macOS codesigning + notarization via a named keychain profile.

This is a strong, working precedent for bundling a second external binary
(a `whisper.cpp` build) the same way — same `_which()`-style discovery, same
PyInstaller data-file bundling approach. This lowers the risk on O-02/O-06
considerably: the packaging mechanism doesn't need to be invented, only
extended.

## 8. Direct-M4B input gap (PRD D-04)

Today the app's primary flow (`load_audiobook`) accepts a *source folder* of
individual audio files, not a single existing `.m4b`. The only code path
that opens an existing `.m4b` directly is `m4b_editor.py`, and it only reads
chapters/metadata — it does not decode/re-encode the audio stream. The new
Media Inspector must be built as a sibling to `m4b_editor.py`'s ffprobe-based
approach, extended to: multi-audio-track detection (§6.1 condition 4, D-09),
fingerprinting (§10.3/§10.4), and full-stream decode readiness for the
renderer. This is net-new code, not an extension of `load_audiobook`.

## 9. Conclusion — does the base architecture support this feature?

**Yes**, with the corrections above folded into the PRD. The existing
codebase gives real, working precedent for every low-level mechanic the
feature needs (ffmpeg subprocess invocation with progress/cancellation,
atomic output writes, chapter/metadata read-write, bundled-binary discovery
and packaging, Qt worker-thread patterns) and a clean module boundary to
build against (`pipeline.py`/`models.py` are stable and narrow). The
genuinely new subsystems — STT engine integration, durable job persistence,
word catalog storage, gain-envelope rendering — have no upstream code to
conflict with, so they can be added as new modules under a new subpackage
(see ADR-0004, O-08) without touching the existing conversion pipeline.

No constrained revision to the PRD is needed beyond the corrections applied
in this pass (O-01 resolution, §11.1 correction, O-07/O-08 additions).

**G0 exit evidence for human review:**
- Pinned SHA, stack, and license baseline: this document, §1–§2.
- Baseline test run: this document, §3 (1013/1013 passed, unmodified tree).
- ADR drafts for O-02, O-03, O-06: `0001-stt-engine-integration.md`, `0002-aac-encode-and-metadata-mapping.md`, `0003-platform-packaging-and-signing.md` (options only, no dependency selected — pending Contributor approval per PRD §17.1 rule 5).
