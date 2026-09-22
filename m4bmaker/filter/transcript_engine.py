"""whisper.cpp subprocess adapter (PRD §10.1, O-02; ADR-0001).

**G3 spike scope.** This proves the whisper.cpp integration end-to-end
against a real ``tiny.en`` model and a short synthetic speech fixture — see
``docs/adr/0001-stt-engine-integration.md``'s "G3 spike findings" section
for the full narrative, the real captured JSON shape this module's parsing
was built against, and what it revealed that wasn't knowable from
whisper.cpp's docs alone (e.g. that its own model-download script does no
checksum verification at all, contrary to what a casual read of PRD D-11
might lead you to assume is "whatever whisper.cpp already does").

**Explicitly not implemented here** — do not mistake this module for a
finished TranscriptionJob:

- **Chunking.** :func:`transcribe_short_audio` makes exactly one whisper.cpp
  call and produces exactly one :class:`~m4bmaker.filter.transcript.TranscriptSegment`.
  A 20-hour source needs the file split into durable, independently
  resumable chunks (PRD §11.3) with atomic per-chunk persistence — that is
  real design work (chunk boundary strategy, overlap/dedup at boundaries,
  pause/resume wiring through the Job Orchestrator) that does not exist yet.
- **The Model Manager.** No download/checksum/storage/removal UI exists.
  The G3 spike downloaded its model manually (see the ADR) and passes a
  local model path straight into this module.
- **Benchmarking.** No throughput/memory measurement across `base.en` /
  `small.en` on named reference hardware (PRD §13.1) has been done —
  deferred by explicit product-owner choice when this spike was scoped.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from m4bmaker.utils import find_binary, subprocess_flags

from .models import normalize_token
from .transcript import (
    SegmentStatus,
    Transcript,
    TranscriptEngine,
    TranscriptSegment,
    TranscriptSource,
    TranscriptStatus,
    TranscriptWord,
)

_BINARY_NAME = "whisper-cli"


class WhisperNotFoundError(Exception):
    """Raised when the whisper-cli binary cannot be located. A caller built
    on the future Job Orchestrator should catch this and transition the job
    to ``NEEDS_ATTENTION`` (PRD §11.2), not let it propagate as a crash."""


class WhisperTranscriptionError(Exception):
    """Raised when whisper-cli runs but exits non-zero (after retries)."""


#: whisper-cli invocation attempts (on the default GPU backend) before
#: falling back to a CPU-only attempt. A real ~23.6-minute chunk was
#: directly observed to crash whisper-cli natively (SIGABRT,
#: ``WHISPER_ASSERT: filter_width < a->ne[2]`` deep in its DTW alignment
#: code) then transcribe cleanly on 5/5 immediate standalone retries of the
#: exact same file and flags — non-deterministic, not a function of this
#: input's content or duration. Root cause looks like Metal/GPU-backend
#: state (undetermined further; out of scope to chase into whisper.cpp/ggml
#: itself), so retrying the same invocation is the first mitigation.
_WHISPER_MAX_ATTEMPTS = 3


def find_whisper_cli() -> str | None:
    """Return the path to the whisper-cli binary, or ``None`` if not found."""
    return find_binary(_BINARY_NAME)


def get_whisper_version(whisper_cli: str | None = None) -> str | None:
    """Return whisper.cpp's self-reported version string (e.g. ``"1.9.2"``),
    or ``None`` if the binary can't be found or its output doesn't match
    the expected ``"whisper.cpp version: X.Y.Z"`` line. Checked against both
    stdout and stderr — the real binary was observed printing this after
    several backend-initialization lines on stderr, not on stdout alone."""
    binary = whisper_cli or find_whisper_cli()
    if binary is None:
        return None
    result = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        encoding="utf-8",
        **subprocess_flags(),
    )
    for line in (result.stdout + result.stderr).splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("whisper.cpp version:"):
            return str(stripped.split(":", 1)[1].strip())
    return None


def _is_special_token(text: str) -> bool:
    """whisper.cpp emits non-word marker tokens like ``[_BEG_]`` and
    ``[_TT_225]`` (an internal timestamp token) interleaved with real words
    in its token list — confirmed against real output in the G3 spike, not
    assumed from documentation."""
    stripped = text.strip()
    return stripped.startswith("[_") and stripped.endswith("]")


def _is_punctuation_only(text: str) -> bool:
    return not any(ch.isalnum() for ch in text)


def run_whisper(
    audio_path: Path,
    model_path: Path,
    *,
    language: str = "en",
    whisper_cli: str | None = None,
    dtw_model_name: str | None = None,
) -> dict[str, Any]:
    """Run whisper.cpp against *audio_path* and return its parsed
    "full JSON" output (whisper.cpp's own schema — see
    :func:`whisper_result_to_segment` for the mapping into ours).

    *audio_path* must already be 16kHz mono PCM WAV; whisper.cpp requires
    this input format. Converting from the source M4B's AAC track is the
    Media Inspector/Renderer's job (an ffmpeg extraction step), not this
    function's — kept as a hard boundary so this adapter has exactly one
    responsibility.

    Uses whichever backend ``ggml`` selects by default: GPU (Metal/CUDA/
    etc.) if the standard build finds a compatible device, CPU otherwise
    — reversed 2026-08-25 (ADR-0001) from the original CPU-only-for-v1
    call. ``--no-gpu`` is a plain runtime flag on the one standard build
    already shipped (confirmed in the G3 spike), so this needed no
    packaging change: real benchmarking against this fork's own reference
    audiobook measured a 4.1-5.6x speedup with no accuracy loss before this
    reversal, on both approved models (ADR-0001's "GPU acceleration"
    section has the full figures).

    Retries up to :data:`_WHISPER_MAX_ATTEMPTS` times on the GPU backend,
    then falls back to one CPU-only (``--no-gpu``) attempt before raising
    (ADR-0032/ADR-0033) — a real, non-deterministic GPU-backend crash was
    observed to clear reliably on CPU, with DTW's timing precision fully
    preserved (``--no-gpu`` doesn't affect ``-dtw``/``-nfa``), at a real
    but one-time ~2.4x slowdown for whichever single chunk needed it.

    *dtw_model_name*, when given (ADR-0025), requests whisper.cpp's DTW
    (Dynamic Time Warping) token-level timestamp mode — its own bare model
    identifier (e.g. ``"base.en"``), not a file path. Passing this also
    disables flash attention (``-nfa``): DTW timestamps are silently
    computed as all ``-1`` under flash attention in this build
    (confirmed directly — whisper-cli logs "dtw_token_timestamps is not
    supported with flash_attn - disabling" rather than erroring), so
    enabling one requires disabling the other. Real measurement (5
    minutes of real narrated audio, this fork's own reference audiobook):
    ~1.5x slower than flash-attn's default heuristic timestamps — a real
    but moderate cost, chosen because the default heuristic was measured
    (ADR-0024) to place word boundaries hundreds of milliseconds off on
    real narration, which DTW's actual per-token alignment corrects.
    """
    binary = whisper_cli or find_whisper_cli()
    if binary is None:
        raise WhisperNotFoundError(
            f"{_BINARY_NAME} not found on PATH or in a bundled location."
        )

    with tempfile.TemporaryDirectory() as tmp:
        out_stem = str(Path(tmp) / "result")
        cmd = [
            binary,
            "-m",
            str(model_path),
            "-f",
            str(audio_path),
            "-l",
            language,
            "-oj",
            "-ojf",
            "-of",
            out_stem,
            "-np",
        ]
        if dtw_model_name is not None:
            cmd += ["-dtw", dtw_model_name, "-nfa"]

        last_result: subprocess.CompletedProcess[str] | None = None
        for attempt in range(1, _WHISPER_MAX_ATTEMPTS + 1):
            last_result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", **subprocess_flags()
            )
            if last_result.returncode == 0:
                out_path = Path(out_stem + ".json")
                parsed: dict[str, Any] = json.loads(
                    out_path.read_text(encoding="utf-8")
                )
                return parsed

        assert last_result is not None  # loop runs at least once

        # GPU attempts exhausted. Real testing against the exact input that
        # crashed this way reproduced it once on GPU then never again across
        # a CPU-only (--no-gpu) rerun — still with DTW+NFA, so per-token
        # timing precision is unaffected — at roughly 2.4x the wall time.
        # One CPU-only attempt is worth that cost rather than failing the
        # whole job on what real evidence says is a GPU-backend-specific
        # failure, not a problem with the audio itself.
        cpu_result = subprocess.run(
            [*cmd, "--no-gpu"],
            capture_output=True,
            encoding="utf-8",
            **subprocess_flags(),
        )
        if cpu_result.returncode == 0:
            out_path = Path(out_stem + ".json")
            parsed = json.loads(out_path.read_text(encoding="utf-8"))
            return parsed

        stderr_tail = cpu_result.stderr.strip()[-2000:]
        raise WhisperTranscriptionError(
            f"whisper-cli exited with code {last_result.returncode} after "
            f"{_WHISPER_MAX_ATTEMPTS} GPU attempt(s), then code "
            f"{cpu_result.returncode} on a CPU-only fallback attempt: "
            f"{stderr_tail}"
        )


def dtw_model_name_for(model_path: Path) -> str:
    """The bare model identifier ``-dtw`` expects (e.g. ``"base.en"``),
    derived from *model_path*'s filename — every model this fork installs
    follows ``model_manager.ModelSpec.filename()``'s own
    ``f"ggml-{name}.bin"`` convention, so stripping that prefix recovers
    *name* without needing a second parameter threaded through every
    caller that already has the path."""
    return model_path.stem.removeprefix("ggml-")


def _dtw_start_ms(token: dict[str, Any]) -> int | None:
    """A token's DTW-aligned start time in ms, or ``None`` if this token
    has no valid DTW value (``t_dtw`` absent entirely when DTW mode
    wasn't requested; ``-1`` for a handful of boundary tokens even when it
    was — both observed directly against real whisper.cpp output, ADR-0025).
    whisper.cpp reports ``t_dtw`` in 10ms units (its mel-spectrogram frame
    resolution), hence the ``* 10``."""
    t_dtw = token.get("t_dtw")
    if isinstance(t_dtw, (int, float)) and t_dtw >= 0:
        return round(t_dtw * 10)
    return None


def whisper_result_to_segment(
    raw: dict[str, Any],
    segment_id: str,
    segment_start_ms: int,
    segment_end_ms: int,
) -> TranscriptSegment:
    """Map one whisper.cpp full-JSON result (from :func:`run_whisper`) into
    one :class:`~m4bmaker.filter.transcript.TranscriptSegment`.

    Confidence is whisper.cpp's per-token ``p`` (probability) value, passed
    through unchanged — real spike output showed this varies meaningfully
    (0.75 for a clearly-spoken uncommon word, 0.47 for a word whose
    surrounding context made it less predictable), so it is a real signal
    worth preserving, not a constant to special-case around.

    Special tokens (``[_BEG_]``, ``[_TT_N]``, etc.) and punctuation-only
    tokens are dropped — they carry timestamps in whisper.cpp's output but
    are not spoken words, and PRD §9.3 matching operates on recognized
    words.

    **Timestamp source, per token (ADR-0025):** whisper.cpp's DTW mode
    (``run_whisper``'s ``dtw_model_name``) gives each token a single
    aligned *start* point (``t_dtw``), not a start/end pair — a token's
    end is taken as the *next* token's DTW start (tokens are contiguous
    in a decode sequence, so one token's alignment boundary is the next
    one's), falling back to whisper.cpp's own heuristic ``offsets``
    wherever a DTW value is missing (either because DTW wasn't requested
    at all, or for the handful of boundary tokens real output showed
    without one even when it was). This means a transcript produced
    without DTW parses exactly as before — this function doesn't need to
    know whether DTW was requested, only whether each token's own data
    has it.
    """
    words: list[TranscriptWord] = []
    for entry in raw.get("transcription", []):
        tokens = entry.get("tokens", [])
        dtw_starts = [_dtw_start_ms(token) for token in tokens]

        for i, token in enumerate(tokens):
            text = token.get("text", "")
            if _is_special_token(text) or _is_punctuation_only(text):
                continue
            stripped = text.strip()
            if not stripped:
                continue

            offsets = token.get("offsets", {})
            start_ms = (
                dtw_starts[i] if dtw_starts[i] is not None else offsets.get("from")
            )
            end_ms = None
            if i + 1 < len(dtw_starts) and dtw_starts[i] is not None:
                end_ms = dtw_starts[i + 1]
            if end_ms is None:
                end_ms = offsets.get("to")

            if start_ms is None or end_ms is None or end_ms <= start_ms:
                # Observed occasionally at segment boundaries in real
                # output — skip rather than construct an invalid
                # TranscriptWord (whose own validation would reject it).
                continue
            words.append(
                TranscriptWord(
                    text=stripped,
                    normalized=normalize_token(stripped),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    confidence=token.get("p"),
                )
            )
    return TranscriptSegment(
        id=segment_id,
        start_ms=segment_start_ms,
        end_ms=segment_end_ms,
        status=SegmentStatus.COMPLETED,
        words=tuple(words),
    )


def transcribe_short_audio(
    audio_path: Path,
    model_path: Path,
    model_checksum: str,
    source: TranscriptSource,
    *,
    language: str = "en",
    whisper_cli: str | None = None,
    engine_version: str | None = None,
) -> Transcript:
    """Transcribe one short audio file in a single whisper.cpp call and
    wrap the result as a one-segment, ``COMPLETE`` :class:`Transcript`.

    **Spike-only convenience, not the production TranscriptionJob.** Real
    transcription of a multi-hour source must chunk audio into durable,
    independently resumable segments (PRD §11.3); this function proves the
    engine call and JSON mapping are correct end-to-end, nothing more.

    Uses DTW timestamps by default (ADR-0025) — same as the production
    orchestrator, so a transcript built here isn't a different, less
    accurate code path than what the real app uses.
    """
    raw = run_whisper(
        audio_path,
        model_path,
        language=language,
        whisper_cli=whisper_cli,
        dtw_model_name=dtw_model_name_for(model_path),
    )
    segment = whisper_result_to_segment(
        raw,
        segment_id="chunk-000000",
        segment_start_ms=0,
        segment_end_ms=source.duration_ms,
    )
    resolved_version = engine_version or get_whisper_version(whisper_cli) or "unknown"
    return Transcript(
        schema_version=1,
        status=TranscriptStatus.COMPLETE,
        source=source,
        engine=TranscriptEngine(
            name="whisper.cpp",
            version=resolved_version,
            model=model_path.stem,
            model_checksum=model_checksum,
            parameters={"language": language, "gpu": "false"},
        ),
        segments=(segment,),
    )
