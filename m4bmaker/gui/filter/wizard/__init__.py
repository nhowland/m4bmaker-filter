"""The filter wizard (PRD §7.2, ADR-0010): guided Source -> Transcript ->
Transcribe -> Profile -> Scan -> Review -> Render -> Complete flow.

A nested subpackage of ``gui/filter/`` rather than flat files alongside
``catalog_window.py``/``model_manager_window.py`` — the wizard is a
multi-screen feature (a shell plus one widget per step) in a way neither
of those single-window screens are, so it gets its own additive-only
boundary one level deeper, consistent with ADR-0004's module-layout
reasoning for ``gui/filter/`` itself.
"""

from __future__ import annotations
