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
**G1 (Foundations): in progress.** Delivered so far: domain schemas
(`m4bmaker/filter/models.py`), the job state machine
(`m4bmaker/filter/jobs.py`), storage-path/atomic-write helpers
(`m4bmaker/filter/storage.py`), and AAC/M4B eligibility inspection with
primary-track selection (`m4bmaker/filter/media_inspector.py`) — 86 tests,
98% coverage on the new code, `black`/`flake8`/`mypy` clean, full existing
suite (1013 tests) still green.

Not yet started: catalog CRUD service, matcher, interval planner (G2);
model manager and transcription engine integration (G3, blocked on ADR-0001
approval); renderer and validator (G4, blocked on ADR-0002 approval); any
UI (G5).

No code in this fork downloads a model, calls an STT engine, or renders
audio yet — by design, per the PRD's own gate protocol (§17.3-§17.4): G1
is schema/interface work only.
