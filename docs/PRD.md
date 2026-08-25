# Product Requirements Document: m4bmaker Audible-Content Silencing

**Status:** G0 Repository Discovery complete; G1 Foundations in progress  
**Target:** Open-source fork of `sageframe-no-kaji/m4bmaker`, working name `m4bmaker-filter`  
**Audience:** Product owner, Contributors, and AI coding agents working under human review  
**Last decision update:** 2026-08-24  
**Decision authority:** Product owner approves product/UX decisions; human Contributor approves architecture, dependency, security, release, and merge decisions.

---

## 1. Purpose

Extend m4bmaker with a local-first workflow that creates a filtered copy of an existing audiobook M4B. The application transcribes English speech, detects user-configured words and phrases, and attenuates the audio during selected occurrences.

**The product must never cut, splice, shorten, concatenate, or time-stretch audio to filter words.** It must reduce amplitude during planned intervals while preserving the source audio timeline. Existing chapter start times therefore remain valid.

The source M4B remains unchanged. The product creates a separate filtered M4B and a local diagnostic/report artifact.

---

## 2. Roles

There are exactly two roles.

### 2.1 User

The **User** operates the desktop application. The User can:

- Open a locally accessible M4B file.
- Create, view, edit, disable, delete, export, and import catalog categories and terms.
- Create and select reusable filter profiles.
- Download/select a supported offline speech model.
- Start, pause, resume, cancel, and monitor transcription.
- Load a compatible completed native transcript artifact.
- Scan a transcript against a profile.
- Review matches, include/exclude individual hits, and configure attenuation settings within supported limits.
- Start a filtered-output render and review validation results.
- Manage local models, transcripts, reports, and storage through supported UI controls.

The User is not expected to understand audio codecs, FFmpeg, speech-model internals, local databases, or source code.

### 2.2 Contributor

The **Contributor** maintains the open-source fork. The Contributor can:

- Maintain code, tests, documentation, build scripts, packaging, release processes, and issue triage.
- Evaluate and approve dependencies, model provenance, security fixes, and license compatibility.
- Maintain data schemas and migrations for local catalogs, jobs, transcripts, scans, and reports.
- Review pull requests and AI-agent changes.
- Run milestone verification, regression tests, performance checks, and supported-platform release validation.
- Approve architecture changes, dependency additions, schema migrations, and releases.

The Contributor is not an in-app role and does not require a separate application permissions model in v1.

---

## 3. Product statement and non-goals

### 3.1 Problem statement

Users who own accessible audiobook M4B files need a straightforward way to make a personal filtered copy that reduces audibility of user-selected spoken words or phrases. Manual audio cutting is time-consuming and shifts chapter positions. Cloud transcription introduces privacy and connectivity concerns.

### 3.2 Product outcome

For a supported source M4B and a completed transcript, the User can create a separate playable M4B in which approved matched intervals are attenuated, while source duration, chapter start times/titles, and required metadata are preserved according to the v1 media contract.

### 3.3 Core invariants

These are non-negotiable and must be enforced in code and tests:

1. The source media file is never modified.
2. The output audio timeline has no intentional cuts, concatenations, removals, insertions, or rate changes.
3. Filtering modifies gain only inside planned attenuation intervals and their defined fade boundaries.
4. Output duration and chapter timing must pass validation before the render is marked successful.
5. The workflow operates offline after model download/verification; audio and transcripts are not uploaded by the application.
6. The app applies only terms/phrases selected by the User through a profile. It does not make opaque semantic or moral judgments.

### 3.4 Out of scope

- DRM bypass, protected-media processing, or any attempt to defeat access controls.
- Real-time playback filtering.
- Automatic semantic moderation, speaker diarization, sentiment analysis, or content-age classification.
- Cloud transcription as a required or default path.
- Editing source chapter boundaries.
- Linux support in v1.
- Non-English transcription in v1.

---

## 4. Decisions and assumptions register

### 4.1 Approved product decisions

| ID | Area | Decision |
|---|---|---|
| D-01 | Roles | Exactly two roles: User and Contributor. |
| D-02 | Platforms | Support Windows and macOS in v1. Linux is out of scope. |
| D-03 | Application stack | Preserve the existing repository’s desktop stack; do not migrate frameworks for this feature. |
| D-04 | Source input | Support directly opening existing, locally accessible, non-DRM `.m4b` files. Existing source-folder-to-M4B capability remains unchanged. |
| D-05 | v1 input audio | Accept M4B input with AAC primary audio only. Unsupported codecs must be rejected before transcription/rendering with clear recovery guidance. |
| D-06 | v1 output audio | Re-encode the selected primary audio track as AAC. |
| D-07 | Output bitrate | Target the source primary track’s detected nominal bitrate. If it cannot be detected or used, require User selection of a documented fallback bitrate; do not silently choose one. |
| D-08 | Metadata | Preserve title, author/artist, album, narrator, genre, description, cover art, chapter titles, and chapter start times. |
| D-09 | Audio tracks | Process and retain only the source primary/default audio track. Exclude additional audio tracks after a pre-render warning. If no default track exists, use the first audio track and disclose this before transcription. |
| D-10 | STT engine | Use Whisper through whisper.cpp or the stack-appropriate maintained equivalent compatible with the same approved engine/model contract. |
| D-11 | Model delivery | Download models after app installation. Once installed and integrity-verified, filtering/transcription must work offline. |
| D-12 | Language | English-language audiobook transcription only in v1. Do not advertise language auto-detection or multilingual support. |
| D-13 | Default model | `base.en` is the default recommended model. |
| D-14 | Optional model | User may download/select `small.en` as a higher-recognition-effort option. |
| D-15 | Transcription pause | Pause is safe at persisted chunk boundaries, not necessarily instantaneous. |
| D-16 | Pause latency | *Revised 2026-08-25 (ADR-0001) — pause responsiveness is not a v1 design priority.* Pause takes effect at the next persisted chunk boundary; with chunks chosen for transcription efficiency and durability rather than pause speed (§11.3), that may take substantially longer than the original 30-second target for a typical chapter-sized chunk. Actual latency is still recorded in diagnostics regardless. |
| D-17 | Restart behavior | After close/crash, preserve state and present a User-controlled `Resume` action. Never automatically resume transcription or rendering. |
| D-18 | Rendering recovery | Prefer validated resumable rendering. If implementation cannot validate segment-level recovery without timeline/metadata risk, preserve all upstream artifacts and require explicit restart of the render only. Never claim a partial file is complete. |
| D-19 | MVP scope | The MVP boundary in §5 is approved. |

### 4.2 Deferred baseline decision

| ID | Decision needed | Status | Gate |
|---|---|---|---|
| O-01 | Pin repository branch/tag and commit SHA; record stack, runtime, existing integrations, license, CI, and supported OS baseline. | **Resolved 2026-08-24 at G0.** See `docs/adr/0000-g0-repository-discovery.md` in the fork for full findings. Summary: pinned commit `3f35b544bd7f5ebe60b42ea389a3231d6034eb27` (`main`, v1.1.1, dated 2026-08-24); GPL-3.0-or-later; Python ≥3.11 (`pyproject.toml`), PySide6≥6.6 GUI (optional extra) + argparse CLI over a shared `pipeline.py`; runtime deps `mutagen`, `natsort`, `platformdirs`; external tool dependency on system `ffmpeg`/`ffprobe` ≥6.0 invoked via `subprocess`; test suite verified at **1013 passed** locally with Python 3.12 (CONTRIBUTING.md's "616 tests" figure is stale — flag upstream, do not treat as current); CI is GitHub Actions, Windows-only (`build.yml`, builds/signs a private installer artifact on tag push); macOS packaging/signing/notarization is documented but manual (`RELEASING.md`), no macOS CI job exists today. | Resolved. G1 may proceed per the findings doc. |

### 4.3 Decisions still required before implementation stage

The following are technical decisions—not invitations for uncontrolled scope expansion. They require an architecture decision record (ADR), human Contributor review, and product-owner acknowledgement where user-facing behavior changes.

| ID | Decision | Required by | Minimum ADR contents |
|---|---|---|---|
| O-02 | Exact whisper.cpp integration strategy, engine version, model source, model/engine licenses, checksums, and packaging behavior | Milestone 1 implementation | Alternatives, license/provenance, supported OS behavior, model storage location, resource benchmarks, test plan |
| O-03 | AAC encode/mux strategy and M4B metadata/chapter preservation method | Milestone 1 implementation | Input/output command/API flow, supported tag/chapter mapping, encoder settings, validation tools/versions, fallback/failure rules |
| O-04 | Gain-envelope implementation and exact timing/ramp behavior | Milestone 1 implementation | Algorithm, rounding/timebase, fade shape, test fixtures/thresholds, reproducibility details |
| O-05 | Render recovery design | Milestone 3 implementation | Segment boundaries, staging/mux strategy, integrity checks, restart fallback, proof-of-timeline validation |
| O-06 | Supported Windows/macOS versions and CPU architectures; packaging/signing/update approach | Milestone 0 exit | Minimum OS/hardware, installer/package design, signing/notarization, update integrity, offline-install behavior |
| O-07 | CLI parity scope for the filtering feature. The base project's stated design principle is "everything in the GUI is scriptable from the command line" — decide whether v1 filtering (model management, transcription, scan/review, render) gets any CLI surface, or is GUI-only for MVP given the review/approval-heavy workflow in §7. | Milestone 1 implementation | Recommendation, rationale against §7's human-review-in-the-loop design, exact command surface if any, what is explicitly deferred |
| O-08 | New module/package layout within the fork (e.g. a `m4bmaker/filter/` subpackage vs. a separate top-level package) and how it composes with the existing `pipeline.py`/`models.py` without those modules gaining filtering-specific knowledge | Milestone 1 implementation (G1) | Package boundaries, import direction, how the existing `Book`/`Chapter`/`PipelineResult` dataclasses in `models.py` are reused vs. left untouched, test directory layout |

---

## 5. Scope and priority

### 5.1 MVP: Must have

| Capability | MVP requirement |
|---|---|
| Platform | Windows and macOS; implementation remains in the base repository’s existing stack. |
| Media input | Open a local non-DRM `.m4b` with AAC primary audio. |
| Audio tracks | Select/process/retain only the primary/default track; disclose first-track fallback and additional-track omission. |
| Output media | Generate a separate AAC M4B targeting source nominal bitrate where feasible. |
| Preservation | Preserve source duration, chapter start times/titles, required metadata, and cover art per §8. |
| STT | English-only, local Whisper/whisper.cpp transcription with timestamped words. |
| Models | On-demand `base.en` download; optional on-demand `small.en` download. |
| Transcription resilience | Chunk-based persisted transcription, chunk boundaries chosen for transcription efficiency and durability granularity rather than pause responsiveness (D-16, revised); explicit User Resume after interruption. |
| Catalog | Category/term CRUD; basic reusable profile; phrase and whole-token matching. |
| Scan/review | Timestamped scan, category/term counts, per-hit include/exclude, interval-plan preview. |
| Filtering | PCM-domain amplitude attenuation with defined fades; no timeline cuts or shifts. |
| Persistence | Persist transcript, scan, profile snapshot, render plan, job state, and diagnostic report locally. |
| Validation | Validate duration, chapter timings/titles, required metadata, and planned attenuation before successful completion. |
| Rendering recovery | Implement validated resumption if the Milestone 3 spike proves it safe; otherwise explicit restart-render fallback retaining all upstream work. |
| Documentation | User help, known limitations, dependency/model attribution, and Contributor build/test instructions. |

### 5.2 Should have only if MVP Musts are stable

- Catalog/profile JSON export and import with schema validation.
- User-configurable lead/tail padding and mute floor within safe bounded ranges.
- Model management UI showing model status, installed size, checksum/version, and removal controls.
- Developer diagnostics view with non-sensitive job/tool versions and performance measurements.

### 5.3 Deferred after MVP

- Batch filtering and batch scheduling.
- Transcript imports beyond the native timestamped transcript artifact.
- Broad multilingual transcription.
- Regex matching.
- Advanced exceptions, aliases, language-specific normalization, and catalog synchronization/sharing.
- Built-in player deep-link/preview from match results.
- Hardware acceleration.
- Advanced localization.
- Real-time playback filtering, speaker diarization, semantic moderation, DRM media.

### 5.4 Explicit MVP exclusions

- The app must not promise recognition completeness, zero false positives, perfect word boundaries, or universal M4B compatibility.
- The app must not silently send media/transcript data to a remote service.
- The app must not overwrite a source M4B.
- The app must not auto-start recovered work after restart.

---

## 6. Supported media contract

### 6.1 Input eligibility

A source file is eligible for v1 only when all conditions below are true:

1. It is locally accessible and not DRM-protected.
2. It is an M4B/MP4-family file accepted by the chosen media inspector.
3. It contains at least one AAC audio track.
4. A primary/default AAC track can be determined, or the User accepts the disclosed first-AAC-track fallback.
5. The file’s chapter and required metadata structures can be read by the selected inspector.
6. Sufficient local storage exists for transcription artifacts, render staging, and output according to §12.4.

The app must preflight eligibility before model work or transcription. If ineligible, it must state the specific unsupported condition and not produce an output.

### 6.2 Output contract

For eligible input, the output must:

- Be a separate AAC M4B file.
- Contain one filtered audio track: the selected source primary/default track.
- Target the detected source nominal bitrate. If unavailable/unusable, require a User-selected fallback from documented options.
- Preserve the required fields in §6.3, subject to validation.
- Preserve chapter count, ordered chapter titles, and canonical chapter start times under §8.2.
- Preserve source runtime under §8.1.
- Include no additional source audio tracks in v1.
- Leave source media unmodified.

### 6.3 Required metadata preservation

The following are required for a successful output unless absent in the source:

- Title.
- Author/artist.
- Album.
- Narrator.
- Genre.
- Description.
- Cover art.
- Chapter titles.
- Chapter start times.

Unknown, proprietary, or unsupported metadata atoms are best effort only and must be listed in validation warnings when their loss can be detected. The product must not claim perfect preservation of all MP4 atoms.

### 6.4 Unsupported/ambiguous conditions

- Non-AAC primary audio: reject with explanation.
- No audio track: reject.
- No marked default track: show selected first AAC track and require acknowledgement before starting transcription.
- Additional audio tracks: warn before render that only the selected primary/default track will be retained.
- Inaccessible or protected media: state that the app processes only locally accessible, non-DRM files and does not bypass protections.
- Invalid/unreadable chapter or required metadata structures: stop before render, retain any transcript if possible, and show diagnostics.

---

## 7. User workflow

### 7.1 Happy path

The happy path must be achievable with no more than these required User decisions after selecting a source:

1. Select a local M4B.
2. Select/download the recommended model if not installed.
3. Select or create a profile.
4. Start transcription and later explicitly resume if interrupted.
5. Review scan results and select `Create filtered M4B`.
6. Choose output location only if the default location is unsuitable; otherwise accept default.

Advanced settings must be behind a clearly labeled disclosure and must not block the happy path.

### 7.2 Workflow stages

1. **Select source** — inspect media eligibility, selected audio track, duration, chapters, required metadata, storage estimate, and compatible saved transcript availability.
2. **Choose transcript path** — load a compatible native transcript or choose local transcription model/settings.
3. **Transcribe** — display durable progress and user controls.
4. **Select profile** — choose existing basic profile or manage categories/terms.
5. **Scan** — create a persisted scan result against an immutable profile snapshot.
6. **Review** — inspect counts/hits and include/exclude specific hits.
7. **Render** — confirm output plan, run attenuation/encode/mux/validation.
8. **Complete** — show validation status, output location, report location, and warnings.

### 7.3 First-run and empty states

The application must handle these states explicitly:

- No model installed: explain that a one-time internet download is needed; allow download, retry, cancel, and supported local-model selection if implemented.
- No catalog/profile: offer to create a category/profile; do not require a built-in offensive-word list.
- No saved transcript: offer transcription.
- No matches: show zero results, leave the source unchanged, and allow profile modification or a no-op render only after explicit User confirmation.
- Offline before first model download: explain that transcription cannot start until a supported model is installed; do not fail silently.
- Insufficient storage: show required/available estimate and allow storage-location change or cancellation.

---

## 8. Audio filtering, timeline, and validation

### 8.1 Timeline integrity

The renderer must not intentionally remove, insert, concatenate, trim, or time-stretch media. It must apply a gain envelope to decoded PCM representing the selected primary audio stream and encode/mux a new output.

**Success duration rule:** measure source and output with the same pinned media-inspection tool/version. Output duration must be within the codec/container tolerance defined in ADR O-03. The initial target is no more than 10 ms difference after canonical millisecond normalization, subject to confirmation with representative AAC fixtures. If that target cannot be met for a supported source class, the source class is not MVP-supported until a documented, tested tolerance is approved.

### 8.2 Chapter integrity

Before render, extract a canonical chapter manifest. After render, extract a canonical output manifest using the same tool/version.

A successful render requires:

- Equal chapter count.
- Equal chapter ordering.
- Equal chapter titles, including absence/presence handling.
- Equal canonical start time for each chapter using the rounding/timebase policy defined in ADR O-03.

Duplicate starts, zero-length chapters, or unsupported chapter structures must be handled according to ADR O-03 and must be covered by fixtures before support is claimed.

### 8.3 Match-to-interval plan

A raw scan hit is not itself an audio operation. The `RenderPlan` is created from included hits.

For each included hit:

1. Start with the recognized word or phrase time range `[startMs, endMs)`.
2. Apply lead and tail padding.
3. Clamp interval to `[0, sourceDurationMs]`.
4. Sort all intervals by start/end.
5. Merge overlapping or near-adjacent padded intervals using the configured adjacency threshold.
6. Retain a many-to-one mapping from each merged interval to raw hit IDs.

MVP defaults:

| Setting | Default | Allowed MVP range |
|---|---:|---:|
| Lead padding | 60 ms | 0–250 ms |
| Tail padding | 80 ms | 0–300 ms |
| Merge adjacency | 20 ms | 0–100 ms |
| Fade in | 15 ms | 5–50 ms |
| Fade out | 15 ms | 5–50 ms |
| Gain floor | -80 dBFS equivalent | -60 to -96 dBFS equivalent |

The exact unit conversion and rounding behavior must be defined in ADR O-04.

### 8.4 Attenuation behavior

MVP behavior is **attenuation to a configurable near-silence gain floor**, not physical removal of audio. User-facing language must say “attenuate” or “mute without changing runtime,” never “remove audio.”

The gain envelope must:

- Apply a deterministic ramp into and out of the gain floor.
- Use the fade shape defined in ADR O-04.
- Scale or resolve fade behavior for intervals shorter than combined fade duration without extending the interval or media duration.
- Avoid global normalization, global gain changes, downmixing, or unrelated audio modification.
- Preserve channel count and sample rate where the selected AAC pipeline supports it; otherwise reject the source class until explicitly supported.

### 8.5 Attenuation verification

Automated verification must decode source/output to a defined PCM format and measure:

- Core mute region gain reduction relative to source over a defined measurement window.
- Envelope behavior excluding configured fade boundary windows.
- No intentional attenuation outside planned intervals plus fade boundaries, within a codec-aware tolerance.
- No clipping introduced by the filter path.

ADR O-04 must define waveform fixture type, RMS/peak metrics, target thresholds, and tolerances for lossy AAC output. Human listening tests are required before MVP release (§16.4).

---

## 9. Catalog, profiles, scanning, and review

### 9.1 Data model

| Entity | Required fields | Notes |
|---|---|---|
| Category | UUID, name, description, enabled-by-default, display order, revision | Names unique after v1 normalization |
| Catalog entry | UUID, category UUID, canonical phrase, enabled, notes, revision | A phrase may contain one or more words |
| Filter profile | UUID, name, selected category/entry IDs, matching settings, attenuation settings, revision | Each scan uses an immutable snapshot |
| Scan hit | UUID, scan ID, raw recognized tokens, canonical entry, category, start/end, confidence if available, match rule, review status | Raw scan data is immutable |
| Review decision | hit ID, included/excluded/manual status, timestamp | Belongs to a scan revision |

MVP does not include regex, advanced aliases, advanced exception rules, catalog synchronization, or shared multi-user catalogs.

### 9.2 Catalog requirements

- User can create, read, update, disable, and delete categories and entries.
- Deletion must be soft-delete/archive in MVP if historical scans/profiles reference the item; historical snapshots remain readable.
- Reject blank entries and warn about duplicates after case/punctuation/whitespace normalization.
- Support English Unicode input. V1 matching behavior is English-oriented and does not claim language-specific morphology support.
- Terms may contain one or multiple words.
- Catalog/profile export/import is a Should-have item, not a Must-have MVP gate.
- A starter catalog is optional and must be disabled by default if shipped. The app may ship with no active offensive-word terms.

### 9.3 Matching policy

The matcher uses ordered, timestamped recognized words; it must not match against an un-timestamped concatenated transcript.

MVP matching modes:

- Exact normalized token match.
- Exact normalized phrase match across ordered adjacent tokens.

Normalization must be versioned and must handle at least case, ordinary punctuation, apostrophe variants, and repeated whitespace. The original recognized text remains available for display.

Phrase tokens may match only when adjacent recognized token gaps are at or below **750 ms**. Do not match across a larger gap or across incomplete transcript coverage.

Every scan stores transcript ID, transcript schema version, source fingerprint, profile snapshot ID/revision, catalog revision IDs, normalization version, and matching settings.

### 9.4 Scan and review requirements

A scan consumes a completed compatible transcript and profile snapshot and produces a persisted immutable raw result. It must not change the transcript.

Each hit displays:

- Timestamp range.
- Category.
- Canonical configured term.
- Recognized text.
- Confidence when supplied by the engine; otherwise `not available`.
- Up to five recognized words before/after for local context, subject to transcript boundaries.
- Review state: included, excluded, or manual.

The review screen provides:

- Total raw hits, included hits, unique terms hit, category counts, term counts, and total planned attenuated duration.
- Sort/filter by time, category, term, confidence availability/value, and review state.
- Per-hit include/exclude controls and category/term bulk actions.
- A rendered interval-plan preview with merged-interval provenance.

Conflict rules:

- Raw matching hits are immutable.
- Review decisions apply to one scan revision only.
- Overlapping included hits remain separately counted in reporting but merge only in the `RenderPlan`.
- A re-scan creates a new scan revision. It does not silently copy decisions from an older scan.
- User-created manual intervals are out of MVP unless explicitly added through a later approved requirement.

---

## 10. Offline transcription

### 10.1 Engine and models

Use Whisper via whisper.cpp or the stack-appropriate approved integration under ADR O-02.

V1 model policy:

| Model | Availability | User-facing label | Intended use |
|---|---|---|---|
| `base.en` | Default on-demand download | Recommended | Default English transcription |
| `small.en` | Optional on-demand download | Higher recognition effort | May improve recognition; requires more disk/RAM/time |

Do not present a generic Fast/Balanced/Accurate promise until model/version/decoder benchmarks are approved. Use only labels backed by exact versioned model and decode settings.

The model UI must display model name, engine/model version, installed/download state, source/provenance, checksum, required disk space, and removal action. Downloads require HTTPS, checksum verification, atomic staging/rename, and corruption/interruption recovery.

### 10.2 Offline and privacy behavior

- A network connection is permitted only for explicitly initiated model download/update behavior approved by the User.
- After an approved model is installed and verified, transcription and filtering work without network access.
- The app must not upload source media, decoded audio, transcripts, catalog data, profiles, scan results, or reports.
- No secret/API key is needed for the MVP path.

### 10.3 Transcript artifact

Use a versioned native JSON artifact with extension `.m4bt.json`.

Minimum required content:

```json
{
  "schemaVersion": 1,
  "status": "complete",
  "source": {
    "fingerprint": "defined-by-ADR",
    "durationMs": 3723456,
    "selectedAudioStream": 0
  },
  "engine": {
    "name": "whisper.cpp",
    "version": "pinned-version",
    "model": "base.en",
    "modelChecksum": "sha256",
    "parameters": {"language": "en"}
  },
  "segments": [
    {
      "id": "chunk-000041",
      "startMs": 600000,
      "endMs": 630000,
      "status": "completed",
      "words": [
        {
          "text": "example",
          "normalized": "example",
          "startMs": 601020,
          "endMs": 601440,
          "confidence": null
        }
      ]
    }
  ]
}
```

Timestamp convention: all timestamps are integer milliseconds from selected source audio timeline start; `startMs` is inclusive and `endMs` is exclusive. Artifact schema, fingerprint algorithm, chunk-overlap/deduplication policy, and migration policy must be finalized in ADR O-02.

### 10.4 Completeness and compatibility

Transcript statuses are `draft`, `partial`, `complete`, `failed`, and `incompatible`.

- A normal scan requires `complete` status.
- Partial scan is not an MVP feature. The UI must not allow a partial transcript to appear complete.
- A transcript is compatible only when source fingerprint, source duration, selected stream, schema version, and required timing data meet current validation rules.
- If a source has moved but content fingerprint matches, provide a relink workflow; do not unnecessarily re-transcribe.
- Imported transcript support in MVP is native `.m4bt.json` only.

### 10.5 Timestamp quality

Word timestamps are estimates and must not be described as exact recordings of spoken-word boundaries. The app must communicate that detection may miss words or produce false positives/poor boundaries.

Before release, Contributors must establish a legally distributable or synthetic English reference corpus with manually labeled target words and test:

- Recognized match coverage for selected catalog terms.
- Start/end alignment error distribution for recognized target terms.
- Behavior at chunk boundaries.
- Invalid timestamps: negative values, end before start, out-of-duration values, zero-length values, duplicate overlap artifacts.

The exact measurable threshold is an ADR O-02 release gate. It must be stated as a named fixture set, model/version, and percentile/tolerance—not “accurate enough.”

### 10.6 Decode alignment

The transcription adapter must record selected stream ID and decode parameters. ADR O-02/O-03 must define source-to-PCM sample rate, channel handling, timestamp origin, and any gapless/encoder-delay behavior. The chosen process must be tested to ensure word timestamps map to the render input timeline.

---

## 11. Jobs, progress, durability, and recovery

### 11.1 Job types

- `ModelDownloadJob`
- `TranscriptionJob`
- `ScanJob`
- `RenderJob`
- `ValidationJob` (may be a render stage but must report separately in UI/logs)

Long-running work must be outside UI component lifecycle and follow the existing project's *concurrency pattern* (`gui/worker.py` and `gui/queue_manager.py` run encode jobs on Qt worker threads and report progress via an in-process `Callable[[str, float], None]` callback, per `pipeline.py`/`encoder.py`). **G0 correction:** that existing queue framework is in-memory and session-scoped only — it holds no persisted state and does not survive an app restart, so it does not itself satisfy §11.3's durability requirement. The new `Job Orchestrator` (§14.2) must be a genuinely new, SQLite-backed durable layer; it may reuse the existing worker-thread/progress-callback *shape* for UI consistency, but must not be described as reusing the existing framework's persistence, because none exists today.

### 11.2 Job states

```text
QUEUED → PREPARING → RUNNING → PAUSING → PAUSED → RESUMING → RUNNING → COMPLETED
                    ↘ FAILED
                    ↘ CANCELLED
                    ↘ NEEDS_ATTENTION
```

The implementation must define an allowed-transition matrix, stable error codes, retry rules, and User actions for every terminal/recoverable state. Schema or app-version incompatibility must produce `NEEDS_ATTENTION`, not silent deletion.

### 11.3 Transcription durability

- Divide source audio into chunks with overlap sufficient to protect words at boundaries. *Revised 2026-08-25 (ADR-0001):* chapter-aligned by default when chapter markers exist (one chunk per chapter), subdividing only a chapter that exceeds a size ceiling chosen for the same reasons as the next bullet; a fixed, generous duration is the fallback for sources without chapter markers.
- Chunk duration is chosen primarily for transcription efficiency (minimizing redundant per-invocation engine overhead — real measurement showed whisper.cpp reloads its full model from disk on every invocation, so fewer/larger chunks meaningfully reduce wasted time on a long book) and durability granularity (bounding how much committed work a crash can lose to roughly one chunk, accepted as up to one chapter's length), not pause-click responsiveness — that target is no longer a chunk-sizing constraint (see D-16). Actual pause latency must still be recorded in diagnostics regardless.
- Persist a completed chunk and its words atomically before marking it complete.
- Successfully committed chunks must not be re-transcribed after restart, except minimal boundary-overlap reconciliation needed to prevent duplicate/missing words.
- Deduplicate overlap words deterministically with documented timestamp/text logic and retain source chunk provenance.
- When Pause is requested, stop scheduling new chunks. Finish or safely checkpoint the active atomic unit, then transition to `PAUSED`.
- On restart/crash, show job state and a `Resume` action. Do not automatically start work.
- Verify source fingerprint, selected track, engine version/model checksum, and artifact compatibility before resume. If invalid, move to `NEEDS_ATTENTION` with explicit choices.

### 11.4 Rendering durability and recovery

The system must stage output to a temporary location and never expose a partial output as successful.

**Preferred implementation:** resume rendering only from validated segment boundaries using a deterministic final assembly/mux step that preserves total timeline and metadata/chapter contract.

**Mandatory fallback:** if validated segment-level render resume is not demonstrated for a source/output class, the app must:

1. Preserve transcript, scan, profile snapshot, render plan, diagnostics, and safe temporary artifacts.
2. Mark the render interrupted/failed/cancelled accurately.
3. On User action, restart only the render from the beginning.
4. Never require re-transcription or re-scan solely because rendering stopped.

No automatic render resume occurs after restart. ADR O-05 must prove any claim of resumable encoding using fixture validation before it can be enabled.

### 11.5 Progress and observability

Each job must display:

- Current stage in plain language.
- State.
- Percentage only when deterministically computable.
- Completed/total units when applicable.
- Elapsed time.
- Estimated remaining time only after sufficient measured progress; show `Estimating` or omit otherwise.
- Pause/resume/cancel availability appropriate to current state.
- Actionable error message and recovery action.

Progress rules:

- Model download: byte-based downloaded/total progress where content length is available; otherwise indeterminate.
- Transcription: committed chunks / planned chunks. Do not mark 100% until transcript completeness validation finishes.
- Rendering: explicit stages `Preparing`, `Decoding/filtering`, `Encoding`, `Muxing`, `Validating`. Use determinate progress only where reliable.
- Validation: show individual duration, chapter, metadata, and attenuation checks.

Persist user-visible job events and non-sensitive diagnostics. Do not log raw transcript text by default.

---

## 12. Desktop platform, packaging, data, and security

### 12.1 Platform commitment

V1 supports Windows and macOS only. Exact minimum versions and CPU architectures are mandatory deliverables of ADR O-06.

Before those versions are decided, no claim of universal Windows/macOS support may be made.

### 12.2 Cross-platform behavior

The implementation must use platform-safe abstractions for:

- Native file open/save/reveal dialogs.
- Path separators, filename legality, case-insensitive filesystems, long paths, Unicode paths, and external volumes.
- Canonical output paths and output collision handling.
- Process execution/cancellation for bundled or discovered media/STT tools.
- File locks, antivirus/indexer interference, and delayed deletion/retry on Windows.
- Application sandbox/permissions and security-scoped file access on macOS if applicable to the base stack.
- High-DPI/scaled displays, standard window behavior, keyboard conventions, and focus restoration.

System tray/menu-bar integration is not an MVP requirement unless it already exists in the base application. Do not add it solely for this feature.

### 12.3 Installation, updates, signing, and size

ADR O-06 must specify:

- Installer/package format inherited from or compatible with the base project.
- Windows code-signing requirements.
- macOS signing and notarization requirements.
- Bundled external binary policy (including FFmpeg/whisper.cpp) and version discovery.
- Auto-update policy, update manifest integrity/signature verification, rollback behavior, and whether updates can be disabled.
- Offline installation behavior.
- Base application size and separate model-download size presentation.

No unsigned production release or unauthenticated executable/model update mechanism is acceptable.

### 12.4 Local storage and cleanup

Use OS-appropriate per-user application-data locations, not the install directory, for models, indexes, local database state, cache, transcripts, job artifacts, and reports.

The app must define and document:

- Default storage roots by platform.
- Configurable storage root if supported by the base stack.
- User-owned exports versus recreatable cache.
- Disk estimate formula and safety margin before transcribe/render.
- Storage cleanup UI and what can be deleted safely.
- Migration behavior across app/schema versions.
- Uninstall behavior: preserve user-generated catalog/profile/transcript/report data by default unless the installer explicitly asks the User to remove it.

### 12.5 Security and privacy requirements

- Never invoke media/STT tools through a shell-concatenated command string. Use argument-array process APIs.
- Treat media files, embedded metadata, transcript imports, catalog imports, and paths as untrusted inputs.
- Validate schema/version/size before importing JSON artifacts.
- Canonicalize output paths and protect against path traversal, unsafe overwrite, and symlink/reparse-point surprises to the extent supported by the platform/runtime.
- Use safe staging files and atomic rename/move where filesystem semantics support it.
- Verify model downloads with approved checksums; record source and checksum locally.
- Redact raw transcript text from routine logs; provide explicit user-approved diagnostics export if detailed content is needed.
- Do not collect telemetry in MVP unless separately approved, disclosed, and implemented with a privacy design review.

---

## 13. Non-functional requirements

### 13.1 Performance

Before MVP release, ADR O-02/O-03 must set named reference hardware and fixture benchmarks. The following must be measured and documented, not assumed:

- Application startup time on supported baseline hardware.
- Idle memory footprint.
- Peak memory during `base.en` and `small.en` transcription.
- CPU utilization/concurrency policy.
- Disk space needed for source, PCM/transcription workspace, artifacts, staged output, and safety margin.
- Transcription throughput and render throughput on reference machines.
- UI responsiveness under long-running jobs.

MVP performance behavior requirements:

- Long-running work must not execute on the UI thread.
- The UI remains interactive for navigation, job visibility, and cancellation/pause requests during jobs.
- Default concurrent transcription/render worker count is one unless a benchmarked scheduler is later approved.
- The app must preflight free space and stop safely with recovery information if space becomes inadequate during work.

### 13.2 Accessibility

Target native platform accessibility APIs and WCAG 2.2 AA principles where applicable to a desktop app.

MVP acceptance requirements:

- User can complete the happy path with keyboard only.
- Controls have accessible names/roles/states.
- Focus order is logical and focus returns predictably after dialogs.
- Job progress/state changes are accessible to screen readers without excessive announcements.
- Status does not rely on color alone.
- UI supports system text scaling and sufficient contrast.

### 13.3 Localization

V1 user interface is English only. The app supports Unicode text input for catalog entries and metadata display, but does not claim broad locale-aware or non-English matching. English-oriented normalization behavior must be documented.

---

## 14. Architecture and persistence

### 14.1 Architecture constraints

- Preserve the base project stack and established patterns after Repository Discovery Gate.
- Do not invoke STT/audio command execution directly from UI components.
- Use interfaces/adapters around media inspection, STT, jobs, persistence, matching, interval planning, rendering, and validation.
- Keep user artifacts inspectable and portable where practical.

### 14.2 Required modules

| Module | Responsibility |
|---|---|
| Media Inspector | Probe source eligibility, selected audio stream, duration, chapters, required metadata, artwork status, and fingerprint. |
| Catalog Service | Catalog/profile CRUD, revisioning, validation, soft deletion, optional import/export. |
| Model Manager | Model discovery, download, verification, install/removal, provenance. |
| Transcript Engine Adapter | Local Whisper execution and normalized word-time output. |
| Job Orchestrator | Persisted state, scheduling, pause/resume/recovery, progress events, locking. |
| Transcript Store | Native artifact persistence, compatibility verification, migration. |
| Matcher | Versioned token/phrase normalization and deterministic profile scan. |
| Interval Planner | Padding, clamp, merge, hit-to-interval provenance, gain-envelope inputs. |
| Renderer | PCM attenuation, AAC encoding, staging, muxing. |
| Validator | Output duration, chapter, metadata, and attenuation-plan verification. |
| UI Layer | Guided workflow, review, accessibility, errors, storage/model management. |

### 14.3 Persistence contract

Recommended design: local SQLite for indexes, job state, locks, and queryable summaries; versioned JSON files for portable transcripts, scans, render plans, and reports.

Before implementation, define one authoritative source for each entity, transaction boundaries between database and artifact files, lock behavior across multiple app instances, schema migration, integrity validation, and rebuild/repair behavior. Do not allow an index/artifact mismatch to silently create a completed job.

### 14.4 Core artifacts

| Artifact | Purpose | Lifecycle |
|---|---|---|
| `MediaManifest` | Canonical source inspection | Recreated/validated from source |
| `.m4bt.json` | Timestamped transcript | User-retainable, versioned |
| `FilterProfileSnapshot` | Immutable settings used by scan/render | Retained with scan/report |
| `FilterScan` | Immutable raw hits and review decisions | Retained with render/report |
| `RenderPlan` | Merged intervals/audio settings | Retained with render/report |
| `filter-report.json` | Reproducibility and validation report | Written beside/with output or user artifact root |

All schemas must define version, IDs, timestamps, time units, nullability, required fields, and migration policy before artifacts are released.

---

## 15. Acceptance criteria

### 15.1 Media and preservation

1. Given a supported AAC M4B fixture, the app selects the default audio stream or presents the first-stream fallback clearly.
2. A successful output contains one filtered AAC audio track and no additional source audio tracks.
3. Source hash is unchanged after every workflow, including failed/cancelled workflows.
4. Required metadata that exists in source is present and semantically equal in output under the mapping defined in ADR O-03.
5. Output chapter count, ordered chapter titles, and canonical start times equal source values under §8.2.
6. Output duration passes the ADR O-03 tolerance using the same pinned inspection tool/version.
7. Unsupported source codecs or unreadable required structures fail before transcription/render with a specific error.

### 15.2 Catalog, profile, scan, review

1. User can create, edit, disable, archive/delete, and select categories/terms/profiles.
2. Blank entries are rejected; normalized duplicates trigger a warning.
3. An exact token/phrase fixture yields expected raw-hit IDs, categories, terms, and counts.
4. Phrase matching does not cross a gap above 750 ms.
5. Scan output records profile snapshot, source fingerprint, transcript, normalization, and catalog revisions.
6. Excluding a hit removes it from the render plan but preserves raw scan history.
7. Overlapping hits are separately counted and merge only in render-plan intervals.
8. Re-scan creates a distinct scan revision and does not silently copy review decisions.

### 15.3 Transcription

1. With an installed approved model and disabled network, English transcription completes without network access.
2. First-run model download presents model provenance/checksum/storage needs and recovers safely from cancellation/interruption/corruption.
3. Completed native transcript includes valid timestamped words, selected stream ID, model/version/checksum, source fingerprint, and `complete` status.
4. A normal scan is unavailable for `partial`, `failed`, or `incompatible` transcript status.
5. After interruption following committed chunks, reopening shows `Resume` and preserves committed chunks. No work starts automatically.
6. Successfully committed chunks are not re-transcribed except documented boundary reconciliation.
7. *Revised 2026-08-25 (ADR-0001):* Pause requests transition to `PAUSED` at the next chunk boundary — potentially on the order of a chapter's length, since chunk size is no longer bounded by a pause-latency target — with actual latency recorded in diagnostics, and the UI communicating that a pending pause is waiting for a safe boundary rather than appearing to hang.
8. Word-timestamp quality meets the numeric, corpus-specific release threshold approved in ADR O-02.

### 15.4 Attenuation and rendering

1. Render planning applies configured padding, clamping, sorting, merging, and hit provenance deterministically.
2. Render changes amplitude only in planned intervals and defined fade boundaries; it does not intentionally alter source timeline length/rate.
3. Decoded waveform verification meets ADR O-04 core-gain and non-target-region tolerance thresholds.
4. Short intervals use the documented scaled fade behavior without changing duration.
5. If output validation fails, the job is not marked complete, partial output is not presented as valid, and diagnostics/retry path remain available.
6. Interrupted render preserves upstream work. If proven segment-resume is unavailable for the source class, User can explicitly restart rendering without re-transcription/re-scan.
7. Any enabled resumable-render implementation passes timeline, chapter, metadata, and audio-continuity fixtures before release.

### 15.5 Desktop, security, and accessibility

1. Supported Windows and macOS builds complete baseline regression suites on the versions/architectures defined by ADR O-06.
2. Installer, signing/notarization, update integrity, and bundled-binary behavior pass ADR O-06 release checks.
3. Media/tool process invocation uses safe argument arrays; malformed paths/metadata/imports do not execute injected commands.
4. Keyboard-only test completes the happy path; screen-reader/accessibility checks meet §13.2.
5. Storage preflight accurately reports available/required storage according to the approved formula and preserves recoverable artifacts on interruption.

---

## 16. Test strategy

### 16.1 Test fixtures

Maintain legally distributable or synthetic fixtures covering:

- AAC M4B with chapters, required metadata, artwork, and known target words.
- Chapter-title edge cases, no chapters, duplicate/zero-length chapter structures where support is claimed.
- Mono and stereo AAC; unsupported codec/tracks; absent default track; multiple tracks.
- Short words, phrases, back-to-back hits, hit overlap, chunk-boundary words, long pauses, and boundary fade cases.
- Unicode paths, long paths, invalid metadata, low disk, locked files, moved source, missing model, and corrupted artifacts.

### 16.2 Automated tests

- Unit: normalization, matching, profile snapshots, interval planning, gain envelope, job transitions, storage calculation, schema validation.
- Integration: media inspection, local STT adapter with deterministic test output, chunk durability, resume, catalog persistence, scan/review, renderer/validator.
- Golden media: duration, chapter manifests, required metadata, encoded audio gain tests, no-target-region tolerance, source hash invariance.
- Security: malformed JSON/media corpus, paths/Unicode/long filenames, process argument injection, dependency vulnerability checks, model/update integrity checks.
- Migration: artifact/database schema upgrades and recovery from index/artifact mismatch.

### 16.3 Platform test matrix

Run a documented matrix on the supported Windows/macOS versions and architectures selected in ADR O-06. Test installation, first-run model flow, transcribe/pause/resume, render interruption/recovery, output validation, path edge cases, storage management, and uninstall/data-retention behavior.

### 16.4 Human review testing

Before MVP release, conduct structured human listening review on the reference corpus:

- Confirm target terms are adequately attenuated for test cases recognized by STT.
- Identify audible clicks, unnatural boundaries, leakage of target words, or excessive attenuation of neighboring speech.
- Record results by model, audio fixture, padding, and envelope configuration.
- Convert repeated defects into fixtures and regression tests.

Human review is required for release approval; it does not replace automated timeline/chapter/metadata validation.

---

## 17. AI coding-agent execution protocol

This section is mandatory for any AI coding agent, including Claude Code. The agent may implement code only in bounded milestones with human review gates.

### 17.1 General operating rules

1. **Read before edit.** Inspect the pinned repository state, architecture, build instructions, tests, package manifests, license, and existing media/job code before proposing implementation.
2. **No assumptions about files or APIs.** If the actual repository differs from this PRD, report the discrepancy and request a human architecture decision rather than inventing a parallel subsystem.
3. **Keep changes small and reversible.** One milestone or coherent vertical slice per pull request/branch. Do not combine unrelated refactors, dependency upgrades, UI redesign, and feature work.
4. **Do not change the stack.** Preserve the base project framework and build patterns unless a human-approved ADR explicitly authorizes a change.
5. **No unapproved dependencies.** Before adding a runtime/build/model dependency, produce its name, version, license, origin, OS impact, binary/model size, security posture, and why existing dependencies cannot satisfy the need. Wait for human approval.
6. **No silent network behavior.** Do not add telemetry, cloud STT, background downloads, or remote calls. Model download must be explicit in UI and approved by the User.
7. **Do not weaken invariants.** Never implement audio excision, timeline shift, source overwrite, automatic restart after crash, or unvalidated output as a shortcut.
8. **Test every claim.** Add or update tests for changed behavior. Do not mark a milestone complete based solely on static review or a successful build.
9. **Report uncertainty.** When a tool/library behavior is unknown, create a focused spike/test and label the result; do not present guesses as verified behavior.
10. **Preserve user data.** Any schema/storage migration needs backup, migration, rollback/recovery plan, and human review.

### 17.2 Required agent deliverable at each milestone

Before requesting review, the agent must produce:

- A concise implementation summary and file-by-file change list.
- Requirements/acceptance-criteria traceability table: requirement ID → implementation → automated/manual test.
- Commands run, environment, test results, warnings, and known failures.
- New/changed dependencies with license/provenance and approval reference.
- Data schema/migration changes and backward-compatibility notes.
- Security/privacy review notes, including whether any new network or process execution path exists.
- Manual validation steps that a human can repeat on Windows and macOS.
- Explicit list of deferred items and unanswered questions.

### 17.3 Human review gates

| Gate | Agent may do | Human must approve before next gate |
|---|---|---|
| G0: Repository Discovery | Inspect/read only; create architecture inventory and ADR drafts; run existing tests | Pinned SHA, stack integration plan, license baseline, supported-platform plan, ADR O-02/O-03/O-06 direction |
| G1: Foundations | Add isolated domain models, schemas, media manifest tests, job-state tests, fixture harness | Schema design, migration strategy, media contract validation approach |
| G2: Catalog and scan | Implement catalog/profile CRUD and deterministic scan against synthetic/native transcript fixtures | UX flow, matching behavior, profile snapshot/review model |
| G3: Transcription | Implement model manager and chunked local transcription with pause/resume | Engine integration, download integrity, benchmark/timestamp-quality findings, privacy review |
| G4: Render spike | Build a narrow prototype proving attenuation, AAC output, chapter/metadata retention, timeline validation | ADR O-03/O-04 confirmation; go/no-go on safe render architecture |
| G5: End-to-end MVP | Integrate guided UI, render recovery behavior, output reports, validation, errors | MVP usability, recovery semantics, platform-specific behavior |
| G6: Release candidate | Fix defects, perform full matrix, package/sign, docs, listening review | Release acceptance, known limitations, attribution/license notices |

No agent may skip a gate by implementing later features first.

### 17.4 Milestone procedures

#### G0 — Repository Discovery (no feature code)

Agent tasks:

1. Pin and report current branch, commit SHA, license, package manager, runtime/UI stack, build/test commands, CI, platform targets, and dependency inventory.
2. Locate existing source import, M4B/MP4 metadata/chapter handling, audio player, FFmpeg/media tooling, process runner, progress/jobs, settings, persistence, packaging, and test fixtures.
3. Identify direct-M4B input gaps and exact integration candidates.
4. Run baseline build/tests without unrelated modifications.
5. Draft ADRs O-02, O-03, O-06 with options and evidence. Do not select a dependency/model unilaterally.

Human review must decide whether base architecture supports the planned feature or whether the PRD requires a constrained revision.

#### G1 — Foundations

Agent tasks:

- Add versioned domain schemas/interfaces and test fixtures without UI coupling.
- Implement media eligibility inspection for AAC M4B and canonical source manifest.
- Add explicit job-state model and transition tests.
- Add secure artifact path/storage abstractions.
- Do not yet download models, transcribe, or render production media.

Exit evidence:

- Fixtures prove primary-track selection/fallback rules, unsupported-source rejection, and canonical manifest behavior.
- Schema validation/migration tests pass.
- Human approves artifact ownership and persistence transaction design.

#### G2 — Catalog and scan

Agent tasks:

- Implement catalog/profile CRUD, revisions, archive behavior, normalization, exact token/phrase matching, scan/review state, and render-plan interval planning.
- Use deterministic synthetic/native transcript fixtures, not live STT.
- Implement required scan/report counts and include/exclude behavior.

Exit evidence:

- Acceptance criteria §15.2 pass.
- Human validates UI copy and confirms no unintended role/persona complexity.

#### G3 — Transcription and model management

Agent tasks:

- Implement approved local engine/model integration only after ADR O-02 approval.
- Implement explicit model download, checksums, staging, storage, and offline behavior.
- Implement chunking, atomic persistence, pause/resume, explicit restart UX, compatibility checks, and transcript artifact generation.
- Benchmark reference hardware and produce timestamp-quality results.

Exit evidence:

- §15.3 criteria pass on named reference fixtures.
- A crash/interruption test demonstrates preserved chunks and manual Resume.
- Human approves performance, timestamp quality, model licensing/provenance, and offline/privacy verification.

#### G4 — Render spike and preservation proof

Agent tasks:

- Implement a narrowly scoped render prototype on reference AAC M4B fixtures.
- Demonstrate PCM gain envelope, AAC encode/mux, required metadata/cover preservation, chapter equality, duration tolerance, and waveform tests.
- Investigate staged/segment render recovery, but do not promise resumability without proof.

Exit evidence:

- ADR O-03/O-04 are finalized with tool versions, mappings, tolerances, command/API behavior, and known unsupported cases.
- Human reviews decoded audio/listening results and approves or rejects render-resume design.

#### G5 — End-to-end MVP

Agent tasks:

- Integrate all approved components into guided UI.
- Add first-run, invalid-source, missing-model, no-profile/no-match, low-storage, interrupted-job, output-collision, and validation-failure UX.
- Implement filter report generation and explicit output confirmation.
- Implement restart-render fallback at minimum; enable proven resumable rendering only if G4 approval permits it.

Exit evidence:

- §15.1–§15.5 automated criteria pass.
- Human performs happy-path and recovery acceptance on both supported platforms.

#### G6 — Release candidate

Agent tasks:

- Complete platform matrix, package/sign/notarize according to ADR O-06, documentation, attribution, licenses, and known-limitations list.
- Run listening review, accessibility tests, security tests, migration tests, and clean-install/upgrade/uninstall tests.
- Produce a release checklist with every pass/fail result.

Human must make the final release decision. An AI agent cannot self-certify production readiness.

---

## 18. Risks and mitigations

| Risk | Mitigation | Owner | Release condition |
|---|---|---|---|
| Unverified base code architecture | G0 pinned-code discovery and human-approved integration plan | Contributor | No feature coding before G0 approval |
| Timestamp errors/missed terms | Model choices, user review, padding, corpus benchmark, listening tests | Contributor | Numeric quality gate and limitation messaging |
| Timeline/chapter/metadata damage | G4 render proof, fixture suite, blocking validation | Contributor | No success status without validation pass |
| Audible clicks/over-muting | Defined envelope, waveform tests, human listening review | Contributor | Quality thresholds approved |
| Unsafe resumable encoding | ADR O-05 proof; explicit restart fallback | Contributor | Resume only if fixtures prove contract |
| Model/FFmpeg/license incompatibility | SPDX/license/provenance review before inclusion | Contributor | Notices/attribution and approved distribution model |
| Installation/signing/notarization failure | O-06 selected early; release matrix | Contributor | Signed/notarized release checks pass |
| Disk exhaustion/corrupt artifacts | Preflight estimate, atomic storage, cleanup/recovery | Contributor | Low-storage and interruption tests pass |
| Privacy/security defects | Offline design, secure execution/import rules, dependency scans | Contributor | Security test checklist passes |
| Scope creep | §5 priority list and mandatory review gates | Product owner + Contributor | Deferred features do not block MVP |

---

## 19. Definition of done

The MVP is complete only when, on each approved Windows and macOS baseline, a User can select a supported non-DRM AAC M4B, install or select the approved local English Whisper model, complete or resume timestamped transcription, configure/select a basic catalog profile, review and approve hits, create a separate filtered AAC M4B, and receive a validation result proving duration/chapter/required-metadata preservation and planned attenuation behavior.

The source file must remain unchanged. The feature must operate offline after model installation. Interrupted transcription must preserve committed work and wait for explicit User Resume. Rendering must either resume only through a human-approved, fixture-validated design or explicitly restart the render while preserving all upstream artifacts. The release must pass automated media tests, human listening review, accessibility checks, platform packaging/signing checks, security/privacy checks, licensing/provenance review, and human Contributor release approval.
