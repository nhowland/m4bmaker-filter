# ADR-0042: Word Variation Scanner — multi-piece token splits

**Status:** Implemented and verified.

*Word pairs below (e.g. "spark"/"shark", "add"/"ad", "thorn") are
non-profane stand-ins for the actual catalog words this investigation
covered, swapped in for this public document; they preserve the exact
technical relationship (edit distance, letter-drop, dropout) of the real
words involved. Real hit counts and catalog entries (e.g. "goddamn")
are unchanged.*

## Context

Investigating why a real filtered book (Carl's Doomsday Scenario, Book 2)
still had 18 profanity instances audible after re-transcription found
that 15 of the 18 were never targeted by the render at all: the original
transcript never produced a matchable token for those specific word
instances (confirmed by direct measurement of the real rendered audio —
the target word's own position was in continuous, untouched full-volume
audio in every case, not a partial or edge-trimmed silence). Three
distinct sub-causes were found, all "recognition," not "timing":

- **Multi-piece splits**: `"damn"` inside "Goddamnit" tokenized as four
  separate pieces, `"G"`/`"odd"`/`"amn"`/`"it"` — the existing Word
  Variation Scanner (ADR-0036) only checks exactly two adjacent tokens,
  so it never considered this.
- **Outright misrecognition**: `"spark"` heard as `"shark"` — a different
  real word, not a split of the target's own letters at all.
- **Dropped audio**: `"thorn"` recognized in one independent
  transcription of the same audio but with nothing at all in the other
  transcript's corresponding span.

Only the first of these is fixable by extending the scanner's existing
mechanism. The other two are categorically different problems (fuzzy
phonetic similarity, and total recognition dropout) that a same-shaped
fix can't safely reach — see "What this does not fix" below.

## Decision: extend split-token detection from exactly 2 pieces to up to 4, still exact-match only

`find_word_variations()`'s split-token check generalizes from "join
exactly two adjacent tokens, compare to the target phrase" to "join 2 to
`_MAX_SPLIT_PIECES` (4) consecutive tokens, compare each length to the
target phrase" — still an **exact string equality** check at every
length, never fuzzy or edit-distance matching. This is the same
mechanism as before, just not artificially capped at two pieces.

4 was chosen as the ceiling because it's exactly what the real Book 2
case needed (`"G"`+`"odd"`+`"amn"` — 3 pieces — matches the catalog's
existing `"goddamn"` entry) with one piece of headroom, not because a
larger number was tested and found unnecessary — there was no real data
point motivating 5+.

**Why not add fuzzy/edit-distance matching to also catch the
misrecognition case (`"shark"`/`"spark"`):** explicitly rejected, and
guarded with a permanent regression test (`test_near_miss_word_is_not_
suggested`). `"shark"` sits at edit-distance 1 from `"spark"` — a
threshold loose enough to catch it would also flag real, unrelated
words throughout a transcript (this module's own docstring already
names this exact risk: "an unexpected auto-suggestion is worse than a
missed one"). Multi-piece exact-join carries none of that risk — it can
only ever match if the joined text is *letter-for-letter* the target
phrase — so it was safe to extend; near-miss matching was not, and stays
out of scope here.

## What this does not fix

Verified directly against the real 18-hit dataset from the Book 2
investigation, not assumed: extending to 4 pieces recovers the
`"goddamn"` case (10 real occurrences across the book, previously
invisible to the scanner) but **not** the other 14 residuals in that
set. Concretely:

- `"spark"`→`"shark"` and `"add"`→`"ad"` (pass-1 heard `"ad"`, not `"add"`
  — a one-letter miss, not a split) are misrecognition, not splitting;
  no exact multi-piece join produces the target phrase because the
  transcribed letters themselves are wrong, not just divided across
  token boundaries.
- `"thorn"` in the "awful ___ we" case has no tokens at all in the
  original transcript's corresponding span to join — the audio was
  seemingly not transcribed as anything there.
- The `"damn"`-alone catalog entry (as opposed to `"goddamn"`) still
  isn't recovered by exact joining in this same real case: no
  consecutive run of `"G"`/`"odd"`/`"amn"`/`"it"` joins to exactly
  `"damn"` (the closest, `"odd"`+`"amn"` = `"oddamn"`, is one letter
  off) — it's only recoverable because `"goddamn"` happens to also be a
  separate catalog entry that the 3-piece run does join to exactly.

These remain open, unsolved problems — disclosed here rather than
implied fixed by this change.

## What changed

- `variation_scan.py`: new `_MAX_SPLIT_PIECES = 4` constant with
  rationale; the split-token loop now accumulates a run of tokens
  (checking the "already covered by a real hit" exclusion and the
  existing `MAX_PHRASE_GAP_MS` adjacency rule at every step of the
  extension, not just the first pair) instead of a fixed two-token
  check. Docstrings updated to describe multi-piece splits instead of
  strictly two.
- No change to `word_variation_dialog.py` or any other caller —
  `WordVariationSuggestion`'s shape is unchanged, so the GUI needed no
  updates.

## Verification

**Unit tests** (`test_variation_scan.py`, 6 new): 3-piece and 4-piece
(at the new ceiling) splits are suggested; a 5-piece run (one beyond the
ceiling) is not, even though the joined text would otherwise match; a
gap exceeding `MAX_PHRASE_GAP_MS` partway through a run stops the
extension (not just checked at the first pair); a word already covered
by a real Matcher hit partway through a run stops the extension the same
way; and the explicit regression guard confirms `"shark"` is never
suggested for `"spark"`. All 22 tests in the file pass (16 pre-existing,
unmodified, plus 6 new).

**Real data**: ran the updated scanner against the real Book 2 pass-1
transcript and the User's actual current "Family Friendly" snapshot.
Found exactly 2 suggestions: the real `"G odd amn"` → `"goddamn"` split
(10 occurrences) described above, and one pre-existing, unrelated
false-positive already present in the *unmodified* stem-match logic
(`"assess"` suggested as a stemmed form of `"asses"`, since `"assess"` =
`"asses"` + the recognized `"s"` suffix) — disclosed to the User as a
separate, out-of-scope issue, not introduced by or fixed in this change.
Explicitly confirmed no `"shark"`-related suggestion appears anywhere in
this real scan.

`black`/`flake8`/`mypy` clean. `tests/filter/` (473 passed, 2 skipped),
`tests/gui/filter/test_word_variation_dialog.py` (10 passed), and the
project's real CI command, `pytest tests/ --ignore=tests/gui` (1008
passed, 2 skipped), all clean.
