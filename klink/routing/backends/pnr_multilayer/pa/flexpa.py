"""FlexPA access-point generation (Stage T2, scoped).

Faithful to ``OpenROAD src/drt/src/pa/FlexPA_acc_point.cpp``
(Tao-of-PAO cost structure). See the mapping table in this module's docstring
first -- it fences the scope (ported vs deferred) and resolves V1 (coord-injection
order).

PORTED: on-track AP coord generation (``genAPOnTrack``), the center fallback
(``genAPCentered``, when <3 on-grid), the cross-product AP creation +
dedup (``createMultipleAccessPoints``), basic access-direction assignment
(pref planar + via U/D), and the ``frAccessPointEnum`` cost tiers.

DEFERRED (named, not silent): via-enclosed ``EncOpt`` / ``NearbyGrid`` / ``HalfGrid``
coords, ALL DRC violation filtering (``isPlanarViolationFree`` / ``isViaViolationFree``
/ ``getViasFromMetalWidthMap`` -> T5 = real DRC), LEF58 right-way-on-grid + unidirectional
wrongway nuances, and std-cell via-in-pin gating (no std cells in our device flow).

The load-bearing property (V1/C2): OnGrid APs land on the track grid, so an AP's
PERPENDICULAR coordinate is already a track line of the crossing layer -- which is why
``track_grid.build`` injects only the AP's preferred-axis coord. Pure, offline, generic
(layer dirs/tracks come from ``ProcessProfile`` + the caller's track sets, as DATA).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

# frAccessPointEnum (frBaseTypes.h:250) -- lower value = preferred.
ONGRID = 0
HALFGRID = 1
CENTER = 2
ENCOPT = 3
NEARBYGRID = 4

Box = Tuple[int, int, int, int]   # (x1, y1, x2, y2) in nm


@dataclass(frozen=True)
class AccessPoint:
    """One access point (OpenROAD ``frAccessPoint``). ``access`` is the set of
    directions the point may be entered/left from (subset of E/N/W/S/U/D);
    ``lower_type``/``upper_type`` are the ``frAccessPointEnum`` cost tiers on the
    pin layer (pref axis) and the via-up axis respectively."""

    x: int
    y: int
    layer: str
    layer_index: int
    access: frozenset
    allow_via: bool
    lower_type: int
    upper_type: int

    def has_access(self, direction: str) -> bool:
        return direction in self.access

    def as_coord(self) -> Tuple[str, int, int]:
        """(layer, x, y) for ``track_grid.build(access_points=...)``."""
        return (self.layer, self.x, self.y)


def _axis_coords(
    tracks: Sequence[int], lo: int, hi: int, mfg_grid_nm: int
) -> Dict[int, int]:
    """Candidate coords on one axis: on-track points in [lo, hi] at tier ``ONGRID``
    (``genAPOnTrack``), plus the manufacturing-grid-snapped midpoint at tier
    ``CENTER`` when fewer than 3 on-grid candidates exist (``genAPCentered``)."""
    coords: Dict[int, int] = {}
    for t in tracks:
        if lo <= t <= hi:
            coords[t] = ONGRID
    on_grid = sum(1 for v in coords.values() if v == ONGRID)
    if on_grid < 3 and hi >= lo:
        mid = ((lo + hi) // 2 // mfg_grid_nm) * mfg_grid_nm
        coords[mid] = min(coords.get(mid, CENTER), CENTER)
    return coords


def gen_access_points(
    rect_nm: Box,
    layer: str,
    profile,
    *,
    x_tracks: Sequence[int],
    y_tracks: Sequence[int],
    layer_index: int | None = None,
    mfg_grid_nm: int = 5,
    offset_nm: int = 0,
    allow_via: bool = True,
    allow_planar: bool = True,
    use_nonpref: bool = False,
) -> List[AccessPoint]:
    """Access points for a pin-shape rectangle ``rect_nm`` on ``layer``.

    ``x_tracks`` = vertical-layer track x positions; ``y_tracks`` = horizontal-layer
    track y positions (the on-track lattices the grid uses). The pin layer's preferred
    direction decides which axis is the on-track ("lower") axis vs the via-up ("upper")
    axis, exactly like ``createMultipleAccessPoints`` (acc_point:338-339).
    """
    x1, y1, x2, y2 = rect_nm
    direction = profile.layer_direction(layer)
    is_horz = direction == "H"
    if layer_index is None:
        layer_index = profile.routing_layers.index(layer)

    x_coords = _axis_coords(sorted(x_tracks), x1 + offset_nm, x2 - offset_nm, mfg_grid_nm)
    y_coords = _axis_coords(sorted(y_tracks), y1 + offset_nm, y2 - offset_nm, mfg_grid_nm)

    # access directions (createSingleAccessPoint): pref planar always, wrongway only
    # with USENONPREFTRACKS; via U/D when allowed.
    access: set = set()
    if allow_planar:
        pref = {"E", "W"} if is_horz else {"N", "S"}
        nonpref = {"N", "S"} if is_horz else {"E", "W"}
        access |= pref
        if use_nonpref:
            access |= nonpref
    if allow_via:
        access |= {"U", "D"}
    access_fs = frozenset(access)

    aps: List[AccessPoint] = []
    seen: set = set()
    # cross product x_coords x y_coords, deduped by (point) -- the union over all
    # (lower_type, upper_type) combos createMultipleAccessPoints would emit.
    for x, cx in sorted(x_coords.items()):
        for y, cy in sorted(y_coords.items()):
            if (x, y) in seen:
                continue
            seen.add((x, y))
            lower = cy if is_horz else cx   # on-track (pin-layer) axis cost
            upper = cx if is_horz else cy   # via-up axis cost
            aps.append(
                AccessPoint(
                    x=int(x),
                    y=int(y),
                    layer=layer,
                    layer_index=int(layer_index),
                    access=access_fs,
                    allow_via=allow_via,
                    lower_type=lower,
                    upper_type=upper,
                )
            )
    return aps
