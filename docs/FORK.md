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
     wireframe review; `0011` — Catalog term masking (category- and
     entry-level, OR-composed), decided directly by the product owner
     and shipped; `0012` — the wizard shell and Review step's real
     PySide6 implementation, porting ADR-0010/0011's approved wireframe
     design to working, tested, visually-verified code; `0013` — the
     Source step's real implementation, plus the storage-estimate and
     compatible-transcript-lookup backend functions it needed; `0014` —
     the Transcript step's real implementation (reuse-a-transcript vs.
     choose-a-model, inline model download), plus the shared
     `DownloadCoordinator` it needed once download moved inline rather
     than linking out to Model Manager; `0015` — the Transcribe step's
     real implementation (chapter-aligned chunking and GPU acceleration
     put into real code for the first time, real pause/resume/retry via
     the job state machine), live-verified with a real GPU-accelerated
     transcription that actually completed; `0016` — the Profile editor's design (a modal dialog owned by the wizard, not a third `CatalogWindow` pane — that pane idea was this fork's own false start, corrected against `catalog_window.py`'s pre-existing docstring before any code was written against it), covering the checkable category/entry tree and the attenuation-settings form; `0017` — the Profile step and editor's real implementation, plus a thoroughly investigated, disclosed local-test-environment limitation (a full local `pytest` run spanning both GUI and non-GUI tests can segfault; this project's actual CI command already excludes `tests/gui/` and is unaffected); `0018` — the Scan step's real implementation (a plain indeterminate-progress worker, not a job-orchestrator-backed one — no chunk boundary exists to checkpoint at, and no evidence yet that one's needed), wiring Profile's chosen profile and whichever real Transcript exists straight into a real scan, and that scan straight into Review, which had been waiting on it since before Scan itself existed; `0019` — the Render step's real implementation (a real determinate progress bar, unlike Scan's — render() already had four real stages to report; no Cancel button, since there's no real stop mechanism underneath it to offer one for), closing three small backend gaps the wireframe pass itself flagged (a default output-path helper, a ported bitrate auto-selection function, and a real persisted filter-report.json ADR-0007 had explicitly deferred to this exact round) and giving Review a small new public getter so Render could finally read its live render plan; `0020` — the Complete step's real implementation, the last of the eight PRD §7.2 stages — a real single "passed validation" statement instead of the original sketch's invented per-check deltas, the "Done closes the window" decision settled directly with the product owner rather than assumed, and one real, confirmed gap (WizardWindow's documented-but-never-built mid-Transcribe/mid-Render close confirmation) flagged for its own follow-up rather than folded in; `0021` — that follow-up, implemented: a real `closeEvent`, asymmetric on purpose — mid-Transcribe gets asked to pause (real, resumable progress) since it can be; mid-Render can only be warned about, since RenderWorker has no stop mechanism at all (ADR-0019's own deliberate choice).
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
items (exact release pin, build/trust model) are still unresolved and gate
G3. CPU-only vs. GPU was resolved 2026-08-25 — see below.

**G3 (Transcription): narrow spike complete.** Product owner confirmed:
pinned prebuilt whisper.cpp binaries (not a vendored build). (CPU-only for
v1 was the original call here — reversed 2026-08-25, see below.) Added
`transcript_engine.py` — a whisper.cpp subprocess adapter proven
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
draft rather than a finished screen.

## G5: Review step fully wireframed, plus a real Catalog masking feature (2026-08-25)

The Scan-Review screen wireframe pass ADR-0008 called for turned out to
live inside the same wizard-shell wireframe rather than a separate file
— the Review step (previously a static placeholder table) is now a
fully interactive, real-data-driven panel in
[docs/design/wizard-shell-wireframe.html](design/wizard-shell-wireframe.html),
iterated through several rounds directly with the product owner:

- **Hits and Render Plan as tabs**, not both inline at once and not
  popped into separate windows (a modal-window split was considered and
  explicitly rejected — see ADR-0010-adjacent discussion in this
  session — because it would break the live coupling between excluding
  a hit and watching its render interval change). Hits is the default,
  primary tab; Render Plan is explicitly the secondary "you probably
  don't need this" view — filters, bulk include/exclude, and a table
  with its own bounded internal scroll live in Hits; a merged-interval
  list with real provenance (which raw hits merged into which interval)
  lives in Render Plan, both driven by one shared dataset so switching
  tabs never loses state.
- **The interval merge is the real algorithm**, not decoration: PRD
  §8.3's MVP defaults (60ms lead / 80ms tail padding, 20ms merge
  adjacency) run against whichever demo hits are currently included,
  live, in the browser — verified by actually executing the extracted
  logic (macOS's built-in JavaScript engine, not just visual inspection)
  before ever publishing it for review.
- **Redacted example terms use real asterisk masking** (first + last
  letter, middle asterisked — "value" → "v\*\*\*e"), applied only to
  categories/words flagged for it, not blanket-applied to every term.

That last point led directly to a real, shipped feature — the wireframe
was demonstrating a display rule with no actual User-facing control
behind it, and the product owner asked for one. See
[docs/adr/0011-catalog-masking.md](adr/0011-catalog-masking.md):

- `Category.mask_all_terms` / `CatalogEntry.mask` (fork-specific fields,
  not in PRD §9.1's table), composed by OR via the new
  `CatalogService.is_masked(entry_id)` — a category-wide mask and a
  per-word mask are independent, additive switches, not an
  override/inheritance pair.
- `CatalogWindow` (ADR-0008) gained a "Mask" checkbox column in both its
  category and word tables, mirroring "Enabled"'s exact pattern.
- The wireframe's own demo data was updated to match: Slurs is now
  category-masked and one Profanity word carries an explicit per-word
  mask, both read through the same OR-composition the real
  `is_masked()` uses, instead of a hardcoded category-name check.
- 13 new tests (6 `catalog.py`, 2 `catalog_store.py` — including
  loading a pre-masking-schema `catalog.json` to confirm the new fields
  default cleanly with no migration needed — and 5 `catalog_window.py`),
  `black`/`flake8`/`mypy` clean, **visually verified against the live
  app**.

Full suite: **1409 passing, 2 correctly skipped**, project-wide.

Not yet built: the rest of the wizard's per-step screens in PySide6
(only the shell and Review are wireframed to this level of detail);
a bulk "mask every entry in this profile" action — masking is set
per-category or per-word only, as decided, with no profile-level
shortcut.

## G5: Wizard shell and Review step — real PySide6 code (2026-08-25)

The wizard shell and Review step move from wireframe to real, working
code. Full rationale in
[docs/adr/0012-wizard-shell-and-review-implementation.md](adr/0012-wizard-shell-and-review-implementation.md).

New subpackage `m4bmaker/gui/filter/wizard/`:
- `stepper.py` — `StepperWidget`, a direct port of the approved
  wireframe stepper (equal-sized cells, one continuous connecting line,
  state via color/weight only, including the exact "furthest-reached
  but not active" edge case the wireframe review caught).
- `wizard_window.py` — `WizardWindow`, the shell: stepper, a
  `QStackedWidget` content pane (which — unlike the wireframe's own
  runtime-JS height-measurement workaround — sizes itself to its
  largest child natively, no measurement trick needed), Back/Continue
  footer.
- `review_step.py` — `ReviewStep`, wired to the real backend rather
  than a demo dataset: `scan.build_report()` for the stat strip,
  `interval_planner.build_render_plan()` (the same function
  `build_report()` itself calls) for the Render Plan tab, and
  `CatalogService.is_masked()` (ADR-0011) for masking. Two departures
  from the wireframe, both toward conventions already proven elsewhere
  in this codebase: one checkable "Included" column instead of two
  Include/Exclude buttons (mirrors `CatalogWindow`'s pattern), and bulk
  actions read Qt's native multi-row table selection instead of a
  separate selection-checkbox column.
- `placeholder_step.py` — the other six steps (Source, Transcript,
  Transcribe, Profile, Scan, Render, Complete) get a plain "isn't built
  yet" stand-in, since only the shell and Review have been through a
  wireframe review. The wizard is fully navigable end-to-end today
  regardless — Back/Continue and the stepper's click-to-revisit all
  work against the placeholders exactly as they will once each step is
  built for real.

New backend piece `scan.TranscriptWordIndex`: an O(1)-per-hit lookup
for PRD §9.4's "up to five recognized words before/after" context
requirement, built once per transcript rather than scanned per hit
(relevant at the ~150k-word scale a 20-hour book's transcript reaches).

Wired into `MainWindow`'s Tools menu as "Filter Audiobook…", above a
new separator from Catalog/Model Manager since it's the fork's central
feature rather than a supporting tool.

50 new tests, all against real backend objects (a real `CatalogService`,
`Transcript`, and a `Scan` produced by the actual matcher — not
hand-built fake hit lists), `black`/`flake8`/`mypy` clean. Two real bugs
caught only by testing: `QTableWidget.selectRow()` replaces rather than
accumulates selection even in `ExtendedSelection` mode (confirmed
empirically), and holding a `QTableWidgetItem` reference across a
checkbox toggle fails because every toggle rebuilds the whole table —
both fixed in the tests themselves once understood, not product bugs.

**Visually verified against the live app**: launched it, opened
Tools → Filter Audiobook…, confirmed the shell renders correctly on
the first step, clicked Continue five times, and confirmed the stepper
correctly shows five green checkmarks with filled connecting lines
while Review's real title/subtitle/stat strip/tabs/filters/table all
render as designed.

Full suite: **1470 passing, 2 correctly skipped**, project-wide.

Not yet built: the other six steps' real designs and implementations;
the Scan step, so nothing calls `ReviewStep.set_scan()` in the running
app yet (verified directly via the widget's own tests instead); a more
prominent wizard entry point than a Tools-menu item, given it's the
fork's central feature rather than a supporting tool like Catalog/Model
Manager.

## G5: Source step — real PySide6 code (2026-08-25)

The Source step (PRD §7.2 stage 1) moves from placeholder to real,
working code — the wizard's second fully-built step after Review. Full
rationale in
[docs/adr/0013-source-step-implementation.md](adr/0013-source-step-implementation.md).

Two new backend functions it needed:
- `renderer.estimate_storage_bytes(manifest)` — sums the three raw-PCM
  scratch stages that exist on disk simultaneously at `render()`'s peak
  (source/envelope/filtered) plus the final AAC output sized from the
  source's own bit rate, since the actual render bitrate isn't chosen
  until the not-yet-built Render step. Cross-checked against ADR-0007's
  real 13.5-hour production fixture (~8.6GB/stage, ~26GB total).
- `transcript.find_compatible_transcript(fingerprint, transcripts_dir=None)`
  — scans saved transcripts for a fingerprint match, requiring
  `TranscriptStatus.COMPLETE` (PRD §10.4 — a draft/partial/failed
  transcript isn't something the wizard can hand off to Scan) and
  returning the most-recently-saved match when more than one qualifies.

New `SourceStep` (`gui/filter/wizard/source_step.py`): a single-file
`.m4b` picker (drag-and-drop plus a native macOS file panel, scoped
down from `gui/widgets.py`'s `FolderDropZone` pattern) feeding a new
`MediaInspectWorker(QThread)` that runs `media_inspector.inspect()`
(an `ffprobe` call) off the UI thread — using `utils.find_binary()`,
not `utils.find_ffprobe()`, since the latter's `sys.exit()` on a
missing binary would be fatal to the whole app from a background
thread. The result renders as either an eligible panel (every real
`MediaManifest` field — audio track, duration, chapters, metadata,
cover art, storage estimate, saved-transcript status) or an ineligible
panel listing **every** failure reason at once, not just the first.

Caught and fixed a real gap in `WizardWindow._on_continue()`: it only
ever checked the Continue button's own `enabled` state, never
`step.can_advance()` directly — invisible until Source became the
first step that can genuinely block advancement. Now defended directly
in `_on_continue()` itself.

38 new tests, all against real backend objects (real `MediaManifest`/
`Transcript` instances, not display-only fakes), `black`/`flake8`/
`mypy` clean. One test-safety bug caught only by running the new tests:
several tests were unknowingly starting a real background
`MediaInspectWorker` thread against a nonexistent file, which could
race a test's own manual result-delivery call — fixed by patching the
worker class at the point of selection, the same pattern
`test_workers.py` already used for worker classes generally.

**Visually verified against the live app with real `ffprobe` inspection,
not mocked**: synthesized a real AAC-in-M4B file with `ffmpeg` (stereo,
two chapters, cover art, title/artist metadata) and selected it via the
native Browse panel — every field rendered correctly and Continue
enabled. Synthesized a second file with an MP3 stream in a mismatched
container and confirmed the ineligible panel listed both simultaneous
failure reasons at once, with Continue staying disabled.

Full suite: **1509 passing, 2 correctly skipped**, project-wide.

Not yet decided: how Source's manifest will eventually feed the
not-yet-built Transcript step (nothing consumes it yet — each wizard
step still holds its own state independently); Transcript's own real
design, which hasn't had a wireframe pass.

## G5: Transcript step — real PySide6 code, plus a shared download guard (2026-08-25)

The Transcript step (PRD §7.2 stage 2) moves from placeholder to real,
working code, wired directly to Source since the two are now adjacent
real steps. Full rationale in
[docs/adr/0014-transcript-step-implementation.md](adr/0014-transcript-step-implementation.md).

Two modes, neither reachable via a default Continue click: a
**"found"** panel when `find_compatible_transcript()` (ADR-0013)
matches — Continue stays disabled here on purpose, forcing an explicit
"Use existing →" or "Transcribe again instead" choice — and a
**"choose a model"** panel showing both real `model_manager.KNOWN_MODELS`
catalog entries with their real install state, one chooser covering
both "nothing installed" (PRD §7.3's first-run state) and "something's
already installed," not two separate screens.

Model download happens inline on the model's own row — the wireframe
review's one open, explicitly-flagged question (link out to Model
Manager, or embed the download here) resolved toward inline, reusing
`ModelDownloadWorker` (ADR-0009) as-is. That decision immediately
opened the gap the wireframe review had already flagged: ADR-0009's
"one download at a time" guard was scoped to `ModelManagerWindow`'s own
state, not shared, so nothing stopped both windows racing to write the
same model file if both were open at once. Closed with one new,
process-wide `DownloadCoordinator` (`gui/filter/workers.py`) both
windows now check before starting a download.

"Use existing →" skips Transcribe entirely — `TranscriptStep.
reuse_requested` drives `WizardWindow` to jump straight to Profile and
mark Transcribe skipped, using `StepperWidget`'s `»`-glyph rendering
that's existed since ADR-0012 but had nothing to exercise it until now.
Visiting Transcribe for real afterward clears the skip mark.

34 new tests, all against real backend objects and a real `tmp_path`
filesystem, `black`/`flake8`/`mypy` clean.

**Visually verified against the live app with a real network
download**: advanced through the real Source step into Transcript,
confirmed the empty model chooser, clicked Download on `base.en` — a
real HTTPS request to the real pinned URL completed and
checksum-verified within the screenshot round-trip, the row flipped to
installed, and Continue enabled immediately. Continuing landed on
Transcribe (still a placeholder) with the stepper correctly showing two
green checkmarks and no skip glyph, confirming the normal, not-skipped
path renders correctly alongside the skip path (covered by dedicated
tests). This left a real, correctly-verified `base.en` model installed
at this machine's actual models directory — not test pollution, a
genuine usable install.

Full suite: **1546 tests total**, up from 1511.

**A pre-existing test-environment issue is now materially worse, and
confirmed unrelated to this round's code**: the full bare `pytest -q`
run now segfaults in the offscreen Qt platform plugin far more often
than the "~1 in 9" rate ADR-0013 disclosed — every attempt today
crashed, at a different test each time. Isolated via `git stash` back
to the prior commit: the identical bare full-suite command still
segfaults there too, on completely untouched code, while no
explicitly-assembled test-ID-list reproduction — including the exact
same tests in the exact same order — ever crashed. Every individual
file and every reasonably-sized combination passed cleanly and
repeatedly. Conclusively a pre-existing, environment-level instability
at full-project scale, not a regression this round introduced, but
flagged here since it's become more disruptive, not less.

Not yet decided: how `TranscriptStep.chosen_model`/`.compatible_transcript`
will feed the not-yet-built Transcribe step; Transcribe's own real
design, which hasn't had a wireframe pass.

## GPU acceleration reverses ADR-0001's CPU-only-for-v1 call (2026-08-25)

While designing Transcribe's real progress/chunking behavior (still
pre-implementation — see ADR-0014's "not yet decided" above), a
provisional `chunk_ms`/`overlap_ms` question led to a real question about
whisper.cpp throughput, which the product owner used to revisit ADR-0001's
original CPU-only-for-v1 recommendation — always flagged there as
provisional ("keep the benchmark story simple, revisit post-MVP"), not a
technical requirement.

Real benchmark, not estimated: two real chapters from the actual 13.5-hour
reference book (18.2 min and 28.8 min — the book's median and longest
chapter respectively), `base.en`, this dev machine (Apple M4 Pro/Metal),
CPU (`--no-gpu`) vs. GPU (flag omitted). **4.1-4.2x speedup** (RTF ~0.05 →
~0.013), and GPU peak memory came in *lower* than CPU, not higher.
Extrapolated to the full book: ~40 minutes CPU-only vs. ~10-11 minutes
with GPU. Full figures, method, and disclosed limits (Apple Silicon only —
Windows/CUDA and Linux paths unverified; `small.en` benchmarked
separately, see below) are in
`docs/adr/0001-stt-engine-integration.md`'s "GPU acceleration — reversed
with real benchmark evidence" section; ADR-0003's corresponding packaging
note is updated to match.

**Decision:** stop always passing `--no-gpu`. GPU is used when `ggml`
finds a compatible backend, CPU is the automatic fallback — no new
detection code needed, no packaging change (still one binary per
platform), since `--no-gpu` was already established as a plain runtime
flag on the standard build, not a separate build to source.

Not yet decided at the time: whether/how to surface which backend is
active in the UI (a diagnostics nicety, not required for the speedup
itself); verification on non-Metal hardware. None of this is implemented
in code yet — Transcribe itself hasn't been built — this round is the
requirements/ADR decision the eventual implementation will follow.

## small.en added to the GPU/CPU benchmark (2026-08-25)

Same method, same two real chapters, extended to the other approved
model:

| Model | Backend | RTF (avg) | Peak mem (avg) | Full book (13.5h) |
|---|---|---|---|---|
| base.en | CPU | 0.054 (≈18.5x) | ~779 MB | ~44 min |
| base.en | GPU | 0.013 (≈77x) | ~625 MB | ~11 min |
| small.en | CPU | 0.153 (≈6.5x) | ~1.3 GB | ~2h 4m |
| small.en | GPU | 0.027 (≈37x) | ~1.08 GB | ~22 min |

Two findings worth keeping: **GPU speedup is larger for the bigger model**
(5.6x for `small.en` vs. 4.1-4.2x for `base.en`) — more compute to
offload means the fixed per-call overhead matters proportionally less.
And `small.en`'s real cost relative to `base.en` shrinks on GPU (~2.8x
slower on CPU, ~2.1x slower on GPU) — on this machine, `small.en` on GPU
(~22 min for the full book) finishes faster than `base.en` did on
CPU-only (~44 min). Full figures and method in
`docs/adr/0001-stt-engine-integration.md`'s "GPU acceleration" section,
which now covers both models.

Still open: verification on non-Metal hardware. Nothing implemented in
code yet — same status as the rest of Transcribe's design work.

## Chunking strategy revised: chapter-sized, pause-latency deprioritized (2026-08-25)

The same investigation that led to the GPU reversal above also surfaced a
real architectural fact: `run_whisper()` reloads the full model from disk
on every chunk invocation, no persistent model across chunks. A small,
pause-latency-driven chunk size (30s, this fork's own provisional default
until today) multiplies that fixed cost nearly 2,000 times over for the
13.5-hour reference book. Product owner's call: User-initiated pause
mid-transcription is an edge case, not worth optimizing chunk size around
at that cost — durability against a *crash* (a separate PRD D-17 concern)
isn't being deprioritized, just re-bounded to roughly one chapter's worth
of lost work instead of one small chunk's, explicitly accepted as an
acceptable trade.

**PRD D-16, §11.3, and §15.3's pause-latency acceptance criterion are
revised** in `docs/PRD.md` — the first edits to that file since the fork's
initial commit, since these are decisions stated directly in the PRD's
own text (unlike the GPU choice above, which was always deferred to ADR
O-02 and never asserted as PRD text in the first place). Full rationale in
`docs/adr/0001-stt-engine-integration.md`'s "Chunking strategy" section.

**Empirically validated with the same real chapters** already extracted
for the GPU benchmark — the median (18.2 min) and longest (28.8 min)
chapters in the actual reference book, plus the shortest (17.4s front
matter) — each run as one whisper-cli call, no external subdivision. All
three completed cleanly: stable ~0.05 RTF across the full range, memory
growing sublinearly (not proportionally) with duration, and spot-checked
transcript accuracy holding up through an 18-minute single call. One test
serving two decisions, not duplicated effort.

Two provisional constants (not yet implemented — Transcribe isn't built
yet): a **45-minute chapter-subdivision ceiling** (headroom above the
~29-minute longest chapter actually tested, not itself tested) and a
**15-minute/10s-overlap chapterless-source fallback** (matched to this
book's own mean chapter length, not independently benchmarked). Same
provisional status the original 30s default had — real data narrowed
them, not a formal G3 benchmark sign-off.

## G5: Transcribe step — real PySide6 code (2026-08-25)

The Transcribe step (PRD §7.2 stage 3) moves from placeholder to real,
working code — the wizard's fourth fully-built step, and the one that
puts ADR-0001's chapter-aligned chunking and GPU-acceleration decisions
into real code for the first time. Full rationale in
[docs/adr/0015-transcribe-step-implementation.md](adr/0015-transcribe-step-implementation.md).

`chunking.py` gains the production constants (45-min chapter ceiling,
15-min chapterless fallback, 10s overlap) and two pure helpers:
`default_chunk_params()`/`default_chunk_plan()` (the one place those
constants get used) and `chapter_for_chunk()` (the "Chapter N of M"
correlation the Transcript-round wireframe needed — unambiguous by
construction, since a chunk's owned range never straddles two chapters).
`transcript_engine.py` stops always passing `--no-gpu` — ADR-0001's GPU
reversal, real code now, not just a decision on paper.
`transcription_orchestrator.run_transcription_job()` gains an optional
`progress_callback` so a GUI worker gets live progress without polling
the job store from a second connection.

New `TranscribeWorker` (three start modes — fresh/resume/retry, each
doing the specific `JobState` transition PRD §11.2's matrix requires
before calling the orchestrator) and `TranscribeStep` (four real
states — Ready/Running/Paused/Needs attention — plus Completed, wired
directly to Transcript's own output). Checks the real job store for an
existing incomplete job on entry, so a resumed session actually resumes
instead of offering to start fresh — the real substance behind "durable
across app restarts." A re-entry guard (own tests caught this): calling
the entry point again with the same source while already running or
completed is a no-op, not a silent reset of real in-flight progress.

41 new tests, `black`/`flake8`/`mypy` clean. Full suite: **1587 passing,
2 correctly skipped**, project-wide.

**Visually verified against the live app with a real GPU-accelerated
transcription that actually completed.** First attempt, against this
fork's existing tiny eligibility fixture, surfaced a genuine unexpected
error (`whisper-cli` produced no output against that fixture's
near-empty encoded audio, a known artifact already documented in
ADR-0013) — the **Needs Attention** state handled it exactly as
designed: real error message, Retry and Cancel Job both present, no
crash, and Retry genuinely re-ran (a fresh temp path each time) rather
than replaying a stale result. Built a second, real-speech `.m4b` (macOS
`say`, same approach ADR-0001's own G3 spike used) with two real
chapters and ran it start to finish: real chapter-aligned chunking, real
`ffmpeg` extraction, real GPU-accelerated `whisper-cli` inference, a real
`.m4bt.json` written and read back — `status: complete`,
`whisper.cpp 1.9.2`, and transcribed text matching the real spoken audio
almost exactly. Continue enabled immediately on completion.

Not yet decided: how `TranscribeStep.transcript` feeds the not-yet-built
Profile step; Profile's own real design; UI surfacing of which backend
(CPU/GPU) is active during a run (a diagnostics nicety, still not
built); verification of the GPU/chunking decisions on non-Apple-Silicon
hardware.

## G5: Profile step and editor — real PySide6 code (2026-08-25)

The Profile step (PRD §7.2 stage 4) moves from placeholder to real,
working code — the wizard's fifth fully-built step. Full rationale in
[docs/adr/0016-profile-editor-design.md](adr/0016-profile-editor-design.md)
(design) and
[docs/adr/0017-profile-step-and-editor-implementation.md](adr/0017-profile-step-and-editor-implementation.md)
(implementation).

The design pass corrected a real false start along the way: the first
instinct was a third "Profiles" pane in `CatalogWindow`, but that
window's own docstring had already ruled that out (ADR-0008) — profile
authoring belongs with the wizard, not the Catalog window. New
`ProfileEditorDialog` (`gui/filter/profile_editor_dialog.py`) is the real
result: a modal dialog with a name field, a checkable category/entry
`QTreeWidget` (tri-state propagation, archived-but-referenced entries
still shown per the "historical snapshots remain readable" rule), and an
`AttenuationSettings` form with every widget's range pinned to the exact
bounds `models.py` already validates. New `ProfileStep`
(`gui/filter/wizard/profile_step.py`): real saved profiles with
live-computed category/word counts, a first-run empty state (the
realistic default — PRD §9.2 ships no starter catalog), and
new/edit/archive actions. `CatalogWindow` gained one small addition of
its own: a `closed` signal, so Profile's own "Manage Word Catalog…" can
refresh its counts after an edit without polling.

54 new tests, all against real `CatalogService`/`FilterProfile`/
`AttenuationSettings` objects, `black`/`flake8`/`mypy` clean.

**A real, disclosed, thoroughly investigated test-environment
limitation** turned up while stress-testing this round, not a defect in
the feature itself: running this machine's entire local test suite as
one `pytest` invocation (GUI and non-GUI together) can segfault once
enough GUI test modules accumulate — traced, after extensive bisection,
to a PySide6 6.11.2/shiboken6 interaction that reproduces with even a
trivial, empty custom `QWidget`, completely independent of this
feature's own code or design. This project's actual CI command
(`pytest tests/ --ignore=tests/gui`) already excludes `tests/gui/`
entirely and is completely unaffected — confirmed clean, repeatedly.
Full disclosure and the investigation trail: ADR-0017.

## G5: Scan step — real PySide6 code (2026-08-25)

The Scan step (PRD §7.2 stage 5, §9.4) moves from placeholder to real,
working code — the wizard's sixth fully-built step, and the first one
wired to two real predecessors that both matter (Transcript/Transcribe
for the completed `Transcript`, Profile for the chosen profile). Full
rationale in
[docs/adr/0018-scan-step-implementation.md](adr/0018-scan-step-implementation.md).

No backend changes at all — `scan.py`/`matcher.py` (`run_scan()`,
`Scan`, `ScanReport`, `build_report()`) already existed from G2/G3 and
needed nothing added; this round is UI-only. New `ScanWorker` mirrors
`MediaInspectWorker`'s simple one-call shape, not `TranscribeWorker`'s
chunked/durable one — `matcher.scan_transcript()` has no chunk boundary
to checkpoint at, and its own docstring already flags it as
unbenchmarked against a real long transcript, so building job-orchestrator
durability for it now would repeat the exact over-engineering mistake the
original 30s/5s pause-latency chunking default turned out to be. New
`ScanStep`: Ready/Running/Needs attention/Complete, real `ScanReport`
fields, a re-entry guard shaped like `TranscribeStep`'s own but with an
extra case Transcribe never needed — if the transcript or chosen profile
actually changed since the last visit, the old scan is stale by
construction and this resets to "ready to scan" rather than keeping it.

`WizardWindow` gained two new hand-offs: Profile's chosen profile plus
whichever real `Transcript` exists into Scan, and Scan's completed result
straight into `ReviewStep.set_scan()` — the exact hand-off Review had
already defined in its own docstring, written before Scan existed to
receive it.

15 new tests, all against real `CatalogService`/`Scan`/`ScanReport`/
`Transcript` objects, `black`/`flake8`/`mypy` clean. The same disclosed
test-environment limitation from the Profile round (ADR-0017) still
applies for the same pre-existing, environment-level reason — nothing
new about it this round; this project's actual CI command remains
completely unaffected.

## G5: Render step — real PySide6 code (2026-08-25)

The Render step (PRD §7.2 stage 7, §8) moves from placeholder to real,
working code — the wizard's seventh fully-built step, and the one with
the most already-built backend of any step so far. Full rationale in
[docs/adr/0019-render-step-implementation.md](adr/0019-render-step-implementation.md).

`renderer.py`/`validator.py` (`render()`/`validate()`, ADR-0006/0007)
needed zero changes — already complete, tested, and benchmarked against
a real ~13.5-hour production audiobook before this round started. Three
small backend pieces the wireframe pass itself flagged as missing got
built instead: `renderer.default_output_path()` (the base app's own
output-path logic turned out to be shaped for a different flow and
didn't transfer), `renderer.pick_default_bitrate()` (porting, not
reinventing, the base app's real bitrate-auto-selection logic — verified
against the same real reference book's own ~126kbps track, snapping to
128k exactly as predicted), and a new `filter_report.py` module
(`write_filter_report()`) — the real, persisted `filter-report.json`
ADR-0007 explicitly deferred as "UI/orchestration-layer work (G5), not
blocked by anything in this ADR." `ReviewStep` gained one small public
method, `current_render_plan()`, so Render could finally read Review's
*live* include/exclude decisions as a real `RenderPlan` — nothing outside
Review could reach that before.

New `RenderWorker`: unlike `ScanWorker`'s indeterminate bar, `render()`
already has four real stages to report via its own progress callback, so
this one relays real determinate progress. No Cancel button in the real
UI, on purpose — `render()` has no handle back to its own ffmpeg
subprocesses to interrupt, so a Cancel that couldn't actually stop
anything was left out rather than shipped as a lie; the wizard's own
confirm-before-close is the real, working escape hatch. New `RenderStep`:
Ready/Running/Needs attention/Complete, real editable output-path/bitrate
defaults, a re-entry guard shaped like Scan's own (structural equality on
the frozen `RenderPlan` dataclass decides "did anything really change").
Deliberately does *not* show a numeric duration/chapter delta on a
successful validation — `validator.py`'s checks only ever produce a
structured issue when something *fails*, so there's no honestly
displayable "matched within Xms" value on the passing path; a richer
Complete/Render summary would need a real `validator.py` change first,
flagged rather than faked.

51 new tests, all against real `RenderResult`/`ValidationReport`/
`RenderPlan`/`MediaManifest` objects — only the ffmpeg-touching calls
(`render`/`validate`/`inspect`) are mocked at the worker boundary, same
as every other worker test in this codebase. `black`/`flake8`/`mypy`
clean. The same disclosed test-environment limitation from the Profile/
Scan rounds (ADR-0017) still applies for the same pre-existing reason;
this project's actual CI command remains completely unaffected, and
every realistic scoped test run — including all wizard-step test files
together (175 tests) — is clean, repeatedly.

Only Complete remains a placeholder now — the last PRD §7.2 stage
without its own wireframe pass.

## G5: Complete step — real PySide6 code (2026-08-25)

The Complete step (PRD §7.2 stage 8) moves from placeholder to real,
working code — the wizard's eighth and final step. All eight PRD §7.2
stages are now real, end to end. Full rationale in
[docs/adr/0020-complete-step-implementation.md](adr/0020-complete-step-implementation.md).

`RenderStep` gained three small public properties (`result`/
`validation`/`report_path`) so Complete could finally read what it
produces — the last "nothing downstream consumes this yet" gap in the
whole wizard. New `CompleteStep`: one real state (unlike every earlier
step, no re-entry guard is needed — nothing here runs or can be
clobbered, so re-displaying the latest result on a later visit is always
exactly correct), a real single "✓ Passed validation" (or "✓ Passed, with
warnings" plus the real warning text) statement rather than the original
wireframe sketch's invented per-check deltas ("duration match Δ 0 ms,"
"chapters 50/50") — corrected once building Render made clear
`validator.py` has no such value to report honestly on a passing check.
A real "Open Folder" button — confirmed via search this had no
precedent anywhere in the codebase before now.

The "Done" question from the Complete wireframe pass got a real answer
instead of a guess: asked directly, settled as "Done closes the wizard
window," with no separate "filter another book" reset needed — every
step's own re-entry guard already makes picking a new file from the
Source rail cell work as that path. Implemented as one new branch in
`WizardWindow._on_continue()`. `placeholder_step.py` is deleted — with
all eight steps real, it had zero remaining callers or tests left
anywhere in the codebase.

A real, confirmed gap turned up while wiring this, flagged rather than
silently folded in: `WizardWindow`'s own docstring has claimed since
ADR-0010 that closing the wizard mid-Transcribe or mid-Render prompts
for confirmation, mirroring `ModelManagerWindow`'s pattern — but it has
no `closeEvent` override at all. That documented intention was never
actually built in any implementation round since. Spun off as its own
follow-up task, since it's a cross-cutting concern touching Transcribe
and Render, not something Complete's own implementation should absorb.

14 new tests (9 for `CompleteStep`, 2 for `RenderStep`'s new properties,
3 for `WizardWindow`'s Render→Complete hand-off and the Done-closes-the-
window behavior), plus removal of a test that no longer applied
(`test_every_other_step_is_a_placeholder`) and a fix to a stale, wrong
code comment in `test_wizard_window.py` (it claimed to flip a
"PlaceholderStep's" advance-ability; the test actually monkeypatches
`ReviewStep`, a leftover from before Review itself was real). `black`/
`flake8`/`mypy` clean. Every realistic test scope — this step's own
tests, all wizard-step test files together (189), a broader batch
alongside `CatalogWindow`/`ModelManagerWindow`/`ProfileEditorDialog`
(281) — is clean, repeatedly, and this project's real CI command
remains unaffected.

## G5: Wizard close confirmation, actually implemented (2026-08-25)

Closes the one real gap the Complete round surfaced rather than folded
in: `WizardWindow`'s own docstring (and ADR-0010's original wireframe
review before it) has claimed since this fork's early G5 work that
closing the wizard mid-Transcribe or mid-Render prompts for
confirmation, mirroring `ModelManagerWindow`'s own pattern — but no
`closeEvent` override existed anywhere in `wizard_window.py`. That
documented intention was never actually built in any round since. Full
rationale in
[docs/adr/0021-wizard-close-confirmation.md](adr/0021-wizard-close-confirmation.md).

New `WizardWindow.closeEvent()`, deliberately asymmetric: mid-Transcribe
gets asked to pause (`TranscribeWorker.request_pause()` — real,
resumable progress via the SQLite Job Orchestrator, ADR-0015), then a
bounded `wait(5000)` before the window closes, exactly mirroring
`ModelManagerWindow`'s own cancel-and-wait sequence. Mid-Render can only
be warned about — `RenderWorker` has no stop mechanism at all (ADR-0019's
own deliberate choice, since there's no handle back to the underlying
ffmpeg subprocess) — so confirming there just acknowledges the work will
be abandoned, with nothing to call. Safe to just let an abandoned
`RenderWorker` keep running in the background afterward: `WizardWindow`
is never destroyed on close (no `Qt.WA_DeleteOnClose`, the same
lazy-create-and-reuse pattern every other secondary window in this
codebase already uses), so nothing destroys the `QThread` object out
from under it.

6 new tests, workers stubbed as `MagicMock`s (same "test the shell, not
the step" split every other shell-level test in this file already
uses) — nothing running closes silently; declining either prompt keeps
the window open (verified via `close()`'s own `False` return, proving
`event.ignore()` actually ran); confirming mid-Transcribe pauses and
waits; confirming mid-Render calls nothing; both running at once
confirms both in order. `black`/`flake8`/`mypy` clean. Full wizard-step
suite (195 tests) and this project's real CI command remain clean.

## G5: Dry-run review remediation — Complete/Render merge, catalog seed, transcript viewing, interface polish (2026-08-26)

The User personally drove the real GUI application end-to-end (a live
dry run, not an automated walkthrough) and reported 19 distinct issues
screen by screen — copy that leaked internal/spec language, contrast
bugs, a progress bar giving a misleading sense of where a render
actually stood, a completely unseeded Word Catalog, and more. Full plan,
the four decisions that needed the User's input before any code
changed, and the itemized list of what changed is in
[docs/adr/0022-dry-run-review-remediation.md](adr/0022-dry-run-review-remediation.md).

**Complete merged into Render (D1).** Once Render's own completed panel
grew an Open Folder button, its content became functionally identical
to the separate Complete step's — same pass/warning heading, output
path, duration, report path. `CompleteStep`/`complete_step.py` are
deleted; `STEP_LABELS` drops `"Complete"`; the wizard is 7 steps now,
not 8. [docs/adr/0020-complete-step-implementation.md](adr/0020-complete-step-implementation.md)
is marked superseded rather than deleted, to keep the historical record
of why the step existed in the first place. No new logic was needed in
`WizardWindow._on_continue()` — "Done closes the window" already
applied generically to whichever step is last; Render is just that step
now.

**Catalog seeding (D2).** `catalog_seed.py` (new) seeds a "Profanity"
category from a bundled, well-known public word list on first run only
— `catalog_store.load_catalog()`'s existing "file doesn't exist yet"
branch now seeds and immediately saves, rather than returning a
never-populated service. Deliberately Profanity-only: a "Slurs" (or any
other sensitive) category stays entirely User-curated, since picking
specific terms for a shipped default is a subjective call this app
shouldn't make unilaterally. This also closes the actual root cause the
dry run found behind an apparent "scan found zero hits" bug: the
selected profile had zero linked catalog entries, which was only
discoverable at all because of a second, real bug below.

**Transcript viewing across Transcribe/Profile/Scan (D3).**
`Transcript` gains a `path` field (`transcript.py`) — deliberately
excluded from the JSON schema itself (`transcript_to_dict`/
`transcript_from_dict`), since a path is local-session metadata, not
portable artifact content. New `transcript_text.py` writes a plain-text
companion (just the words, reading order) lazily, only when a "View
Transcript" action is actually clicked — not on every
`write_transcript()` call, so fixture-building code doesn't grow a
surprise side file. `WizardWindow` gained a `_current_transcript()`
helper (de-duplicating logic that used to exist only inside the
Profile→Scan hand-off) and a new Transcribe→Profile hand-off, wired on
both paths a transcript can reach Profile by — the normal advance, and
the "reuse a compatible transcript" skip path, which bypasses
`_on_continue()`'s own dispatch entirely.

**Interface polish, mostly copy and contrast, several with one shared
root cause:**
- Transcribe/Profile/Scan subtitles and Render's "No Pause or Cancel…"
  line rewritten — all previously read like internal spec/ADR notes
  ("immutable snapshot", literal PRD section citations, `render()` and
  `ADR-0019` by name) rather than something aimed at the person running
  the wizard.
- No step body had left/right padding, window-wide — traced to a single
  shared cause (the `QStackedWidget` wrapper in `wizard_window.py` had
  none, and every step's own root layout deliberately has none either,
  expecting a parent to supply it) and fixed at that one point rather
  than in all 7 step files individually.
- Checkbox contrast was unreadable in dark mode in three separate
  places — Word Catalog, the Profile editor's category/word tree, and
  Review's Included column — all for the same reason: checkable
  `QTreeWidgetItem`/`QTableWidgetItem`s render with Qt's unstyled native
  indicator, since `styles.py` only ever defined `QCheckBox::indicator`.
  One new shared `QTreeWidget::indicator`/`QTableWidget::indicator` rule
  (light + dark) fixes all three.
- The Transcribe and Render progress bars were both stuck at a 6px
  unstyled default with no visible percentage text — the app already had
  a taller, readable `QProgressBar#jobProgress` variant built for job
  progress elsewhere, just never wired onto either of these.
- The Continue/Done button had no visual weight — given the app's
  existing primary-CTA treatment via a new `QPushButton#primaryBtn` rule
  mirroring `#convertBtn`, rather than reusing `#convertBtn` itself
  (semantically a different button) or touching it.
- A literal typo, "Categories && words", plus a missing instruction
  telling the User to actually check entries in the Profile editor —
  the same screen whose invisible-in-dark-mode checkboxes (above) is
  what made an apparently-populated profile actually have zero entries.

**Review's confidence filter now filters by confidence.** It used to
offer only "Available"/"Not available" — presence, not the actual
percentage. Replaced with real threshold buckets (below 25%/75%/90%,
plus "Not available" for hits with no confidence at all).

**Render's progress-accuracy problem (D4) got the cheap, honest fix,
not the expensive one.** `render()`'s callback marks each of its four
stages' *start* at a fixed quarter, so a long `encode_and_mux()` stage
(the longest by far on a real audiobook) can sit at the same percentage
for most of a render — confirmed as the real mechanism behind the User
watching the bar reach 75% in about a minute, then stay there for five.
Rather than weighting the stage fractions by expected duration or
parsing ffmpeg's own progress stream, Render got an elapsed-time display
instead (`RenderStep`'s own `QTimer`, mirroring `TranscribeStep`'s
already-proven one exactly) — it can't lie about how long the render
has actually been running the way the percentage can.

**Verification:** 33 new/changed tests across `test_catalog_seed.py`
(new), `test_transcript_text.py` (new), `test_catalog_store.py`,
`test_transcript.py`, `test_profile_step.py`, `test_scan_step.py`,
`test_transcribe_step.py`, `test_review_step.py`, and
`test_wizard_window.py` (the last with `CompleteStep` removed
entirely — `TestRenderToCompleteWiring` deleted, `TestDoneClosesWizard`
rewritten against Render as the last step). Every scope actually
touched by this round is clean: `tests/filter/` (392 passed, 2 skipped),
`tests/gui/filter/` — every wizard step plus catalog/profile-editor/
workers (287 passed), and this project's real CI command,
`pytest tests/ --ignore=tests/gui` (927 passed, 2 skipped). One
newly-surfaced, out-of-scope finding, not fixed here: running a large
enough combination of `tests/gui/` files together in one process
(specifically `tests/gui/filter/` alongside the base app's own
`tests/gui/test_window.py`) segfaults in native Qt/Shiboken teardown
code — reproducible even against this round's own unmodified files, and
confirmed as a volume-triggered pre-existing ceiling in this offscreen-
Qt test configuration (not a logic bug this round introduced) by
bisecting against the pre-round commit, where the same combined run was
too small to reach it. Flagged as its own follow-up rather than folded
in here, since it's an environment/tooling question, not a UI or wiring
one — and moot for this project's real CI gate either way, since that
already never runs `tests/gui/` at all.

## Combined-run GUI test segfault, root-caused and fixed (2026-08-26)

Follow-up to the open finding at the end of the previous round: root
cause and fix for `pytest tests/gui/filter/ tests/gui/test_window.py`
(and the larger `pytest tests/gui/`) segfaulting in native Qt/Shiboken
teardown code. Full mechanism and evidence in
[docs/adr/0023-gui-test-segfault-gc-teardown.md](adr/0023-gui-test-segfault-gc-teardown.md).

**Root cause: CPython's cyclic GC racing Qt's own native teardown.**
Nearly every `win` fixture outside `test_window.py` — `test_catalog_
window.py`, `test_model_manager_window.py`, every `tests/gui/filter/
wizard/*.py` file — just does `return Window(...)` with no teardown at
all. Each is a top-level, unparented `QObject`, so nothing Qt-side
destroys its C++ half when the test ends; it just waits for CPython's
garbage collector to notice it's unreachable. A `QWidget` tree is full
of reference cycles (parent↔child pointers, signal/slot connections),
so these are cyclic garbage — collected only whenever CPython's
generational GC happens to run. Across `tests/gui/filter/` plus the
wizard step files, several hundred such orphaned widget trees
accumulate uncollected in the same process. By the time `test_window.
py`'s own `win` fixture — the one fixture in the tree that explicitly
does real Qt-side teardown (`deleteLater()` +
`QCoreApplication.sendPostedEvents(None, DeferredDelete)`, per its own
docstring) — reaches that `sendPostedEvents` call, enough allocations
have happened that CPython's GC threshold trips *during* it, destroying
a batch of orphaned widgets via Shiboken while Qt's own native
object/event bookkeeping is mid-traversal on the same call stack. Two
independent native teardown paths going reentrant on one thread is what
segfaults, not any single widget being wrong — which is exactly why the
crash only ever showed up at that one line, only past a volume
threshold, and with no project code anywhere in the native crash frames.

**Fix: disable the cyclic collector for the GUI test session, not
retrofit teardown into every leaking fixture.** A new session-scoped,
autouse `_disable_cyclic_gc` fixture in `tests/gui/conftest.py` calls
`gc.disable()` before any GUI test runs and restores the prior state
after the session ends. Reference counting alone still frees everything
that isn't a cycle; only the cyclic collector — the part that can fire
at an arbitrary, Qt-hostile moment — is turned off, and only for the
lifetime of a short pytest process. This was chosen over retrofitting
`test_window.py`'s explicit `deleteLater()`/`sendPostedEvents` pattern
into all ~15 other `win` fixtures: that's real work for a defect that's
purely a test-process object-lifetime artifact (the real application
creates one window per run and shuts down normally — it never gets
near the widget volume a 700+-test pytest process accumulates).

**Verification:** confirmed experimentally before implementing — manually
disabling GC around both the original combined repro and the full
`tests/gui/` suite eliminated the segfault completely and repeatably,
before any fixture was touched. With the fixture in place: the original
repro, `pytest tests/gui/filter/ tests/gui/test_window.py`, passes
467/467 (previously segfaulted, exit 139); the full `pytest tests/gui/`
passes 784/784 (previously segfaulted); both stable across repeated
runs. This project's real CI command, `pytest tests/
--ignore=tests/gui`, is untouched by the change and remains clean: 927
passed, 2 skipped.

## G5: Attenuation timing accuracy — real whisper.cpp word-timestamp error, not a renderer bug (2026-08-26)

The User listened back to a real ~13.5-hour audiobook rendered through
the full wizard and found silenced segments landing too soon and
lasting too short relative to the actual spoken words — the single most
load-bearing metric in this whole feature, so this got the deepest
investigation of any round so far. Full narrative and every measurement
in
[docs/adr/0024-attenuation-timing-accuracy.md](adr/0024-attenuation-timing-accuracy.md).

**The renderer was cleared first, not assumed innocent.**
`generate_envelope_pcm`, `apply_gain_envelope`, and `encode_and_mux`
were each reproduced by hand against real audio and found to implement
the documented math exactly — the gain envelope reaches the configured
floor precisely where the interval planner says it should. The real
culprit is whisper.cpp's own word-level timestamps, which this round
measured directly rather than assumed: a purpose-built harness
(`say`'s embedded `[[slnc N]]` command to engineer *real* digital
silence around target words, so true onset/offset can be found by
energy thresholding with no manual judgment call) put a real number on
an error that turned out to be substantial and inconsistent in
direction — sometimes 100s of ms early, sometimes 100s of ms late, and
in ~29% of an early 8-word sample, disconnected from the real word
entirely.

**A second, categorically different problem turned up along the way:**
`crap`/`piss`/`bitch`/`goddamn` scored zero hits at any padding value,
because whisper.cpp consistently tokenizes them as two separate words
(`"C"+"rap"`, `"B"+"itch"`, `"P"+"iss"`, `"God"+"damn"`) — confirmed
identical across base.en and small.en, and confirmed against the User's
own listening test that this is whisper's behavior, not a synthesis
artifact, for at least "crap"/"bitch". This is a recognition problem,
not a timing one, and had a free fix: the matcher already supports
exact multi-word phrase entries (PRD §9.3) — adding phrase entries that
match what whisper actually outputs recovered 26 of 30 previously-missed
instances (67% → 96% recognition) with zero code changes.

**Model comparison** (base.en/small.en/medium.en, same audio): small.en
showed much tighter, one-directional timing error but missed more words
outright; medium.en was a lateral move despite its 10x larger download
— nearly identical results to small.en except trading one failure mode
for another on a single word. base.en (the existing default) stayed the
better overall choice once the word-splitting fix closed most of its
recognition gap.

**Decision: `lead_padding_ms` 60→300ms, `tail_padding_ms` 80→400ms** —
derived from the 90-instance pass's stable percentiles plus every
hand-verified real case, deliberately not chasing the automated
harness's own least-reliable tail-percentile numbers (two separate
measurement-tooling bugs were found and fixed mid-investigation, both
disclosed rather than silently folded into the reported figures). This
exceeds the PRD's own enforced valid range (250ms/300ms) — confirmed
directly with the User before widening `AttenuationSettings.
__post_init__`'s range checks, `docs/PRD.md` §8.3's table, and the
Profile editor's spin-box ranges together, rather than treating a
PRD-encoded range as just an implementation detail.

**Verified against the real pipeline, not just theory:** rendering the
90-instance sample with the new defaults plus the phrase-entry fix, then
re-transcribing the filtered output, found only 7 of 86 recognized
target words (8%) still recognizable as clean, unattenuated speech — a
92% real-world success rate, up from the roughly 30% the old defaults'
own measured data implied. The 7 survivors match the investigation's
own hand-confirmed genuinely-unfixable cases, not a new failure mode —
disclosed as a real, remaining ceiling, not eliminated.

**One unrelated, genuine bug found and fixed as a direct side effect,
not deferred:** `test_wizard_window.py`'s `win` fixture never passed an
explicit `catalog_service`, so it silently read whatever real,
persisted catalog exists on the machine running the tests — invisible
until this round's default change diverged from what a leftover real
profile had historically saved to disk. Fixed with an explicit, empty
`CatalogService()`, the same isolation every other wizard-step test file
already used.

`black`/`flake8`/`mypy` clean. `tests/filter/` (392 passed, 2 skipped),
`tests/gui/filter/` (287 passed), and this project's real CI command
(927 passed, 2 skipped) all clean.

## G5: DTW-based word timestamps, replacing whisper.cpp's default heuristic (2026-08-26)

Rendering real chapters with ADR-0024's new padding defaults produced
the *opposite*-looking symptom from the original report: silence now
landing consistently *after* the word, not before it. Direct
verification — the actual rendered interval boundaries drawn onto a
waveform image and visually inspected, not just an automated number —
confirmed it precisely: the start marker sat in the middle of the
target word, the end marker well past its natural finish. Real
narration was showing a materially different (more consistently late)
timing bias than the synthetic-TTS characterization ADR-0024's padding
values were derived from, so a bigger `tail_padding_ms` just extended
an already-late interval further into subsequent speech. Full
investigation and every measurement in
[docs/adr/0025-dtw-word-timestamps.md](adr/0025-dtw-word-timestamps.md).

Rather than chase padding values further against a wrong root
assumption, this fixed the actual input: whisper.cpp has its own more
accurate word-timing mode (`-dtw MODEL`, real per-token Dynamic Time
Warping alignment) that this fork simply wasn't using — a much smaller,
more targeted change than evaluating a different transcription engine
outright, tried first per the User's own explicit "exhaust the smaller
levers before the bigger one" framing.

**Two non-obvious things had to be discovered directly against the real
binary, not assumed from docs:** DTW silently produces no data at all
under flash attention in this build (`whisper-cli` logs the reason,
`t_dtw` comes back `-1` for every token, no error surfaces anywhere
else) — flash attention must always be disabled together with enabling
DTW, not as a second setting a caller could forget. And DTW gives each
token a single aligned *start* point, not a start/end pair — a token's
end is the *next* token's DTW start, since tokens are contiguous in a
decode sequence, falling back to the existing heuristic per-token
wherever a DTW value is missing (either DTW wasn't requested, or a
boundary token came back `-1` even though it was).

**Speed cost, measured directly** (5 minutes of real narration, same
hardware, GPU/Metal both ways): ~1.5x slower than the flash-attn
default — a real but moderate cost, not the multi-x hit originally
feared, since ADR-0001's older "4-5x speedup" figure was GPU-vs-CPU
overall, not flash attention's own isolated share of it.

**What changed:** `transcript_engine.run_whisper()` gained
`dtw_model_name`, appending `-dtw <name> -nfa` together when given; new
`dtw_model_name_for()` derives whisper.cpp's own bare model identifier
from the model file's path, reusing `model_manager`'s existing
`ggml-{name}.bin` naming convention rather than threading a second
parameter through every caller; `whisper_result_to_segment()` now
prefers each token's DTW timing over the heuristic offsets wherever
valid, falling back per-token otherwise — a transcript produced without
DTW parses identically to before. Both the real chunked orchestrator
and the single-shot spike/test path now always request DTW — it's the
fork's actual default behavior now, not an opt-in flag sitting unused.
No GUI toggle was added; no change to `renderer.py`, the interval
planner, or ADR-0024's padding defaults, which needed the *input*
timestamps fixed, not the padding arithmetic around them.

**Verification, real pipeline not just units:** all 4 real chapters
were re-transcribed, re-scanned, and re-rendered through the actual
production path with DTW built in; all 4 still pass the app's own
validator. Four real hits across three chapters were checked the exact
same rigorous way the original bug was confirmed — rendered interval
boundaries drawn directly on the waveform and visually inspected. Where
the pre-DTW render showed the start marker buried mid-word and the end
marker well past it on the identical hit, the DTW-based render shows
both markers landing at the word's real edges. A quick automated
numeric cross-check (energy-derivative onset detection) was also tried
across all 23 real hits, but it directly contradicted the visual
ground-truth check on the one hit both methods examined — disclosed as
unreliable for continuous narration and not used as evidence; the
visual check is what this claim rests on. A fresh before/after audio
pack went to the User for final confirmation — the same discipline
that caught both the original bug and the regression the padding-only
fix introduced.

`black`/`flake8`/`mypy` clean. `tests/filter/` (398 passed, 2 skipped)
and this project's real CI command (933 passed, 2 skipped) both clean.

## G5: Attenuation timing accuracy — closed out (2026-08-26)

Three more real findings closed out the investigation ADR-0024/0025
started, none of which required further code changes:

**A word-boundary-aware padding clamp was designed, implemented, and
then abandoned** after real-pipeline re-verification (not just its own
unit tests) showed it firing on nearly every hit in a real chapter, not
just the rare true zero-gap case it targeted — one interval shrank
enough to fail the renderer's own attenuation validator outright. Root
cause: DTW's word timestamps are contiguous by construction (ADR-0025's
own finding), so "gap to the next transcript word" reads as near-zero
almost everywhere in a DTW transcript, including across real pauses —
not a signal the clamp could safely act on. A candidate fix (using
whisper's non-DTW heuristic offsets to measure the gap instead) was
checked *before* touching any code again: on the real "damn"/"cat" case,
DTW said 0ms gap, the heuristic said 440ms, and direct RMS measurement
of the actual audio said ~110-140ms — both transcript-derived signals
were wrong, in opposite directions, and the heuristic's answer would
have silently reintroduced the original bleed-into-the-next-word bug.
Fully reverted; no production code changes from this line of work.

**base.en vs. small.en, now that DTW is the timing mechanism:** with
DTW enabled, both models recognized the exact same 23 real hits across
all 4 chapters — zero hits either model found that the other missed.
Timing was not identical, though: small.en's DTW timestamps landed
systematically later than base.en's on 96% of hits (mean +70ms start /
+101ms end, max +300ms end), never earlier. Since late-tail overrun is
the one failure mode already proven fragile here (the "damn"/"cat"
case above), this is a real mark against switching, not just a wash —
`base.en` stays the default, which it already was; no code changed.

**Final listening confirmation:** all 4 chapters re-rendered fresh
against the current TestProfile (150ms lead / 200ms tail, base.en +
DTW) to guarantee the sample reflected the real current state, not a
stale prior round. A 10-clip before/after set spanning all 4 chapters
went to the User, who confirmed it: "they are turned out well. Still a
little variance, but I think it's as good a we're going to get." This
closes the attenuation-timing-accuracy investigation that ADR-0024
opened — remaining variance is accepted, not hidden.

## G5: Review step — Render Plan tab blank on real-sized scans (2026-08-26)

Bug, not a design decision: the Render Plan tab's "Merged intervals" list
had no scroll area — just a `QGroupBox`/`QVBoxLayout` added directly to
the tab. On a small test fixture (2-3 intervals) this looked fine; on a
real scan (273 hits → 250 merged intervals in the User's own real-book
run), the group box's content requires ~5,300px of height with no way
to fit or scroll to it inside an actual fixed-size wizard window —
reproduced headlessly with a real `QMainWindow.setFixedSize()` (not
just asserted): without the fix, the list only rendered when the test
let the window grow freely to fit all content, which a real window
never does. Fixed by wrapping the group box in a `QScrollArea`
(`setWidgetResizable(True)`), matching how every other
variable/unbounded-length list in this app already handles this (the
Hits tab's own `QTableWidget` scrolls natively). 2 new tests
(`TestRenderPlanTab`) verify the scroll area exists and that a
120-interval scan stays reachable in a realistic fixed-size window, not
just a synthetic one that can grow to fit anything.

## G5: Encoder safety widening — real render validation failure, real root cause (2026-08-27)

A real render (the User's own ~11.5-hour book) failed validation on one
interval: measured -65.1dB where -74.0dB was expected. Root-caused
against the real audio, ruling out (in order, each with direct
evidence, not assumption): this session's own ADR-0028 render-progress
change (a byte-identical old-path-vs-new-path A/B test on the real
audio), scan/matching/interval-planning, gain-envelope generation
(verified the exact correct floor-gain byte at the target sample), and
the attenuation multiply itself (reproducing the full pipeline from
source extraction through the multiply gave a correct -94dB there).
The corruption is specifically in AAC encoding: it reproduced with
ffmpeg's native encoder AND macOS's separate AudioToolbox encoder,
was unaffected by bitrate or encoder quality settings, and reproduced
with fully synthetic audio (no real speech needed) — a genuine codec
behavior where an abrupt loud→near-silent→loud transition leaves the
original audio audible for roughly the first 30-50ms into the
"silent" side, independent of how gradually or smoothly the gain ramps
there. Only a genuinely *wider* interval reliably cleared it in
testing — this is why only one interval failed out of many: a single,
isolated, unmerged short word (630ms total) is the worst case.

Fixed with an internal-only renderer widening (ADR-0030): the actual
gain-envelope hold given to AAC encoding is extended by a safety
margin beyond each interval's own edges, capped so it never reaches
into a *neighboring RenderInterval's* own territory (a solid,
already-known-safe signal — unlike individual transcript-word gaps,
which ADR-0026's abandoned word-boundary clamp already found
unreliable). The User-facing RenderPlan, filter report, and what
`validate()` checks against are all untouched. 100ms was chosen
deliberately smaller than the ~200-500ms first proven to work, trading
some of the fix's own margin for less reach into any directly-adjacent
non-hit word (the same risk class as ADR-0026's "damn"/"cat" case).

Verified against the actual failing case: extracted the real ~4.6s
window around the failing interval from the User's real book,
reproduced the bug through the unmodified pipeline, then ran the real
(unmodified) top-level `render()`/`validate()` functions against it
with the fix in place — validation passed. 9 new tests, 1747 passed/2
skipped overall; `black`/`flake8`/`mypy` clean.

## G5: Render step — stage reweighting, live encode progress, ETA (2026-08-26)

The Render step's progress bar sat visibly at 75% for most of a real
render — `render()`'s four stages (extract, envelope, attenuate,
encode+mux) each only reported their own start checkpoint (0/25/50/75%)
regardless of real relative cost, and ADR-0007's own measurement on a
full 13.5-hour audiobook had already shown why: encode+mux alone is
587.7s of 631.4s total render time, roughly 93%, not a quarter. The
User asked for the same kind of estimate Transcribe already had
(ADR-0027), naming encode+mux specifically, and asked about the
overhead of tapping ffmpeg's own real-time progress before committing
to it — answered as negligible, since ffmpeg already computes these
stats every ~0.5s by default and the only real change is which stream
they're written to.

Three changes, all confirmed by that same real-book relative-cost data:
the four stage checkpoints were reweighted to extract 0%, envelope 5%,
attenuate 6%, encode+mux 8%→100%, derived directly from ADR-0007's own
measurement; encode+mux was instrumented with ffmpeg's own `-progress
pipe:1` output via a new `_run_with_progress()` (replacing one blocking
`subprocess.run()` call with a streaming `Popen`, parsing the
human-readable `out_time=HH:MM:SS.ffffff` field rather than the
misleadingly-named `out_time_ms`/`out_time_us` fields, both of which
are actually in microseconds despite the name); and an "Est. remaining"
label was added next to Elapsed for encode+mux specifically, seeded
from an 80x-realtime default (derived from ADR-0007's own ~82.7x
measurement) then replaced by this run's own measured rate once real
progress starts arriving, cleared entirely once `validate()` takes over
since it had no progress signal of its own yet.

Verified against a real book chapter (chapter_04, 28.8 minutes of real
narration) rendered through the actual production `render()` function
end to end: extract/envelope/attenuate completed in 1.5s combined,
exactly as the reweighting predicts, and encode+mux then reported 41
real progress events climbing smoothly from 8% to 100% rather than
jumping straight from 75%. Measured real rate for this render (~84x
realtime) landed close to both the hardcoded default (80x) and
ADR-0007's own original measurement (82.7x). 7 new tests; full suite
1731 passed, 2 skipped; `black`/`flake8`/`mypy` clean.

## G5: Widened filter-report.json (2026-08-26)

The filter report written next to a render's output only ever held the
output path/duration/bitrate and the validation result — real, but not
what a User would actually want to know about a run. The User asked
for exactly this, naming transcription and rendering timing as
examples.

`write_filter_report()` widened to a keyword-only, all-optional
signature covering source path, transcript JSON/text paths and model,
filter stats, and per-stage timings — schema bumped to 2. Filter stats
are deliberately category-level counts, never raw matched terms: a
slur category's name isn't sensitive, the actual words matched under
it are, and this is a plain-text file that might be opened by someone
other than the person who set up the profile — the same reasoning
behind this app's own catalog masking feature. The call itself moved
from `RenderWorker` to `RenderStep._on_result_ready` (the UI thread)
since the `Transcript`/`Scan`/`CatalogService`/earlier-stage timings a
useful report needs all already live at the step, not the worker.
Per-stage timing is captured where the wall-clock boundary actually is:
`TranscribeStep` now accumulates real elapsed time across every run
segment, `ScanStep` gained its own start/finish timestamp capture, and
Render/Validate timing came for free from `RenderStep`'s own existing
`_start_time` and `validating` signal.

Real-pipeline verification generated an actual report from real
chapter_01 data — real transcript, real scan, real render, real
validation — and the first attempt surfaced two genuine things, not
synthetic: a stale on-disk render (the User's own catalog had grown
since that chapter was last rendered) and the `ggml-` model-name prefix
leaking into the JSON, both caught and fixed before shipping. After
re-rendering fresh, the report showed 16 real hits across 1 category,
12.76s of real attenuated audio, and a real passing validation. Full
suite: 1738 passed, 2 skipped; `black`/`flake8`/`mypy` clean.

## G5: Transcribe step — estimated remaining time (2026-08-26)

The Transcribe step already showed elapsed time while a job ran, but
nothing about how much longer it would take. The User asked for a
rough estimate — explicitly not exact prediction, since transcription
speed depends heavily on the machine — that starts reasonable and gets
more accurate once the job itself produces real timing data.

A two-phase estimate, computed entirely from data the step already
has: a cold start (no chunk completed yet in this run) seeds the
estimate from a small hardcoded per-model table
(`_DEFAULT_REALTIME_MULTIPLIER` — base.en: 15x, small.en: 6x, both
explicitly rough), then live refinement replaces the guess with a real
measured rate — audio-ms actually transcribed this run divided by
wall-clock-ms it took — the moment any chunk completes, weighted by
each remaining chunk's own duration since chapter-aligned chunks
aren't uniform length. "This run," not "this job," matches
`_start_time`'s own existing convention, so a rate measured before a
pause never carries into a fresh resume's estimate. The countdown
itself only re-anchors on real data rather than recomputing every
second, which would make the estimate visibly worsen while waiting on
the in-flight chunk then jump back up the instant it completes — a
real behavior, but a confusing one to watch.

New `TestEstimatedRemaining` class in `test_transcribe_step.py`: cold
start reflects the chosen model's own default and differs between
base.en/small.en on identical audio; a completed chunk's real measured
rate overrides the default; a resumed job seeds its segment
bookkeeping from prior progress without letting pre-pause progress
pollute this segment's own rate. 5 new tests; full suite 1722 passed,
2 skipped; `black`/`flake8`/`mypy` clean.

## G5: Chunk-extraction truncation guard (ADR-0031, 2026-08-28)

A real transcription job (the User's own 26.8-hour book, 79
chapter-aligned chunks) crashed at chunk 50 with a native `SIGABRT`
from `whisper-cli` — no Python exception, just the process dying
inside miniaudio's WAV decode. A live filesystem watcher captured the
actual crashing chunk file, and direct inspection against a clean
manual re-extraction of the same byte range was conclusive: the real
file was only 59% the expected size, with a RIFF header still holding
ffmpeg's own placeholder size — the extraction's ffmpeg process had
been cut short before finishing, yet still returned exit code 0, since
the only success check up to that point was the return code.

Fixed by verifying actual audio duration on disk after every
extraction, never trusting the WAV header's declared size (exactly
what's unreliable in this failure mode): `_actual_wav_duration_ms()`
parses the RIFF chunk structure directly, counting everything from the
`data` chunk's payload start to the real end-of-file rather than its
own declared size. A single retry is attempted before failing loudly
with a clear `RuntimeError` that surfaces through the job's existing
`NEEDS_ATTENTION` → Retry flow, since a clean manual re-extraction of
the identical range had succeeded, real evidence this is a transient
condition.

Verified directly against the real captured evidence from the
investigation: the real crashing file computes at 106,478ms actual
against 181,395ms expected (correctly rejected, past tolerance); the
clean re-extraction computes at exactly 181,395ms (correctly accepted)
— an exact match to the independently-obtained `ffprobe` durations
from the original investigation. 10 new tests; full suite 1757 passed,
2 skipped; `black`/`flake8`/`mypy` clean.

## G5: whisper-cli native-crash retry (ADR-0032, 2026-08-29)

ADR-0031's truncation guard shipped on the theory that a truncated WAV
was crashing whisper.cpp's decoder — the User retried and hit the
identical crash again, unchanged, directly falsifying that theory. A
race-free capture (an unconditional copy of each chunk's WAV
immediately after extraction, placed *after* ADR-0031's own
completeness check had already passed) revealed the real story: the
earlier filesystem-watcher approach had been racing ahead of ffmpeg's
write and capturing a still-in-progress file — a diagnostic-tool
artifact, not a production bug. The real chunk 50 is ~23.6 minutes of
audio, not the ~3-minute window earlier reproduction attempts had used
(chapter-aligned chunking makes one chunk per chapter, and this fork's
own 45-minute subdivision ceiling doesn't touch a chapter this short)
— every earlier "clean reproduction" had silently been testing the
wrong audio. Feeding the real, complete, correctly-sized capture to
whisper-cli reproduced a *different* failure on the first attempt:
`WHISPER_ASSERT: filter_width < a->ne[2]`, deep inside whisper.cpp's
own DTW alignment code, well past decoding.

Empirical follow-up (binary-searching truncated prefixes, then
rerunning the untrimmed file repeatedly) showed this isn't
deterministic: 5 immediate reruns of the exact same file, same flags,
all succeeded after the first crash. That gap between isolated testing
(zero reproductions in roughly a dozen trials) and the real job (three
separate, real, in-app failures) suggests something about running the
same GPU-backed inference alongside the app's own Metal usage — not
pursued further, since its exact mechanism inside whisper.cpp/ggml's
Metal backend is upstream territory past this fork's scope.

Fixed by retrying the `whisper-cli` invocation itself (not
re-extraction — ADR-0031 remains in place for its own separate failure
mode) up to `_WHISPER_MAX_ATTEMPTS = 3` times before raising
`WhisperTranscriptionError`, a direct evidence-based mitigation given
the same input that crashed on attempt 1 transcribed correctly on 5/5
immediate reruns in real testing. 2 new tests; full suite 1759 passed,
2 skipped; `black`/`flake8`/`mypy` clean.

## G5: Resume progress display bug + whisper-cli CPU fallback (ADR-0033, 2026-08-29)

The User retried the real job after ADR-0032's retry fix shipped and
hit the identical crash on all 3 attempts this time — plain retry
alone wasn't reliable enough inside the app's real execution
conditions. Running the real crashing chunk (captured during
ADR-0032's investigation) with `--no-gpu` three times in a row all
succeeded, each taking about 46s versus roughly 19s on GPU — consistent
with the earlier finding that this is GPU/Metal-backend specific
rather than about the audio content or DTW itself.

After the GPU retry budget is exhausted, one final CPU-only attempt is
now made before raising, keeping every other flag unchanged including
`-dtw`/`-nfa` — `--no-gpu` only selects the compute backend and has no
effect on DTW's alignment math, so the fallback costs real wall-clock
time (~2.4x on whichever single chunk needs it) but zero
timing-precision quality. A separate, unrelated bug was found while
answering the User's question about why the progress bar showed
"Chapter 1" on reload: `set_transcript_choice()` only seeded the
progress display from an existing job's real progress for a `PAUSED`
job, never for `NEEDS_ATTENTION` — the far more common state a real
crash leaves a job in. The underlying job itself always resumed
correctly (driven by the job store's own committed-chunk state); only
the display was wrong until the first new progress callback arrived.
Now seeded for both states.

New tests confirm the GPU-exhausted-then-CPU-fallback path recovers
transparently with the exact call count and flag presence (including
`-dtw`/`-nfa` surviving onto the CPU attempt) asserted, and a new
`TestSetTranscriptChoiceExistingJob` builds a real `NEEDS_ATTENTION`
job via `JobStore` with real committed progress, confirming the
display is correct immediately after Retry. Full suite 1761 passed, 2
skipped; `black`/`flake8`/`mypy` clean.

## G5: Friendlier Tools naming + Model Manager entry point from Transcript (ADR-0034, 2026-08-29)

The User asked for more user-friendly names for the three Tools-menu
entries, flagging that jargon like "catalog" and "models" doesn't read
naturally to someone unfamiliar with this fork's own internal
vocabulary: "Filter Audiobook…" became "Filter for Language…", "Manage
Word Catalog…" became "Word List…", and "Manage Models…" became
"Manage Transcription Models…". The Profile step's own "Manage Word
Catalog…" button got the matching "Word List…" rename for consistency
between the two entry points to the same window.

Separately, the Transcript step already let a User inline-download a
model while picking one, but had no way to open the full Model Manager
without leaving the wizard — the Profile step already had exactly this
kind of escape hatch to its own Catalog window. A new "Manage
Transcription Models…" button on Transcript's model-choosing panel
opens `ModelManagerWindow` directly, the same lazy-create-and-reuse
pattern the other entry points already use. `ModelManagerWindow` gained
a `closed` signal (mirroring `CatalogWindow.closed`) so Transcript can
re-render its model list and re-derive `can_advance()` after the
window closes, since a download or removal made there needs to be
reflected immediately.

5 new tests: closing the window emits `closed` (a declined close
during an active download does not); the button opens the window with
the step's own `dest_dir` and reuses the same instance on a second
call; closing after a model gets installed elsewhere flips
`can_advance()` from `False` to `True` without any other action. Full
suite 1766 passed, 2 skipped; `black`/`flake8`/`mypy` clean.

## G5: Matching window titles, and Scan step's thin progress bar (2026-08-29)

Follow-up to the Tools menu rename above: window titles hadn't followed,
so clicking "Filter for Language…" opened a window still titled "Filter
Audiobook" — a mismatch. Worked through the naming with the User
directly rather than guessing: settled on **"Filter Audiobook Language"**
(main wizard window — keeps "Audiobook" per the User's preference, drops
a redundant "for"), **"Word List"** (Catalog window — deliberately
*not* "Filter Word List", since having "Filter" in both that title and
the main window's title read as confusing; reverted to the User's own
already-chosen Tools-menu wording instead, which was never redundant to
begin with), and **"Transcription Models"** (Model Manager window —
drops "Manage" since a window title names what's open, not an action).

Separately: the Scan step's progress bar (shown briefly during a scan —
fast enough that it's barely on screen) was still using the bare
`QProgressBar` default style (6px thin) instead of the `#jobProgress`
QSS rule (14px, the style every other step's progress bar already uses)
— just a missing `setObjectName("jobProgress")` call. 1 new test, full
suite 1767 passed/2 skipped; `black`/`flake8`/`mypy` clean.

Tools menu's own "Filter for Language…" was then brought in line with
the finished window-title wording too: **"Filter Audiobook Language…"**.

## G5: Settings window — storage overrides and transcription defaults (ADR-0035, 2026-08-29)

Following the Tools-menu naming pass, the User asked whether Word List
and Model Manager should really live inside a single "Settings" window
rather than as two separate ones. Working through it: those two are
full data-management workspaces people leave open while actively
working, not small values set once and left alone — the wrong shape
for a Preferences-style window. The better move was to keep them
separate and add a genuine, small Settings window for actual
low-frequency configuration, with the User naming the first real
candidates: folder defaults for transcription-related storage, and a
default output folder for filtered audiobooks. The User was explicit
that dark mode / check-for-updates — the base app's own existing
preferences in `gui/prefs.py` — should stay separately owned, not
folded into this new window.

New `filter/settings.py`, the same small
load/save/get/set-with-merged-defaults shape as `gui/prefs.py` but
living entirely under this feature's own storage root. Four settings
for v1: `models_dir`/`transcripts_dir` override `storage.py`'s
existing lookup functions, which now check the setting before falling
back to their fixed default; `output_dir` lets
`renderer.default_output_path()` check an override before its "same
folder as source" fallback; `preferred_model` lets
`TranscriptStep._pick_default_model()` prefer an installed model the
User has chosen as default. New `gui/filter/settings_window.py`
(`SettingsWindow`) added to the Tools menu as "Settings…", with Storage
Locations (folder rows — current effective path, Browse…, Reset to
Default) and Transcription Defaults (a Preferred Model dropdown)
sections, every field persisting immediately on change.

41 new tests across `settings.py`/`storage.py`/`renderer.py`/
`transcript_step.py`/`settings_window.py`; full suite 1809 passed, 2
skipped; `black`/`flake8`/`mypy` clean on every changed/new file.

## G5: Word Variation Scanner (ADR-0036, 2026-08-29)

The User noticed words still getting through a filtered audiobook,
largely because whisper.cpp's own tokenization doesn't line up with a
catalog entry — a target word transcribed as a different surface form,
or split across two adjacent tokens. This had already come up earlier
(the "shuck"/"shucking"/"sh uck" example is the User's own real-world
one), where the explicit decision was to leave the Matcher
exact-match-only and instead grow the catalog by hand. This is a tool
to make that hand-curation faster, not a reopening of that decision.

New `filter/variation_scan.py` — pure detection logic, no Qt:
`find_word_variations()` finds stem matches (a transcript word that's
a catalog entry's normalized form plus one of a small explicit suffix
set) and split-token matches (two adjacent transcript words, within
the Matcher's own gap tolerance, whose concatenated normalized forms
exactly equal a catalog entry) — both checked against what the real
Matcher already catches, so a suggestion is always a genuine gap. New
`gui/filter/word_variation_dialog.py` (`WordVariationDialog`) shows a
table of suggestions with a short "Why" label, occurrence count, real
context snippet, and a per-row "+ Add" button, wired into `ProfileStep`
as a new "Find More Words…" button.

A real test-infrastructure bug was found along the way: the full suite
segfaulted deterministically at the exact signature ADR-0023 already
diagnosed, but this time `gc.disable()` didn't prevent it — bisection
found `test_word_variation_dialog.py` as the trigger, via real Qt
event-queue growth from repeated `setCellWidget()` calls scheduling old
widgets for `deleteLater()`, not CPython cyclic garbage. Fixed by
flushing the event queue immediately after each such click in the
tests, not touching production code. 29 new tests total; full suite
1840 passed, 2 skipped, stable across repeated runs; `black`/`flake8`/
`mypy` clean.

## G5: Profile Editor tabs + attenuation tooltips (ADR-0037, 2026-08-29)

`ProfileEditorDialog` stacked the category/word tree and the
Attenuation form in one column — the User's real-world profiles grow
to dozens of words across several categories, and the fixed-size
Attenuation box below the tree permanently ate into the tree's own
vertical space even though attenuation is set once and rarely
revisited. Settled on a `QTabWidget` with "Words" and "Attenuation"
tabs (the User's own choice among a few alternatives discussed), a
pure layout change with no change to how either tab's own content
works.

Separately, the six attenuation numbers had no explanation anywhere
beyond their bare labels. The User asked for a tooltip on each, then —
after trying the first version — asked to switch to always-visible
explainer text instead: a tooltip requires already knowing to hover
over the right spot, and doesn't show up in a screenshot. Each field's
label/input row is now followed by its own full-width description
row, plain-language and example-driven rather than citing internal
terms the User has no reason to know.

9 new tests confirm the tab structure (tree under Words, every
attenuation field under Attenuation, found via `findChildren` scoped
to each tab) and the description rows (exactly one per field,
non-empty, distinct from each other, no tooltip left set anywhere).
All 13 pre-existing tests pass unmodified, confirming the redesign is
a real layout-only change. Full suite 1848 passed, 2 skipped;
`black`/`flake8`/`mypy` clean.

## G5: Expanded default profanity seed list (ADR-0038, 2026-08-29)

The default "Profanity" category seeded on first run held only 10
words. The User asked for a more thorough list, specifically wanting
to use an existing, established word list rather than one
hand-invented from scratch. The real candidate, LDNOOBW's open-source
"bad words" list, turned out on inspection to be built for blocking
adult websites, not filtering narration — most of its ~400 entries are
explicit sexual/fetish jargon, and a real number are ethnic/racial/
disability slurs and extremist terms, directly contradicting this
project's own already-stated design principle that the shipped default
is profanity-only, with slur selection left entirely to each User.

Curated LDNOOBW down to its ordinary, everyday-spoken-profanity entries
(plus a few obvious common words it happened to omit), presented to
the User for review before touching anything, and expanded
`PROFANITY_WORDS` from 10 to 29 words once confirmed — everything
sexual/fetish, pornography-site, drug-name, extremist, or
slur-related explicitly excluded, the same boundary the original
10-word list already drew. Scoped to only the default seed for a
brand-new/empty catalog; the User's own existing, already-seeded
catalog is untouched.

2 new tests guard the list itself: no duplicate words, no
blank/whitespace-only entries. Full suite 1850 passed, 2 skipped;
`black`/`flake8`/`mypy` clean.

## G5: Word List click-to-sort (ADR-0039, 2026-08-29)

With the catalog now holding 31+ words in one category, the entries
table's fixed insertion order made it hard to tell whether a word — or
a close variant — was already present before adding what turns out to
be a duplicate. The User asked for a sort function on the "Phrase"
column header. Standard Qt click-to-sort, scoped to the entries table
only. Population had to suspend it: `QTableWidget` re-sorts on every
`insertRow()`/`setItem()` call while sorting is enabled, which can
scatter a row's own cells across the wrong rows mid-populate —
`_refresh_entries()` now disables sorting before repopulating and
re-enables it after, on every exit path.

4 new tests, including a regression guard that specifically reproduces
the population pitfall this ADR guards against. Full suite 1854
passed, 2 skipped; `black`/`flake8`/`mypy` clean.

## G5: Word List rejects duplicate words outright (ADR-0040, 2026-08-29)

`CatalogService.create_entry()` deliberately allows duplicates, and
`CatalogWindow`'s manual "+ Word" flow used that latitude to add the
word anyway, just noting the duplicate afterward. With the catalog now
holding dozens of words, the User asked for this specific flow to
prevent duplicates outright. `CatalogWindow._add_entry()` now checks
`find_duplicate_entry()` before creating anything, using the same
normalized-phrase matching the real Matcher itself uses. If a
duplicate is found, nothing is created and the typed text stays in the
input field so the User can see what triggered the rejection.

A same-day follow-up found the Word Variation dialog had a worse
version of the same gap: its own suggestions are only checked against
the selected profile's entries, so a word already in the catalog under
the same category but not yet part of this profile wasn't excluded at
all. `_populate_table()` now checks for a duplicate per suggestion at
population time and shows "Already in catalog" instead of an active
"+ Add" button.

New tests for both: a duplicate add is rejected and the typed text
remains in the field; the same word in a different category is
correctly not treated as a duplicate; the dialog's "Already in
catalog" label appears for a covered suggestion and doesn't for one in
a different category. Full suite 1860 passed, 2 skipped, stable across
repeated runs; `black`/`flake8`/`mypy` clean.

## G5: Profile Editor word list always alphabetical (ADR-0041, 2026-08-29)

The Edit Profile dialog's word tree showed each category's entries in
catalog insertion order, not alphabetically — with 31+ words in
"Profanity," finding a specific word meant scanning the whole list.
The User asked for automatic alphabetical display, explicitly not a
manual sort control, since this dialog's tree has no column headers to
click in the first place. `_populate_tree()` now sorts each category's
visible entries by `canonical_phrase.lower()` before building tree
items — case-insensitive, so capitalization doesn't distort the order.
Categories themselves stay in catalog order; the request was
specifically about words.

2 new tests: non-alphabetical insertion order still displays
alphabetically; sorting is case-insensitive. All pre-existing tests
pass unmodified. Full suite 1862 passed, 2 skipped; `black`/`flake8`/
`mypy` clean.

## G5: Book 2 residual-hits investigation, and Variation Scanner multi-piece splits (ADR-0042, 2026-08-30)

Investigating why a real filtered book (Carl's Doomsday Scenario, Book
2) still had 18 profanity instances audible after re-transcription
found that 15 of the 18 were never targeted by the render at all — the
original transcript never produced a matchable token for those
instances, confirmed by direct measurement of the real rendered audio.
Three distinct sub-causes, all "recognition," not "timing": multi-piece
splits ("damn" inside "Goddamnit" tokenized as four separate pieces,
beyond the Variation Scanner's existing two-piece check), outright
misrecognition ("fuck" heard as "folk," a different word entirely),
and dropped audio ("bitch" recognized in one independent transcription
of the same audio but nothing at all in another).

Only the first is fixable by extending the scanner's existing
mechanism. `find_word_variations()`'s split-token check generalized
from exactly two adjacent tokens to up to 4, still exact-string-
equality only at every length — 4 chosen because it's exactly what the
real Book 2 case needed with one piece of headroom. Explicitly rejected
adding fuzzy/edit-distance matching to also catch the misrecognition
case: "folk" sits at edit-distance 2 from "fuck," a threshold loose
enough to catch it would also flag real, unrelated words throughout a
transcript, guarded with a permanent regression test.

Verified directly against the real 18-hit dataset: extending to 4
pieces recovers the "goddamn" case (10 real occurrences, previously
invisible) but not the other 14 residuals — disclosed as open,
unsolved problems rather than implied fixed. 6 new tests; all 22 tests
in the file pass; the project's real CI command 1008 passed, 2
skipped; `black`/`flake8`/`mypy` clean.

## G5: Find More Words / Profile step explainer text, Word List move-between-categories (ADR-0043, 2026-08-31)

The User asked for the ability to move a selected word to a different
category from the Word List window — previously the only way was
delete-and-recreate, losing its `enabled`/`mask`/`notes` state and its
`id` (breaking any profile referencing it). `CatalogService.
update_entry()` already accepted an arbitrary field change, so this
needed no service-layer change at all: a new "Move to Category…"
button builds a list of other, non-archived categories as targets,
checks for a duplicate in the target category the same way "+ Word"
already does, and calls `update_entry(entry_id, category_id=target.id)`.

7 new tests, including one real bug found and fixed while writing
them: the first version of the archived-category-exclusion test only
had one active category besides the archived one, so the code took
the "nothing to move to" branch instead of the intended path — fixed
by adding a second real category to the fixture. A same-day addendum
fixed a second, unrelated defect in the same table: opening Word List
showed the Phrase column sorted Z-A by default, not the A-Z a User
expects, since `setSortingEnabled(True)` alone leaves Qt's own default
sort indicator (descending) rather than anything this code had
actually chosen. Fixed with one explicit `sortByColumn(...,
AscendingOrder)` call at construction time.

`tests/gui/filter/test_catalog_window.py` 40 passed (8 new total);
full `tests/gui/filter/` 374 passed; `black`/`flake8`/`mypy` clean.

## G5: Render step's ETA now spans the validate stage (ADR-0044, 2026-08-31)

The User reported the "Est. remaining" label disappears once
"Validating output…" starts, even though that phase can itself cost
minutes — a real filter-report from an earlier investigation recorded
~1.8 minutes of validation against ~8.4 minutes of render for a real
~11.5-hour book, roughly 18% of the combined time, not a negligible
tail. `render()`'s own progress fraction only covers extract→envelope→
attenuate→encode/mux; `validate()` ran after with no progress callback
of its own. Investigating why validation costs what it does found the
real driver: `validate_attenuation()` spawns one real ffmpeg
subprocess per render interval (293 for the book above), at a roughly
uniform ~371ms/interval — knowable in advance, since the interval
count is known the moment Render starts.

Gave `validate_attenuation()`/`validate()` a real progress callback
mirroring `render()`'s own shape, wired through a new
`RenderWorker.validating_progress` signal to the Render step's ETA,
using the same "hardcoded default, then corrected by this run's own
real data" pattern already used for Transcribe and encode+mux. A
related, separately-requested fix: whenever no estimate exists yet,
the "Est. remaining" label now shows "Calculating…" instead of going
blank, which had read as the feature being silently absent rather than
still working on an answer.

7 new tests plus 2 renamed for accuracy across `validator.py`/
`workers.py`/`render_step.py`; `tests/gui/filter/` 378 passed; the
project's real CI command 1011 passed, 2 skipped; `black`/`flake8`/
`mypy` clean.

## G5: Review step explains overlapping-hit merging (ADR-0045, 2026-08-31)

The User asked what happens when two hits overlap — e.g. a single
word and a phrase containing it, both flagged at the same spot. The
real answer, confirmed against the actual code rather than assumed:
they're separate `ScanHit` records merged into one padded
`RenderInterval` at render-plan time, and "Attenuated total" already
reflects that merged duration, not a naive sum — but nothing in the UI
said so. Two small, coordinated additions, iterated on directly with
the User to keep the wording simple and jargon-free: the Render Plan
tab's explainer note now describes overlapping hits being "combined
into one silenced section instead of being treated separately," with a
generic example rather than the real profanity pair that prompted the
question; and "Attenuated total" gained a tooltip explaining that
overlapping time is only counted once.

2 new tests confirm both pieces of text; all 35 pre-existing tests
pass unmodified, since this is additive UI text with no behavior
change. `tests/gui/filter/wizard/test_review_step.py` 37 passed, full
`tests/gui/filter/` 380 passed; `black`/`flake8`/`mypy` clean.

## G5: Persistent file card, cover art, bitrate hint (ADR-0046, 2026-08-31)

Four User-requested UI changes, mocked up together first (a published
artifact iterating on three weighted options for a persistent file
card) then implemented as one batch: a persistent `FileCard` below the
stepper visible across every step from Source through Render, reading
`SourceStep`'s own state directly; real cover art on the Source step
via a new `CoverArtWorker` wrapping the render pipeline's
already-existing `extract_cover_from_audio()` off the UI thread; a
bitrate-hint label next to Render's dropdown when the current
selection matches what `pick_default_bitrate()` picked from the
source's own bitrate; and "Est. storage needed" reworded to "Temp
storage needed" with a tooltip/inline sentence explaining it includes
both temporary working files and the kept output.

Two real bugs caught by writing tests, neither shipped: a race
condition where picking a second file while the first file's cover
extraction was still running could let the stale worker's late result
overwrite the newer selection, fixed with a default-argument
lambda-capture-and-compare pattern already used elsewhere in this
package; and the bitrate hint would have claimed a match even when the
source's bitrate was unknown and the app fell back to a generic
default, fixed by only recording the auto-pick when a real source
bitrate existed.

Real use the same day surfaced four follow-on refinements: the
persistent card's badge read "✓ Eligible" on every step even after the
check itself was long past, settled on "✓ Verified" after discussion
(rejecting "Ready," which collides with each step's own notion of
readiness); the Source step's cover thumbnail was visibly shorter than
the six rows of info text beside it, fixed by sizing the thumbnail to
the info panel's own measured height instead of a fixed guess; the
User pointed at the base app's own "Build" panel (a placeholder cover
box and blank fields before any file loads) and asked the wizard's
persistent `FileCard` to match, since it previously stayed hidden
entirely until a manifest existed; and a follow-up screenshot showed
the Source step's own larger info panel still had the same gap, plus a
deeper problem once fixed the first way — the placeholder disappeared
during inspection and its height didn't reliably match the real
panel's, since they were two separate widget instances. Root-caused
and fixed properly per the User's own explicit choice of "fix it
properly, not incrementally": one persistent `_InfoPanel`, built once
and updated in place, never torn down and rebuilt. A regression test
written for this caught two real, distinct height bugs in sequence —
row values weren't guaranteed single-line (fixed with eliding), and
the panel's own heading was hidden outright in the placeholder state,
which Qt's layout system skips entirely when computing height (fixed
by keeping it always visible and toggling only its text/QSS state).

26 new tests across the initial batch, plus further tests for each
addendum; full `tests/gui/filter/` 416 passed; `black`/`flake8`/`mypy`
clean throughout.

## G5: Configurable scratch/temp folder, with a shortcut from Source (ADR-0047, 2026-08-31)

Discussed before building, per the User's own request: a new Settings
option to redirect where render/transcription scratch files go — a
real audiobook can need 50+ GB at once, the same mechanism behind an
earlier session's real 24GB orphaned-temp-directory cleanup — plus a
possible shortcut from the Source step once a User sees that figure.
Traced the real call sites before proposing anything: `render()`'s own
`TemporaryDirectory()` (the actual big consumer — full-length
`source.pcm`/`envelope.pcm`/`filtered.pcm`) and
`transcription_orchestrator.py`'s per-chunk WAV extraction both used
the bare OS default with no override; a different, small
`TemporaryDirectory()` inside `renderer.py`'s own progress-streaming
helper was correctly left alone. Also found `storage.py` already had
an unused `cache_root()` whose own docstring already described it as
being for exactly this — nothing called it yet.

New `settings.py` key `temp_dir` and `storage.temp_root()`, mirroring
`models_dir()`/`transcripts_dir()`'s existing override pattern exactly
— local `settings.get()` import, no new parameters threaded through
`render()`'s or the orchestrator's signatures. Settings window gained
a fourth folder row ("Temp Files"), reusing `_build_folder_row()`
unchanged in shape, plus a new optional `tooltip` param on that
helper. Source step's info panel gained a "Change temp storage
location…" link right on the "Temp storage needed" row itself, present
in both the placeholder and eligible states equally rather than
toggled, forwarded up through `SourceStep` → `WizardWindow` →
`MainWindow._show_settings_window`. Deliberately left alone:
`cover.py`'s own, unrelated `get_temp_root()` — raised during the
discussion and judged not worth folding a base-app utility into this
feature's settings for a few MB of preview data.

Driven in the real running app immediately after, not just unit tests
— caught two things a widget-level test couldn't: the link's own
wording ("Change location…") was vague without the row it sat next to
for context, renamed to "Change temp storage location…"; and the
Settings window's new fourth row pushed total content past its old
hardcoded `resize(720, 380)`, visibly clipping the last row's
Browse/Reset buttons. Fixed by sizing the window off its real built
content (`centralWidget().sizeHint()`) instead of a guessed constant,
and giving each folder row a deliberate spacing differential (tight
within one setting's own label-and-path, generous between different
settings).

5 new/updated test files, full project suite 1936 passed / 2 skipped,
`black`/`flake8`/`mypy` clean.

## G5: Real disk-space audit, and get_temp_root()'s own leak fix (ADR-0047 addendum, 2026-08-31)

The User reported ~25GB of unaccounted-for disk space. Real
investigation, not speculation: `du`/`df` against `$TMPDIR` found 20GB
was stale `pytest-of-nate/pytest-<N>` session directories (the
model-manager test suite's realistic-sized fake `ggml-*.bin` fixtures,
~4GB/session, across five sessions that outlived pytest's own default
retention) — unrelated to this feature, deleted directly. A separate
~7MB across ~40 directories was `get_temp_root()`'s own leak
(`m4bmaker/utils.py`, backing `cover.py`'s preview extraction) — the
same mechanism behind this whole engagement's earlier, real 24GB
orphaned-temp incident, still leaking at a much smaller scale.

Root cause: its cleanup is `atexit`-only, which skips a crash,
force-quit, or `kill`/`pkill` entirely — no signal handler closes that
gap, since `SIGKILL` is uncatchable by design. Fixed with a
startup-time sweep instead of relying solely on a shutdown-time
promise: directory names now embed the owning PID
(`m4bmaker_<pid>_<random>`), and each first `get_temp_root()` call in
a process sweeps sibling directories first, removing only those whose
embedded PID is confirmed dead and leaving everything else — including
unrecognized pre-fix names — alone. Windows carve-out found while
writing this: `os.kill(pid, 0)` calls `TerminateProcess()` under the
hood there, not a safe existence probe, so the liveness check always
reports "alive" on Windows instead of risking that, leaving it on its
pre-existing (unchanged) behavior.

Verified with real OS processes, not just mocks: spawned a subprocess
that calls `get_temp_root()`, `SIGKILL`'d it before `atexit` could run
(confirmed the directory survives), then spawned a second process and
confirmed its own `get_temp_root()` call swept the first one's
dead-pid directory away. 9 new unit tests, full suite 1948 passed / 2
skipped, `black`/`flake8`/`mypy` clean.

## G5: py-review findings — transcript lookup, confirmation defaults, atomic report write (2026-09-04)

First run of the new `/py-review` skill against this whole feature,
covering `filter/` and `gui/filter/` end to end (models, job/catalog
persistence, matcher/scan, chunking, renderer, and the wizard steps).
Five real findings; all addressed.

**Transcript lookup — real, measured perf fix.** `find_compatible_transcript()`
fully parsed (built every `TranscriptWord`/`TranscriptSegment` for)
every saved transcript on every Source-step file selection, real cost
1.5-5s once a real transcripts directory (~20 files, 321MB) had built
up. The obvious fix — key the lookup off the fingerprint-derived
filename `transcribe_step.py` already uses when saving — turned out
wrong: the existing test suite explicitly exercises arbitrary
filenames, "most recent wins" on multiple matches, and tolerating one
corrupt file alongside a good one, none of which a pure filename
lookup would preserve. Landed a two-pass version instead: a cheap raw-
JSON peek at just `source.fingerprint`/`status` first, full parse only
for the most-recent-first candidates that peek-matched, falling
through to the next if a full parse still fails — same per-file
semantics as the old single-pass version, just spread across two
passes. Measured against a real-scale fixture (20 files, 477MB, 150k
words each, this project's own reference-audiobook scale): 3.29s ->
1.40s, a real 2.3x, not a full elimination (`json.loads()` of every
file's raw text is still unavoidable without a persistent index).

**Destructive confirmations now default to No.** `QMessageBox.question()`
for "Delete Category"/"Delete Word" (`catalog_window.py`), "Remove
Model" (`model_manager_window.py`), and "Archive Profile"
(`profile_step.py`) didn't set an explicit `defaultButton` — this
codebase already knows the fix (its own "close while a job is running"
dialogs do set `defaultButton=No`), just hadn't applied it to the
actual delete confirmations, arguably the higher-stakes case. All four
now explicit.

**Filter report now written atomically.** `write_filter_report()` used
a bare `path.write_text()` — the one JSON artifact in this codebase not
going through `storage.write_json_atomic()`, so a crash mid-write could
leave a corrupt report next to an otherwise-good render. Now consistent
with every other persisted JSON here.

**Benchmarked, not changed:** `matcher.py`/`variation_scan.py`'s
self-disclosed-as-unbenchmarked O(words × catalog) scans, against the
same 150k-word real-book scale. At the actual default seeded catalog
(29 words): `scan_transcript()` 0.38s, `find_word_variations()` 0.89s
— both fine. Pushed to a heavily-customized 200-entry catalog (~7x the
default): 2.0s and 5.1s — scales roughly linearly with catalog size as
the module's own docstring predicted, noticeable but not severe, and
`find_word_variations()` is an explicit on-demand action, not part of
every scan. No code change — real evidence now backs "fine as-is,"
closing the disclosed uncertainty without guessing.

**Re-examined, not changed:** `compute_fingerprint()`'s placeholder
collision risk (`media_inspector.py`) — the review had flagged this as
higher-stakes if the transcript-lookup fix shipped in its originally-
proposed filename-keyed form, since a collision would then alias two
different books' transcript files directly. It didn't ship that way
(see above) — the shipped fix still matches on `source.fingerprint`
exactly as before, so the collision risk is unchanged from what it's
always been. Still a tracked, deliberate open decision (ADR-0001/O-02),
correctly left alone rather than patched ad hoc outside that process.

7 new/updated tests (2 for the transcript lookup, 4 for the
confirmation defaults, 1 for the atomic report write). Full suite 1955
passed / 2 skipped, `black`/`flake8`/`mypy` clean.

## G5: "About Language Filter" menu item and dialog (ADR-0048, 2026-09-04)

The User asked for an "About"-style item in the Language Filter menu
giving a user-friendly overview of what the feature does and its real
capabilities — informational only. The base app already has its own
"About m4Bookmaker" (Help menu, credits/version/links, hardcoded
light-only colors) — the wrong shape and scope for a feature tour, so
this got its own dialog rather than extending that one.

New `gui/filter/about_dialog.py` (`AboutLanguageFilterDialog`), built
fresh on every open since there's no state to preserve between opens.
A centered header (title, tagline, a one-line "how it works" naming
the real wizard flow) above a scrollable list of feature cards
covering, in the order a User actually encounters them: Word List,
Filter Profiles, Automatic Transcription, Smart Scanning, Review
Before You Commit, Filtered Render, and Models & Storage — real,
theme-aware styling via the app's existing `get_stylesheet(dark)`,
unlike the base app's own About dialog. New "About Language Filter"
menu item, placed right after the wizard action with its own
`MenuRole.NoRole` (same defensive pattern already used for "Settings…"
in this menu — Qt's macOS integration would otherwise try to relocate
an "About"-titled action into the app's own top-level menu, colliding
with the base app's own About entry there).

9 new tests; full suite 1965 passed, 2 skipped; `black`/`flake8`/`mypy`
clean. Verified in the real running app in both dark and light mode,
resized to confirm all 7 feature cards render correctly, not just the
ones visible without scrolling.

Real use immediately asked for the obvious next step: at its original
520px width, the dialog needed real scrolling to reach the last few
cards. Feature cards now lay out two-across in a grid instead of one
long column — 7 cards become 4 short rows instead of 7 tall ones — and
the dialog widened from 520×640 to 860×600 to fit it, confirmed
against the real running app with no scrollbar needed at the default
size. The scroll area stays as a fallback for an unusually small
screen; it just isn't exercised at any normal size anymore. No test
changes needed — the existing tests check content, not layout shape.

A screenshot from the User's own machine showed that fix wasn't
actually enough: the fixed 860×600 default still clipped the bottom
row on a real screen, no scrollbar visible. Two compounding causes:
860×600 was itself still a guessed constant, and even a measured
`sizeHint()` would have kept undercounting, since every card
description is a word-wrapped `QLabel` — a plain `sizeHint()` reports
the *unwrapped* single-line height for those; only `heightForWidth()`
at the real target width reflects how many lines it actually wraps
to. The same root lesson as the Settings window's own sizing bug
(ADR-0047 addendum), compounded by a second gotcha specific to wrapped
text. Fixed by measuring `heightForWidth(860)` on the header's and
body's own layouts directly and summing with the footer's `sizeHint()`,
capped at 90% of the screen's available height. Separately, per the
User's request, the combined "Models & Storage" card split into two —
"Manage Transcription Models" and "Settings," each mapping to a real,
distinct window — bringing the total to an even 8 cards (4 clean rows
of 2). Verified against the real running app in both themes: opens at
860×707 (computed, not guessed) with all 8 cards visible, no scrollbar
in either theme. Full suite 1965 passed, 2 skipped; `black`/`flake8`/
`mypy` clean.

Moved "About Language Filter" to the very bottom of the menu, after
its own separator following Settings — it had originally been placed
second, right after the wizard action, which read as more prominent
than an informational item belongs. Verified in the real running app:
Filter Audiobook Language… / Word List… / Manage Transcription
Models… / Settings… / About Language Filter, in that order. Full
suite 1965 passed, 2 skipped; `black`/`flake8`/`mypy` clean.
