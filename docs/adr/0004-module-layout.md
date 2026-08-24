# ADR-0004 (O-08): New module/package layout within the fork

**Status:** Decided for G1 scaffolding purposes — informal, pending explicit
Contributor confirmation before G2 builds further on it.
**Related PRD items:** O-08, §14.2, §17.4 G1

## Decision

All new filtering-feature code lives under a new `m4bmaker/filter/`
subpackage, mirrored by `tests/filter/`. The existing top-level modules
(`models.py`, `pipeline.py`, `encoder.py`, `m4b_editor.py`, `metadata.py`,
`preflight.py`, `utils.py`, `cli.py`, and everything under `gui/`) are
**not modified** by G1 — the filtering feature is additive, imports *from*
the existing modules where it reuses their logic (ffprobe invocation
helpers, `subprocess_flags()`, `find_ffmpeg()`), and never the reverse. The
existing `Book`/`Chapter`/`BookMetadata`/`PipelineResult` dataclasses in
`models.py` stay exactly as they are; the new `MediaManifest` in
`filter/models.py` is a distinct type for a distinct purpose (inspecting an
*existing* M4B for filtering, vs. `Book`'s role of describing files about
to be *converted into* one).

## Rationale

- Keeps the blast radius of this large feature at zero on the existing,
  well-tested (1013 passing tests) conversion pipeline — a regression in
  filtering code cannot touch chapter/metadata conversion correctness.
- Matches PRD §17.1 rule 3 ("keep changes small and reversible") and rule 4
  ("do not change the stack") — this is purely additive.
- Gives the Job Orchestrator, Catalog Service, Transcript Store, Matcher,
  Interval Planner, Renderer, and Validator (PRD §14.2) a single natural
  package to live in without inventing per-module top-level packages.
- If the feature is ever reverted or split into a separate installable
  extra, `m4bmaker/filter/` is a clean deletion/extraction boundary.

## Package layout (G1 scope — schemas/interfaces/inspector only)

```
m4bmaker/filter/
├── __init__.py
├── models.py            # domain dataclasses (§9.1, §14.4) + validation
├── jobs.py               # JobState/JobType enums + transition matrix (§11.2)
├── storage.py            # platformdirs-based artifact roots, atomic JSON I/O
└── media_inspector.py     # AAC M4B eligibility + primary-track selection (§6.1, D-09)

tests/filter/
├── __init__.py
├── test_models.py
├── test_jobs.py
├── test_storage.py
└── test_media_inspector.py
```

Not yet created in G1 (later gates, per PRD §17.3): `catalog.py` (G2),
`matcher.py`/`interval_planner.py` (G2), `transcript_engine.py`/
`model_manager.py` (G3), `renderer.py`/`validator.py` (G4), any UI code
under `gui/filter/` (G5).

## Import direction

`m4bmaker/filter/*` may import from `m4bmaker/utils.py` (process helpers),
`m4bmaker/preflight.py` (ffprobe patterns, read-only reference — not
imported directly in G1 since `preflight.py`'s `FileInfo`/`probe_file` are
scoped to source-folder conversion; `media_inspector.py` implements its own
ffprobe call shaped for single-existing-M4B, multi-track inspection, kept
separate to avoid overloading `preflight.py`'s existing contract). Nothing
outside `m4bmaker/filter/` imports from it yet — G1 ships no UI wiring.
