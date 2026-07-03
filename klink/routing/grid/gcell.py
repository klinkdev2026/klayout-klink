"""Coarse gcell capacity from the fine grid + profile.

Self-contained helper for grt (FastRoute) global routing: it derives integer
H/V edge capacities for ``global_router.route_global``. Extracted from the
legacy ``detailed_router`` (the old grt->TA->DR stack: track_router /
route_detailed / pdn) so the active FlexDR flow (``flexdr.route_flexdr``) and
``hier_router`` depend on NONE of that legacy code. Imports only the grid and
profile -- no router.
"""
from __future__ import annotations

from math import floor
from typing import Iterable, Optional, Sequence, Tuple

from klink.routing.grid.capacity_grid import CapacityGrid
from klink.routing.grid.process_profile import ProcessProfile


def gcell_capacity(
    fine_grid: CapacityGrid,
    GC: int,
    profile: ProcessProfile,
) -> Tuple[list[list[int]], list[list[int]]]:
    """Derive coarse H/V edge capacities from the fine grid and profile.

    Capacity is track count per gcell span times routable layer count,
    minus blocked fine cells along the coarse boundary.  It is deliberately
    expressed as integer edge capacities for ``global_router.route_global``.
    """

    if GC <= 0:
        raise ValueError("GC must be positive")
    pitch_um = fine_grid.pitch_nm / 1000.0
    track_um = profile.wire_width_um + profile.wire_clear_um
    h_layers = _layer_indices_for_direction(fine_grid, profile, "H")
    v_layers = _layer_indices_for_direction(fine_grid, profile, "V")
    span_um = GC * pitch_um
    tracks = max(1, floor(span_um / track_um))
    base_h = tracks * len(h_layers)
    base_v = tracks * len(v_layers)
    gx_n = max(1, (fine_grid.nx + GC - 1) // GC)
    gy_n = max(1, (fine_grid.ny + GC - 1) // GC)
    cap_h = [[base_h for _ in range(max(0, gx_n - 1))] for _ in range(gy_n)]
    cap_v = [[base_v for _ in range(gx_n)] for _ in range(max(0, gy_n - 1))]

    for gy in range(gy_n):
        y0, y1 = gy * GC, min(fine_grid.ny, (gy + 1) * GC)
        for gx in range(max(0, gx_n - 1)):
            x = min(fine_grid.nx - 1, (gx + 1) * GC)
            cap_h[gy][gx] = max(0, base_h - _blocked_tracks(fine_grid, range(y0, y1), x=x, layers=h_layers))
    for gy in range(max(0, gy_n - 1)):
        y = min(fine_grid.ny - 1, (gy + 1) * GC)
        for gx in range(gx_n):
            x0, x1 = gx * GC, min(fine_grid.nx, (gx + 1) * GC)
            cap_v[gy][gx] = max(0, base_v - _blocked_tracks(fine_grid, range(x0, x1), y=y, layers=v_layers))
    return cap_h, cap_v


def _blocked_tracks(
    fine_grid: CapacityGrid,
    span: Iterable[int],
    *,
    x: Optional[int] = None,
    y: Optional[int] = None,
    layers: Optional[Sequence[int]] = None,
) -> int:
    blocked = 0
    layer_indices = range(len(fine_grid.layers)) if layers is None else layers
    for layer_i in layer_indices:
        common = fine_grid.wire_blocked_all.get(layer_i, set())
        if x is not None:
            blocked += sum(1 for yy in span if (x, yy) in common)
        else:
            blocked += sum(1 for xx in span if (xx, y) in common)
    return blocked


def _layer_indices_for_direction(fine_grid: CapacityGrid, profile: ProcessProfile, direction: str) -> list[int]:
    wanted = direction.upper()
    return [idx for idx, layer in enumerate(fine_grid.layers) if profile.layer_direction(layer) == wanted]
