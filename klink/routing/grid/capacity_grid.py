"""Capacity-grid multilayer router (F4) — grt-style substrate.

The Hanan-grid substrate (feature_grid_3d) explodes when large device
pads are added as obstacles: every pad edge spawns grid lines (STATUS
Update 57, the scaling wall). This module replaces it with a UNIFORM
fixed-pitch capacity grid (borrowing OpenROAD grt's capacity-grid /
rip-up-reroute / negotiated-cost concepts — concept only, BSD-3, adapted
to our bbox world), so obstacle count no longer drives grid size.

Legality is FIRST-CLASS (drt-style concept, not code): each cell carries,
per net, whether a wire may occupy it and whether a via may land there.
A wire over a foreign device pad, a via on a device body, and a same-cell
collision between different nets are all expressed as legality/usage on
the grid — the same things LVS checks. The offline legality check is
therefore faithful to LVS (lesson 65: the proxy must model what LVS
checks, or it lies).

Pure and offline: no KLayout. All um/layer values are parameters.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

Cell = Tuple[int, int, int]            # (ix, iy, layer_index)
Box = Tuple[float, float, float, float]


def _q(v: float) -> int:
    return int(round(v * 1000))


@dataclass(frozen=True)
class ViaRule:
    a: str                              # conductor layer "L/D"
    b: str
    cell: str
    footprint_um: Tuple[float, float]
    cost: float = 2.0


@dataclass
class NetInput:
    """One electrical net to connect. Each terminal is given EITHER as an
    access point (x,y,layer) OR, preferably, as the SET of grid cells its
    pad covers (``terminal_cells``): the router then ends a wire anywhere
    on the pad instead of via a long, unchecked stub to one point (which
    can cross other nets — STATUS 58 F4B shorts)."""
    net: str
    access: List[Tuple[float, float, str]] = field(default_factory=list)
    terminal_cells: List[set] = field(default_factory=list)   # list of {Cell,...}


@dataclass
class CapacityGrid:
    pitch_nm: int
    x0_nm: int
    y0_nm: int
    nx: int
    ny: int
    layers: Tuple[str, ...]
    # per (layer_index) set of blocked cells common to ALL nets (channel)
    wire_blocked_all: Dict[int, set] = field(default_factory=dict)
    # per (layer_index) -> owner_net -> set of cells that are that net's
    # pad (blocked for OTHER nets, allowed for the owner)
    pad_cells: Dict[int, Dict[str, set]] = field(default_factory=dict)
    # cells where a via may NOT land (device bodies), layer-agnostic
    via_blocked: set = field(default_factory=set)
    # via transitions allowed between these layer-index pairs + the rule
    via_rules: List[ViaRule] = field(default_factory=list)
    # OPTIONAL O(1) inverse of pad_cells: per layer_index -> {cell: owner_net}.
    # When present (built by _augmented_grid for the per-box maze), _wire_ok does
    # an O(1) owner lookup instead of an O(num_nets) scan over pad_cells -- the
    # FlexDR maze hot path (faithful to OpenROAD's per-node grid-graph flags,
    # not a dict-of-sets scan). None on the base grid (falls back to the scan).
    pad_owner: Optional[Dict[int, Dict[Tuple[int, int], str]]] = None

    def cx(self, ix: int) -> int:
        return self.x0_nm + ix * self.pitch_nm

    def cy(self, iy: int) -> int:
        return self.y0_nm + iy * self.pitch_nm

    def in_bounds(self, ix: int, iy: int) -> bool:
        return 0 <= ix < self.nx and 0 <= iy < self.ny

    def cell_of(self, x_nm: int, y_nm: int) -> Tuple[int, int]:
        return (round((x_nm - self.x0_nm) / self.pitch_nm),
                round((y_nm - self.y0_nm) / self.pitch_nm))

    def via_index(self) -> List[Tuple[int, int, float]]:
        """Cached [(lower_layer_idx, upper_layer_idx, via_cost)] for the via
        rules, sorted by (a, b, cell) and skipping rules whose conductors are
        not in this grid's layer stack -- IDENTICAL order/content to the old
        per-call ``sorted(via_rules)`` + ``layers.index`` in ``_neighbors`` (it
        was recomputed on every one of ~16M neighbor calls). layers/via_rules
        are fixed after build, so this is memoised on the instance. Process-
        general: derived from the profile's via rules, no hardcoded layers."""
        cache = getattr(self, "_via_index_cache", None)
        if cache is None:
            li = {lyr: i for i, lyr in enumerate(self.layers)}
            cache = [(li[vr.a], li[vr.b], vr.cost)
                     for vr in sorted(self.via_rules, key=lambda r: (r.a, r.b, r.cell))
                     if vr.a in li and vr.b in li]
            self._via_index_cache = cache
        return cache


def _boxes_to_cells(boxes_nm: Sequence[Box], g: "CapacityGrid",
                    grow_nm: int = 0) -> set:
    """Cells whose center falls within any box (optionally grown)."""
    out: set = set()
    for (bx1, by1, bx2, by2) in boxes_nm:
        ix0 = max(0, (bx1 - grow_nm - g.x0_nm) // g.pitch_nm)
        ix1 = min(g.nx - 1, (bx2 + grow_nm - g.x0_nm) // g.pitch_nm)
        iy0 = max(0, (by1 - grow_nm - g.y0_nm) // g.pitch_nm)
        iy1 = min(g.ny - 1, (by2 + grow_nm - g.y0_nm) // g.pitch_nm)
        for ix in range(int(ix0), int(ix1) + 1):
            for iy in range(int(iy0), int(iy1) + 1):
                cx, cy = g.cx(ix), g.cy(iy)
                if (bx1 - grow_nm <= cx <= bx2 + grow_nm
                        and by1 - grow_nm <= cy <= by2 + grow_nm):
                    out.add((ix, iy))
    return out


def build_capacity_grid(
    *,
    layers: Sequence[str],
    bbox_um: Box,
    pitch_um: float,
    channel_boxes_um: Sequence[Box],
    pad_boxes_by_layer: Mapping[str, Sequence[Tuple[str, Box]]],
    device_body_boxes_um: Sequence[Box],
    via_rules: Sequence[ViaRule],
    via_footprint_um: float,
    real_pad_boxes_by_layer: Optional[Mapping[str, Sequence[Tuple[str, Box]]]] = None,
) -> CapacityGrid:
    """Build the capacity grid + legality.  ``pad_boxes_by_layer`` maps a
    conductor layer to (owner_net, box) pairs (a device pad belongs to
    its net and is a keep-out for every OTHER net).  ``channel_boxes`` are
    keep-outs for ALL nets/wires; ``device_body_boxes`` forbid via
    landings (no via on a device, lesson 67)."""
    p = _q(pitch_um)
    x1, y1, x2, y2 = (_q(bbox_um[0]), _q(bbox_um[1]), _q(bbox_um[2]), _q(bbox_um[3]))
    nx = (x2 - x1) // p + 1
    ny = (y2 - y1) // p + 1
    g = CapacityGrid(pitch_nm=p, x0_nm=x1, y0_nm=y1, nx=nx, ny=ny,
                     layers=tuple(layers), via_rules=list(via_rules))
    li = {lyr: i for i, lyr in enumerate(layers)}
    ch_nm = [(_q(b[0]), _q(b[1]), _q(b[2]), _q(b[3])) for b in channel_boxes_um]
    # spacing/footprint halo so a via never swallows a foreign wire: a pad
    # /channel grows by via_footprint/2 for via-blocking purposes
    half_fp = _q(via_footprint_um / 2.0)
    ch_cells = _boxes_to_cells(ch_nm, g)
    for lyr in layers:
        g.wire_blocked_all[li[lyr]] = set(ch_cells)
        g.pad_cells[li[lyr]] = defaultdict(set)
    for lyr, items in pad_boxes_by_layer.items():
        if lyr not in li:
            continue
        for owner, box in items:
            cells = _boxes_to_cells(
                [(_q(box[0]), _q(box[1]), _q(box[2]), _q(box[3]))], g)
            g.pad_cells[li[lyr]][owner] |= cells
    # Real-pad priority: a GROWN clearance halo must not claim a grid cell that is
    # another net's ACTUAL (ungrown) pad metal. Otherwise a terminal's own pin is
    # multi-owned -- e.g. a load device's VDD-drain halo reaching across the ~3um
    # channel onto its signal-source pad -- and the route cannot start on its own
    # pin. A cell that is exactly ONE net's real pad belongs to that net; strip
    # every other (halo) owner from it. Two real pads that truly overlap are left
    # as a conflict. Aligned pads never collide here -> no-op -> byte-parity holds.
    # g.real_pad_owner[li] = {cell: owner} for cells whose CENTRE is in a real pad
    # -- FlexPA's sub-pitch access (open-one-cell) reads it to tell a real-metal
    # conflict from a mere clearance halo.
    g.real_pad_owner = {}
    for lyr, items in (real_pad_boxes_by_layer or {}).items():
        if lyr not in li:
            continue
        layer_i = li[lyr]
        real_owner: Dict[Tuple[int, int], str] = {}
        conflict: Set[Tuple[int, int]] = set()
        for owner, box in items:
            for c in _boxes_to_cells(
                    [(_q(box[0]), _q(box[1]), _q(box[2]), _q(box[3]))], g):
                if real_owner.get(c, owner) != owner:
                    conflict.add(c)
                real_owner[c] = owner
        g.real_pad_owner[layer_i] = real_owner
        pc = g.pad_cells[layer_i]
        for c, owner in real_owner.items():
            if c in conflict:
                continue
            for o in list(pc):
                if o != owner:
                    pc[o].discard(c)
            pc[owner].add(c)
    body_nm = [(_q(b[0]), _q(b[1]), _q(b[2]), _q(b[3])) for b in device_body_boxes_um]
    # a via may not land where its footprint would touch a device body or
    # a channel (grown by the via half-footprint)
    g.via_blocked = (_boxes_to_cells(body_nm, g, grow_nm=half_fp)
                     | _boxes_to_cells(ch_nm, g, grow_nm=half_fp))
    return g


_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


_EMPTY: Dict[Tuple[int, int], str] = {}


def _wire_ok(g: CapacityGrid, ix: int, iy: int, lyr_i: int, net: str) -> bool:
    if (ix, iy) in g.wire_blocked_all.get(lyr_i, ()):  # channel: all nets
        return False
    po = g.pad_owner
    if po is not None:                                  # O(1) per-cell owner (hot path)
        owner = po.get(lyr_i, _EMPTY).get((ix, iy))
        return owner is None or owner == net
    for owner, cells in g.pad_cells.get(lyr_i, {}).items():  # O(num_nets) fallback (base grid)
        if owner != net and (ix, iy) in cells:          # foreign pad
            return False
    return True


def _clear(occ: Mapping[Cell, str], padmap: Mapping[Cell, str],
           ix: int, iy: int, lyr: int, net: str, r: int) -> bool:
    """True if no FOREIGN metal — a foreign net's routed wire (``occ``) OR
    a foreign device pad (``padmap``) — lies within Chebyshev radius r of
    (ix,iy) on layer lyr.  r=0 => no-short (own cell only); r>0 => a
    spacing / via-footprint clearance.  Checking foreign PADS (not just
    wires) is what stops a via landing or a wire abutting a device pad
    (the F4B via-bridge / 1.5um-to-pad shorts)."""
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            cell = (ix + dx, iy + dy, lyr)
            o = occ.get(cell)
            if o is not None and o != net:
                return False
            p = padmap.get(cell)
            if p is not None and p != net:
                return False
    return True


def _route_one(
    g: CapacityGrid, starts: Sequence[Cell], goals: set, net: str,
    *, usage: Mapping[Cell, set], history: Mapping[Cell, float],
    pres_fac: float, via_cost: Dict[Tuple[int, int], float],
    occ: Optional[Mapping[Cell, str]] = None,
    padmap: Optional[Mapping[Cell, str]] = None,
    wire_halo_r: int = 0, via_halo_r: int = 0,
) -> Optional[List[Cell]]:
    """A* over the capacity grid.  Cost prices congestion (PathFinder).
    Hard-excluded: illegal cells (foreign pad / channel); vias on device
    bodies; a wire cell whose ``wire_halo_r`` neighbourhood holds foreign
    metal (DRC spacing); a via whose ``via_halo_r`` footprint on EITHER
    landing layer holds foreign metal (the 15um landing must not touch a
    foreign net -- the F4B via-bridge short).  All radii derive from
    process spacings (parameters), nothing preset."""
    li_n = len(g.layers)
    occ = occ or {}
    padmap = padmap or {}

    def h(c: Cell) -> int:
        # manhattan to nearest goal (in cells)
        return min(abs(c[0] - gx) + abs(c[1] - gy) for (gx, gy, _gl) in goals) if goals else 0

    heap: List[Tuple[float, int, Cell]] = []
    best: Dict[Cell, float] = {}
    came: Dict[Cell, Cell] = {}
    seq = 0
    for s in starts:
        if _wire_ok(g, s[0], s[1], s[2], net) and _clear(occ, padmap, s[0], s[1], s[2], net, wire_halo_r):
            best[s] = 0.0
            heappush(heap, (float(h(s)), seq, s)); seq += 1
    seen: set = set()
    while heap:
        _, _, c = heappop(heap)
        if c in seen:
            continue
        seen.add(c)
        if (c[0], c[1], c[2]) in goals:
            path = [c]
            while path[-1] in came:
                path.append(came[path[-1]])
            path.reverse()
            return path
        ix, iy, lyr = c
        base = best[c]
        # in-plane neighbours: keep DRC spacing from foreign metal
        nbrs: List[Tuple[Cell, float]] = []
        for dx, dy in _STEPS:
            nx_, ny_ = ix + dx, iy + dy
            if (g.in_bounds(nx_, ny_) and _wire_ok(g, nx_, ny_, lyr, net)
                    and _clear(occ, padmap, nx_, ny_, lyr, net, wire_halo_r)):
                nbrs.append(((nx_, ny_, lyr), 1.0))
        # via transitions: the landing footprint must clear foreign metal
        # on BOTH layers it bridges
        if (ix, iy) not in g.via_blocked:
            for vr in g.via_rules:
                try:
                    a_i = g.layers.index(vr.a); b_i = g.layers.index(vr.b)
                except ValueError:
                    continue
                other = b_i if lyr == a_i else (a_i if lyr == b_i else None)
                if (other is not None and _wire_ok(g, ix, iy, other, net)
                        and _clear(occ, padmap, ix, iy, lyr, net, via_halo_r)
                        and _clear(occ, padmap, ix, iy, other, net, via_halo_r)):
                    nbrs.append(((ix, iy, other), vr.cost))
        for nb, step in nbrs:
            others = len(usage.get(nb, set()) - {net})
            hist = history.get(nb, 0.0)
            cost = base + step * (1.0 + hist) * (1.0 + pres_fac * others)
            if cost < best.get(nb, float("inf")) - 1e-9:
                best[nb] = cost
                came[nb] = c
                seq += 1
                heappush(heap, (cost + h(nb), seq, nb))
    return None


@dataclass
class RouteResult:
    ok: bool
    routes: Dict[str, List[Cell]]       # net -> cells in the routed tree
    iterations: int
    problems: Tuple[dict, ...] = ()
    # net -> list of (cellA, cellB) tree edges; same-layer = wire segment,
    # same-cell/diff-layer = via. Used by the geometry writer.
    edges: Dict[str, List[Tuple[Cell, Cell]]] = field(default_factory=dict)


def _terminal_cellsets(g: CapacityGrid, net: NetInput) -> List[set]:
    """Per terminal, the set of candidate goal cells.  Prefer the pad
    cell-set (``terminal_cells``); else a single cell from the access
    point."""
    if net.terminal_cells:
        return [s for s in net.terminal_cells if s]
    li = {lyr: i for i, lyr in enumerate(g.layers)}
    out = []
    for (x, y, lyr) in net.access:
        ix, iy = g.cell_of(_q(x), _q(y))
        out.append({(ix, iy, li[lyr])})
    return out


def route_nets(
    g: CapacityGrid, nets: Sequence[NetInput], *,
    max_iters: int = 30, pres0: float = 0.5, growth: float = 1.6,
    hist_fac: float = 1.0, wire_clear_um: float = 0.0, via_clear_um: float = 0.0,
    width_um: float = 0.0,
) -> RouteResult:
    """Negotiated multi-net routing on the capacity grid.  Nets are
    connected into trees by incremental A* (greedy Steiner).  Two
    clearances, both from process spacings (parameters, never preset):
      * ``wire_clear_um`` -- min spacing between DIFFERENT nets' wires
        (DRC); 0 => only no-short (no shared cell).
      * ``via_clear_um``  -- extra clearance a via's footprint keeps from
        foreign metal on both landing layers.
    Convergence means: no shared cell AND every clearance met."""
    termsets = {n.net: _terminal_cellsets(g, n) for n in nets}
    history: Dict[Cell, float] = defaultdict(float)
    order = [n.net for n in nets]
    pres = pres0
    import math
    pitch_um = g.pitch_nm / 1000.0
    fp_half = max((max(vr.footprint_um) / 2.0 for vr in g.via_rules), default=0.0)
    # spacing -> cell radius. wire: forbid foreign whose gap to this wire
    # would be < wire_clear. via: forbid foreign inside the landing
    # footprint + clearance (conservative ceil; vias then live in open
    # channels). All from process spacings -- nothing preset.
    wire_halo_r = max(0, math.ceil((wire_clear_um + width_um - 1e-6) / pitch_um) - 1) if pitch_um else 0
    via_halo_r = math.ceil((fp_half + width_um / 2.0 + via_clear_um) / pitch_um) if pitch_um else 0
    li_n = len(g.layers)
    # static foreign-metal map: every device pad cell -> its owner net
    padmap: Dict[Cell, str] = {}
    for lyr_i, owners in g.pad_cells.items():
        for owner, cells in owners.items():
            for (ix, iy) in cells:
                padmap[(ix, iy, lyr_i)] = owner

    def _violations(occ, edges):
        """Cells where a clearance is breached (spacing or via footprint)."""
        bad: set = set()
        for net, es in edges.items():
            for a, b in es:
                if a[2] == b[2]:                      # wire cell: spacing
                    for cc in (a, b):
                        if not _clear(occ, padmap, cc[0], cc[1], cc[2], net, wire_halo_r):
                            bad.add(cc)
                else:                                  # via: footprint both layers
                    for lz in range(li_n):
                        if not _clear(occ, padmap, a[0], a[1], lz, net, via_halo_r):
                            bad.add((a[0], a[1], lz))
        return bad

    for it in range(max_iters):
        usage: Dict[Cell, set] = defaultdict(set)
        occ: Dict[Cell, str] = {}
        routes: Dict[str, List[Cell]] = {}
        edges: Dict[str, List[Tuple[Cell, Cell]]] = {}
        failed = None
        for net in order:
            sets = termsets[net]
            if not sets:
                continue
            tree: set = set(sets[0])
            used: set = set()
            net_edges: List[Tuple[Cell, Cell]] = []
            ok = True
            for tset in sets[1:]:
                if tree & tset:                      # already connected
                    tree |= tset
                    continue
                starts = [c for c in tree if _wire_ok(g, c[0], c[1], c[2], net)]
                path = _route_one(g, starts, set(tset), net,
                                  usage=usage, history=history, pres_fac=pres,
                                  via_cost={}, occ=occ, padmap=padmap,
                                  wire_halo_r=wire_halo_r, via_halo_r=via_halo_r)
                if path is None:
                    ok = False
                    break
                for a, b in zip(path, path[1:]):
                    net_edges.append((a, b))
                for c in path:
                    tree.add(c); used.add(c)
            if not ok:
                failed = net
                break
            routes[net] = sorted(used)
            edges[net] = net_edges
            for c in used:
                usage[c].add(net)
                occ[c] = net
        if failed is not None:
            return RouteResult(False, routes, it + 1,
                               ({"type": "no_path", "net": failed},), edges)
        overused = [c for c, who in usage.items() if len(who) > 1]
        bad = _violations(occ, edges)
        if not overused and not bad:
            return RouteResult(True, routes, it + 1, (), edges)
        for c in set(overused) | bad:
            history[c] += hist_fac
        pres *= growth
        congestion: Dict[str, int] = defaultdict(int)
        for c in list(overused) + list(bad):
            for n in usage.get(c, ()):
                congestion[n] += 1
        order = sorted(order, key=lambda n: (-congestion.get(n, 0), n))
    return RouteResult(False, {}, max_iters,
                       ({"type": "not_converged",
                         "overused": len(overused), "clearance": len(bad)},))

