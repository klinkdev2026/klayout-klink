"""TrackGrid -- Stage T1 faithful node grid (OpenROAD ``FlexGridGraph`` port).

Faithful to ``OpenROAD src/drt/src/dr/FlexGridGraph.h``
(+ ``.cpp`` init) and ``db/obj/frTrackPattern.h``. Field-by-field mapping and the
design rationale are in this module's docstring set. Read that first.

What this is (and is NOT):

* It IS the *addressing + node model*: a dense node array indexed by maze index
  over the SORTED UNION of per-layer track coords + access-point/pin coords, with
  a per-direction node state (blocked E/N/U, edges, special-via, grid/AP costs,
  route/marker/fixed-shape costs). Pure, offline, generic (no process constants:
  layers/tracks/pitch/vias come from ``ProcessProfile`` as DATA).
* It is NOT the maze (FlexDR, T5), FlexPA (T2), or 3D global route (T3). Those
  stages consume this grid; they live in sibling packages.

Two fidelity traps preserved exactly (see the doc):

1. ``frTrackPattern::isHorizontal()==true`` means a VERTICAL track (constant-x
   line). A layer's preferred routing direction therefore decides which axis it
   contributes lines to: a V (vertical) layer -> x-coords, an H layer -> y-coords.
2. ``get_idx`` flips row/column-major with the layer's preferred direction so the
   hot maze direction is unit-stride per layer (H: ``x + y*X``; V: ``y + x*Y``).
   Any reuse of this index (the future Rust kernel, a ported worker) MUST use the
   same flip or addresses silently diverge.

NDR (non-default-rule) node fields are intentionally dropped for now (no NDR nets
in our flow); the names are reserved for an additive later pass (doc decision D2).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

# A direction enum mirroring frDirEnum's planar/via axes we use in T1.
# (E,N,U) are the "forward" half; (W,S,D) reduce to them in get/set, exactly like
# FlexGridGraph::correct/reverse. T1 stores forward-half state on the node.
DIR_E, DIR_N, DIR_U = "E", "N", "U"

_COST_BITS = 8
_COST_MAX = (1 << _COST_BITS) - 1   # saturating cap, == OpenROAD addToByte limit


def add_to_byte(augend: int, summand: int) -> int:
    """Saturating add (FlexGridGraph::addToByte, H:1210)."""
    return min(augend + summand, _COST_MAX)


def sub_from_byte(minuend: int, subtrahend: int) -> int:
    """Flooring sub (FlexGridGraph::subFromByte, H:1224)."""
    return max(minuend - subtrahend, 0)


# Node SoA field names -- one bytearray of length capacity each. Order documented
# against FlexGridGraph.h:1079-1124 (NDR fields omitted, decision D2).
_NODE_FIELDS: Tuple[str, ...] = (
    # Byte 0: edges + per-direction hard block
    "edge_E", "edge_N", "edge_U",
    "blocked_E", "blocked_N", "blocked_U",
    # Byte 1: via flags + grid/AP costs
    "svia", "override_via",
    "gridcost_E", "gridcost_N", "gridcost_U",
    "apcost_E", "apcost_N", "apcost_U",
    # Bytes 2-8: cost bytes
    "rsc_planar", "rsc_via",          # routeShapeCost{Planar,Via}
    "mc_planar", "mc_via",            # markerCost{Planar,Via}
    "fsc_via", "fsc_planar_h", "fsc_planar_v",  # fixedShapeCost{Via,PlanarHorz,PlanarVert}
)


@dataclass
class TrackGrid:
    """Dense track-resolution node grid. Index any node by ``get_idx(xi,yi,zi)``;
    map a coord to an index by ``maze_x/maze_y/maze_z`` (binary search)."""

    xCoords: List[int]          # sorted-unique nm: vertical-track + AP/pin x-lines
    yCoords: List[int]          # sorted-unique nm: horizontal-track + AP/pin y-lines
    zCoords: List[int]          # routing-layer indices, in stack order (0..Z-1)
    z_dir: List[str]            # "H" | "V" per z (drives get_idx + edges)
    z_height: List[int]         # accumulated via-cost height per z (T5 cost only)
    layers: Tuple[str, ...]     # "L/D" per z (LVS / via-rule join)
    # via adjacency: set of (zi, zi+1) layer-index pairs a via rule bridges
    via_z_pairs: frozenset = frozenset()
    # Node SoA -- each a bytearray(capacity); allocated in __post_init__.
    nodes: Dict[str, bytearray] = field(default_factory=dict)
    # auxiliary parallel arrays (FlexGridGraph srcs_/dsts_/guides_/prevDirs_)
    srcs: bytearray = field(default_factory=bytearray)
    dsts: bytearray = field(default_factory=bytearray)
    guides: bytearray = field(default_factory=bytearray)
    prev_dirs: bytearray = field(default_factory=bytearray)
    # access-point locations per layer index (FlexGridGraph ap_locs_, H:1152):
    # {layer_index: {(x_nm, y_nm), ...}}. Populated by build() from FlexPA APs,
    # faithful to FlexDRWorker::initTrackCoords_pin (addAccessPointLocation).
    ap_locs: Dict[int, set] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # cached dims + bool z-dir for the get_idx hot path (called ~10^7-10^8 times):
        # avoid the len()/@property + string compare per call.
        self._nx = len(self.xCoords)
        self._ny = len(self.yCoords)
        self._nz = len(self.zCoords)
        self._xy = self._nx * self._ny
        self._zh = [d == "H" for d in self.z_dir]
        cap = self._xy * self._nz
        if not self.nodes:
            self.nodes = {f: bytearray(cap) for f in _NODE_FIELDS}
        if not self.srcs:
            self.srcs = bytearray(cap)
        if not self.dsts:
            self.dsts = bytearray(cap)
        if not self.guides:
            self.guides = bytearray(cap)
        if not self.prev_dirs:
            self.prev_dirs = bytearray(cap * 3)

    # ---- dimensions ---------------------------------------------------------
    @property
    def nx(self) -> int:
        return len(self.xCoords)

    @property
    def ny(self) -> int:
        return len(self.yCoords)

    @property
    def nz(self) -> int:
        return len(self.zCoords)

    @property
    def capacity(self) -> int:
        return self.nx * self.ny * self.nz

    # ---- addressing (faithful to FlexGridGraph.h) ---------------------------
    def get_idx(self, xi: int, yi: int, zi: int) -> int:
        """Maze index (FlexGridGraph.h:1197). Row/column-major flips with the
        layer's preferred direction so the hot dir is unit-stride per plane."""
        if self._zh[zi]:               # HORIZONTAL: x contiguous (walk E = +1)
            return xi + yi * self._nx + zi * self._xy
        return yi + xi * self._ny + zi * self._xy   # VERTICAL: y contiguous

    def maze_x(self, x_nm: int) -> int:
        return bisect_left(self.xCoords, x_nm)

    def maze_y(self, y_nm: int) -> int:
        return bisect_left(self.yCoords, y_nm)

    def maze_z(self, layer_index: int) -> int:
        return bisect_left(self.zCoords, layer_index)

    def has_x(self, x_nm: int) -> bool:
        i = bisect_left(self.xCoords, x_nm)
        return i < self.nx and self.xCoords[i] == x_nm

    def has_y(self, y_nm: int) -> bool:
        i = bisect_left(self.yCoords, y_nm)
        return i < self.ny and self.yCoords[i] == y_nm

    def has_z(self, layer_index: int) -> bool:
        i = bisect_left(self.zCoords, layer_index)
        return i < self.nz and self.zCoords[i] == layer_index

    def has_idx(self, x_nm: int, y_nm: int, layer_index: int) -> bool:
        return self.has_x(x_nm) and self.has_y(y_nm) and self.has_z(layer_index)

    # ---- access-point locations (FlexGridGraph addAccessPointLocation) ------
    def add_access_point_location(self, layer_index: int, x_nm: int, y_nm: int) -> None:
        self.ap_locs.setdefault(layer_index, set()).add((int(x_nm), int(y_nm)))

    def is_access_point_location(self, layer_index: int, x_nm: int, y_nm: int) -> bool:
        return (int(x_nm), int(y_nm)) in self.ap_locs.get(layer_index, ())

    def is_valid(self, xi: int, yi: int, zi: int) -> bool:
        return 0 <= xi < self.nx and 0 <= yi < self.ny and 0 <= zi < self.nz

    def point(self, xi: int, yi: int) -> Tuple[int, int]:
        return self.xCoords[xi], self.yCoords[yi]

    def idx_box(self, x1: int, y1: int, x2: int, y2: int) -> Tuple[int, int, int, int]:
        """Rect (nm) -> inclusive maze-index box (FlexGridGraph::getIdxBox,
        'uncertain' mode): lower_bound for the min corner, upper_bound-1 for max."""
        xi1 = bisect_left(self.xCoords, x1)
        yi1 = bisect_left(self.yCoords, y1)
        xi2 = max(0, bisect_right(self.xCoords, x2) - 1)
        yi2 = max(0, bisect_right(self.yCoords, y2) - 1)
        return xi1, yi1, xi2, yi2

    # ---- node accessors (forward-half: E/N/U) -------------------------------
    def _f(self, name: str, xi: int, yi: int, zi: int) -> int:
        return self.nodes[name][self.get_idx(xi, yi, zi)]

    def is_blocked(self, xi: int, yi: int, zi: int, d: str) -> bool:
        return bool(self.nodes["blocked_" + d][self.get_idx(xi, yi, zi)])

    def set_blocked(self, xi: int, yi: int, zi: int, d: str, val: bool = True) -> None:
        self.nodes["blocked_" + d][self.get_idx(xi, yi, zi)] = 1 if val else 0

    def has_edge(self, xi: int, yi: int, zi: int, d: str) -> bool:
        return bool(self.nodes["edge_" + d][self.get_idx(xi, yi, zi)])

    def set_edge(self, xi: int, yi: int, zi: int, d: str, val: bool = True) -> None:
        self.nodes["edge_" + d][self.get_idx(xi, yi, zi)] = 1 if val else 0

    # ---- cost ops (saturating/flooring, faithful to H:546-657) --------------
    def add_route_shape_planar(self, idx: int) -> None:
        a = self.nodes["rsc_planar"]
        a[idx] = add_to_byte(a[idx], 1)

    def sub_route_shape_planar(self, idx: int) -> None:
        a = self.nodes["rsc_planar"]
        a[idx] = sub_from_byte(a[idx], 1)

    def add_route_shape_via(self, idx: int) -> None:
        a = self.nodes["rsc_via"]
        a[idx] = add_to_byte(a[idx], 1)

    def add_marker_planar(self, idx: int) -> None:
        a = self.nodes["mc_planar"]
        a[idx] = add_to_byte(a[idx], 10)   # OpenROAD adds 10 per hit (H:595)

    def add_marker_via(self, idx: int) -> None:
        a = self.nodes["mc_via"]
        a[idx] = add_to_byte(a[idx], 10)

    def decay_marker_planar(self, idx: int, d: float = 0.0) -> bool:
        """Decay (×d, floored at 0). Returns True when it reaches 0 (H:619)."""
        a = self.nodes["mc_planar"]
        a[idx] = max(0, int(a[idx] * d))
        return a[idx] == 0

    def set_fixed_shape_planar(self, idx: int, c: int, *, vert: bool) -> None:
        self.nodes["fsc_planar_v" if vert else "fsc_planar_h"][idx] = min(c, _COST_MAX)

    def add_fixed_shape_via(self, idx: int) -> None:
        a = self.nodes["fsc_via"]
        a[idx] = add_to_byte(a[idx], 1)

    # ---- edge initialisation (T1 smoke: full interior edges) ----------------
    def init_full_edges(self) -> None:
        """Mark every interior planar edge present + every via edge where a via
        rule bridges adjacent layers. T5 (FlexDR) refines this; T1 uses it so the
        gate-5 routability smoke has a connected graph. Blocked nodes still cut
        traversal -- edges express geometry, blocked expresses keep-out."""
        for zi in range(self.nz):
            for yi in range(self.ny):
                for xi in range(self.nx):
                    if xi + 1 < self.nx:
                        self.set_edge(xi, yi, zi, DIR_E)
                    if yi + 1 < self.ny:
                        self.set_edge(xi, yi, zi, DIR_N)
                    if (zi, zi + 1) in self.via_z_pairs:
                        self.set_edge(xi, yi, zi, DIR_U)


def _lattice_points(pitch: int, lo: int, hi: int) -> List[int]:
    """Track lines on a global pitch lattice (origin 0) within [lo, hi].
    Mirrors FlexGridGraph::initTracks clipping to the maze bbox. Inclusive of
    both ends so a coord exactly on the bbox edge is a usable stop."""
    if pitch <= 0:
        return []
    k0 = -(-lo // pitch)        # ceil(lo / pitch)
    k1 = hi // pitch            # floor(hi / pitch)
    return [k * pitch for k in range(k0, k1 + 1)]


def build(
    profile,
    bbox_nm: Tuple[int, int, int, int],
    *,
    access_points: Sequence[Tuple[str, int, int]] = (),
    pitch_nm: int | None = None,
) -> TrackGrid:
    """Build a TrackGrid from a ``ProcessProfile`` (the ONLY process-aware entry).

    * ``profile.routing_layers`` -> the z stack; ``profile.layer_direction(L)`` ->
      whether layer L contributes vertical (x) or horizontal (y) track lines.
    * one global ``pitch_nm`` for T1 (== wire_width+wire_clear if not given;
      doc decision V2: per-layer pitch is an additive later field).
    * ``access_points`` = ``(layer "L/D", x_nm, y_nm)`` from FlexPA (T2). Faithful to
      ``FlexDRWorker::initTrackCoords_pin`` (FlexDR_init.cpp:1719), an AP injects ONLY
      its PREFERRED-axis coord (y for a horizontal layer, x for a vertical layer) -- the
      cross-axis coord is already a track line because FlexPA places APs ON-TRACK. The
      AP point is registered in ``ap_locs`` on its own layer and propagated UP across
      same-direction layers (via-landing tracks). See the mapping table in the FlexPA
      module's docstring, §0.
    """
    x1, y1, x2, y2 = bbox_nm
    if pitch_nm is None:
        pitch_nm = int(round((profile.wire_width_um + profile.wire_clear_um) * 1000))

    layers = tuple(profile.routing_layers)
    z_dir = [profile.layer_direction(L) for L in layers]
    li = {L: i for i, L in enumerate(layers)}

    xset: set = set()
    yset: set = set()
    for L, d in zip(layers, z_dir):
        if d == "V":                    # vertical lines -> x positions
            xset.update(_lattice_points(pitch_nm, x1, x2))
        else:                           # horizontal lines -> y positions
            yset.update(_lattice_points(pitch_nm, y1, y2))

    # AP injection: pref-axis coord only (C1), registered in ap_locs (C3).
    ap_locs: Dict[int, set] = {}
    for L, ax, ay in access_points:
        if L not in li:
            continue
        zi0 = li[L]
        d = z_dir[zi0]
        if d == "V":
            if x1 <= ax <= x2:
                xset.add(int(ax))       # pref axis = x
        else:
            if y1 <= ay <= y2:
                yset.add(int(ay))       # pref axis = y
        # register the AP point on its own layer + same-direction layers above it
        # (the via stack lands on those same-dir tracks at the same pref coord)
        for zi in range(zi0, len(layers)):
            if z_dir[zi] == d:
                ap_locs.setdefault(zi, set()).add((int(ax), int(ay)))

    xCoords = sorted(xset)
    yCoords = sorted(yset)
    zCoords = list(range(len(layers)))

    # via adjacency between consecutive layers in the stack
    via_pairs = set()
    for lo, _cut, up in profile.vias:
        if lo in li and up in li:
            a, b = sorted((li[lo], li[up]))
            if b == a + 1:
                via_pairs.add((a, b))

    # z_height: accumulated pitch (T5 cost only; per-layer pitch is V2)
    z_height = [(zi + 1) * pitch_nm for zi in range(len(layers))]

    return TrackGrid(
        xCoords=xCoords,
        yCoords=yCoords,
        zCoords=zCoords,
        z_dir=z_dir,
        z_height=z_height,
        layers=layers,
        via_z_pairs=frozenset(via_pairs),
        ap_locs=ap_locs,
    )
