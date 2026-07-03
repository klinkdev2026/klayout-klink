"""Stage T4 of the multilayer engine -- FlexTA (track assignment) on the T3
layer-assigned guides.

Reuses the faithful FlexTA engine in `pnr_multilayer/pnr_flexta.py`, but feeds it
SINGLE-LAYER iroutes (T3 owns the layer now -- the `_assign_run_layers` hack is
superseded) on real `TrackGrid` tracks, and emits T5-ready assignments. Design +
source mapping are in this module's docstring set. No import from
`backends/flexdr/` (the frozen single-stack engine).
"""

from klink.routing.backends.pnr_multilayer.ta.track_assign import (
    TAResult,
    assign_tracks,
    build_iroutes,
)

__all__ = ["TAResult", "assign_tracks", "build_iroutes"]
