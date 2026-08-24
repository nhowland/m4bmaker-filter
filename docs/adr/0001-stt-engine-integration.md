# ADR-0001 (O-02): STT engine integration strategy

**Status:** **Accepted 2026-08-24.** Product owner confirmed Option A
(whisper.cpp as a bundled subprocess binary) as the direction. The specific
open items in the "Open questions" section below (exact release tag/commit
to pin, vendored-build vs. prebuilt-binary trust model, CPU-only vs.
GPU-accelerated build) remain unresolved and must still be settled — with
evidence, not by default — before G3 implementation begins.
**Decision needed by:** Milestone 1 implementation (G1 exit does not require
this; G3 cannot start without the remaining open items resolved)
**Related PRD items:** D-10, D-11, D-13, D-14, O-02

## Context

PRD D-10 already commits the product to "Whisper through whisper.cpp or the
stack-appropriate maintained equivalent." This ADR is about *how*, not
*whether*: exact integration mechanism, model source/licensing, storage
layout, and the benchmark/test plan G3 must execute — not a re-litigation of
the Whisper decision itself.

## Options considered

### Option A — `whisper.cpp` as a bundled subprocess binary (recommended)

Invoke a compiled `whisper-cli`/`whisper-stream`-equivalent binary via
`subprocess`, following the exact discovery pattern already used for ffmpeg
(`m4bmaker/utils.py:_which()` → extend to `find_whisper()`), and the exact
bundling pattern already used for `static-ffmpeg` in the PyInstaller specs
(§7 of the G0 discovery doc).

- **License:** whisper.cpp is MIT-licensed (ggerganov/whisper.cpp). OpenAI's
  Whisper model weights (`ggml` GGUF-converted variants) are distributed
  under MIT. Both are GPL-3.0-or-later compatible in the direction this
  project needs (a GPL project may include/depend on MIT code). **This must
  still be re-verified against the exact model files selected** (checksum,
  exact license file, redistribution terms) before G3, per PRD §12.5/§17.1
  rule 5 — stated here as the expected outcome, not a substitute for that
  verification.
- **Why subprocess over a Python binding:** the existing app has zero
  Python ML/audio-decode dependencies (no PyTorch, no NumPy even). A
  compiled binary invoked via `subprocess` keeps the Python dependency
  surface unchanged (same `mutagen`/`natsort`/`platformdirs`/`PySide6` set),
  keeps the packaged app size and attack surface predictable, and reuses
  100% of the existing process-invocation, cancellation, and Windows
  `CREATE_NO_WINDOW` handling in `utils.py`/`encoder.py` verbatim. This is
  the same shape as the project's own architecture principle already
  encoded in D-03 ("preserve the existing stack").
- **Model format:** `ggml`/GGUF quantized model files, downloaded on demand
  per D-11 (never bundled — sidesteps redistribution-size and
  license-bundling questions for the model itself, only the engine binary
  needs to be bundled).
- **Model storage location:** OS-appropriate per-user app-data dir via
  `platformdirs.user_data_dir()` (same library the project already depends
  on), e.g. `~/Library/Application Support/m4bmaker/models/` on macOS,
  `%LOCALAPPDATA%\m4bmaker\models\` on Windows — consistent with PRD §12.4.
- **Word-level timestamps:** whisper.cpp supports token-level timestamps
  natively (`--max-len`/`-ml` and word-timestamp flags depending on build);
  exact CLI flags and JSON output schema must be pinned to a specific
  whisper.cpp release tag as part of this ADR's finalization, not left
  floating on "latest."

### Option B — `faster-whisper` (CTranslate2) as an in-process Python dependency

- Pros: pure-Python integration, no subprocess plumbing, mature word-timestamp API.
- Cons: pulls in CTranslate2 + (depending on backend) a sizeable native
  runtime; meaningfully larger PyInstaller bundle than the current app;
  changes the dependency risk profile the existing project has deliberately
  kept minimal (three small pure-Python runtime deps today). Contradicts
  D-03's "preserve the existing stack" more than Option A does.
- Not recommended as the default, but recorded here as the fallback if G3
  benchmarking finds whisper.cpp's CLI output/timestamp granularity
  insufficient for PRD §10.5's accuracy gate.

### Option C — OpenAI's reference Python `whisper` package

- Rejected outright: requires PyTorch, is not optimized for CPU-only
  offline desktop use, and is far heavier than either option above for no
  accuracy benefit over whisper.cpp on the same model weights.

## Recommendation

**Option A.** It is the only option that doesn't expand the Python
dependency/runtime surface, reuses existing, tested subprocess/discovery/
packaging code paths near-verbatim, and matches D-03 most closely.

## Supported OS behavior

- macOS: bundle a universal or per-arch (`arm64`/`x86_64`) whisper.cpp build
  the same way `static-ffmpeg` is bundled today; verify against O-06's final
  minimum-OS decision.
- Windows: bundle a prebuilt `.exe`, same PyInstaller data-file mechanism as
  `ffmpeg.exe`.
- Both: no system-wide install step required by the user, consistent with
  the existing "ffmpeg is bundled — nothing else to install" promise in the
  README.

## Resource benchmarks (open — G3 deliverable, not fabricated here)

Per PRD §13.1 and §17.4 G3 exit evidence, the following must be *measured*
on named reference hardware before this ADR can close, not estimated:
peak memory for `base.en` and `small.en`, transcription throughput
(real-time factor) for both models, disk footprint per model, and pause
latency achievability at the chunk duration chosen in §11.3. No numbers are
asserted here — this section exists to record that the benchmark is owed,
by whom (Contributor sign-off gate), and against what (G3 named fixtures).

## Test plan

- Deterministic golden-output test: fixed short WAV fixture → whisper.cpp →
  assert exact word list + timestamp tolerance, pinned to a specific model
  checksum and engine build so the test doesn't drift with model updates.
- Chunk-boundary test: a fixture with a target word intentionally spanning a
  chunk boundary, verifying the overlap/dedup logic in PRD §11.3.
- Corrupted/interrupted download recovery test (checksum mismatch → retry,
  not silent accept).
- Offline-after-install test: network disabled, transcription still
  succeeds once model+engine are verified present.

## G3 spike findings (2026-08-24)

A narrow spike was run to prove the integration end-to-end before writing
any more of it on assumption. Scope, as agreed with the product owner:
prove the mechanism on a short synthetic fixture with the smallest model;
do **not** run the base.en/small.en benchmark suite yet.

**What was done:**
1. Installed `whisper-cpp` 1.9.2 via Homebrew (`brew install whisper-cpp`,
   MIT license, confirms the assumption above) for local dev-environment
   proof — this is *not* the shipping distribution mechanism, which stays
   "pinned prebuilt binaries bundled via PyInstaller" per the decision
   below; Homebrew was only the fastest way to get a real binary to spike
   against.
2. Downloaded `ggml-tiny.en.bin` (74MB) directly from
   `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin`
   and computed its SHA-256 myself: `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f`.
3. Generated a synthetic speech fixture via macOS `say` (legally clean, no
   copyrighted audio) — "This is a test of the darn filtering system. Go
   to hell and back, my friend." — converted to 16kHz mono PCM WAV via
   ffmpeg (whisper.cpp's required input format).
4. Ran `whisper-cli -m ggml-tiny.en.bin -f speech.wav -oj -ojf --no-gpu`
   and inspected the real JSON output structure directly.
5. Implemented `m4bmaker/filter/transcript_engine.py` against that real
   structure (not the docs, which don't describe the JSON schema in this
   level of detail) and verified it end-to-end with both a mocked test
   suite and one real-binary, real-model integration test
   (`tests/filter/test_transcript_engine.py::TestRealWhisperIntegration`,
   opt-in via `M4BMAKER_WHISPER_MODEL` env var, skipped by default/in CI
   since no model file is committed to the repo per D-11) — **that real
   test passed**, correctly recognizing both target words ("darn" at
   1060–1250ms, confidence 0.75; "hell" at 2900–3200ms, confidence 0.47).

**What this found that wasn't knowable without running it:**

- **whisper.cpp's own model-download script does no checksum verification
  at all** (`models/download-ggml-model.sh` relies purely on HTTP status
  codes). This matters directly for PRD D-11/§10.1's checksum-verification
  requirement: the future Model Manager cannot delegate to or mirror
  upstream's download script and call the requirement satisfied — it must
  compute and verify its own checksum against a value *this project* pins
  and records, independent of anything whisper.cpp ships. The checksum
  above is this project's own, not one whisper.cpp published.
- **`--no-gpu` is a plain runtime flag, not a separate build.** The same
  Homebrew binary auto-detects and uses Metal on Apple Silicon unless told
  not to. This resolves a concern raised in ADR-0003: shipping "CPU-only
  for v1" does not require sourcing/building a different binary — it's one
  flag, always passed, on every platform. Simplifies the packaging story.
- **whisper.cpp's full-JSON output (`-ojf`) is token-level, not strictly
  word-level**, and mixes non-word marker tokens into the same list as real
  words: a `[_BEG_]` sentinel at the start, punctuation as standalone
  tokens (`"."`, `","`) with their own (often zero-width) timestamps, and
  an internal `[_TT_N]` timestamp token at the end. None of this is
  documented anywhere findable — `whisper_result_to_segment()` filters all
  three categories out, verified against the real captured shape (now a
  test fixture, so a future whisper.cpp upgrade that changes this shape
  will fail loudly rather than silently miscount words).
- **Per-token confidence (`p`) is a real, variable signal**, not a
  formality: 0.75 for "darn" (a less common word to a `tiny.en` model) vs.
  0.47 for "hell" in this run — worth preserving as-is in the transcript
  schema rather than discarding or normalizing away.
- **Token text carries a leading space** (`" darn"`, not `"darn"`) that
  must be stripped before normalization — an easy silent-bug source if
  missed (would make every word fail to match its catalog entry, since
  `normalize_token(" darn")` and `normalize_token("darn")` differ only by
  whether the leading space survives into the stored `text` field; the
  `normalized` field is unaffected since normalization strips whitespace,
  but the raw `text` field displayed to the User in scan review, PRD §9.4,
  would show a stray leading space on every single word without this).

**What remains explicitly unresolved (see the "Open items requiring G4
fixture evidence" section above, now joined by G3-specific ones below):**
- Chunking strategy for multi-hour sources (PRD §11.3) — this spike proved
  the single-call mechanism works; it says nothing about how to split a
  20-hour book into durable, resumable chunks with correct
  overlap/deduplication at boundaries.
- Throughput/memory benchmarking of `base.en`/`small.en` on named reference
  hardware (PRD §13.1) — deliberately deferred, not started.
- The Model Manager itself (download UI, checksum verification wiring,
  storage/removal) — none of it exists; this spike downloaded its model
  with a bare `curl` command outside the app.

## Open questions for Contributor decision

1. Exact whisper.cpp release tag/commit to pin, and its own dependency
   footprint (some builds use Core ML/Metal on macOS or CUDA on Windows —
   must decide whether to ship a CPU-only build for predictability or a
   GPU-accelerated build with a CPU fallback; recommend CPU-only for v1 to
   keep the benchmark story simple, revisit post-MVP).
2. Whether to vendor a build script (project builds whisper.cpp from source
   as part of CI) vs. downloading prebuilt release binaries and pinning
   their checksums. Vendoring the build is more auditable but adds CI
   complexity; recommend starting with pinned prebuilt binaries + checksum
   verification, matching the model-download trust model in D-11.
