"""The multilayer engine -- multi-layer P&R (under optimization).

Two PnR engines live in this codebase, fully decoupled:

  * the frozen single-stack engine (backends/flexdr) : klink/routing/backends/flexdr/
                           -- greedy seed, 3 shared layers, BYTE-PARITY FROZEN,
                           fastest, small-scale only. DO NOT MODIFY.
  * the multilayer engine : THIS package -- FlexTA track-assigned seed, dedicated
                           signal layers above the device terminals, the path
                           toward large scale. Optimize freely HERE.

(Process profiles are process data: an example-owned multilayer ProcessProfile
(see your example/PDK file) and the algorithms here read whatever profile is
passed.)

This engine is a COMPLETE COPY of the routing engine (pnr_flexdr + pnr_flexta) so
its optimization can never touch the frozen single-stack engine. The two share
only the frozen FOUNDATION (capacity_grid datastructure, pathfinder helpers,
klink_boxmaze_rs kernel).
"""
from klink.routing.backends.pnr_multilayer.pnr_flexdr import (  # noqa: F401
    route_flexdr, flexgc_lite, flexpa_access_nets,
)
from klink.routing.backends.pnr_multilayer.pnr_flexta import flexta_seed  # noqa: F401
