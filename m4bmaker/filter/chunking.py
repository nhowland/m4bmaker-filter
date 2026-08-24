"""Chunk planning and overlap deduplication for long-source transcription
(PRD §11.3).

**Scope of this module vs. what's still missing.** This module is the pure,
deterministic *algorithm* half of §11.3: how to split a long source into
overlapping windows, and how to merge each window's transcribed words back
into one deduplicated timeline. It has no I/O and touches no job state.

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

from .transcript import TranscriptSegment, TranscriptWord


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


def plan_chunks(duration_ms: int, chunk_ms: int, overlap_ms: int) -> list[ChunkPlan]:
    """Split ``[0, duration_ms)`` into overlapping chunks.

    Consecutive chunks overlap by *overlap_ms*: chunk *i* starts at
    ``i * step`` where ``step = chunk_ms - overlap_ms``. Ownership
    boundaries fall exactly on multiples of *step*, so every millisecond of
    the source is owned by exactly one chunk with no gap and no double
    ownership — the last chunk owns everything remaining up to
    *duration_ms* (there is no next chunk to hand its tail off to).

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
    step = chunk_ms - overlap_ms

    plans: list[ChunkPlan] = []
    index = 0
    start = 0
    while start < duration_ms:
        end = min(start + chunk_ms, duration_ms)
        is_last = end >= duration_ms
        owned_end = end if is_last else min(start + step, end)
        plans.append(
            ChunkPlan(
                index=index,
                start_ms=start,
                end_ms=end,
                owned_start_ms=start,
                owned_end_ms=owned_end,
            )
        )
        if is_last:
            break
        index += 1
        start += step
    return plans


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
