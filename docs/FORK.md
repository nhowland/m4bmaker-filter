# This fork

`m4bmaker-filter` is a fork of [sageframe-no-kaji/m4bmaker](https://github.com/sageframe-no-kaji/m4bmaker)
adding an offline offensive-speech filtering feature for M4B audiobooks.
Everything in this file and `docs/adr/`, `docs/PRD.md`, and `docs/TESTING.md`
is fork-specific; everything else in `docs/` (e.g. `architecture.md`) and
the rest of the repository describes the upstream conversion tool
unchanged, and is intentionally left untouched to keep future
`git fetch upstream && merge` operations clean (see `docs/adr/0004-module-layout.md`).

## Where to start

1. **`docs/PRD.md`** — the product requirements document. This is the
   authoritative planning document; if code and PRD disagree, that's a bug
   in one of them, not a judgment call to resolve silently.
2. **`docs/adr/`** — architecture decision records, numbered in the order
   they were opened:
   - `0000-g0-repository-discovery.md` — pinned upstream state, verified
     facts about the existing stack (this is the evidence base every other
     ADR cites).
   - `0001` (O-02), `0002` (O-03), `0003` (O-06) — proposed options for the
     three architecture decisions the PRD explicitly defers to human
     Contributor review. **None of these are finalized decisions** — no
     dependency has been added, no engine chosen. They exist to make the
     eventual decision well-evidenced, not to make it unilaterally.
   - `0004` (O-08) — the module layout decision this fork's code already
     follows (`m4bmaker/filter/`, additive-only).
   - `0005` — Job Orchestrator SQLite/JSON persistence split; `0006` —
     gain-envelope mechanism; `0007` — Renderer/Validator, proven against
     a real 13.5-hour audiobook; `0008` — G5 catalog persistence (JSON,
     not SQLite), UI structure (new window), and start-screen sequencing,
     decided directly by the product owner; `0009` — Model Manager window
     design (table + details panel, one download at a time, shared
     `gui/filter/workers.py`); `0010` — wizard shell design (horizontal
     stepper, consistent-height content pane, transcript-reuse auto-skip,
     chapter/section progress terminology), approved after an iterative
     wireframe review.
3. **`docs/TESTING.md`** — test conventions specific to the new code.

## Status

**G0 (Repository Discovery): complete.**
**G1 (Foundations): complete.** Domain schemas (`models.py`), the job state
machine (`jobs.py`), storage-path/atomic-write helpers (`storage.py`), and
AAC/M4B eligibility inspection with primary-track selection
(`media_inspector.py`).

**G2 (Catalog and scan): complete for its PRD-defined scope.** Added:
- `transcript.py` — native `.m4bt.json` transcript schema (PRD §10.3) and
  JSON round-trip. Schema only; no STT engine produces one yet.
- `catalog.py` — `CatalogService`: category/entry/profile CRUD, revisioning,
  archive-vs-hard-delete-by-reference, category-scoped duplicate detection,
  and profile snapshotting. In-memory; SQLite-backed persistence (PRD
  §14.3) is an explicitly deferred decision, not yet made.
- `matcher.py` — deterministic exact token/phrase matching against a
  transcript, honoring the 750ms phrase-gap rule (PRD §9.3).
- `interval_planner.py` — the six-step pad/clamp/sort/merge algorithm from
  PRD §8.3, with hit-to-interval provenance.
- `scan.py` — ties the above together: `Scan` (immutable hits + per-scan
  review decisions), `run_scan()`, and `build_report()` (the PRD §9.4
  review-screen counts, with attenuated-duration computed from the actual
  merged render plan rather than a naive sum).

ADR-0001 (STT engine = whisper.cpp as a bundled subprocess binary) is
**Accepted** by the product owner as of 2026-08-24; its remaining open
items (exact release pin, build/trust model, CPU-only vs. GPU) are still
unresolved and gate G3.

**G3 (Transcription): narrow spike complete.** Product owner confirmed:
pinned prebuilt whisper.cpp binaries (not a vendored build), CPU-only for
v1. Added `transcript_engine.py` — a whisper.cpp subprocess adapter proven
against a real `tiny.en` model and a real (synthetic, via macOS `say`)
speech fixture, including one real-binary integration test (opt-in via
`M4BMAKER_WHISPER_MODEL`, skipped by default/CI — no model file is
committed to the repo). Full findings, including two things that weren't
knowable without running it (whisper.cpp's own download script does no
checksum verification at all; `--no-gpu` is a runtime flag, not a separate
build), are in `docs/adr/0001-stt-engine-integration.md`'s "G3 spike
findings" section.

**G3 continued: Model Manager and chunking algorithm added.**
- `model_manager.py` — the known-model catalog (`base.en`/`small.en`, PRD
  §10.1) with **real** SHA-256 checksums this project computed itself (both
  models were actually downloaded from Hugging Face and hashed during the
  spike — see the ADR; whisper.cpp's own download script does no checksum
  verification of its own, so these values exist nowhere upstream).
  Checksum-verified, atomically-staged download over stdlib `urllib`
  (no new HTTP dependency), install-state checks, and removal — all with
  mocked-network tests plus real captured checksums as ground truth.
- `chunking.py` — `plan_chunks()`/`merge_segment_words()`: the pure
  timestamp-ownership algorithm for splitting a long source into
  overlapping chunks and deduplicating the overlap deterministically (PRD
  §11.3). Sanity-checked against **real** whisper.cpp output: a naive
  non-overlapping split of the spike's speech fixture silently dropped the
  word "hell" at a chunk boundary; the overlapping second chunk recovered
  it. Documented in the ADR as concrete motivation, not just a synthetic
  test case.

**G3: the durable pause/resume path is real and proven.** Made the
SQLite/JSON persistence decision explicit (ADR-0005 — SQLite for job
state/transition history/chunk-durability tracking; JSON artifacts stay
authoritative for transcript content) and built on it:
- `job_store.py` — SQLite-backed `JobStore`: job CRUD, state transitions
  re-validated against `jobs.py`'s transition matrix at the persistence
  boundary (an invalid transition is rejected and writes nothing), a
  `job_events` audit trail, and per-chunk commit tracking.
- `transcription_orchestrator.py` — the actual `TranscriptionJob`: chunks
  a source via `chunking.py`, extracts each chunk's audio with ffmpeg,
  transcribes it, trims each chunk down to its *owned* word range before
  persisting (so the stored transcript is already deduplicated — the
  Matcher needs no extra dedup pass), commits durably (JSON write, then
  SQLite row), and honors a pause request by stopping before the next
  chunk and leaving the job `PAUSED`.

**This was proven end-to-end for real, not just against mocks**: a real
~4.5s speech fixture, split into two real overlapping whisper.cpp calls,
paused after the first chunk, resumed via a **brand-new `JobStore` backed
by a fresh SQLite connection** (simulating an actual app restart, not just
reusing the same Python object) — and the resulting transcript correctly
contains "hell" exactly once, despite it sitting in the real overlap
window transcribed independently by both chunks
(`TestRealPauseResume::test_real_chunked_transcription_with_pause_and_resume`,
opt-in via `M4BMAKER_WHISPER_MODEL`, passing).

**Still not built:** a Model Manager UI (only the backend service exists);
throughput/memory benchmarking; multi-instance database locking (ADR-0005
flags this as an open gap for G5); render-side persistence (PRD O-05,
separate from this transcription-durability work, a G4 concern).

Cumulative: 266 tests in `tests/filter/` + 3 in `tests/test_utils.py`,
97%+ coverage on `m4bmaker/filter/`, `black`/`flake8`/`mypy` clean, full
suite — 1282 passing, 2 correctly skipped (the two opt-in real-binary
tests).

**G4: gain-envelope mechanism resolved by direct empirical spike.** See
[docs/adr/0006-gain-envelope-implementation.md](adr/0006-gain-envelope-implementation.md)
for the full trail — five approaches tested, four ruled out with hard
data, two rounds of self-caught measurement error along the way.
**Resolution:** generate the gain envelope as its own sample-accurate PCM
signal directly in Python (never via an ffmpeg expression), apply with a
single `amultiply` pass. Real measurements: this solves gating, genuinely
smooth configurable-floor fades, *and* interval-count scaling all at
once — ~30x faster than the next-best approach, and the only one of five
tested that didn't degrade or fail approaching 1000 intervals (effectively
constant-time regardless of interval count). The naive-seeming
`atrim`+`concat` approach (exact sample-accurate boundaries) turned out to
be operationally unusable past ~300 intervals — exactly the kind of
reversed intuition a real spike exists to catch before it's built into
production code.

**G4 complete: Renderer and Validator implemented and proven against a
real ~13.5-hour production audiobook**, not a synthetic fixture. See
[docs/adr/0007-renderer-and-validator.md](adr/0007-renderer-and-validator.md).
`renderer.py` (extract → generate envelope → `amultiply` → encode/mux)
and `validator.py` (duration/chapters/metadata/attenuation checks, with
tolerances set from real measurement rather than the earlier ADR-0002
placeholder) ran the full pipeline end-to-end on a real, user-owned,
non-DRM `.m4b` (verified empirically decodable with no DRM handling
needed — not assumed) and **passed with zero errors and zero warnings**:
exact 0ms duration match, all 50 real chapters exactly preserved, full
metadata and cover art preserved, and a real narration passage measured
at -17.95dB before attenuation and -97.6dB after, with regions
immediately outside the interval unchanged. Total render time for the
entire 13.5-hour book: **615 seconds (~10.25 minutes)** — extraction
31.3s, envelope generation 2.3s, `amultiply` 10.0s, AAC encode+mux 587.7s.

Real testing surfaced and fixed three things no synthetic fixture would
have caught: (1) the classic WAV format's 4GiB size ceiling — this
book's PCM is ~8.6GB per stage, so every intermediate file is raw
headerless PCM instead; (2) `amultiply`'s two inputs must be byte-exact
in length or it silently truncates — envelope length is now derived from
the real extracted PCM's actual byte count, not re-estimated from
duration; (3) the most significant one — mapping cover art directly from
the source container (`-map 1:v?`) alongside `-map_chapters` from that
same input **silently corrupted the first several chapter titles**
(swapped with unrelated later titles, start times unaffected, no error
raised). Fixed by extracting the cover to a standalone image first (the
base project's own already-proven `cover.py` pattern) and supplying it
as an independent third input — exactly how `encoder.py` already does
it. A regression test for this exact defect is in `test_validator.py`.

Not yet built: any UI (G5); render-stage resumability (PRD O-05 —
current renderer is all-or-nothing per PRD §11.4's "mandatory fallback,"
which a real 615s/13.5-hour render makes a reasonable MVP position); a
persisted `filter-report.json` artifact (the `ValidationReport` return
value exists, writing it to disk is G5/orchestration work).

**Chapter-aware transcription chunking** (`chunking.py`, `transcription_orchestrator.py`):
the base project already has a proven pattern for chapter-based audio
splitting — `gui/worker.py`'s `SplitWorker` extracts each chapter via
`ffmpeg -ss/-to -c copy`, using `chapter[i].start` → `chapter[i+1].start`
(or total duration for the last chapter) as the boundary rule. `plan_chunks()`
now reuses that exact boundary logic when chapter start times are
supplied (the natural source is `MediaManifest.chapters` from the Media
Inspector, G1) — a real chapter break is almost always a natural pause,
so splitting there is far less likely to cut a word than an arbitrary
fixed-duration cut. This isn't a replacement for fixed-duration
planning: a chapter longer than the pause-latency budget (PRD §11.3's
≤30s target) is subdivided using the same fixed-step logic the no-chapter
path already used, and a source with sparse/no chapter markers (common
for the non-DRM, possibly self-produced M4Bs this app targets) falls back
to it entirely. Overlap padding is still applied at chapter boundaries
too — there's no verified evidence a real-world chapter marker never
lands mid-word, so this stays a safety margin, not an assumption.
`run_transcription_job()` takes an optional `chapter_start_times_ms` and
passes it straight through; omitting it preserves the original
uniform-windowing behavior exactly (10 new chunking tests + 2 orchestrator
tests confirm both the chapter-aware paths and that fallback).

## Current overall status (2026-08-25)

**G0-G4 complete**, each with real proof, not just passing mocked tests:
real whisper.cpp transcription, real chunked pause/resume across a
simulated app restart, and now a real ~13.5-hour production audiobook
rendered and validated end-to-end with zero errors. 1333 tests passing
project-wide (317 in `tests/filter/`), 98% coverage on `m4bmaker/filter/`,
`black`/`flake8`/`mypy` clean throughout, 11 commits, nothing in the
original conversion tool touched.

Not yet built: any UI (G5) — every gate so far is backend/service work
with no PySide6 screens wired up yet; catalog persistence (in-memory
only by design); resume compatibility verification (documented as the
caller's responsibility, not yet implemented); render-stage
resumability (deferred, see G4 above); CLI parity (O-07, never decided);
multi-instance database locking (ADR-0005's flagged gap); real
throughput/memory benchmarking on named reference hardware (PRD §13.1,
deliberately deferred); anything G6-only (human listening review,
accessibility, security review, packaging/signing).

## G5: Catalog persistence and first screen (2026-08-25)

Four decisions the product owner made directly before any G5 code was
written — recorded in full in
[docs/adr/0008-catalog-ui-and-persistence.md](adr/0008-catalog-ui-and-persistence.md):
catalog data persists as a JSON file (`catalog_store.py`, matching
`gui/prefs.py`'s pattern — not SQLite, which stays scoped to job/chunk
state per ADR-0005); the new UI lives in a separate window launched from
`MainWindow`, not embedded in its existing layout; the first screen
built is catalog management, since both the Model Manager and the
transcribe/scan/review/render wizard assume a non-empty catalog already
exists; and wireframing is deferred to the wizard shell and scan-review
screen specifically, not applied to this conventional CRUD screen.

Built:
- `catalog.py`: `export_all()`/`from_records()` bulk accessors, keeping
  `CatalogService` itself storage-agnostic — all 24 pre-existing tests
  still pass unchanged.
- `catalog_store.py` (new): JSON round-trip of
  categories/entries/profiles/attenuation/archived items/revisions;
  returns a fresh empty service (never raises) on a missing or corrupted
  file. 12 tests.
- `m4bmaker/gui/filter/catalog_window.py` (new subpackage): `CatalogWindow`,
  a two-pane category/word CRUD screen, persisting on every mutation
  (no separate Save button). Archive-vs-hard-delete needs no UI branching
  — the service already decides internally. Wired into `MainWindow` via a
  new "Tools" menu, following `QueueWindow`'s exact lazy-create/
  `apply_stylesheet`/`closeEvent` pattern. Confirmed via `git stash`
  comparison to introduce zero new lint/type issues in `window.py` beyond
  its pre-existing debt.

**A disclosed limitation:** this sandboxed environment cannot grant the
macOS Screen Recording or Accessibility permissions needed to screenshot
or drive the live desktop app — both `screencapture` and AppleScript/
System Events automation were attempted and both failed. Unlike the web
and iOS surfaces used elsewhere in this project, the live `CatalogWindow`
could not be visually self-verified. The substitute:
`tests/gui/filter/test_catalog_window.py` builds the real widget tree
under `QT_QPA_PLATFORM=offscreen` and drives it exactly as a User would
(selecting rows, checking boxes, editing cells, clicking buttons),
asserting on both resulting widget state and the underlying
`CatalogService` — 20 tests, `black`/`flake8`/`mypy` clean.

Full suite: **1367 passing, 2 correctly skipped**, project-wide.

## G5: Model Manager window (2026-08-25)

The second G5 screen. `model_manager.py`'s backend (known-model catalog,
checksum-verified download, install-state checks, removal — all built
during the G3 spike) had no UI until now. Full design rationale in
[docs/adr/0009-model-manager-window.md](adr/0009-model-manager-window.md).

Built:
- `m4bmaker/gui/filter/workers.py` (new): `ModelDownloadWorker`, a
  `QThread` wrapping `download_model()`, mirroring the base project's
  `ConvertWorker` exactly (progress/result_ready/cancelled/error
  signals, `threading.Event`-based cancellation). Starts this file as
  the shared home for filtering-feature UI workers, matching how the
  base project keeps every `QThread` worker in one `gui/worker.py`
  rather than one file per widget.
- `m4bmaker/gui/filter/model_manager_window.py` (new): `ModelManagerWindow`.
  A table (Model/Description/Size/Status) covers the scannable PRD
  §10.1 fields; a details panel below it shows the selected model's
  full source URL and SHA-256 checksum (too long for a table cell); an
  engine-version label at the top reflects the real installed
  whisper.cpp binary via `transcript_engine.get_whisper_version()`. Only
  one download runs at a time. Wired into `MainWindow`'s Tools menu as
  "Manage Models…", following `CatalogWindow`'s exact
  lazy-create/`apply_stylesheet`/`closeEvent` pattern.
- 26 tests (5 worker, 21 window), `black`/`flake8`/`mypy` clean.
  Confirmed via `git stash` comparison to introduce zero new lint/type
  issues in `window.py`.

**Visually verified against the live app**: launched it, opened Tools →
Manage Models…, and confirmed the window renders as designed —
including an unplanned real-world proof point, since this development
machine actually has whisper.cpp installed: the window's engine label
correctly read "Engine: whisper.cpp 1.9.2" from the live binary rather
than a stubbed value.

Full suite: **1395 passing, 2 correctly skipped**, project-wide.

## G5: Wizard shell wireframe, approved (2026-08-25)

Per ADR-0008's decision to wireframe the wizard shell before writing any
code for it, built an interactive, self-contained HTML wireframe and
iterated on it directly with the product owner over several review
rounds until approved. Full design rationale, including everything
corrected along the way, in
[docs/adr/0010-wizard-shell-design.md](adr/0010-wizard-shell-design.md);
the approved wireframe itself is archived at
[docs/design/wizard-shell-wireframe.html](design/wizard-shell-wireframe.html)
(open directly in a browser — no build step, no server).

Settled by the review:
- **Shell structure**: horizontal 8-step stepper (Source → Transcript →
  Transcribe → Profile → Scan → Review → Render → Complete) across the
  top of the window; content pane below; Back/Continue footer anchored
  to the bottom.
- **Stepper visuals**: equal-width/height cells regardless of state or
  label length; state carried by color and weight alone (no extra status
  captions); done = green check, current = bold accent, locked = gray;
  a solid connecting line runs through all eight circles.
- **Content pane sizing**: measured from the tallest step's actual
  rendered content rather than a guessed value, so every step shares one
  consistent window height and none of them needs to scroll internally.
- **Auto-skip**: reusing a compatible saved transcript jumps straight
  from Transcript to Profile — Transcribe is marked done-but-skipped
  (a distinct glyph, not a plain checkmark) rather than visited as an
  empty step. Chosen over merging Transcript+Transcribe into one step,
  which was considered and rejected (pros/cons in the ADR).
  Backend note: nothing to build yet — this is shell/UI-flow behavior
  layered on top of the existing G2/G3 transcript-compatibility and
  model-selection backend, which already supports the check.
- **Progress terminology**: "Chapter *N* / *M*" only when a chunk maps
  1:1 to a real chapter (per `chunking.py`'s actual chapter-*aware*, not
  chapter-*equivalent*, planning); "Section *N* of *M*" otherwise. Flagged
  in the ADR as a real data requirement for the eventual Transcribe step
  (needs per-chunk real-chapter mapping), not just copy.

Not yet built: any of this in PySide6 — the wireframe is HTML/CSS/JS
only, deliberately low-fidelity and in its own "blueprint" visual
language distinct from `gui/styles.py`, so it reads as a structural
draft rather than a finished screen. The Scan-Review screen still needs
its own wireframe pass (also called for in ADR-0008) before either
screen's real implementation begins.
