"""TrackGridWorkerAdapter -- the GEOMETRY surface (group A) of the FlexDR worker,
backed by a TrackGrid (Stage T5, increment A).

The detailed-route worker (`pnr_flexdr.route_flexdr`) reads a small `CapacityGrid`
geometry surface: `nx`, `ny`, `cx(ix)`, `cy(iy)`, `cell_of(x,y)`, `in_bounds(ix,iy)`,
`layers`, `pitch_nm`. On the UNIFORM `CapacityGrid` these are arithmetic
(`cx = x0 + ix*pitch`). On the NON-UNIFORM `TrackGrid` they must read the real sorted
coordinate arrays. This adapter provides exactly that surface and nothing else --
**no legality (group B), no maze (group C), no DRC**. See the mapping table in
this module's docstring, §1.

Faithful, binding (constraint): `cx(ix) == xCoords[ix]`, `cy(iy) == yCoords[iy]` --
NEVER `x0 + ix*pitch` (the grid is non-uniform; an AP/pin coord makes the spacing
irregular). `cell_of` inverts via binary search, so `cell_of(cx(ix), cy(iy)) == (ix, iy)`.
"""

from __future__ import annotations

from bisect import bisect_left

from klink.routing.backends.pnr_multilayer.dr.legality import BLOCK as _BLOCK
from klink.routing.backends.pnr_multilayer.grid.track_grid import TrackGrid


def _min_pitch(coords) -> int:
    """Representative track pitch = the smallest positive gap between consecutive
    coords. Used ONLY for halo sizing (cells-per-spacing); coordinates themselves
    always come from `cx`/`cy`, never from this. 0 if fewer than two coords."""
    best = 0
    for a, b in zip(coords, coords[1:]):
        d = b - a
        if d > 0 and (best == 0 or d < best):
            best = d
    return best


class TrackGridWorkerAdapter:
    """CapacityGrid geometry (A) + legality (B) surface over a TrackGrid.

    Geometry is pure (cx/cy/cell_of/in_bounds/nx/ny/layers/pitch). Legality
    (``wire_ok``/``via_ok``) reads the TrackGrid Node fixed-shape/blocked fields written
    by ``dr.legality.load_legality`` plus the owner index. Still NO maze (group C) and NO
    DRC (spacing/PRL/min-area) -- those are later increments.
    """

    def __init__(self, grid: TrackGrid, *, pad_owner=None):
        self._g = grid
        self.layers = grid.layers          # tuple of "L/D" per z
        # owner index {(zi, xi, yi): owner} from load_legality (group B); empty until set.
        self._pad_owner = dict(pad_owner) if pad_owner else {}

    # --- dimensions ---------------------------------------------------------
    @property
    def nx(self) -> int:
        return len(self._g.xCoords)

    @property
    def ny(self) -> int:
        return len(self._g.yCoords)

    @property
    def nz(self) -> int:
        return len(self._g.zCoords)

    # --- coordinates (NON-UNIFORM, faithful: read the sorted arrays) --------
    def cx(self, ix: int) -> int:
        return self._g.xCoords[ix]

    def cy(self, iy: int) -> int:
        return self._g.yCoords[iy]

    # --- index lookup (inverse of cx/cy via binary search) ------------------
    def cell_of(self, x_nm: int, y_nm: int):
        return (bisect_left(self._g.xCoords, x_nm),
                bisect_left(self._g.yCoords, y_nm))

    def in_bounds(self, ix: int, iy: int) -> bool:
        return 0 <= ix < self.nx and 0 <= iy < self.ny

    # --- representative pitch (halo sizing ONLY; never coordinate math) -----
    @property
    def pitch_nm(self) -> int:
        # min positive gap on either axis -- the min-pitch of the track grid.
        px = _min_pitch(self._g.xCoords)
        py = _min_pitch(self._g.yCoords)
        cand = [p for p in (px, py) if p > 0]
        return min(cand) if cand else 0

    # --- legality (group B): reads Node fixed-shape/blocked fields ----------
    def set_pad_owner(self, pad_owner) -> None:
        self._pad_owner = dict(pad_owner) if pad_owner else {}

    def wire_ok(self, xi: int, yi: int, zi: int, net: str) -> bool:
        """May a wire of ``net`` occupy this node? False on a channel keep-out (all-net)
        or a FOREIGN net's fixed pad; True on the pad's own owner. Faithful to the
        worker's ``_wire_ok`` (cell-based legality), read from Node fields."""
        idx = self._g.get_idx(xi, yi, zi)
        nd = self._g.nodes
        if nd["fsc_planar_h"][idx] >= _BLOCK or nd["fsc_planar_v"][idx] >= _BLOCK:
            owner = self._pad_owner.get((zi, xi, yi))
            return owner is not None and owner == net   # None => all-net channel block
        return True

    def via_ok(self, xi: int, yi: int, zi: int) -> bool:
        """May a via land here? False on a device-body keep-out (``fsc_via`` saturated)."""
        return self._g.nodes["fsc_via"][self._g.get_idx(xi, yi, zi)] < _BLOCK

    def planar_edge_blocked(self, xi: int, yi: int, zi: int, d: str) -> bool:
        """Is the planar edge leaving this node in dir ``d`` (E|N) hard-blocked? The
        maze rejects a blocked edge (channel keep-out)."""
        return bool(self._g.nodes["blocked_" + d][self._g.get_idx(xi, yi, zi)])
