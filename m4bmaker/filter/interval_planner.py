"""Interval Planner: hits -> merged, padded RenderPlan (PRD §8.3).

Implements the six numbered steps in PRD §8.3 exactly:

1. Start with each hit's ``[start_ms, end_ms)``.
2. Apply lead/tail padding.
3. Clamp to ``[0, source_duration_ms]``.
4. Sort intervals by start/end.
5. Merge overlapping or near-adjacent (within ``merge_adjacency_ms``)
   intervals.
6. Retain a many-to-one mapping from each merged interval to raw hit IDs.

**Explicitly out of scope here:** the fade-shape/short-interval-scaling
*behavior* described in PRD §8.4 ("Scale or resolve fade behavior for
intervals shorter than combined fade duration without extending the
interval or media duration") is a Renderer (G4) concern — this module only
carries the target ``fade_in_ms``/``fade_out_ms`` values forward on each
:class:`~m4bmaker.filter.models.RenderInterval`. Deciding *how* to shrink a
fade that doesn't fit is PCM-domain rendering logic, not interval planning,
and must not be silently implemented here as if it were solved.
"""

from __future__ import annotations

from .models import AttenuationSettings, RenderInterval, RenderPlan, ScanHit


def build_render_plan(
    included_hits: list[ScanHit],
    source_duration_ms: int,
    attenuation: AttenuationSettings,
) -> RenderPlan:
    """Build a :class:`RenderPlan` from *included_hits*.

    *included_hits* must already be filtered to the hits the User has
    approved for attenuation — excluding a hit is the caller's job (PRD
    §9.4: "Excluding a hit removes it from the render plan but preserves
    raw scan history"), not this function's; a raw, unfiltered hit list
    passed here would silently attenuate excluded words.
    """
    raw: list[tuple[int, int, str]] = []
    for hit in included_hits:
        start = max(0, hit.start_ms - attenuation.lead_padding_ms)
        end = hit.end_ms + attenuation.tail_padding_ms
        if source_duration_ms > 0:
            start = min(start, source_duration_ms)
            end = min(end, source_duration_ms)
        if end <= start:
            # Only possible if the hit lies at/beyond source_duration_ms,
            # which should never happen for a hit derived from a transcript
            # whose words are within [0, duration] — skip defensively
            # rather than constructing an invalid RenderInterval.
            continue
        raw.append((start, end, hit.id))

    raw.sort(key=lambda t: (t[0], t[1]))

    merged: list[RenderInterval] = []
    for start, end, hit_id in raw:
        if merged and start <= merged[-1].end_ms + attenuation.merge_adjacency_ms:
            prev = merged[-1]
            merged[-1] = RenderInterval(
                start_ms=prev.start_ms,
                end_ms=max(prev.end_ms, end),
                fade_in_ms=attenuation.fade_in_ms,
                fade_out_ms=attenuation.fade_out_ms,
                hit_ids=prev.hit_ids + (hit_id,),
            )
        else:
            merged.append(
                RenderInterval(
                    start_ms=start,
                    end_ms=end,
                    fade_in_ms=attenuation.fade_in_ms,
                    fade_out_ms=attenuation.fade_out_ms,
                    hit_ids=(hit_id,),
                )
            )

    return RenderPlan(
        intervals=tuple(merged),
        attenuation=attenuation,
        source_duration_ms=source_duration_ms,
    )
