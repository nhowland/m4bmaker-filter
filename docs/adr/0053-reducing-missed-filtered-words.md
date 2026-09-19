# ADR-0053: Reducing missed filtered words — options for Contributor decision

**Status:** Proposed — options recorded for Contributor decision; nothing
here changes `matcher.py`'s own exact-match behavior. **Options 3 and 4,
tested against real data (2026-09-17), do not work as designed — see
"Real-data validation" below.** Recommendation revised accordingly: Option 1
first (design settled via mockup, 2026-09-19, **built and implemented as the
Transcript tab** — see "Performance fixes" below for a real-app follow-up
pass), Option 2 tested and unaffected (safe, low-value — see "Real-data
validation, part 2"), Options 3/4 rejected rather than deferred (their
mockup deleted, 2026-09-19).
**Related:** ADR-0036 (word variation scanner), ADR-0042 (multi-piece
splits, the real-book investigation this ADR builds directly on), ADR-0025 /
ADR-0049 (DTW word timestamps and per-word confidence), ADR-0052 (hit
preview playback — Option 1's own preview player is reused directly,
not reimplemented)
**Related PRD items:** §9.3 (Matcher), §9.4 (Scan and review), §15.2

*Word pairs below (e.g. "spark"/"shark", "add"/"ad", "thorn") are
non-profane stand-ins for the actual catalog words ADR-0042 found, kept
consistent with that ADR's own substitutions; real hit counts and
catalog entries (e.g. "goddamn") are unchanged.*

## Context

The Contributor's stated hypothesis was that missed filtered words come
from Whisper's tokenization of compound words/phrases — the existing Word
Variation Scanner (ADR-0036) already targets exactly this, and helped.

ADR-0042's real investigation (the test audiobook, Book 2 — 18 audible
misses) found that tokenization splits are only one of three distinct
causes, and the only one a same-shaped fix can safely reach:

- **Multi-piece splits** — `"damn"` tokenized as `"G"`/`"odd"`/`"amn"`/`"it"`
  inside "Goddamnit." Fixed by extending the scanner to join up to 4
  adjacent tokens (ADR-0042). Purely a tokenization problem.
- **Outright misrecognition** — `"spark"` transcribed as `"shark"`, `"add"`
  as `"ad"`. Whisper hears a *different real word*; no exact join of the
  target's own letters can ever produce it. Not a tokenization problem —
  a recognition-accuracy problem.
- **Total dropout** — `"thorn"` present in one independent transcript of
  the same audio, absent entirely from the other's corresponding span. No
  token exists anywhere to match against.

ADR-0036/0042 already tried and explicitly rejected bounded edit-distance
fuzzy matching as a fix for the misrecognition case, guarded by a permanent
regression test (`test_near_miss_word_is_not_suggested`): `"shark"` sits at
edit-distance 1 from `"spark"`, and any threshold loose enough to catch it
also flags real, unrelated words throughout a transcript. That rejection
was scoped to the Matcher/scanner acting **automatically** — it does not
by itself rule out the same technique used purely as a **human-reviewed
suggestion** (Options 2 and 3 below revisit this distinction explicitly).

No fuzzy, phonetic, or n-gram matching exists anywhere else in the
codebase to reuse (repo-wide grep for soundex/phonetic/levenshtein/
difflib/fuzzywuzzy/rapidfuzz/jellyfish/metaphone/SequenceMatcher returns
nothing outside `variation_scan.py`'s own docstrings explaining why it was
rejected). `TranscriptWord` (`transcript.py`) already carries a real,
variable per-word `confidence` field (ADR-0025/0049), currently unused by
any GUI surface.

## Options considered

### Option 1 — Full-transcript review with hits masked out

Extend the Review step (`m4bmaker/gui/filter/wizard/review_step.py`) with a
view showing the entire chapter transcript, catalog hits struck
through/masked inline, everything else left visible and readable.

- **What it reaches:** all three failure categories, including
  misrecognition and total dropout — the two ADR-0042 called
  "categorically different problems... a same-shaped fix can't safely
  reach." No pattern-matching technique can flag a word that isn't in the
  transcript at all, or that Whisper transcribed as a different valid
  real word; only a human reading the surrounding text (or listening,
  via the hit-preview player from ADR-0052) can catch those.
- **Cost:** doesn't scale on its own — skimming a full audiobook
  transcript, even one book, is a lot of text. Needs real UI support
  (jump-to-hit, marking spans as reviewed) to stay usable. (Originally
  expected to pair with Option 4's triage signal to narrow what needs a
  close read — moot now that Option 4 is rejected; see "Real-data
  validation" below.)
- **Effort:** moderate — mostly a new tab/view over data (`Transcript`,
  `ScanHit`s) the app already has in memory; no new detection algorithm.

**Design settled via mockup (2026-09-19)** — see
`docs/design/full-transcript-review-wireframe.html`:

- A new "Transcript" tab on the Review step (alongside Hits and Render
  Plan — no "Low Confidence" tab; that line of work is rejected, see
  below). Paginated by chapter, reusing the transcript's own
  `segments` (already roughly chapter-aligned) rather than any new
  data, plus a "Jump to next hit" button so a hit-dense chapter doesn't
  need reading word-by-word around parts already verified.
- **Selection is word-span, not native text selection**: click a word,
  or drag across several: a drag stops at a hit's edge rather than
  spanning over one, so what's about to be added is never ambiguous.
- **Two paths to the same action**: a persistent "＋ Add to Catalog"
  control (enabled only with a selection, same enable/disable pattern
  the ADR-0052 preview dock already uses) *and* a right-click "Add… to
  Catalog" menu item on the same selection.
- **Listening reuses ADR-0052's player directly** — select, then press
  play to hear that exact span before deciding to add it. Single fixed-
  span mode only, same reasoning as before: nothing selected here has a
  catalog entry yet, so there's no padding window to toggle to.
- **"Add" changes the catalog, not the current scan — and says so
  visibly.** Review's own hits are matched against an "immutable
  profile snapshot"; adding a word here doesn't retroactively create
  hits for every other occurrence in this book. A banner tracks how
  many entries were added this session (correct singular/plural) and
  prompts a re-scan, and newly-added words get a dashed pending-
  underline in the text — visually distinct from a real hit's solid
  strikethrough — so the gap between "added to catalog" and "actually
  applied to this book" is never silently assumed away.
- **Signaled as optional, not just described as optional (2026-09-19
  refinement).** This tab is a supplementary check, not a required
  step — most Contributors should finish at Hits/Render Plan and never
  need it. Every inactive tab in this app's real `QTabWidget` already
  renders identically muted, so there's no "extra-muted" style to
  reach for without inventing a new visual language just for one tab;
  the actual, cheap levers used instead: **tab order** (last, after
  Render Plan, so it never competes with or interrupts the
  Hits → Render Plan → Continue path), **a hover tooltip** on the tab
  itself stating it's optional before anyone even clicks in, and **an
  in-tab note** saying so outright — reusing ADR-0045's exact wording
  pattern for the Render Plan tab's own "most people won't need to
  check this before continuing," not a new convention. No "NEW" badge
  in the real build: that invites checking, which is backwards from
  the goal. As a hard rule rather than a UI element: Continue must
  never be gated on visiting this tab. Considered and rejected: pulling
  this out of the tab row into a separate menu/link — tabs are already
  the established pattern for "another view of Review's data" in this
  step, and a second UI paradigm for one tab would be *more*
  conspicuous, not less.
- **An unverified idea, included but flagged as such**: an optional
  "Highlight uncertain words" toggle puts a dotted underline on
  low-confidence words without hiding anything else — a different,
  much smaller claim than Option 4's rejected use of confidence as a
  *filter*. Off-by-default would be the more defensible starting point
  given it's never been tested; shown togglable mainly so the
  trade-off itself is visible to evaluate, not as a settled decision.

### Option 2 — Extend the variation scanner's own heuristics (curation aid only)

`variation_scan.py`'s stem check is a fixed suffix whitelist
(`"ing"/"ers"/"er"/"ed"/"es"/"s"`, `_stem_suffix`), explicitly documented
as not a general stemmer — no consonant-doubling (run→running), no
irregular plurals. A real stemmer, or even bounded edit-distance, is worth
reconsidering *specifically in this module*, because every suggestion it
produces already goes through `word_variation_dialog.py` for human
approval before it can touch the catalog — the "too noisy" rejection in
ADR-0036/0042 applies to the Matcher acting automatically, not to a
human-reviewed suggestion list.

- **What it reaches:** still bounded to spelling-adjacent misses — a
  broader stemmer catches more legitimate inflections
  (`run`/`running`), but nothing that requires acknowledging Whisper
  heard a wholly different word or nothing at all.
- **Cost:** lowest of the five options — same shape as existing,
  already-tested code, same review gate already in place.
- **Effort:** small.

**Verdict: tested against real data (2026-09-18) — safe, but no real
recall gain shown.** See "Real-data validation" below. Unlike Options
3/4, the consonant-doubling extension is structurally narrow (a
candidate must literally start with the exact catalog word's letters)
and did not flood — but it also found nothing genuinely new across 11
real books. Cheap and low-risk enough to build for completeness, not
something to expect a real recall improvement from on this evidence.

### Option 3 — Phonetic/sound-alike suggestions (advisory only)

Compare catalog words against **all** transcript words (not just
split-adjacent ones) with a coarse phonetic algorithm (Soundex/Metaphone/
Double Metaphone), surfaced only as suggestions in the same
variation-scan dialog as Options 2.

- **What it reaches:** the misrecognition case specifically — the one
  Option 2's spelling-adjacency can't touch. `"shark"`/`"spark"` differ by
  edit-distance 1, and depending on the algorithm chosen may or may not
  cluster as phonetically similar; that would need to be checked against
  the real Book 2 data before trusting it.
- **Cost:** highest false-positive risk of the five — this is the
  same shape of noise ADR-0036/0042 already found and rejected once,
  now run advisory-only instead of automatic. Scoping is real work: run
  unscoped against every word in a 20-hour book's transcript and the
  suggestion list itself becomes unreadable. Likely needs to be paired
  with Option 4 (only phonetic-compare low-confidence words) to be
  practical at all.
- **Effort:** moderate — new dependency or hand-rolled phonetic
  algorithm, plus real tuning against real book data before it's
  trustworthy enough to ship.

**Verdict: rejected, tested against real data (2026-09-17).** See
"Real-data validation" below — neither spelling edit-distance nor a
real phonetic algorithm (metaphone) produced a usable signal across 11
real books. Not a scoping/threshold problem; structural.

### Option 4 — Surface Whisper's per-word confidence as a triage signal

`TranscriptWord.confidence` is already computed and stored per word
(ADR-0025/0049) but nothing in the GUI reads it today. A "review
low-confidence words" view would be a much smaller, higher-signal list
than Option 1's full transcript — and it's the natural, already-available
explanation for *why* a specific misrecognition happened (if the `"shark"`
span had low confidence, that's discoverable and shown to the User,
rather than requiring a blind full-transcript re-read).

This view must **exclude any word already covered by a real Matcher
hit** — the same exclusion `find_word_variations()` already applies to
its own suggestions (`variation_scan.py`). Without it, this view would
duplicate the Hits tab's own existing Confidence filter (`Below 25%/
75%/90%`, already shipped) for any hit that happens to also have low
confidence. The two must partition cleanly: the Hits filter narrows
*within* already-matched hits ("of what matched, which should I
double-check"); this view shows only the complement — words that never
matched at all, which is structurally where misrecognition lives, since
a misrecognized word by definition isn't in the catalog under the form
Whisper produced.

- **What it reaches:** misrecognition, and narrows Option 1/3's scope
  usefully. Does **not** reach total dropout — a dropped word has no
  token at all, so there's nothing with a confidence value to flag; that
  case only shows up in Option 1's full-transcript view (or not at all,
  since the transcript simply skips that span).
- **Cost:** confidence is a triage signal, not a guarantee — plenty of
  low-confidence words will be unrelated (accents, background noise,
  proper nouns), so this narrows attention, it doesn't eliminate the
  need for human judgment.
- **Effort:** small — the data already exists; this is a new GUI view
  over an existing field, no new detection algorithm.

**Design settled via mockup (2026-09-17), mockup later deleted
(2026-09-19)** — was `docs/design/low-confidence-review-wireframe.html`,
removed once real-data testing rejected the criterion it demonstrated
(see "Real-data validation" below and open question 6):

- A new "Low Confidence" tab on the Review step, not folded into Option
  1's future full-transcript view — resolves half of open question 4
  below (the threshold *value* that defines "low" is still open; where
  it surfaces is now decided).
- The confidence threshold is a live combo on the tab itself, reusing
  the Hits tab's own existing wording (`All`/`Below 25%`/`Below 75%`/
  `Below 90%`) — not a saved profile setting. A profile is about what
  gets filtered and how it renders; nothing about it should change how
  much a Contributor wants to manually check in a given session.
- **"＋ Add to Catalog" is in scope** — without it this tab is
  read-only, which undercuts the whole point of finding a miss.
  Confirmed to reuse `word_variation_dialog.py`'s existing add-entry
  flow rather than build a separate one.
- The preview reuses ADR-0052's dock exactly, but single-mode only — a
  non-hit word has no catalog padding to define a "Filtered word"
  window, so only the fixed ±2s context clip applies.
- **Distinctiveness from the Hits tab's own Confidence filter** needed
  an explicit design answer, not just the exclusion rule above: both
  tabs show a `Conf.` column with the *same* underlying score, which
  risks reading as the same table twice. Solved the same way ADR-0045
  solved an analogous "these two things might look related but aren't"
  problem in this same step — a short, plain-language explainer label
  (matching the real `QLabel#statusLabel` convention `_build_plan_tab()`
  already uses) at the top of the tab's content, stating outright that
  the score *is* the same measurement, just shown for a different,
  non-overlapping set of words.

**Verdict: rejected, tested against real data (2026-09-17).** Confidence
alone produced 286,061 non-hit candidates across 11 real books with zero
genuine misses in a 70-item manual read — see "Real-data validation"
below. The mockup's UI mechanics remain a sound design record; the
confidence-only criterion it's built on does not work and should not be
implemented as-is.

### Option 5 — Targeted re-transcription of low-confidence spans

Re-run a stronger model or different decoding settings on short spans
flagged by Option 4, rather than the whole file, hoping a second
independent pass recovers what the first missed or dropped.

- **What it reaches:** potentially misrecognition and dropout both, if
  a second pass genuinely produces different output on the same audio.
- **Cost / open risk:** ADR-0049 already found `base.en` and `small.en`
  produce **identical hits** under DTW on the case tested — the existing
  evidence does not yet support this finding anything new. Per this
  project's own verify-before-building discipline, this needs a real
  spike against actual flagged spans before committing engineering time,
  not an assumption that a second pass helps.
- **Effort:** highest of the five — two-model pipeline, plus the spike
  itself before any real build decision.

## Real-data validation (2026-09-17): Options 3 and 4 rejected

Before building the Low Confidence tab mockup's design any further, the
recommended 4 → 1 → 2 sequence below was checked against real data —
same discipline as ADR-0042's Book 2 investigation, not assumed to hold
just because the underlying `"spark"`→`"shark"` motivating case was
real. It didn't hold.

**Method.** All 11 real, already-transcribed books cached on this
machine (`~/Library/Application Support/m4bmaker/filter/transcripts/`,
118k–298k words each), re-scanned in memory against the real "Family
Friendly" profile snapshot (`catalog.create_snapshot()`, no changes to
`matcher.py`). For each book: every transcript word not covered by a
real `ScanHit` span, filtered to confidence < 90%, tested against the
profile's own real catalog vocabulary (fragment entries added purely to
catch a specific split-token pattern, e.g. `"godd"` from `"g odd amn
it"`, excluded from the comparison target set — they aren't real words
and match almost anything).

**Round 1 — confidence alone (Option 4 as designed).** 286,061
candidates across 11 books. Manually read 70 (40 random + the 30
single lowest-confidence words in one book, where a real miss should be
most likely to surface): **zero** looked like a plausible missed word.
Almost all were either ordinary function words with an unexplained low
score, or this-book's-own invented LitRPG vocabulary and character
names (`Donut`, `Astral`, `gnomish`, `Kartia`, `Gwyn`) — rare tokens for
a small model, nothing to do with profanity.

**Round 2 — confidence + spelling edit-distance (Option 3, first
attempt).** A flat edit-distance≤2 filter barely reduced the pool
(196,319 — short catalog words match almost anything at that
threshold). A length-scaled bound (distance 0 for ≤3 letters, 1 for
4–5, 2 for 6+) got real-book totals down to 8,779, but a 60-sample
random read across all 11 books still found **zero** genuine misses —
every false positive was an ordinary word one spelling-edit from a
short catalog word (verified pairs: `want`/`wart`, `where`/`wire`,
`that`/`thaw` — each real, each distance 1, none related), repeated
many times over with different ordinary words each time.

**Round 3 — confidence + a real phonetic algorithm (metaphone).**
Calibration first: metaphone's exact code correctly separates most of
round 2's worst offenders (`want`/`wart` → `WNT`/`WRT`, no match;
`heck`/`fell`/`well`/`bell` all distinct from each other) — real
precision improvement. But it does *not* match the motivating case
itself — the real pair's own phonetic codes sit more than a trivial
edit apart, so strict equality misses exactly the case this whole
approach was meant to catch — and a handful of real near-homophones
survive exact matching regardless (`where`/`wire` both → `WR`;
`count`/`cant` both → `KNT`, and `kind` lands on that same code too).
At scale across all 11 books: exact-code matches, 2,639 — smaller than
round 2, but a large sample showed the identical failure pattern
(`were`/`where`/`wore`/`wary` all collapsing onto `wire`'s code;
`kind`/`count` onto `cant`; `take` onto `teak`, both real `TK`).
Loosening the phonetic-code match enough to catch the motivating case
reopens `want`/`wart`-shaped false positives and produces 60,022
candidates — worse than spelling distance, not better.

**Conclusion, disclosed rather than papered over: this is not a
threshold-tuning problem.** Three different techniques (raw confidence,
spelling distance, phonetic distance — both strict and loose) all fail
the same way, because short profanity words are inherently close, in
spelling and in any lossy phonetic encoding, to enormous swaths of
ordinary English. No amount of re-tuning the cutoff fixes that; the
false-positive volume is structural, not a parameter choice. Options 3
and 4, as designed, are rejected on this evidence — not deferred to
"someday," rejected.

**What survives this finding, and what doesn't:**

- **Option 4's UI mechanics** (the tab pattern, the exclusion-from-Hits
  rule, the single-mode preview reuse, the explainer-label convention,
  the `word_variation_dialog.py` add-path decision) were all still sound
  *engineering* — none of it was wrong. What's wrong was the premise
  that confidence alone identifies which words are worth showing there.
  The mockup (`docs/design/low-confidence-review-wireframe.html`) was
  deleted on 2026-09-19, Contributor decision, rather than kept as a
  record — the whole feature was rejected outright, not deferred, and
  the sound UI mechanics above were still reusable by description
  (this ADR) without needing the file itself to survive; the Full
  Transcript mockup's own equivalents (preview reuse, add-path,
  explainer-label pattern) were built fresh rather than copied from it.
- **Option 3**, as "compare against the catalog with some
  similarity metric," is rejected in the general form tested. A future,
  much narrower technique might still be worth trying (real ASR
  confusion-pair data instead of a generic algorithm, for instance), but
  that would need its own real-data validation before being trusted —
  it does not get the benefit of the doubt this ADR originally gave it.
- **Option 2** is architecturally untouched by this finding — it
  checks specific, narrow relationships (adjacent-token joins, suffix
  stripping), not "any word vaguely similar to any catalog word," so
  none of the false-positive-flood evidence above applies to it. (Since
  separately tested in its own right — see "Real-data validation,
  part 2" below.)
- **Option 1** is unaffected and, on this evidence, is now the *only*
  one of the five options with a real claim to reliably reaching
  misrecognition and dropout — because it doesn't depend on any
  automated technique correctly guessing which words to flag.

## Real-data validation, part 2 (2026-09-18): Option 2 tested — safe, modest

Option 2's specific, named gap (`variation_scan.py`'s own docstring:
"Does not handle consonant doubling (e.g. `run` -> `running`) — a
known, deliberate scope limit, not an oversight") was tested the same
way, reusing the real `find_word_variations()` function directly rather
than reimplementing it, across the same 11 real books.

**Method.** For every catalog word whose spelling doubles its final
consonant before `-ing`/`-ed` in standard English (`shit`, `slut`,
`twat`, and a handful of others — the real single-word entries this
actually applies to), checked whether any real transcript word matches
`base + doubled-final-consonant + suffix` (e.g. `shit` -> `shitt` +
`ing` = `shitting`), excluding anything the real, unmodified function
already catches or that a real Matcher hit already covers.

**Result: one new candidate across all 11 books (~2.2 million words
total)** — and it wasn't a genuine catch. It was `"assessing"` matching
the catalog word `"asses"`, the same false-positive class ADR-0042
already disclosed and marked out-of-scope (`"assess"` colliding with
`"asses"`), just surfaced again by a broader net. Zero genuine new
recall.

**Why this is a different outcome from Options 3/4, not just a smaller
version of the same failure:** the doubling check is structurally
narrow — a candidate must literally *start with* the exact catalog
word's letters — so it cannot flood the way a blind similarity scan
does, and it didn't (1 candidate, not tens of thousands). It's genuinely
safe. It just isn't valuable on this evidence: none of these 11 real
books ever had someone actually say `"shitting"`, `"slutting"`,
`"twatting"`, or similar doubling-eligible forms of the catalog's
vocabulary. A real implementation would also need to restrict the check
to genuine bare root-form entries first — my test naively applied it to
every single-word entry, including plurals (`assholes`, `bitches`,
`penis`) and already-inflected forms (`fucked`, `tosser`, `damned`)
where a further stem+suffix check makes no grammatical sense, which is
exactly how the one false positive above got generated.

**Verdict:** worth building for completeness and because it costs
almost nothing in false-positive risk, but disclosed here rather than
oversold — it should not be expected to meaningfully improve real
missed-word recall the way the ADR-0042 split-token fix did (10 real
recovered occurrences in one book). It's a safe, low-value addition,
not a high-leverage one.

## Recommendation

**Revised.** Build **Option 1 first** — the real load-bearing part of
this plan, and the only option with a real claim to reliably reaching
misrecognition and dropout. **Option 2** is worth building too, cheaply,
but expect it to be a completeness item, not a meaningful recall win —
its own real-data test found one candidate across 11 books, and it
wasn't genuine. **Options 3 and 4 are not part of the current plan** —
rejected by real-data testing, not merely deprioritized. **Option 5**
remains an open, untested question (ADR-0049's existing finding is
discouraging but not conclusive for this specific use).

Rationale: the original 4 → 1 → 2 sequence assumed 4 would cheaply
narrow Option 1's scope. It doesn't — it doesn't work at all — so
there's no cheap narrowing available, and Option 1 has to stand on its
own. That's a real cost (a full-transcript view is the most novel, most
UI-heavy option of the five, and now has no confidence-based shortcut
to lean on for keeping the review scope manageable), but it's the
option the real evidence actually supports.

## Performance fixes to the built Transcript tab (2026-09-18)

The Contributor reported the real, built Transcript tab (Option 1)
loading slowly on first open, on every chapter switch, and again on
toggling "Highlight uncertain words" — sometimes 10-20 seconds — and
asked whether transcript loading could be made per-chapter rather than
whole-book.

It already was: `ReviewStep._load_chapter()` only ever passes the
current segment's own `segment.words` to `TranscriptView.load_words()`,
never the whole transcript. The real cost was inside that per-chapter
load itself — `TranscriptView.load_words()` called `cursor.insertText()`
twice per word (the text, then a space), and
`_apply_low_confidence_formatting()` looped over *every word in the
chapter* with three cursor operations each and no
`beginEditBlock()`/`endEditBlock()` batching at all. A chapter can run
to several thousand words for a long book; both costs applied on *every*
chapter load, since `_load_chapter()` unconditionally called
`set_low_confidence_hint()` right after `load_words()` regardless of
whether the checkbox was even checked.

Fixed in `transcript_view.py`:

- **`load_words()`** now precomputes each word's char offset in a pure
  Python pass, loads the whole chapter's text in one `setPlainText()`
  call, and formats only the actual hit spans afterward — typically a
  handful of words, not every word in the chapter.
- **`_apply_low_confidence_formatting()`** now only ever touches a
  `_low_confidence_indices` list precomputed once by `load_words()`
  (words that qualify: not a hit, confidence below the threshold) —
  not every word — and wraps its cursor operations in one edit block.
  This is also what makes toggling the checkbox on an already-loaded
  chapter cheap: nothing re-scans word confidence values per toggle.
- **`mark_pending()`** picked up the same edit-block batching for
  consistency, though it was never the reported bottleneck (it only
  ever touches the handful of words just added to the catalog).

Fixed in `review_step.py`:

- **`_load_chapter()`** now skips calling `set_low_confidence_hint()`
  entirely when the checkbox is unchecked (its default) — a fresh
  `load_words()` call already leaves every word in its default/hit
  format, so re-applying a no-op "plain" format to every qualifying
  word on every chapter load when the feature is off was pure waste.
- **The chapter combo** (`_chapter_combo`) now calls
  `setMaxVisibleItems(15)` — a long audiobook's popup was one giant,
  unscrollable native-style menu; this caps it to a scrollable list.
  Confirmed this actually takes effect rather than being silently
  ignored by native combo-box rendering: `styles.py` already applies
  QSS to `QComboBox QAbstractItemView`, which forces Qt's own
  (non-native) popup view — the one `setMaxVisibleItems()` affects.

**Verification:** 9 new tests — 5 in `test_transcript_view.py`
(`_low_confidence_indices` excludes hits/high-confidence/no-confidence
words correctly; enabling the hint underlines only the qualifying word,
confirmed via `underlineStyle()` rather than the legacy
`fontUnderline()` property, which doesn't reliably mirror a style set
only through `setUnderlineStyle()` in this PySide6 build; disabling
reverts it; a pending word's dashed underline survives the hint being
turned on rather than being overwritten by the dotted one) and 4 in
`test_review_step.py` (the chapter popup's `maxVisibleItems()`; loading
a chapter with the checkbox off never calls `set_low_confidence_hint()`
at all; loading with it checked still does, with the right argument).
Full suite: 2169 passed, 2 skipped (up from 2162 before this pass);
`black`/`flake8`/`mypy` clean on every file touched.

## Performance-fix follow-up: two real issues from live testing (2026-09-18)

Overall load/toggle speed confirmed much better, but two more real
issues surfaced from screenshots of the actual app.

**1. The chapter popup still showed every chapter in one long list.**
`setMaxVisibleItems(15)`, the previous fix, had no effect at all —
confirmed live, not just suspected. Qt documents this property as
ignored for a non-editable combo box under a style that reports true
for `QStyle::SH_ComboBox_Popup`, which includes macOS's native style;
this app's combo boxes render with exactly that native popup despite
the QSS applied to `QComboBox QAbstractItemView` (that QSS restyles
colors, it doesn't change which popup mechanism macOS's style hint
selects). Fixed by capping the popup view's own height directly —
`self._chapter_combo.view().setMaximumHeight(360)` — a hard geometry
constraint on the real widget the popup lays out from, which holds
regardless of the ignored style hint.

**2. "Highlight uncertain words" was too subtle to scan for.** A
one-pixel dotted underline on running, single-spaced text doesn't
draw the eye the way the toggle's own label ("highlight") promises.
Replaced with a translucent background wash — the actual highlighter-
pen convention — using the same accent hue a real hit's strikethrough
already uses (`#c45a2d`) but as a soft fill (alpha 70/255) rather than
solid text color, so it reads as related ("worth a look") without
being confused with a confirmed match.

**Verification:** the two existing tests asserting the underline style
were rewritten to assert the background brush instead
(`test_enabling_highlights_only_the_qualifying_word`,
`test_disabling_reverts_the_highlight`); the chapter-popup test gained
an assertion that the view's `maximumHeight()` is actually constrained
(not Qt's `QWIDGETSIZE_MAX` "unset" sentinel), not just that
`maxVisibleItems()` was called — the earlier version of this test
would have passed even with the fix from the first pass doing nothing,
exactly the class of gap that let this ship unnoticed the first time.
Full suite: 2169 passed, 2 skipped (unchanged count — existing tests
rewritten/strengthened, not added); `black`/`flake8`/`mypy` clean.

**3. Neither visual treatment was ever explained anywhere a Contributor
would see it.** A bold, struck-through word (a real catalog hit) and a
highlighted word (once "Highlight uncertain words" is checked) both
meant something specific, but nothing on screen said what — a tooltip
on the checkbox alone doesn't help someone who hasn't hovered it, and
this app already prefers a persistently visible caption over a tooltip
for exactly this reason (`ProfileEditorDialog`'s own per-field
descriptions, same precedent). Added a `statusLabel`-styled legend line
directly under the chapter/highlight controls, above the transcript
text itself: "Bold, struck-through words are already tagged as catalog
hits. When checked, 'Highlight uncertain words' also shades any other
word the transcript is less sure about, so it's easy to spot."

**Verification:** 1 new test (`test_legend_explains_both_visual_treatments`)
checking the legend's own text names both cues. Full suite: 2170
passed, 2 skipped (up from 2169); `black`/`flake8`/`mypy` clean.

## Performance-fix follow-up, round 2: the popup fix didn't actually work (2026-09-18)

Live testing again: capping the popup view's own `maximumHeight` (the
fix two sections up) did shrink the visible list, but left large blank
white space above and below it inside the native popup frame — a
worse result than before, not a fixed one. And the legend line landed
in the wrong place: under the chapter/highlight controls, when it
should sit with the other informational text at the top of the tab,
above the chapter selector it's partly explaining.

**Chapter popup, take three.** `view().setMaximumHeight()` constrains
the list widget itself, but on macOS the surrounding native popup
*frame* is sized independently (by the native style, from the
unconstrained content), so shrinking the view just left the frame's
own now-unfilled space blank rather than shrinking the frame to match.
Two native-popup-shaped fixes in a row both failed for the same root
reason: the native macOS combo-box style doesn't behave like an
ordinary scrollable list popup no matter which property on it gets
adjusted. Fixed by sidestepping the native popup entirely — forcing
`_chapter_combo` onto Qt's own Fusion style
(`QStyleFactory.create("Fusion")`), a plain, predictable list-view
popup that honors `maxVisibleItems()` the way every non-Mac style
already does. The created `QStyle` object is kept as an instance
attribute (`_chapter_combo_style`) rather than a throwaway local —
`QWidget.setStyle()` does not take ownership, so a Python-GC'd style
object while the widget still references it is a real crash risk, not
just a style hint.

**Legend placement.** Moved from after `nav_row` (the chapter/
highlight/jump controls) to right after the existing "Optional — the
scan already caught…" note and rescan banner, before `nav_row` — so
reading order matches visual/logical order: what this tab is, what
the colors mean, then the controls themselves.

**Verification:** the chapter-popup test was rewritten again — it now
checks that the combo's effective style *is* the Fusion style instance
that was created and stored, rather than checking `maximumHeight()`
(which the take-two fix also satisfied while visibly failing live, the
same class of weak-test gap called out in the previous round). One new
test, `test_legend_appears_above_the_chapter_selector`, walks the
tab's real `QVBoxLayout` to confirm the legend's own item index
precedes the row containing `_chapter_combo` — an actual layout-order
assertion, not just that both widgets exist somewhere in the tab.
Full suite: 2171 passed, 2 skipped (up from 2170); `black`/`flake8`/
`mypy` clean.

## Performance-fix follow-up, round 3: abandoned QComboBox's native popup entirely (2026-09-18)

Live testing a third time: forcing `_chapter_combo` onto Qt's own
Fusion style had no visible effect at all — the popup still rendered
every chapter in one long list, unchanged from the very first report.
Three attempts, three different failure modes, all targeting the same
native macOS combo-box popup: `setMaxVisibleItems()` ignored outright;
capping the popup view's height left blank native-frame padding
instead of shrinking the frame; and now, forcing a different style
onto the combo box's own `style()` apparently never reached the
popup's actual internal container (`QComboBoxPrivateContainer`) at
all — a unit test had confirmed the style *object* was assigned to
the combo box, but never verified the popup itself rendered
differently, which is exactly the gap that let this ship unnoticed a
third time.

Rather than try a fourth lever on the same native popup, replaced it
outright. `_chapter_combo` (`QComboBox`) is gone; `_chapter_button`
(`QPushButton`, labeled with the current chapter) opens
`_ChapterPickerDialog`, a small `QDialog` containing a plain
`QListWidget` of every chapter — the same widget shape
`catalog_window.py`'s `_ExportDialog` already uses for its category/
profile checklists, already proven this session to scroll and size
correctly with zero native-popup involvement. Double-clicking (or
selecting + "Go") picks a chapter and closes the dialog; Cancel leaves
the current chapter unchanged. `ReviewStep` tracks chapter state
itself now (`_chapter_labels: list[str]`, `_chapter_index: int`)
rather than delegating to a combo box's own `count()`/`currentIndex()`
— `_step_chapter()` (Prev/Next) and the picker both update the same
two attributes and call the same `_load_chapter()`.

**Why this is expected to actually hold, unlike the previous three
fixes:** none of the three failures were really about combo-box
*properties* — they were about the native macOS popup mechanism
itself resisting every lever tried against it. A `QListWidget` inside
an ordinary `QDialog` isn't a native popup at all; it's the exact
widget already verified working (sized, scrolled, styled) for the
Export dialog earlier in this same session, so there's direct, already-
observed proof this shape renders correctly in this app rather than a
fourth assumption about combo-box internals.

**Verification:** `_ChapterPickerDialog` tested directly (lists every
label; pre-selects the current chapter; double-click/`itemActivated`
selects and accepts; the Go button selects the current row; Cancel
rejects without setting a selection) plus `ReviewStep`'s own wiring
(clicking the button opens the right dialog type via `_run_dialog`;
an accepted selection updates `_chapter_index` and reloads; a
rejected one leaves it unchanged; no chapters is a no-op). The
legend-ordering test's reference to `_chapter_combo` was updated to
`_chapter_button` (its own layout-walking logic, from the previous
round, is otherwise untouched here). Full suite: 2180 passed, 2
skipped (up from 2171); `black`/`flake8`/`mypy` clean on the source
file. `mypy` on the test file itself carries pre-existing `union-attr`
warnings, including from that same untouched layout-walking code
(`QLayout.itemAt()`'s return isn't null-checked) — present before this
round's edits, not introduced by them.

## Low-confidence highlight noise: a length floor (2026-09-18)

Real-app testing surfaced the same finding this ADR's own real-data
validation already reached for Option 4, just in a new place: whisper's
per-word confidence isn't a clean "was this transcribed correctly"
signal. A real chapter screenshot showed "Highlight uncertain words"
flooded with short, ordinary function words — "juice", "box", "is",
"not", "do", "yes", "he", "their", "the" — drowning out any real
signal. Root cause: whisper's per-word confidence tracks how
*acoustically distinct* a word's pronunciation was, not whether it was
transcribed *correctly* — short, fast, unstressed words score low
regardless of correctness, independent of actual uncertainty.

Fixed with a length floor: a word shorter than
`_LOW_CONFIDENCE_MIN_WORD_LENGTH` (4 characters) never qualifies for
the highlight, no matter how low its confidence. Not a new, blind
number — this reuses the exact len≥4 "real fuzzy target" floor this
ADR's own earlier real-data validation pass (Option 3) already
established as a working way to separate real content words from
short function words in this same codebase. Applied in `load_words()`,
alongside the existing hit/confidence-present checks that already
compute `_low_confidence_indices` once per chapter load — no new pass
over the words, same cost as before.

**What this does not fix:** a length floor removes the single biggest,
most systematic source of noise (short function words), but it's a
blunt instrument, not a validated signal — a long word can still score
low for the same acoustic-brevity reasons if spoken quickly, and a
genuinely mistranscribed short word (rare, but possible) is now never
flagged at all. This toggle remains what ADR-0053 always called it: an
explicitly unvalidated skim aid, improved by removing its worst,
most-reported failure mode, not proven correct on real data the way
Options 3/4 were tested and rejected.

**Verification:** the existing "qualifying indices" test's fixture
words were widened from single letters to real length≥4 words (the
new filter would otherwise exclude them, breaking the test for an
unrelated reason); one new test confirms a short word never qualifies
even at the lowest possible confidence, while a length≥4 word at the
same confidence does. Full suite: 2181 passed, 2 skipped (up from
2180); `black`/`flake8`/`mypy` clean.

## Marked "Highlight uncertain words" as experimental, not removed (2026-09-18)

Real-app testing with the length floor in place: the highlight was
better, but the Contributor reported it was still mostly flagging
ordinary, correctly-transcribed words — not adding much real value.
This is the same wall Option 4's own real-data validation already hit,
reached a second, independent way: whisper's per-word confidence
doesn't reliably separate real errors from correct words in this data,
whatever threshold or word-length floor sits on top of it. Two
directions were on the table — drop the toggle outright (matching
Options 3/4's own fate), or swap the underlying signal entirely (e.g.
an out-of-vocabulary/dictionary check instead of acoustic confidence,
a genuinely different, untested feature). The Contributor chose
neither: keep it, but make its unreliability visible rather than
implied.

Labeled "(experimental)" directly in the checkbox's own text, not only
in the legend paragraph above it — a Contributor scanning the controls
rather than reading the paragraph still needs to see the caveat right
where they'd act on it. Added a tooltip spelling out *why* ("based on
the transcript engine's own per-word confidence, which doesn't
reliably separate real errors from ordinary words. Expect false
positives.") and updated the legend text to say the same thing in
context. No behavior changed — only how honestly the feature
represents its own reliability.

**Verification:** 3 new tests (the checkbox's own label contains
"experimental"; its tooltip names both "experimental" and
"confidence"; the legend paragraph also says "experimental"). Full
suite: 2184 passed, 2 skipped (up from 2181); `black`/`flake8`/`mypy`
clean.

## Open questions for Contributor decision

1. Whether to build Option 1 (full-transcript review) as the primary
   MVP scope for this ADR now that Options 3/4 are rejected rather than
   deferred — the revised recommendation is a suggestion, not a
   commitment.
2. ~~Option 1: how to keep a full-book transcript review usable in
   practice...~~ **Mostly resolved via mockup (2026-09-19):** see
   `docs/design/full-transcript-review-wireframe.html` and "Design
   settled via mockup" under Option 1 above — chapter pagination,
   jump-to-next-hit, word-span selection, and the explicit
   catalog-only/re-scan-to-apply model. Still open: search within a
   chapter wasn't designed (deprioritized — the whole point of this tab
   is catching words you don't already know to search for), and the
   "Highlight uncertain words" toggle is included but explicitly
   unvalidated.
3. ~~Option 3: which phonetic algorithm...~~ **Resolved by real-data
   testing (2026-09-17): rejected in the general form tested, see
   "Real-data validation" above.**
4. ~~Option 4: what confidence threshold defines "low"...~~ **Resolved
   by real-data testing (2026-09-17): rejected outright — no threshold
   value produces a usable signal, see "Real-data validation" above.**
5. Option 5: whether to spend a spike measuring real recovery on real
   flagged spans before any build decision, given ADR-0049's existing
   negative-ish finding. Unaffected by this ADR's Option 3/4 validation.
6. ~~What becomes of `docs/design/low-confidence-review-wireframe.html`~~
   **Resolved (2026-09-19):** deleted, both the local file and the
   published artifact — the Contributor's call, given the feature it
   demonstrated was rejected outright rather than deferred.
