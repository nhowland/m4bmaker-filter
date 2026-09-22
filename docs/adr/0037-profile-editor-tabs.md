# ADR-0037: Profile Editor tabs, and tooltips on every attenuation field

**Status:** Implemented and verified.

## Context

`ProfileEditorDialog` stacked the category/word tree and the
Attenuation form in one column: the User's real-world profiles grow to
dozens of words across several categories (their own "My Default"
profile, rebuilt after the data-loss incident below, is a real
example), and the fixed-size Attenuation box below the tree permanently
ate into the tree's own vertical space even though attenuation is set
once and rarely revisited afterward. Discussed a few alternative
patterns (a collapsible/disclosure section, a side-by-side split, a
separate secondary dialog) before settling on tabs as the best fit for
this specific shape of problem — the User's own choice.

Separately, the six attenuation numbers (lead/tail padding, merge
adjacency, fade in/out, gain floor) had no explanation anywhere in the
UI beyond their bare labels — meaningful only to someone who already
knows this project's own terminology (padding absorbs whisper's
timestamp bias, merge adjacency avoids rapid mute/unmute chatter, gain
floor is how quiet "muted" actually gets). The User asked for a
user-friendly tooltip on each.

## Decision

**`QTabWidget` with "Words" and "Attenuation" tabs**, Name kept outside
both (it applies to the whole profile, not either tab). The tree gets
the full tab height to itself; Attenuation is a tab-click away instead
of a fixed box under the tree. No change to how either tab's own
content works — same tree/checkbox logic, same spin boxes, same
`_on_save()` — this is purely a layout change, achieved by moving each
existing section's widgets into an independent panel handed to the tab
widget, factored into `_build_words_tab()`/`_build_attenuation_tab()`.

**Persistently visible explainer text under each attenuation field**
(`_add_form_row()`), not a hover tooltip — the first version used
tooltips, but the User asked to switch to always-visible text once they
tried it: a tooltip requires already knowing to hover over the right
spot, and doesn't show up at all in a screenshot. Each field's
label/input row is followed by its own full-width `QFormLayout` row
holding a `statusLabel`-styled, word-wrapped description directly
beneath it. Plain-language, example-driven wording (e.g. lead padding:
"covers for whisper's timestamps sometimes starting a little early or
late") rather than restating the field name or citing internal terms
like "PRD §8.3"/"ADR-0023" a User has no reason to know.

## What changed

- `profile_editor_dialog.py`: `_build_ui()` now builds a `QTabWidget`
  instead of stacking a `QTreeWidget` and a `QGroupBox` in one
  `QVBoxLayout`; `_build_words_tab()`/`_build_attenuation_tab()` factor
  out each tab's content; `_add_form_row()` adds each field's own
  description as a full-width row directly beneath it (no tooltips set
  on the fields at all). Every existing attribute name (`_tree`,
  `_lead_spin`, etc.) is unchanged, so nothing else in the dialog's own
  logic needed to change.

## Verification

**Unit tests** (`test_profile_editor_dialog.py`): two tabs named
"Words"/"Attenuation" in that order; the tree lives under the Words
tab and every attenuation field lives under the Attenuation tab (found
via `findChildren` scoped to each tab's own widget, not just present
somewhere in the dialog); exactly one description label per field,
each non-empty, word-wrapped, and distinct from the others (guards
against a copy-paste mistake reusing one field's wording for another);
no tooltip left set on any field (confirms the switch, not an addition
on top). 9 new tests; all 13 pre-existing tests continue to pass
unmodified, since none of them depended on the dialog's layout
structure — only on `_tree`/the spin boxes by attribute, confirming
the redesign is a real layout-only change. Full suite 1848 passed, 2
skipped; `black`/`flake8`/`mypy` clean (2 pre-existing, unrelated
`no-untyped-def`/`union-attr` issues in this test file confirmed
unchanged against the pre-edit baseline via `git stash`, same
discipline as every other ADR this session).
