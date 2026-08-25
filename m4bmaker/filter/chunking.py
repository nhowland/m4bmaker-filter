"""Chunk planning and overlap deduplication for long-source transcription
(PRD §11.3).

**Scope of this module vs. what's still missing.** This module is the pure,
deterministic *algorithm* half of §11.3: how to split a long source into
overlapping windows, and how to merge each window's transcribed words back
into one deduplicated timeline. It has no I/O and touches no job state.

**Chapter-aware planning.** The base project already has a proven pattern
for turning an M4B's chapter markers into audio-slice boundaries —
``gui/worker.py``'s ``SplitWorker`` computes each chapter's
``[start, next_chapter_start)`` and extracts it via ``ffmpeg -ss/-to``.
:func:`plan_chunks` reuses that same boundary logic (chapter *i* ends where
chapter *i+1* starts, the last chapter ends at the source duration) when
chapter start times are supplied, because a real chapter break is almost
always at a natural pause in the narration — splitting there is far less
likely to land mid-word than an arbitrary fixed-duration cut. This does
**not** replace fixed-duration planning, for two reasons that make chapter
boundaries alone insufficient: a chapter can run long enough that
subdividing it still matters (revised 2026-08-25, ADR-0001 — no longer
for pause-latency: pause responsiveness is explicitly deprioritized in
favor of chapter-aligned chunking's efficiency and durability benefits,
so a chapter is only subdivided past ``PRODUCTION_CHAPTER_CHUNK_MS``,
real headroom above the longest real chapter this fork has actually
tested end-to-end, not a tight pause-latency ceiling), and plenty of real
non-DRM M4Bs (exactly what this app targets) have sparse or no chapter
markers at all. So a chapter longer than *chunk_ms* is subdivided
internally using the same fixed-step logic the no-chapter path already
uses, and a source with no usable chapter data falls back to plain
fixed-duration planning entirely (``PRODUCTION_CHAPTERLESS_CHUNK_MS``).
Overlap padding is still applied at chapter boundaries, not skipped —
there is no verified evidence that a real-world chapter break never lands
mid-word (self-produced or messily-tagged files could easily have an
imprecise marker), so this stays a safety margin rather than an assumption.

**Not yet built** (a separate, larger slice of work): the durable,
resumable *orchestration* around this algorithm — persisting each
committed chunk atomically via ``storage.write_json_atomic``, wiring pause
requests to "stop scheduling new chunks, finish the in-flight one" (§11.3),
verifying source/model compatibility before resume, and driving all of it
through the ``JobState`` machine in ``jobs.py``. That needs the SQLite
persistence contract from PRD §14.3, which is itself still an open,
undecided item — building resumability on top of an undecided persistence
layer would mean guessing at a contract twice. This module exists so that
guess isn't required for the algorithm itself, which can be nailed down
and tested independently right now.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ChapterInfo
from .transcript import TranscriptSegment, TranscriptWord

#: Production chunk-planning defaults (ADR-0001, "Chunking strategy",
#: 2026-08-25). Chapter-aligned chunking is chosen for transcription
#: efficiency (whisper-cli reloads its full model from disk on every
#: invocation, so fewer/larger chunks meaningfully reduce wasted time —
#: real measurement, not a guess) and durability granularity (bounding a
#: crash's lost work to roughly one chunk), not pause-click responsiveness
#: — that target is explicitly deprioritized (PRD D-16, revised).

#: A chapter longer than this still gets subdivided. Real headroom above
#: the ~29-minute longest chapter this fork has actually run through
#: whisper-cli end-to-end (ADR-0001) — not itself benchmarked, since
#: durability still wants *some* bound on how much one crash can lose,
#: and whisper-cli's behavior well beyond the tested range is genuinely
#: unknown, not assumed safe.
PRODUCTION_CHAPTER_CHUNK_MS = 45 * 60 * 1000

#: Fallback chunk size for sources with no usable chapter markers, chosen
#: to land in the same rough durability-loss range chapter-aligned
#: chunking gives on this fork's own real reference book (mean chapter
#: ~16 minutes) — not independently benchmarked.
PRODUCTION_CHAPTERLESS_CHUNK_MS = 15 * 60 * 1000

#: Trailing overlap padding, used in both the chapter-aligned and
#: chapterless cases above.
PRODUCTION_OVERLAP_MS = 10 * 1000


@dataclass(frozen=True)
class ChunkPlan:
    """One planned chunk of audio to feed to the STT engine.

    ``start_ms``/``end_ms`` is the *audio slice* to transcribe, including
    the overlap padding used to protect words near a boundary from being
    cut off mid-word. ``owned_start_ms``/``owned_end_ms`` is the narrower
    range whose *words* are kept once this chunk's transcription is merged
    with its neighbors — the overlap region is transcribed twice (once by
    this chunk, once by the next) but only the second chunk's version of it
    is kept, since that chunk has trailing audio context whisper.cpp can
    use for the words right at the boundary, whereas this chunk was cut off
    there.
    """

    index: int
    start_ms: int
    end_ms: int
    owned_start_ms: int
    owned_end_ms: int


def _plans_from_owned_segments(
    segments: list[tuple[int, int]], overlap_ms: int, duration_ms: int
) -> list[ChunkPlan]:
    """Build padded :class:`ChunkPlan`\\ s from *segments* — a list of
    non-overlapping ``(owned_start, owned_end)`` pairs that already exactly
    partition ``[0, duration_ms)`` with no gaps, in order.

    Each segment's *audio slice* extends *overlap_ms* past its own owned
    end (clamped to *duration_ms*) — matching the trailing-only padding
    documented on :class:`ChunkPlan`: a chunk gets extra trailing context
    beyond what it owns, while its neighbor independently transcribes that
    same region with better leading context and wins ownership there.
    """
    plans: list[ChunkPlan] = []
    for i, (owned_start, owned_end) in enumerate(segments):
        end = (
            min(duration_ms, owned_end + overlap_ms)
            if duration_ms > 0
            else owned_end + overlap_ms
        )
        plans.append(
            ChunkPlan(
                index=i,
                start_ms=owned_start,
                end_ms=end,
                owned_start_ms=owned_start,
                owned_end_ms=owned_end,
            )
        )
    return plans


def _uniform_segments(
    duration_ms: int, chunk_ms: int, overlap_ms: int
) -> list[tuple[int, int]]:
    """Owned-segment boundaries spaced by ``step = chunk_ms - overlap_ms``,
    the no-chapter-data fallback (and the whole-file behavior when no
    chapter subdivides more finely than this already would)."""
    step = chunk_ms - overlap_ms
    segments: list[tuple[int, int]] = []
    start = 0
    while start < duration_ms:
        end = min(start + chunk_ms, duration_ms)
        is_last = end >= duration_ms
        owned_end = end if is_last else min(start + step, end)
        segments.append((start, owned_end))
        if is_last:
            break
        start += step
    return segments


def _chapter_segments(
    chapter_start_times_ms: list[int],
    duration_ms: int,
    chunk_ms: int,
    overlap_ms: int,
) -> list[tuple[int, int]]:
    """Owned-segment boundaries derived from chapter start times.

    Chapter *i* spans ``[start[i], start[i+1])``, or ``[start[i],
    duration_ms)`` for the last one — the exact boundary rule
    ``gui/worker.py``'s ``SplitWorker`` already uses for extracting chapter
    files. If the first chapter doesn't start at 0 (a preamble/intro before
    chapter markers begin), that leading span is treated as an implicit
    chapter so no audio is silently dropped. A chapter longer than
    *chunk_ms* — too long to respect the pause-latency budget as a single
    transcription call — is subdivided using the same fixed-step logic
    :func:`_uniform_segments` uses, scoped to that chapter's own span.
    Degenerate entries (duplicates, out-of-range, non-increasing) are
    dropped defensively rather than raised, since chapter metadata on a
    real-world file is exactly the kind of input this app must not crash
    on (PRD §12.5 treats embedded metadata as untrusted input).
    """
    starts = sorted({s for s in chapter_start_times_ms if 0 <= s < duration_ms})
    if not starts:
        return []
    if starts[0] > 0:
        starts.insert(0, 0)

    chapter_bounds: list[tuple[int, int]] = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else duration_ms
        if e > s:
            chapter_bounds.append((s, e))

    step = chunk_ms - overlap_ms
    segments: list[tuple[int, int]] = []
    for chapter_start, chapter_end in chapter_bounds:
        if chapter_end - chapter_start <= chunk_ms:
            segments.append((chapter_start, chapter_end))
            continue
        cursor = chapter_start
        while cursor < chapter_end:
            owned_end = min(cursor + step, chapter_end)
            is_last_piece = owned_end >= chapter_end
            segments.append((cursor, chapter_end if is_last_piece else owned_end))
            if is_last_piece:
                break
            cursor += step
    return segments


def plan_chunks(
    duration_ms: int,
    chunk_ms: int,
    overlap_ms: int,
    chapter_start_times_ms: list[int] | None = None,
) -> list[ChunkPlan]:
    """Split ``[0, duration_ms)`` into overlapping chunks.

    Without *chapter_start_times_ms* (or when it's empty): chunk *i* starts
    at ``i * step`` where ``step = chunk_ms - overlap_ms``. Ownership
    boundaries fall exactly on multiples of *step*, so every millisecond of
    the source is owned by exactly one chunk with no gap and no double
    ownership — the last chunk owns everything remaining up to
    *duration_ms* (there is no next chunk to hand its tail off to).

    With *chapter_start_times_ms* supplied: ownership boundaries follow
    chapter breaks instead (see :func:`_chapter_segments`), each chapter
    longer than *chunk_ms* subdivided the same way. Every chunk, chapter-
    aligned or not, still gets *overlap_ms* of trailing padding — a chapter
    break is a strong signal for a natural pause, not a guarantee.

    Raises :class:`ValueError` if *overlap_ms* is not smaller than
    *chunk_ms* (a non-positive or zero step would never advance, or would
    advance backwards).
    """
    if duration_ms <= 0:
        return []
    if overlap_ms >= chunk_ms:
        raise ValueError(
            f"overlap_ms ({overlap_ms}) must be smaller than chunk_ms ({chunk_ms})."
        )

    if chapter_start_times_ms:
        segments = _chapter_segments(
            chapter_start_times_ms, duration_ms, chunk_ms, overlap_ms
        )
    else:
        segments = []
    if not segments:
        segments = _uniform_segments(duration_ms, chunk_ms, overlap_ms)

    return _plans_from_owned_segments(segments, overlap_ms, duration_ms)


def default_chunk_params(chapters: tuple[ChapterInfo, ...]) -> tuple[int, int]:
    """``(chunk_ms, overlap_ms)`` for this fork's production defaults
    (ADR-0001, 2026-08-25): the 45-minute chapter-subdivision ceiling when
    *chapters* is non-empty, else the 15-minute chapterless fallback —
    both with 10s overlap. Shared by :func:`default_chunk_plan` and by
    real callers (e.g. a transcription worker) that need the raw
    parameters to pass to ``transcription_orchestrator.run_transcription_job``
    directly, not just a plan."""
    if chapters:
        return PRODUCTION_CHAPTER_CHUNK_MS, PRODUCTION_OVERLAP_MS
    return PRODUCTION_CHAPTERLESS_CHUNK_MS, PRODUCTION_OVERLAP_MS


def default_chunk_plan(
    duration_ms: int, chapters: tuple[ChapterInfo, ...]
) -> list[ChunkPlan]:
    """:func:`plan_chunks` using this fork's production defaults — see
    :func:`default_chunk_params`. The one call site every real caller
    should use instead of picking constants themselves, so the production
    defaults live in exactly one place."""
    chunk_ms, overlap_ms = default_chunk_params(chapters)
    chapter_starts = [c.start_ms for c in chapters] if chapters else None
    return plan_chunks(duration_ms, chunk_ms, overlap_ms, chapter_starts)


def chapter_for_chunk(
    chunk: ChunkPlan, chapters: tuple[ChapterInfo, ...]
) -> ChapterInfo | None:
    """Return the chapter *chunk*'s owned range falls within, or ``None``
    if *chapters* is empty or nothing matches.

    Well-defined and unambiguous whenever *chapters* was the same list used
    to plan *chunk* in the first place: :func:`_chapter_segments` scopes
    each chapter's own subdivision strictly to that chapter's
    ``[start, end)`` span, so a chunk's owned range never straddles two
    chapters. This is what makes a friendly "Chapter N of M" progress
    label a direct lookup rather than a fuzzy correlation problem (PRD
    §7.2 stage 3; see ADR-0015 for the full reasoning)."""
    for i, chapter in enumerate(chapters):
        next_start = chapters[i + 1].start_ms if i + 1 < len(chapters) else None
        if chunk.owned_start_ms >= chapter.start_ms and (
            next_start is None or chunk.owned_start_ms < next_start
        ):
            return chapter
    return None


def merge_segment_words(
    committed: list[tuple[TranscriptSegment, ChunkPlan]],
) -> list[TranscriptWord]:
    """Merge each committed chunk's transcribed words into one deduplicated,
    timeline-ordered word list.

    For each ``(segment, plan)`` pair, only words whose ``start_ms`` falls
    within ``[plan.owned_start_ms, plan.owned_end_ms)`` are kept — this is
    the timestamp-based ownership rule documented on :class:`ChunkPlan`,
    chosen over text-based deduplication (matching repeated words by
    content) because timestamp ownership is unambiguous even when the same
    word legitimately appears twice in the overlap window; text matching
    would either wrongly drop a real repeat or wrongly keep a true
    duplicate depending on which heuristic broke the tie.

    *committed* is assumed to already be sorted by chunk index — this
    function does not re-sort chunks, only the words within the result,
    since silently re-ordering out-of-sequence input would hide a caller
    bug (an out-of-order commit list) rather than surface it.
    """
    words: list[TranscriptWord] = []
    for segment, plan in committed:
        for word in segment.words:
            if plan.owned_start_ms <= word.start_ms < plan.owned_end_ms:
                words.append(word)
    words.sort(key=lambda w: w.start_ms)
    return words
