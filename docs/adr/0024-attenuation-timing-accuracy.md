# ADR-0024: Attenuation timing accuracy — real whisper.cpp word-timestamp error, not a renderer bug

**Status:** Implemented and verified.

## The report

The User rendered a real ~13.5-hour audiobook through the full wizard
pipeline and, listening back, found the silenced segments landing too
soon and lasting too short a duration relative to the actual spoken
words. Given this metric — silence lands on the actual chosen word,
reliably — is the one thing the whole feature exists to get right, this
investigation treated it as the highest-priority open question in the
fork so far.

## Method: a purpose-built, ground-truth measurement harness

Rather than trust a re-transcription of the filtered output as the
primary signal (noisy — confounded by whisper's own robustness to
partially-attenuated audio, and requires a full second STT pass over a
multi-hour file to even get a data point), this investigation built a
small suite of diagnostic scripts (`/Users/nate/Claude/test/
diagnostic_0{1..6}_*.py`, kept outside the package — throwaway
harnesses, not shipped code) around one core technique: macOS `say`
with the embedded `[[slnc N]]` command to synthesize short speech
samples where a target word is isolated by *engineered, real* digital
silence on both sides. Because that silence is genuine (not just
perceptually quiet), the true spoken onset/offset of each target word
can be found automatically and precisely by energy thresholding — no
manual per-word judgment call, and no dependency on whisper's own
output for ground truth.

This produced, in order:

1. **A single organic-sentence sample** (2 target words) — first
   real signal that a word's whisper-reported timestamp can differ
   substantially (~150-200ms) from where it's actually spoken.
2. **An 8-word isolated-word sample** — confirmed the bias is real but
   *inconsistent in direction* (sometimes early, sometimes late) and
   found ~29% of instances had a reported timestamp landing entirely
   inside silence, nowhere near the real word.
3. **A model comparison** (base.en / small.en / medium.en) on that same
   audio — small.en showed much tighter, one-directional timing error
   on the words it recognized, but recognized fewer words outright;
   medium.en was a lateral move, not an improvement, despite its 10x
   larger download.
4. **A 90-instance pass** (all 10 real seeded `PROFANITY_WORDS`, 9
   repeats each) — the first sample size large enough to trust
   percentiles rather than single data points.
5. **Word-splitting discovery**: `crap`, `piss`, `bitch`, `goddamn`
   came back with **zero hits at any padding value**, because
   whisper.cpp consistently tokenizes them as two separate words
   (`"C"+"rap"`, `"B"+"itch"`, `"P"+"iss"`, `"God"+"damn"`) — confirmed
   identical across both base.en and small.en, and confirmed against
   the User's own listening ("crap"/"bitch" were clearly one word in
   the synthesized audio, so this is whisper's behavior, not a TTS
   artifact; "piss" was inconclusive since the synthesized audio itself
   wasn't clear). This is a **recognition** problem, categorically
   different from a **timing** problem, and had an immediately
   available fix: the matcher already supports exact multi-word phrase
   entries (PRD §9.3) — adding `"c rap"`/`"b itch"`/`"god damn"`/
   `"p iss"` as catalog phrase entries, no code change, recovered 26 of
   30 previously-missed instances (67% → 96% recognition).
6. **Verification against the real render pipeline** — `generate_
   envelope_pcm`, `apply_gain_envelope`, and `encode_and_mux` were each
   individually reproduced by hand against the real audio and found to
   implement the documented math *exactly* (the gain envelope reaches
   the configured floor precisely where the interval planner says it
   should, `amultiply` correctly zeroes the padded region, and the
   final encode preserves that). This ruled out the renderer as the
   culprit early, which is what justified going all-in on measuring
   whisper's own timestamp accuracy instead.

**Two measurement-tooling bugs were found and fixed along the way, both
disclosed rather than silently absorbed into the reported numbers:** an
initial "search window" for finding a hit's true onset/offset was too
narrow, producing several biases that were actually just the window's
own edge (not real data) — fixed by adaptively widening the search
margin on retry. A second bug conflated "genuinely disconnected
timestamp" with "the winning candidate happened to touch the window
edge in a tie-break" — fixed by only trusting a real overlap match. A
third-order effect of the same root technique (natural speech runs of
short words merging into one measured span when the gap between them is
under the 75ms "real pause" threshold) inflated a handful of remaining
outliers; these were hand-verified against the raw waveform rather than
trusted, and the true worst-case magnitude is real but smaller than the
raw automated numbers suggested.

## Decision: raise `lead_padding_ms`/`tail_padding_ms` defaults, and widen the PRD-enforced range to allow it

**Old defaults (PRD §8.3's original MVP table): lead 60ms (0-250ms
range), tail 80ms (0-300ms range).**

**New defaults: lead 300ms (0-400ms range), tail 400ms (0-500ms
range).**

Derived from the 90-instance pass's stable percentiles (lead p75 ≈
74ms, tail p50 ≈ 196ms/p75 ≈ 351ms) plus every hand-verified real case
across the whole investigation (worst confirmed lead miss ~292ms, worst
confirmed tail miss ~203ms) — the chosen values sit comfortably above
every trustworthy data point with real margin, without chasing the
automated tool's own least-reliable tail-percentile numbers (shown
above to be partly measurement artifacts).

**This required widening the PRD's own enforced valid range, not just
the default value** — 300/400 exceeds the old 250/300 ceiling
`AttenuationSettings.__post_init__` enforced. Explicitly confirmed with
the User before doing this, since a PRD-encoded range is a product
decision, not an implementation detail a default-value tune-up should
silently override. `docs/PRD.md` §8.3's table, `AttenuationSettings`'s
`_check_range` calls, and the Profile editor's spin-box ranges
(`profile_editor_dialog.py`) were all updated together so the enforced
range and the UI's own input limits never disagree.

## What this does not change

No change to `renderer.py`'s gain-envelope/multiply/encode math, which
this investigation verified is already correct. No change to the
matcher's phrase-matching capability, which already supported the
word-splitting fix without modification — only new catalog *content*
(phrase entries) uses it differently than before. `merge_adjacency_ms`/
`fade_in_ms`/`fade_out_ms`/`gain_floor_db` are untouched.

**Explicitly not solved, and disclosed as a real ceiling:** a small
residual fraction of instances (roughly 2-4%, hand-verified) have
whisper.cpp word timestamps so far from the true word — or so entangled
with a merge-artifact-inducing burst of fast, closely-spaced speech —
that no single fixed padding value reasonably covers them without also
padding every other interval far more generously than most cases need.
Larger padding is a real, verified improvement, not a guarantee.

## Verification

**Full render + re-transcribe test against the real 90-instance
sample**, using the app's actual `AttenuationSettings()` bare default
(now 300/400) and the phrase-entry fix together: 86/90 target words
were matched and rendered against; re-transcribing the filtered output
found only **7 of 86 (8%) still recognizable as clean, unattenuated
words** — a 92% real-world success rate, up from the roughly 30%
theoretical ceiling the old 60ms/80ms defaults implied. The 7 survivors
(`damn` x3, `hell` x1, `fuck` x3) line up with the same words that
produced the investigation's hand-confirmed genuinely-unfixable cases,
not a new or different failure mode.

**Test suite:** fixing the new defaults surfaced two categories of
now-stale test expectations, both corrected: `test_interval_planner.py`
and `test_scan.py` had literal padded-interval boundary numbers baked
in against the old 60/80 defaults (updated to match, with one test's
hit positions redesigned so it still exercises a real "gap under the
merge threshold" scenario rather than accidentally testing plain
overlap once padding grew past the gap). `test_review_step.py`'s shared
fixture now passes its own explicit, small `AttenuationSettings` rather
than relying on the bare default, so its "3 distinct, non-merging
intervals" design intent stays stable independent of whatever the
app's production default is at any given time — a real, if narrow,
robustness improvement to that fixture, not just a numbers patch.

**One unrelated, genuine test-isolation bug was found and fixed as a
direct side effect of this change, not deferred:**
`test_wizard_window.py`'s `win` fixture never passed an explicit
`catalog_service`, so `WizardWindow.__init__`'s own `catalog_service or
load_catalog()` fallback silently read whatever real, persisted catalog
exists on the machine running the tests. A leftover real profile's own
persisted (old-default) attenuation settings got selected over a
freshly-created in-test profile via `ProfileStep`'s "preserve previous
selection" logic, only becoming visible once the in-code default
diverged from what was historically saved to disk. Fixed by passing an
explicit, empty `CatalogService()` — the same isolation pattern every
other wizard-step test file already used; this file was the one
outlier.

`black`/`flake8`/`mypy` clean on every file touched. `tests/filter/`
(392 passed, 2 skipped), `tests/gui/filter/` (287 passed), and this
project's real CI command, `pytest tests/ --ignore=tests/gui` (927
passed, 2 skipped), all clean.
