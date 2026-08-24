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

**Still not built, and these modules should not be mistaken for it:** the
durable orchestration around chunking — persisting each committed chunk,
wiring pause/resume through the `JobState` machine, compatibility
re-verification on resume — needs the SQLite persistence contract from PRD
§14.3, which is still an explicitly undecided item (§14.3: "Before
implementation, define one authoritative source for each entity..."). No
Model Manager UI exists either — only the backend service. No
throughput/memory benchmarking has been run.

Cumulative: 217 tests in `tests/filter/` + 3 in `tests/test_utils.py`,
99% coverage on `m4bmaker/filter/`, `black`/`flake8`/`mypy` clean, full
suite — 1233 passing, 1 correctly skipped (the opt-in real-binary test).

Not yet started: the durable Job Orchestrator + SQLite persistence
(blocks finishing G3's pause/resume requirement); renderer and validator
(G4, blocked on ADR-0002 approval and its own render-correctness spike);
any UI (G5).

No code in this fork renders audio yet, and the one real STT path that
exists is a proof spike, not a production job — by design, per the PRD's
own gate protocol (§17.3-§17.4).
