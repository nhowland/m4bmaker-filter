# ADR-0038: Expanded default profanity seed list

**Status:** Implemented and verified.

## Context

The default "Profanity" category seeded on first run (ADR-0022) held
only 10 words — a reasonable minimal starting point at the time, but
the User asked for a more thorough common-English profanity list and
specifically wanted to use an existing, established word list rather
than one hand-invented from scratch.

The real, well-known candidate is LDNOOBW's open-source "bad words"
list (MIT licensed) — but fetching and reading it directly showed it's
built for blocking adult websites, not filtering audiobook narration:
of its ~400 entries, most are explicit sexual/fetish jargon, and a real
number are ethnic/racial/disability slurs and extremist terms (real
slurs, "neonazi", "swastika"). Importing it wholesale would directly
contradict this project's own already-stated design principle
(`catalog_seed.py`'s own docstring, ADR-0022): the shipped default is
deliberately profanity-only, with slur selection left entirely to each
User, since picking which slurs belong on a shipped list is a
subjective editorial call the app shouldn't make unilaterally.

## Decision

Curated LDNOOBW down to just its ordinary, everyday-spoken-profanity
entries (plus a small number of obvious common words it happened to
omit, like "damn"/"hell"/"goddamn"), presented the resulting list to
the User for review before touching anything, and — once confirmed —
expanded `PROFANITY_WORDS` from 10 to 29 words. Everything explicitly
excluded: sexual-act/fetish jargon, pornography-site jargon, drug/
medication names, extremist symbols, and every slur — same boundary
the original 10-word list already drew, just applied to a larger,
externally-sourced candidate pool instead of invented from scratch.

**Scope: this only changes the default seed for a brand-new/empty
catalog** (`catalog_store.load_catalog()`'s "file doesn't exist yet"
branch, the only real caller of `seed_default_catalog()`) — it does
not touch any User's existing, already-seeded catalog. The User
confirmed this scope explicitly (code-level default only, not applied
to their current real catalog) — a deliberate, narrow choice given the
same day's earlier incident where a script wrote to the real catalog
file by mistake; nothing here touches that file at all.

## Verification

**Unit tests** (`test_catalog_seed.py`): the two pre-existing tests
(one category created, one entry per word) already assert against
`len(PROFANITY_WORDS)` rather than a hardcoded count, so they needed no
change and passing them confirms the larger list still seeds correctly.
Two new tests guard the list itself: no duplicate words (`create_entry`
allows duplicates rather than rejecting them, so the list itself has to
avoid that on its own) and no blank/whitespace-only entries. 2 new
tests; full suite 1850 passed, 2 skipped; `black`/`flake8`/`mypy` clean.
