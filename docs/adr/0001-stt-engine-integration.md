# ADR-0001 (O-02): STT engine integration strategy

**Status:** **Accepted 2026-08-24.** Product owner confirmed Option A
(whisper.cpp as a bundled subprocess binary) as the direction. **GPU vs.
CPU-only resolved 2026-08-25** (see "GPU acceleration — reversed with real
benchmark evidence" below): use GPU when available, CPU as automatic
fallback — reversing this ADR's original CPU-only-for-v1 recommendation.
The remaining open items (exact release tag/commit to pin, vendored-build
vs. prebuilt-binary trust model) are still unresolved and must still be
settled — with evidence, not by default — before G3 implementation begins.
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
(real-time factor) for both models, and disk footprint per model. (Pause
latency achievability at the chosen chunk duration was originally listed
here too — no longer applicable in the same way now that pause latency
is not a chunk-sizing constraint, PRD D-16 revised 2026-08-25; actual
pause latency is still recorded in diagnostics, just not benchmarked
against a target.) No numbers were asserted here originally — this
section existed to record that the benchmark was owed, by whom
(Contributor sign-off gate), and against what (G3 named fixtures).

**Partially measured now** (see "GPU acceleration" above for the full
figures): peak memory and RTF for **both** `base.en` and `small.en`,
both CPU and GPU, on one real machine (Apple M4 Pro) against real
chapters from the actual reference book. Still genuinely open: any
measurement on non-Apple-Silicon hardware, and formal sign-off against
named reference hardware per §13.1's original bar, which one dev machine
doesn't satisfy on its own even though it's real data rather than none.

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

## Chunking algorithm validated against real output (2026-08-24)

`m4bmaker/filter/chunking.py`'s `plan_chunks()`/`merge_segment_words()`
(timestamp-ownership overlap deduplication, PRD §11.3) is unit-tested
against synthetic data, but was also sanity-checked against a real
boundary-cutting failure: the G3 spike's `speech.wav` was split into two
overlapping chunks (`[0, 3.52s)` and `[2.0s, 4.54s)`, a ~1.5s overlap) and
each transcribed independently with `whisper-cli`.

The first chunk's transcription cut off mid-sentence — "Go to." — losing
"hell" entirely, because the chunk boundary landed inside that word's
audio. The second, overlapping chunk transcribed the same region with more
trailing context and produced "go to hell and back, my friend." in full.
This is precisely the failure mode chunk overlap exists to prevent, and
confirms the ownership-handoff design (the *later* chunk wins the overlap
region, since it has trailing context the earlier one lacked) is the right
call — a naive non-overlapping split would have silently dropped a target
word from the transcript, which for this feature specifically means a
profanity hit gets missed. No numeric merge assertions are drawn from this
manual run (that precision lives in the unit tests); it exists as
real-world motivation and confirms the algorithm's design intent holds up
outside synthetic fixtures.

## GPU acceleration — reversed with real benchmark evidence (2026-08-25)

**This ADR originally recommended CPU-only for v1** ("keep the benchmark
story simple, revisit post-MVP" — see the superseded open question below,
struck through). The product owner revisited that call once real benchmark
data existed to decide it on, rather than by default — the thing this ADR
itself said should happen before settling it.

**What was measured:** the same real 13.5-hour reference audiobook used
throughout this fork (ADR-0007), two real chapters extracted directly from
it (18.2 min, near the book's own median chapter length of ~16.25 min
across all 50 real chapters; and 28.8 min, the longest chapter in the
book), **both approved models** (`base.en` and `small.en`), this dev
machine (Apple M4 Pro, Metal backend), CPU run (`--no-gpu`) vs. GPU run
(flag omitted) on identical audio:

| Model | Backend | 18.2 min chapter | 28.8 min chapter | RTF (avg) | Peak mem (avg) |
|---|---|---|---|---|---|
| base.en | CPU | 57.7s | 95.5s | 0.054 (≈18.5x) | ~779 MB |
| base.en | GPU | 14.1s | 22.6s | 0.013 (≈77x) | ~625 MB |
| small.en | CPU | 164.1s | 267.5s | 0.153 (≈6.5x) | ~1.3 GB |
| small.en | GPU | 29.4s | 47.4s | 0.027 (≈37x) | ~1.08 GB |

Extrapolated to the full 13.5-hour book:

| Model | CPU | GPU |
|---|---|---|
| base.en | ~44 min | ~11 min |
| small.en | ~2h 4m | ~22 min |

Two things worth calling out beyond the raw numbers. First, **GPU
speedup is larger for the bigger model, not smaller** — 4.1-4.2x for
`base.en`, 5.6x for `small.en` on both chapters — more compute to offload
means the fixed per-call overhead matters proportionally less, so GPU is
if anything *more* valuable for the heavier model. Second, **`small.en`'s
real CPU-vs-GPU cost relative to `base.en` isn't fixed** — about 2.8x
slower than `base.en` on CPU, but only ~2.1x slower on GPU — so "Higher
recognition effort" costs meaningfully less than its CPU-only number
alone would suggest once GPU is available. On this machine, `small.en` on
GPU (~22 min for the full book) finishes faster than `base.en` did on CPU
alone (~44 min) in the original round.

In all four combinations, peak memory scaled with model size as expected
(~1.3GB for `small.en` vs. ~780MB for `base.en`, CPU) and GPU runs used
*less* memory than CPU runs for both models, not more. Transcript text
between CPU and GPU runs is not byte-identical for either model (e.g.
177 vs. 191 segments on `small.en`'s 18.2-minute chapter) — the same
expected floating-point/backend variance already noted for `base.en`, not
a quality regression; all four runs were spot-checked directly and read as
clean, accurate, coherent transcriptions of the real content.

**The decision:** use GPU acceleration when available; CPU remains the
automatic fallback, not a separate mode the User picks. Mechanically, this
needs no new detection code and no packaging change — exactly the finding
that unblocks it: `--no-gpu` is (per this ADR's own G3 spike) a plain
runtime flag on the one standard whisper.cpp build already being shipped.
The concrete change is to **stop always passing `--no-gpu`** and let
`ggml`'s own backend selection do what it already does by default —
initialize a compatible GPU backend if one exists, fall back to CPU
transparently if not. `whisper-cli` already prints a real, checkable
device-probe line to stderr confirming which backend initialized (e.g.
`ggml_metal_device_init: GPU name: MTL0 (Apple M4 Pro)`) — the same
"parse the CLI's own output for a fact it doesn't document elsewhere"
pattern `get_whisper_version()` already uses, useful if the UI later wants
to surface which backend is active (diagnostics/Model Manager), but not
required just to get the speedup.

**What this evidence does not cover, disclosed rather than assumed away:**
Apple Silicon/Metal only, one machine. Both approved models are now
covered (`small.en` benchmarked 2026-08-25, same method as `base.en`
above). Windows (CUDA) and Linux GPU paths are architecturally supported
by whisper.cpp but completely unverified here — ggml's graceful CPU
fallback when no compatible GPU exists is a well-established pattern for
this tool family, not something untested-and-assumed, but it hasn't been
confirmed on non-Metal hardware within this project. That verification is
the natural next real test before this is trusted across every supported
platform — flagged as follow-up, not blocking this decision, which
already has real evidence behind it (both models now) where the original
recommendation had none.

## Chunking strategy: chapter-sized chunks, pause-latency deprioritized (2026-08-25)

**The decision:** chunk boundaries follow chapter markers by default — one
chunk per chapter — rather than a small fixed duration chosen to keep
pause-click response fast. PRD D-16, §11.3, and §15.3's pause-latency
acceptance criterion are all revised in `docs/PRD.md` accordingly; this
section is the rationale record for that revision, the same relationship
this ADR already has with D-16's original text.

**Why:** designing Transcribe's real progress/chunking behavior surfaced
an architectural fact — `run_whisper()` shells out to `whisper-cli` as a
fresh subprocess per chunk, reloading the full model from disk every
single invocation, with no persistent/resident model across chunks. A
small, pause-latency-driven chunk size (the 30s this ADR was working
toward before this revision) multiplies that fixed reload cost by a large
factor — nearly 2,000 invocations for this fork's 13.5-hour reference
book. The product owner's call: User-initiated pause mid-transcription is
an edge case, not worth optimizing chunk size around at the cost of that
much redundant overhead. Durability against a *crash* (PRD D-17) is a
separate concern that isn't being deprioritized — chapter-sized chunks
still bound how much committed work a crash can lose, just to roughly one
chapter's worth instead of one small fixed-duration chunk's worth, a
trade-off the product owner explicitly accepted as acceptable ("likely an
edge case that won't happen often").

**Empirical validation — the same real book, not a synthetic guess:**
three real chapters extracted directly from the actual 13.5-hour reference
audiobook (ADR-0007) — the shortest (17.4s, front-matter "Opening
Credits"), one near the book's own median chapter length (1,089.7s /
18.2 min), and the longest chapter in the book (1,730.6s / 28.8 min) —
each run as a single `whisper-cli` call, `base.en`, no external
subdivision. All three completed cleanly: RTF held stable at ~0.05 across
the full 100x range of input length (not degrading at the long end),
peak memory grew sublinearly (~350MB → ~831MB, nowhere near proportional
to the 100x duration increase), and the transcript text itself was spot-
checked as coherent and accurate throughout an 18-minute single call, no
drift or repetition artifacts. (The 18.2-min and 28.8-min chapters are the
same two the GPU benchmark above reused as its CPU baseline — one real
test serving both decisions, not two separate efforts.) Real per-chapter
durations across all 50 chapters in this book: 17.4s to 1,730.6s, mean and
median both ~975s (~16.25 min) — confirming meaningful real variance, not
a uniform assumption.

**No technical barrier found** to chapter-sized single calls, up to the
longest length actually tested (~29 minutes). That's the basis for the
decision; it is not proof for chapters longer than that, which is why the
subdivision ceiling below is set with headroom rather than assumed
unbounded.

**Two provisional constants, needed to fully specify this and not yet
implemented in code** (Transcribe itself hasn't been built — this is the
same "decide now, implement when the step is built" status the chunk_ms
default and the GPU decision both have):

- **Chapter-subdivision ceiling: 45 minutes.** A chapter longer than this
  still gets subdivided (using the existing fixed-step logic in
  `chunking.py`, unchanged) rather than sent as one call. Set with real
  headroom above the ~29-minute longest chapter actually tested — not
  because 45 minutes itself has been tested, but because durability still
  wants *some* upper bound on how much work one crash can lose, and
  whisper-cli's behavior well beyond the tested range is genuinely
  unknown, not assumed safe.
- **Chapterless-source fallback: 15 minutes, 10s overlap.** Sources
  without chapter markers still fall back to `_uniform_segments()`
  (`chunking.py`, unchanged) — this is that fallback's duration, chosen to
  land in the same rough durability-loss range chapter-aligned chunking
  gives on this real book (mean chapter ~16 min), not independently
  benchmarked.

Both are explicitly provisional defaults, the same status `chunk_ms`
already had before this revision — real data narrowed them, but neither
is a formal G3 benchmark sign-off.

## Open questions for Contributor decision

1. ~~Exact whisper.cpp release tag/commit to pin, and its own dependency
   footprint (some builds use Core ML/Metal on macOS or CUDA on Windows —
   must decide whether to ship a CPU-only build for predictability or a
   GPU-accelerated build with a CPU fallback; recommend CPU-only for v1 to
   keep the benchmark story simple, revisit post-MVP).~~ **GPU-vs-CPU
   resolved above (2026-08-25).** The release tag/commit to pin is still
   open — no separate GPU build to source, since it's the same standard
   binary either way.
2. Whether to vendor a build script (project builds whisper.cpp from source
   as part of CI) vs. downloading prebuilt release binaries and pinning
   their checksums. Vendoring the build is more auditable but adds CI
   complexity; recommend starting with pinned prebuilt binaries + checksum
   verification, matching the model-download trust model in D-11.
