"""Stage T2 of the multilayer engine -- FlexPA (access points).

Faithful (scoped) port of OpenROAD ``drt/src/pa/FlexPA_acc_point.cpp``. Design +
source mapping + scope fence are in this module's docstring set. Generates
on-track access points (Tao-of-PAO cost tiers) for pin shapes; the grid injection
(pref-axis only + ap_locs) lives in ``grid/track_grid.build`` (V1 resolution).
Does NOT import from ``backends/flexdr/`` (the frozen single-stack engine).
"""

from klink.routing.backends.pnr_multilayer.pa.flexpa import (
    CENTER,
    ENCOPT,
    HALFGRID,
    NEARBYGRID,
    ONGRID,
    AccessPoint,
    gen_access_points,
)

__all__ = [
    "AccessPoint",
    "gen_access_points",
    "ONGRID",
    "HALFGRID",
    "CENTER",
    "ENCOPT",
    "NEARBYGRID",
]
