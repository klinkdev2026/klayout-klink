"""The multilayer engine (multi-layer P&R) -- FlexDR detailed router. Copy of the
engine, OWNED BY the multilayer engine and freely optimizable. The frozen
single-stack engine (lab-fast greedy) lives in
`klink/routing/backends/flexdr/flexdr.py` and is FROZEN/byte-parity-locked --
NOTHING here may import or modify it. Shared frozen FOUNDATION (NOT the engine):
`capacity_grid` (grid datastructure), `pathfinder` (low-level helpers), and the
`klink_boxmaze_rs` Rust kernel -- both engines import these read-only. See
this package's README.

FlexDR detailed router -- faithful port of OpenROAD `drt`.

Implemented strictly to the faithful-port spec cited in this module's docstring
set (cites OpenROAD source). Build order = the spec's M1..M5; each piece
verified vs the golden oracle in tests/unit/test_flexdr.py.

Status: Step 1 (worker-box partition + checkerboard) and Step 2 (boundary-pin
extraction) are DONE + tested. M1 here = the faithful per-box maze:
`BoxCost` (hard legality + boolean routeShape/marker/fixedShape adj), additive
`box_maze` (exact bend + via + boolean adj costs, spec section 3), and
`flexgc_lite` (markers WITH sources, spec section 6). The non-faithful PathFinder
`_box_negotiate` and the old worker/schedule have been REMOVED; the faithful
worker (M2, route_queue) and schedule (M3) are rebuilt on top of M1.
"""
from __future__ import annotations

import dataclasses
from collections import defaultdict, deque
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import (DefaultDict, Dict, List, Mapping, NamedTuple, Optional,
                    Sequence, Set, Tuple)

from klink.routing.grid.capacity_grid import (
    Cell, CapacityGrid, NetInput, RouteResult, _terminal_cellsets, _wire_ok,
)
from klink.routing.grid.pathfinder import (
    _footprint, _halos, _heuristic, _neighbors, _via_cells,
)

GCell = Tuple[int, int]
Edge = Tuple[Cell, Cell]

# Optional Rust box_maze kernel (byte-parity accelerator; graceful fallback).
# klink stays runnable with zero compiled deps -- the pure-Python box_maze below
# is the reference. Build: maturin develop --release --manifest-path
# rust/klink_boxmaze/Cargo.toml.
try:                                # pragma: no cover - presence is environmental
    import klink_boxmaze_rs as _BOXMAZE_RS
except Exception:                   # pragma: no cover
    _BOXMAZE_RS = None


def _grt_guide_corridors(g, ordered, termsets, GC, profile, halo=1):
    """grt (FastRoute) guides -> per-net gcell corridor, so the initial routing
    DISTRIBUTES nets across channel capacity (OpenROAD FlexDR routes within grt
    guides). Returns {net: set[gcell]} or None if grt is unavailable."""
    try:
        from klink.routing.grid.gcell import gcell_capacity
        from klink.routing.grid.global_router import route_global
    except Exception:
        return None
    cap_h, cap_v = gcell_capacity(g, GC, profile)
    ngy = len(cap_h); ngx = len(cap_h[0]) if ngy else (len(cap_v[0]) if cap_v else 1)
    if not ngy or not ngx:
        return None

    def gc_of(c):
        return (min(c[0] // GC, ngx - 1), min(c[1] // GC, ngy - 1))

    gnets = [{"net": n.net, "pins": sorted({gc_of(c) for tset in termsets[n.net] for c in tset})}
             for n in ordered]
    gr = route_global(ngx, ngy, cap_h, cap_v, {}, gnets, max_iters=200)
    guides = gr.get("routes", {}) if isinstance(gr, dict) else {}
    out = {}
    for n in ordered:
        cells = {gc_of(c) for tset in termsets[n.net] for c in tset}
        for edge in guides.get(n.net, ()):
            k, x, y = str(edge[0]), int(edge[1]), int(edge[2])
            cells.add((x, y))
            cells.add((min(x + 1, ngx - 1), y) if k == "H" else (x, min(y + 1, ngy - 1)))
        grown = set(cells)
        for _ in range(max(0, halo)):
            ring = set()
            for (x, y) in grown:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if 0 <= x + dx < ngx and 0 <= y + dy < ngy:
                        ring.add((x + dx, y + dy))
            grown |= ring
        out[n.net] = grown
    return out


def _grt_guides(g, ordered, termsets, GC, profile):
    """Raw per-net grt (FastRoute) guide edges {net: [(kind,x,y), ...]} in gcell
    coords -- FlexTA's input. Separate from _grt_guide_corridors (which the greedy
    seed uses) so the no-TA path stays byte-identical. Returns {} on failure."""
    try:
        from klink.routing.grid.gcell import gcell_capacity
        from klink.routing.grid.global_router import route_global
    except Exception:
        return {}
    cap_h, cap_v = gcell_capacity(g, GC, profile)
    ngy = len(cap_h); ngx = len(cap_h[0]) if ngy else (len(cap_v[0]) if cap_v else 1)
    if not ngy or not ngx:
        return {}

    def gc_of(c):
        return (min(c[0] // GC, ngx - 1), min(c[1] // GC, ngy - 1))

    gnets = [{"net": n.net, "pins": sorted({gc_of(c) for tset in termsets[n.net] for c in tset})}
             for n in ordered]
    gr = route_global(ngx, ngy, cap_h, cap_v, {}, gnets, max_iters=200)
    return gr.get("routes", {}) if isinstance(gr, dict) else {}


# --- Step 1: spatial worker-box partition + checkerboard (spec section 5) -----

class Box(NamedTuple):
    """A worker box = inclusive gcell range + tiling index (xi, yi) for batching."""
    xi: int
    yi: int
    gx0: int
    gy0: int
    gx1: int
    gy1: int


def worker_boxes(ngx: int, ngy: int, size: int, offset: int = 0) -> List[Box]:
    """Partition the ngx x ngy gcell grid into size x size boxes from ``offset``
    (offset may be negative -> partial first box, as in FlexDR which shifts the
    partition each pass)."""
    if size < 1:
        raise ValueError("size must be >= 1")
    xs = list(range(offset, ngx, size))
    ys = list(range(offset, ngy, size))
    boxes: List[Box] = []
    for xi, i in enumerate(xs):
        gx0, gx1 = max(0, i), min(ngx - 1, i + size - 1)
        if gx0 > gx1:
            continue
        for yi, j in enumerate(ys):
            gy0, gy1 = max(0, j), min(ngy - 1, j + size - 1)
            if gy0 > gy1:
                continue
            boxes.append(Box(xi, yi, gx0, gy0, gx1, gy1))
    return boxes


def checkerboard_batches(boxes: Sequence[Box], bsx: int = 2, bsy: int = 2) -> List[List[Box]]:
    """Group boxes so same-batch boxes are never adjacent (parallel-safe).
    FlexDR getBatchInfo = 2x2. batch key = (xi % bsx, yi % bsy)."""
    batches: DefaultDict[Tuple[int, int], List[Box]] = defaultdict(list)
    for b in boxes:
        batches[(b.xi % bsx, b.yi % bsy)].append(b)
    return [batches[k] for k in sorted(batches)]


def boxes_touch(a: Box, b: Box) -> bool:
    """True if the gcell ranges overlap or share an edge/corner. Same-batch
    boxes must NOT touch."""
    return (max(a.gx0, b.gx0) <= min(a.gx1, b.gx1) + 1
            and max(a.gy0, b.gy0) <= min(a.gy1, b.gy1) + 1)


def box_cell_bounds(g: CapacityGrid, box: Box, GC: int) -> Tuple[int, int, int, int]:
    return (box.gx0 * GC, box.gy0 * GC,
            min(g.nx - 1, (box.gx1 + 1) * GC - 1), min(g.ny - 1, (box.gy1 + 1) * GC - 1))


# --- Step 2: boundary-pin extraction (spec section 7) ------------------------

def cell_in_box(cell: Cell, box: Box, GC: int) -> bool:
    return (box.gx0 <= cell[0] // GC <= box.gx1) and (box.gy0 <= cell[1] // GC <= box.gy1)


class BoxExtract(NamedTuple):
    keep_cells: Set[Cell]          # out-of-box cells + boundary pins (NOT ripped)
    keep_edges: List[Edge]         # out-out edges + boundary-crossing edges
    pins: Set[Cell]                # in-box boundary cells the reroute reconnects to
    in_box_terms: List[Set[Cell]]  # the net's terminal sets that lie in the box
    ripped_cells: Set[Cell]        # in-box interior cells removed (to reroute)
    ripped_edges: List[Edge]       # in-in edges removed


def extract_box(cells: Sequence[Cell], edges: Sequence[Edge],
                terminals: Sequence[Set[Cell]], box: Box, GC: int) -> BoxExtract:
    """Split one net's route around a worker box: keep out-of-box geometry, rip
    in-box interior, turn boundary crossings into fixed PINS. A fresh in-box route
    reconnecting pins + in-box terminals restores the whole net."""
    def inb(c: Cell) -> bool:
        return cell_in_box(c, box, GC)

    keep_cells: Set[Cell] = {c for c in cells if not inb(c)}
    keep_edges: List[Edge] = []
    ripped_edges: List[Edge] = []
    pins: Set[Cell] = set()
    for a, b in edges:
        ia, ib = inb(a), inb(b)
        if not ia and not ib:
            keep_edges.append((a, b))
        elif ia and ib:
            ripped_edges.append((a, b))
        else:
            pins.add(a if ia else b)
            keep_edges.append((a, b))
    keep_cells |= pins
    ripped_cells = {c for c in cells if inb(c)} - pins
    in_box_terms = [set(t) for t in terminals if any(inb(c) for c in t)]
    return BoxExtract(keep_cells, keep_edges, pins, in_box_terms, ripped_cells, ripped_edges)


# --- M1: faithful per-box maze (spec section 3 / 3.1) ------------------------

@dataclass
class BoxCost:
    """Per-worker maze cost state (spec section 3.1). Three DISTINCT classes:
    HARD legality (impassable) vs additive routeShape vs additive fixedShape,
    plus the per-cell marker counter (BOOLEAN in the cost, spec section 3)."""
    hard: Set[Cell] = field(default_factory=set)          # wall + extra impassable
    route_shape: Set[Cell] = field(default_factory=set)   # foreign signal routes -> ggDRCCost
    fixed_shape: Set[Cell] = field(default_factory=set)   # fixed obstacles -> ggFixedShapeCost
    marker: Mapping[Cell, int] = field(default_factory=dict)  # marker counter (bool in cost)
    gg_drc: float = 0.0
    gg_marker: float = 0.0
    gg_fixed: float = 0.0


_DELTA_DIR = {(1, 0): "E", (-1, 0): "W", (0, 1): "N", (0, -1): "S"}


def _dir(a: Cell, b: Cell) -> str:
    if a[2] != b[2]:
        return "U" if b[2] > a[2] else "D"
    return _DELTA_DIR.get((b[0] - a[0], b[1] - a[1]), "?")


def _cell_legal(g: CapacityGrid, net: str, cell: Cell, bc: BoxCost) -> bool:
    # HARD legality only: in-bounds, _wire_ok (foreign pad/channel), and the
    # worker's hard set (confinement wall + foreign rerouting-net pins as pads).
    return (g.in_bounds(cell[0], cell[1])
            and _wire_ok(g, cell[0], cell[1], cell[2], net)
            and cell not in bc.hard)


def _attach_rust_grid(g: CapacityGrid) -> None:
    """Build the persistent Rust box_maze Grid ONCE from g's constant legality
    (wire_blocked_all + pad_owner + via_blocked + via_index, all fixed during
    route_flexdr) and attach it + a net->id map to g. No-op if the kernel is
    absent. The per-box cost classes (hard/routeShape/marker/fixedShape) are NOT
    here -- they are passed per box_maze call. Net names (incl. the _MULTI_OWNER
    sentinel) map to ints; a routing net that is not a pad owner gets a fresh id
    on first use, distinct from every pad owner -> foreign pads block it."""
    if _BOXMAZE_RS is None:
        return
    net_id: Dict[str, int] = {}
    nxt = 0
    pad_owner_list: List[Tuple[int, int, int, int]] = []
    for li, cells in (g.pad_owner or {}).items():
        for (ix, iy), owner in cells.items():
            oid = net_id.get(owner)
            if oid is None:
                oid = nxt; net_id[owner] = oid; nxt += 1
            pad_owner_list.append((li, ix, iy, oid))
    blocked_list = [(li, ix, iy) for li, cells in g.wire_blocked_all.items() for (ix, iy) in cells]
    via_blocked_list = [(ix, iy) for (ix, iy) in g.via_blocked]
    via_index = [(int(a), int(b), float(c)) for (a, b, c) in g.via_index()]
    g._rust_grid = _BOXMAZE_RS.Grid(int(g.nx), int(g.ny), len(g.layers),
                                    blocked_list, pad_owner_list, via_blocked_list, via_index)
    g._net_id = net_id
    g._net_id_next = [nxt]


def _net_id(g, name: str) -> int:
    """Get-or-assign this net's stable int id in g._net_id (shared by the Rust
    box_maze + occ, so a net's maze id and its occ key always agree)."""
    nid = g._net_id.get(name)
    if nid is None:                                   # routing net with no pad
        nid = g._net_id_next[0]
        g._net_id[name] = nid
        g._net_id_next[0] = nid + 1
    return nid


def _cells_i(cells) -> List[Tuple[int, int, int]]:
    return [(int(a), int(b), int(c)) for (a, b, c) in cells]


def _edges_i(edge_list):
    return [((int(a[0]), int(a[1]), int(a[2])), (int(b[0]), int(b[1]), int(b[2])))
            for (a, b) in edge_list]


def _rust_box_maze(g, rg, net, starts, goals, bc, corridor, GC) -> Optional[List[Cell]]:
    nid = _net_id(g, net)
    marker_pos = [c for c, v in bc.marker.items() if v > 0]
    # Stage 3b: once occ is initialized (worker passes), box_maze reads routeShape
    # from the persistent occupancy and ignores the (empty) route_shape arg. During
    # initial routing occ is not ready -> use the explicit route_shape (placed_fp).
    use_occ = bool(getattr(g, "_occ_ready", False))
    path = rg.box_maze(int(nid), list(starts), list(goals),
                       list(bc.hard), list(bc.route_shape), marker_pos, list(bc.fixed_shape),
                       float(bc.gg_drc), float(bc.gg_marker), float(bc.gg_fixed),
                       list(corridor), int(GC), use_occ)
    return None if path is None else [(int(a), int(b), int(c)) for (a, b, c) in path]


def _init_rust_occ(g, routes, edges, wire_halo, via_halo, supply=()) -> None:
    """Build the persistent Rust occupancy from the global SIGNAL routes (once,
    after initial routing). occ then tracks routeShape; the in-Rust worker reads it
    (global occ + a per-worker local signed-delta) and merges accepted deltas back
    in box order. SUPPLY nets are excluded -- they are hard keep-outs (the worker
    still builds their footprint into ``hard``), not additive routeShape. No-op
    without the kernel."""
    rg = getattr(g, "_rust_grid", None)
    if rg is None:
        return
    supply_set = set(supply)
    nets = [(_net_id(g, nm), _cells_i(cells), _cells_i(_via_cells(edges.get(nm, ()))))
            for nm, cells in routes.items() if nm not in supply_set]
    rg.occ_init(nets, int(wire_halo), int(via_halo))
    g._occ_ready = True


def _init_rust_routes(g, routes, edges) -> None:
    """Stage 3c: load the global routes (signal AND supply -- flexgc covers every
    net) into the persistent Rust store, in routes-dict order, so a worker box's
    ``box_markers`` reads them IN PLACE (only the box's few changed nets are
    marshaled per call) instead of re-marshaling all routes every flexgc call (the
    ~2.9s/add4 cost). The worker keeps the store in sync via ``rg.routes_update``
    on each committed global change. Also builds the id->name inverse so the Rust
    worker's marker sources (global net ids) map back to names. No-op without the
    kernel."""
    rg = getattr(g, "_rust_grid", None)
    if rg is None:
        return
    nets = [(_net_id(g, nm), _cells_i(cells), _edges_i(edges.get(nm, ())))
            for nm, cells in routes.items()]
    rg.routes_init(nets)
    g._id_to_net = {i: nm for nm, i in g._net_id.items()}
    g._routes_ready = True


def _rust_flexgc(rg, routes, edges, wire_halo, via_halo, region, prl_halo, prl_len,
                 dir_by_li, n_layers) -> List["Marker"]:
    """Marshal a flexgc_lite call to the Rust kernel. Nets are passed in
    ``routes`` dict order (the order Python's cover/owner first-touch depends on)
    so the returned marker LIST ORDER matches the Python reference byte-for-byte.
    Net names map to local ints (built + consumed here); sources come back as ids
    and map straight back to names (route_box sorts sources by NAME, so id order
    within a marker is irrelevant)."""
    name_to_id: Dict[str, int] = {}
    id_to_name: List[str] = []
    nets = []
    for nm, cells in routes.items():
        nid = name_to_id.get(nm)
        if nid is None:
            nid = len(id_to_name); name_to_id[nm] = nid; id_to_name.append(nm)
        via = _via_cells(edges.get(nm, ()))
        nets.append((nid,
                     [(int(c[0]), int(c[1]), int(c[2])) for c in cells],
                     [(int(c[0]), int(c[1]), int(c[2])) for c in via]))
    vertical = [bool((dir_by_li or {}).get(li, "H") == "V") for li in range(n_layers)]
    reg = None if region is None else (int(region[0]), int(region[1]),
                                       int(region[2]), int(region[3]))
    raw = rg.flexgc(nets, int(wire_halo), int(via_halo), reg,
                    int(prl_halo), int(prl_len), vertical)
    return [Marker(frozenset((int(a), int(b), int(c)) for (a, b, c) in cells),
                   int(layer), frozenset(id_to_name[s] for s in sources))
            for (cells, layer, sources) in raw]


def box_maze(g: CapacityGrid, net: str, starts: Sequence[Cell], goals: Set[Cell],
             bc: BoxCost, corridor: Set[GCell], GC: int, via_halo: int) -> Optional[List[Cell]]:
    """A* over BoxCost confined to ``corridor`` gcells. ADDITIVE edge cost
    (spec section 3): unit edgeLength + EXACT bend + via cost + boolean
    ggDRC/ggMarker/ggFixed adj. HARD cells (``_cell_legal``) are impassable.
    State = (cell, entry_dir) so the bend is exact. Returns the cell path or None.

    If a Rust kernel grid is attached to ``g`` (``_attach_rust_grid``, set up in
    route_flexdr), delegate to it -- byte-parity with this Python reference is the
    contract (same A* + (f, cost, ix, iy, layer, dir) tie-break). via_halo is not
    passed: the maze ignores the halo _neighbors yields (only nxt + via extra)."""
    rg = getattr(g, "_rust_grid", None)
    if rg is not None:
        return _rust_box_maze(g, rg, net, starts, goals, bc, corridor, GC)
    legal_goals = {c for c in goals if _cell_legal(g, net, c, bc)}
    legal_starts = [s for s in starts if _cell_legal(g, net, s, bc)]
    if not legal_goals or not legal_starts:
        return None
    allowed = set(corridor)
    for c in list(legal_starts) + list(legal_goals):
        allowed.add((c[0] // GC, c[1] // GC))

    best: Dict[Tuple[Cell, Optional[str]], float] = {}
    came: Dict[Tuple[Cell, Optional[str]], Tuple[Cell, Optional[str]]] = {}
    heap: List[Tuple[float, float, Tuple[Cell, Optional[str]]]] = []
    for s in legal_starts:
        st = (s, None)
        best[st] = 0.0
        heappush(heap, (_heuristic(s, legal_goals), 0.0, st))
    done: Set[Tuple[Cell, Optional[str]]] = set()
    while heap:
        _, cost, state = heappop(heap)
        if state in done:
            continue
        done.add(state)
        cell, d = state
        if cell in legal_goals:
            return _reconstruct_cells(came, state)
        for nxt, _base, extra, _halo in _neighbors(g, cell, net, via_halo):
            if nxt not in legal_goals and (nxt[0] // GC, nxt[1] // GC) not in allowed:
                continue
            if not _cell_legal(g, net, nxt, bc):
                continue
            nd = _dir(cell, nxt)
            step = 1.0                              # edgeLength
            if d is not None and nd != d:           # exact bend
                step += 1.0
            if nxt[2] != cell[2]:                   # via cost
                step += extra
            if nxt in bc.route_shape:               # additive routeShape (bool)
                step += bc.gg_drc
            if bc.marker.get(nxt, 0) > 0:           # additive marker (bool)
                step += bc.gg_marker
            if nxt in bc.fixed_shape:               # additive fixedShape (bool)
                step += bc.gg_fixed
            ncost = cost + step
            ns = (nxt, nd)
            if ncost + 1e-12 < best.get(ns, float("inf")):
                best[ns] = ncost
                came[ns] = state
                heappush(heap, (ncost + _heuristic(nxt, legal_goals), ncost, ns))
    return None


def _reconstruct_cells(came, end_state) -> List[Cell]:
    path = [end_state]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    return [cell for (cell, _d) in path]


# --- FlexPA-lite: on-grid pin access (spec FlexPA section) --------------------

def _cells_in_box(g, x1, y1, x2, y2, li):
    """Grid cells (on layer li) whose CENTER lies inside the um box -- the
    on-grid / on-track access points within a pin's pad metal."""
    gx1, gy1 = g.cell_of(round(x1 * 1000), round(y1 * 1000))
    gx2, gy2 = g.cell_of(round(x2 * 1000), round(y2 * 1000))
    out: Set[Cell] = set()
    for ix in range(min(gx1, gx2), max(gx1, gx2) + 1):
        for iy in range(min(gy1, gy2), max(gy1, gy2) + 1):
            if g.in_bounds(ix, iy):
                cx, cy = g.cx(ix) / 1000.0, g.cy(iy) / 1000.0
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    out.add((ix, iy, li))
    return out


def _cells_overlapping_pad(g, x1, y1, x2, y2, li, w):
    """Grid cells (on layer li) whose drawn WIRE BOX (side w, centred on the
    cell) overlaps the pin pad metal [x1,y1,x2,y2]. This is the faithful FlexPA
    'access point must be ON the pin' guarantee for the grid model: an AP here,
    realized as a wire box, physically overlaps the pad -> LVS-connected, even
    when the pad is smaller than the pitch so no cell CENTRE lands inside it
    (OpenROAD genAPEnclosedBoundary: APs are clipped to the pin boundary). Search
    one extra ring (ceil(w/2/pitch)) around the pad so boundary cells qualify."""
    h = w / 2.0
    pitch = max(g.pitch_nm / 1000.0, 1e-6)
    pad = max(1, int(__import__("math").ceil(h / pitch)))
    gx1, gy1 = g.cell_of(round(x1 * 1000), round(y1 * 1000))
    gx2, gy2 = g.cell_of(round(x2 * 1000), round(y2 * 1000))
    out: Set[Cell] = set()
    for ix in range(min(gx1, gx2) - pad, max(gx1, gx2) + pad + 1):
        for iy in range(min(gy1, gy2) - pad, max(gy1, gy2) + pad + 1):
            if not g.in_bounds(ix, iy):
                continue
            cx, cy = g.cx(ix) / 1000.0, g.cy(iy) / 1000.0
            ox = min(cx + h, x2) - max(cx - h, x1)
            oy = min(cy + h, y2) - max(cy - h, y1)
            if ox > 1e-9 and oy > 1e-9:
                out.add((ix, iy, li))
    return out


def _ap_escapable(g, c, net, pb):
    """Planar escape (FlexPA filterPlanarAccess): a legal same-layer neighbour
    whose center is OUTSIDE the pin's pad box -- i.e. a wire can come out of the
    pad here. Boxed-in interior APs (e.g. over a device channel) fail this."""
    ix, iy, li = c
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = ix + dx, iy + dy
        if not g.in_bounds(nx, ny) or not _wire_ok(g, nx, ny, li, net):
            continue
        cx, cy = g.cx(nx) / 1000.0, g.cy(ny) / 1000.0
        if not (pb[0] <= cx <= pb[2] and pb[1] <= cy <= pb[3]):
            return True
    return False


def _ap_via(g, c, net):
    """Via access (FlexPA checkViaPlanarAccess): a via may land here (not via
    -blocked) and the adjacent routing layer over the pad is routable -- the
    route reaches the pin via a via from an adjacent layer's track."""
    ix, iy, li = c
    if (ix, iy) in g.via_blocked:
        return False
    for vr in g.via_rules:
        try:
            a, b = g.layers.index(vr.a), g.layers.index(vr.b)
        except ValueError:
            continue
        other = b if li == a else a if li == b else None
        if other is not None and _wire_ok(g, ix, iy, other, net):
            return True
    return False


def flexpa_access_nets(g, netlist, placement, device_pads, terms, *, route_only=None,
                       wire_width_um: float = 0.0):
    """Faithful FlexPA pin access (spec section 6b). The harvested terminal's
    launch ORIENTATION is the access DIRECTION: access_point = pad center +
    length/2 along it = the pad edge FACING the routing channel. The chosen AP =
    the grid cell nearest that launch-edge point that is INSIDE the pad metal and
    legal -> ON the pad (LVS-connected) yet approached from the open channel
    (0-marker), not by burrowing into the packed device row. Prefer APs with a
    planar escape / via access; fall back to nearest legal in-pad cell, else
    Center. Returns NetInputs with terminal_cells (one chosen AP per pin)."""
    import math
    out: List[NetInput] = []
    for net in netlist["nets"]:
        nm = net["net_id"]
        if route_only is not None and nm not in route_only:
            continue
        tcells: List[Set[Cell]] = []
        for ref in net["terminals"]:
            xi, t = ref.rsplit(".", 1)
            cell, dx, dy = placement[xi]
            layer, (x1, y1, x2, y2) = device_pads[cell][t]
            li = g.layers.index(layer)
            pb = (dx + x1, dy + y1, dx + x2, dy + y2)
            td = terms[cell][t]                    # access direction = launch orientation
            ori = math.radians(td["orientation"]); half = td["length"] / 2.0
            ax = dx + td["center"][0] + math.cos(ori) * half
            ay = dy + td["center"][1] + math.sin(ori) * half
            # On-grid APs (cell centre inside the pad) are strongest; when the
            # pad is smaller than the pitch (no centre lands inside) fall back to
            # cells whose WIRE BOX overlaps the pad -> still ON the pin (faithful
            # FlexPA pin-access guarantee; a centre-fallback cell could miss the
            # pad metal entirely -> LVS open).
            legal = {c for c in _cells_in_box(g, pb[0], pb[1], pb[2], pb[3], li)
                     if _wire_ok(g, c[0], c[1], c[2], nm)}
            if not legal:
                w_ap = wire_width_um if wire_width_um > 0 else g.pitch_nm / 1000.0
                overlap = _cells_overlapping_pad(g, pb[0], pb[1], pb[2], pb[3], li, w_ap)
                legal = {c for c in overlap if _wire_ok(g, c[0], c[1], c[2], nm)}
                if not legal and overlap:
                    # A sub-pitch pad whose every overlapping cell is globally
                    # wire-blocked: build_grid registered NO pad cell for it
                    # (registration is by centre-in-pad), so the pin is an
                    # invisible obstruction. Open ONE on-pad cell for THIS net
                    # (it is the pin's own metal) and register it as this net's
                    # pad so foreign nets stay blocked (no short). This is the
                    # grid analogue of FlexPA guaranteeing a reachable AP on the
                    # pin (genAPEnclosedBoundary + checkViaPlanarAccess).
                    # Prefer a foreign-free overlapping cell; if the chosen cell is
                    # no net's REAL pad metal -- only foreign clearance HALOs claim
                    # it (a sub-pitch pad whose access lands in a neighbour's halo)
                    # -- strip those owners so this pin is reachable. A foreign REAL
                    # pad here is a genuine conflict: leave it.
                    free = [c for c in overlap
                            if not any((c[0], c[1]) in cs
                                       for o, cs in g.pad_cells.get(li, {}).items() if o != nm)]
                    bx = min(free or overlap, key=lambda c: abs(g.cx(c[0]) / 1000.0 - ax)
                             + abs(g.cy(c[1]) / 1000.0 - ay))
                    g.wire_blocked_all.get(li, set()).discard((bx[0], bx[1]))
                    if getattr(g, "real_pad_owner", {}).get(li, {}).get((bx[0], bx[1]), nm) == nm:
                        for o in list(g.pad_cells.get(li, {})):
                            if o != nm:
                                g.pad_cells[li][o].discard((bx[0], bx[1]))
                    g.pad_cells.setdefault(li, {}).setdefault(nm, set()).add((bx[0], bx[1]))
                    legal = {bx}
            cand = ({c for c in legal if _ap_escapable(g, c, nm, pb) or _ap_via(g, c, nm)}
                    or legal)
            if cand:                               # nearest pad-overlapping cell to launch-edge AP
                best = min(cand, key=lambda c: abs(g.cx(c[0]) / 1000.0 - ax)
                           + abs(g.cy(c[1]) / 1000.0 - ay))
                tcells.append({best})
        if tcells:
            out.append(NetInput(nm, terminal_cells=tcells))
    return out


# --- M2: faithful worker route_queue (spec section 4) ------------------------

def _connect_targets(g, net, target_sets, bc: BoxCost, corridor, GC, via_halo):
    """Connect a net's target cell-sets into one tree via the additive box_maze.
    Returns (cells, edges) or None if a connection fails. <2 targets -> nothing
    to route."""
    targets = [set(t) for t in target_sets if t]
    if len(targets) < 2:
        return set(), []
    # First connection: ANY access point of terminal0 to ANY of terminal1
    # (box_maze is multi-start/multi-goal). Only the CHOSEN APs (path cells)
    # enter the tree -- never the whole candidate AP set -- so a multi-AP pin
    # does not make the net claim every pad cell (faithful FlexPA: pick one AP).
    if targets[0] & targets[1]:
        tree: Set[Cell] = set(targets[0]) | set(targets[1])
        edges: List[Edge] = []
    else:
        path = box_maze(g, net, sorted(targets[0]), targets[1], bc, corridor, GC, via_halo)
        if path is None:
            return None
        tree = set(path)
        edges = list(zip(path, path[1:]))
    for tgt in targets[2:]:
        if tree & tgt:
            continue
        path = box_maze(g, net, sorted(tree), tgt, bc, corridor, GC, via_halo)
        if path is None:
            return None
        edges.extend(zip(path, path[1:]))
        tree.update(path)
    return tree, edges


def _route_net_in_box(g, net, ex: BoxExtract, bc: BoxCost, corridor, GC, via_halo):
    """Reroute one net's IN-BOX portion: connect boundary pins + in-box terminals
    via the additive box_maze (keep handles the out-of-box part).

    Pins are SORTED before becoming target singletons. ``_connect_targets``
    connects targets in sequence (target0->target1, then each remaining to the
    growing tree), so the target ORDER changes the route -- and ``ex.pins`` is a
    set whose Python iteration order is not portable to Rust. Sorting makes the
    target sequence deterministic/portable, the prerequisite for the byte-parity
    Rust worker (Stage 3c-1b); proven route-changing by _pins_order_probe, so the
    oracle was re-frozen to this canonical order."""
    return _connect_targets(g, net, [{p} for p in sorted(ex.pins)] + ex.in_box_terms,
                            bc, corridor, GC, via_halo)


def route_box(g: CapacityGrid, box: Box, GC: int,
              routes: Mapping[str, Sequence[Cell]], edges: Mapping[str, Sequence[Edge]],
              termsets: Mapping[str, Sequence[Set[Cell]]], wire_halo: int, via_halo: int,
              *, ripup_mode: str = "DRC", maze_end_iter: int = 8,
              gg_drc: float = 8.0, gg_marker: float = 8.0, gg_fixed: float = 32.0,
              marker_decay: float = 0.95, supply: Sequence[str] = (),
              obstacles: Set[Cell] = frozenset(),
              prl_halo: int = 0, prl_len: int = 0, dir_by_li=None):
    """Faithful FlexDR worker on one box (spec section 4). Three boxes
    (route/ext/drc), QUEUE-DRIVEN rip-up+reroute, FlexGC-lite markers drive
    marker cost (boolean, escalated by the schedule's gg_*) + decay, best-route
    tracking, commit-if-not-worse (DRC best<=init, ALL best<=5*init). Foreign
    SIGNAL nets are ADDITIVE route_shape (passable), never hard-walled
    (convergence-critical, spec section 3.1). Returns (new_routes, new_edges) for
    the nets it changed if committed, else None (keep the box's original).

    This is the pure-Python reference / graceful fallback used when the Rust
    kernel is absent; when it is present, route_flexdr runs the in-Rust parallel
    worker (``Grid.route_boxes``) instead."""
    rx0, ry0, rx1, ry1 = box_cell_bounds(g, box, GC)
    dm = max(wire_halo, via_halo, prl_halo) + 1
    drc_region = (max(0, rx0 - dm), max(0, ry0 - dm),
                  min(g.nx - 1, rx1 + dm), min(g.ny - 1, ry1 + dm))
    corridor = {(gx, gy) for gx in range(box.gx0, box.gx1 + 1)
                for gy in range(box.gy0, box.gy1 + 1)}
    supply_set = set(supply)

    R: Dict[str, List[Cell]] = {k: list(v) for k, v in routes.items()}
    E: Dict[str, List[Edge]] = {k: list(v) for k, v in edges.items()}
    marker_count: DefaultDict[Cell, float] = defaultdict(float)

    def box_markers():
        return flexgc_lite(g, R, E, wire_halo, via_halo, region=drc_region,
                           prl_halo=prl_halo, prl_len=prl_len, dir_by_li=dir_by_li)

    def in_box(nm):
        return any(cell_in_box(c, box, GC) for c in R.get(nm, ()))

    def can_ripup(nm):
        return nm not in supply_set and numReroute[nm] < maze_end_iter

    numReroute: DefaultDict[str, int] = defaultdict(int)
    init_markers = box_markers()
    init_n = len(init_markers)
    # FlexDR route_queue calls route_queue_addMarkerCost() at worker START on the
    # pre-existing markers (FlexDR_maze.cpp:1751), so the worker begins with cost
    # on the existing violation cells -> ripped nets avoid them instead of
    # re-picking the same conflicting route. Seed marker_count from init markers.
    for m in init_markers:
        for c in m.cells:
            marker_count[c] += 1.0

    queue: "deque[str]" = deque()
    queued: Set[str] = set()

    def enqueue(nm):
        if nm in queued or not can_ripup(nm) or not in_box(nm):
            return
        queued.add(nm); queue.append(nm)

    if ripup_mode == "ALL":
        for nm in sorted(n for n in R if in_box(n) and len(termsets.get(n, ())) >= 2):
            enqueue(nm)
    else:  # DRC: movable (non-supply, rippable) sources of each marker
        # Enqueue in a CANONICAL order over the SET of sources, independent of the
        # marker LIST order (which the from-scratch checker emits but a persistent
        # incremental cover cannot reproduce). Marker order proven to
        # change routes via this enqueue sequence; decoupling it (Stage 3b) frees
        # flexgc to return markers in any order. marker_count bumps stay where
        # they are (commutative). Re-frozen oracle = the new byte-parity baseline.
        for nm in sorted({s for m in init_markers for s in m.sources
                          if s not in supply_set}):
            enqueue(nm)

    while queue:
        nm = queue.popleft()
        queued.discard(nm)
        if not can_ripup(nm):
            continue
        numReroute[nm] += 1
        ex = extract_box(R[nm], E.get(nm, []), termsets[nm], box, GC)
        # foreign SIGNAL nets -> additive route_shape (passable); supply -> hard.
        route_shape: Set[Cell] = set()
        hard: Set[Cell] = set(obstacles)
        for o, cells in R.items():
            if o == nm:
                continue
            if o in supply_set:
                hard |= _net_footprint(g, cells, E.get(o, []), wire_halo, via_halo)
            else:
                route_shape |= _net_footprint(g, cells, E.get(o, []), wire_halo, via_halo)
        bc = BoxCost(hard=hard, route_shape=route_shape, fixed_shape=set(),
                     marker={c: 1 for c, v in marker_count.items() if v >= 0.5},
                     gg_drc=gg_drc, gg_marker=gg_marker, gg_fixed=gg_fixed)
        routed = _route_net_in_box(g, nm, ex, bc, corridor, GC, via_halo)
        if routed is None:
            continue                       # keep old route for this net
        cells, ne = routed
        R[nm] = sorted(set(ex.keep_cells) | set(cells))
        E[nm] = list(ex.keep_edges) + ne

        ms = box_markers()
        for m in ms:                       # FlexGC -> marker cost (order-free)
            for c in m.cells:
                marker_count[c] += 1.0
        for src in sorted({s for m in ms for s in m.sources  # requeue: canonical
                           if s not in supply_set}):         # order over the SET
            enqueue(src)
        for c in list(marker_count):       # decay
            marker_count[c] *= marker_decay
        if not ms:
            break                          # box is clean -> stop

    # commit per FlexDR_end.cpp:682. OpenROAD saves the FINAL worker state
    # (setBestMarkers at route_queue end) and commits if NOT WORSE than the box's
    # initial: DRC/NEARDRC require final <= init (committing an EQUAL count lets a
    # marker RELOCATE so a later offset/box can kill it -- the escape, NOT a
    # strict improvement); ALL may commit up to 5x init.
    final_n = len(box_markers())
    limit = 5 * init_n if ripup_mode == "ALL" else init_n
    changed = {nm for nm in R if R[nm] != list(routes.get(nm, []))}
    accept = final_n <= limit and bool(changed)
    if not accept:
        return None
    return ({nm: R[nm] for nm in changed}, {nm: E[nm] for nm in changed})


# --- M1: FlexGC-lite (markers WITH sources, spec section 6) ------------------

class Marker(NamedTuple):
    cells: frozenset      # the violating cell(s)
    layer: int
    sources: frozenset    # net names sharing the violation (victim + aggressor)


def _in_region(c, region) -> bool:
    return region is None or (
        region[0] <= c[0] <= region[2] and region[1] <= c[1] <= region[3])


def _prl_markers(g, routes, dir_by_li, prl_halo: int, prl_len: int, region) -> List[Marker]:
    """Parallel-run-length spacing (OpenROAD SPACINGTABLE PARALLELRUNLENGTH): two
    DIFFERENT-net wires on the same layer that run PARALLEL (along the layer's
    preferred direction) within ``prl_halo`` perpendicular tracks for a contiguous
    run of >= ``prl_len`` cells violate the wide (prl) spacing. Short adjacencies
    and crossings (run < prl_len) are allowed. One Marker per violating run,
    carrying both run tracks' cells + the two source nets."""
    if prl_halo <= 0 or prl_len <= 0:
        return []
    owner: Dict[Cell, str] = {}
    for nm, cells in routes.items():
        for c in cells:
            owner.setdefault(c, nm)            # multi-owner overlaps -> short markers
    runs: DefaultDict[Tuple, Set[int]] = defaultdict(set)
    for (ix, iy, li), a in owner.items():
        vertical = (dir_by_li or {}).get(li, "H") == "V"
        run_c = iy if vertical else ix         # coord ALONG the wire direction
        sep = ix if vertical else iy           # perpendicular (track) coord
        for k in range(1, prl_halo + 1):
            c2 = (ix + k, iy, li) if vertical else (ix, iy + k, li)
            b = owner.get(c2)
            if b is not None and b != a:
                runs[(li, vertical, sep, sep + k, frozenset((a, b)))].add(run_c)
    markers: List[Marker] = []
    for (li, vertical, s0, s1, pair), coords in runs.items():
        for seg in _contiguous_runs(sorted(coords)):
            if len(seg) < prl_len:
                continue
            cells = set()
            for rc in seg:
                for s in (s0, s1):
                    cell = (s, rc, li) if vertical else (rc, s, li)
                    if _in_region(cell, region):
                        cells.add(cell)
            if cells:
                markers.append(Marker(frozenset(cells), li, pair))
    return markers


def _prl_params(g: CapacityGrid, profile, width_um: float):
    """(prl_halo, prl_len_cells, dir_by_li) from the profile's parallel-run-length
    spacing rule, or (0, 0, None) when disabled. prl_halo = perpendicular tracks
    within which a parallel run violates: edge gap = d*pitch - width < prl_spacing
    -> d < (width+prl_spacing)/pitch, so halo = ceil((width+prl_spacing)/pitch)-1.
    prl_len = the run length (cells) above which the wide spacing applies."""
    from math import ceil
    pitch = g.pitch_nm / 1000.0
    ps = getattr(profile, "prl_spacing_um", 0.0) if profile is not None else 0.0
    pl = getattr(profile, "prl_length_um", 0.0) if profile is not None else 0.0
    if pitch <= 0 or ps <= 0 or pl <= 0:
        return 0, 0, None
    w = width_um or getattr(profile, "wire_width_um", 0.0)
    prl_halo = max(0, ceil((w + ps) / pitch) - 1)
    prl_len = max(1, ceil(pl / pitch))
    dir_by_li = {i: profile.layer_direction(layer) for i, layer in enumerate(g.layers)}
    return prl_halo, prl_len, dir_by_li


def _contiguous_runs(sorted_coords: Sequence[int]) -> List[List[int]]:
    out: List[List[int]] = []
    for c in sorted_coords:
        if out and c == out[-1][-1] + 1:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def flexgc_lite(g: CapacityGrid, routes: Mapping[str, Sequence[Cell]],
                edges: Mapping[str, Sequence[Edge]], wire_halo: int, via_halo: int,
                region: Optional[Tuple[int, int, int, int]] = None,
                *, prl_halo: int = 0, prl_len: int = 0, dir_by_li=None) -> List[Marker]:
    """[KLINK-APPROX -- interface faithful, rule coverage NOT faithful] grid
    FlexGC: a marker = a cell where >=2 nets' footprints overlap (short/spacing),
    PLUS parallel-run-length spacing markers (``_prl_markers``) when ``prl_halo``/
    ``prl_len`` are set. Returns Markers carrying ``sources`` (the victim +
    aggressor nets) so the worker queue can enqueue both (spec section 6).
    ``region`` = (cx0,cy0,cx1,cy1) cell bounds to restrict to a drcBox; None =
    whole grid.

    If a Rust kernel grid is attached to ``g`` (``_attach_rust_grid``), delegate to
    its ``flexgc`` -- byte-parity with this Python reference is the contract,
    including the marker LIST ORDER (proven to change routes)."""
    rg = getattr(g, "_rust_grid", None)
    if rg is not None:
        return _rust_flexgc(rg, routes, edges, wire_halo, via_halo, region,
                            prl_halo, prl_len, dir_by_li, len(g.layers))
    cover: DefaultDict[Cell, Set[str]] = defaultdict(set)
    for nm, cells in routes.items():
        via = _via_cells(edges.get(nm, ()))
        for c in cells:
            for f in _footprint(g, c, via_halo if c in via else wire_halo):
                if _in_region(f, region):
                    cover[f].add(nm)
    markers = [Marker(frozenset([cell]), cell[2], frozenset(srcs))
               for cell, srcs in cover.items() if len(srcs) > 1]
    markers.extend(_prl_markers(g, routes, dir_by_li, prl_halo, prl_len, region))
    return markers


# --- M3: schedule loop (spec section 2/5, port of FlexDR::strategy) ----------

# (size, offset, mazeEndIter, drcMult, markerMult, fixedShapeMult, markerDecay, mode)
# Verbatim port of dr/FlexDR.cpp:1573 strategy(); NEARDRC mapped to DRC [KLINK-APPROX].
_STRATEGY: List[Tuple[int, int, int, int, int, int, float, str]] = [
    (7, 0, 3, 1, 0, 1, 0.950, "ALL"), (7, -2, 3, 1, 1, 1, 0.950, "ALL"),
    (7, -5, 3, 1, 1, 1, 0.950, "ALL"),
    (7, 0, 8, 1, 1, 2, 0.950, "DRC"), (7, -1, 8, 1, 1, 2, 0.950, "DRC"),
    (7, -2, 8, 1, 1, 2, 0.950, "DRC"), (7, -3, 8, 1, 1, 2, 0.950, "DRC"),
    (7, -4, 8, 1, 1, 2, 0.950, "DRC"), (7, -5, 8, 1, 1, 2, 0.950, "DRC"),
    (7, -6, 8, 1, 1, 2, 0.950, "DRC"),
    (7, 0, 8, 2, 1, 3, 0.950, "DRC"), (7, -1, 8, 2, 1, 3, 0.950, "DRC"),
    (7, -2, 8, 2, 1, 3, 0.950, "DRC"), (7, -3, 8, 2, 1, 3, 0.950, "DRC"),
    (7, -4, 8, 2, 1, 3, 0.950, "DRC"), (7, -5, 8, 2, 1, 4, 0.950, "DRC"),
    (7, -6, 8, 2, 1, 4, 0.950, "DRC"),
    (7, -3, 8, 1, 1, 4, 0.950, "ALL"),
    (7, 0, 8, 4, 1, 4, 0.950, "DRC"), (7, -1, 8, 4, 1, 4, 0.950, "DRC"),
    (7, -2, 8, 4, 1, 10, 0.950, "DRC"), (7, -3, 8, 4, 1, 10, 0.950, "DRC"),
    (7, -4, 8, 4, 1, 10, 0.950, "DRC"), (7, -5, 8, 1, 1, 10, 0.950, "DRC"),
    (7, -6, 8, 4, 1, 10, 0.950, "DRC"),
    (5, -2, 8, 1, 1, 10, 0.950, "ALL"),
    (7, 0, 8, 8, 2, 10, 0.950, "DRC"), (7, -1, 8, 8, 2, 10, 0.950, "DRC"),
    (7, -2, 8, 8, 2, 10, 0.950, "DRC"), (7, -3, 8, 8, 2, 10, 0.950, "DRC"),
    (7, -4, 8, 1, 1, 50, 0.950, "DRC"), (7, -5, 8, 8, 2, 50, 0.950, "DRC"),
    (7, -6, 8, 8, 2, 50, 0.950, "DRC"),
    (3, -1, 8, 1, 1, 50, 0.950, "ALL"),
    (7, 0, 8, 16, 4, 50, 0.950, "DRC"), (7, -1, 8, 16, 4, 50, 0.950, "DRC"),
    (7, -2, 8, 16, 4, 50, 0.950, "DRC"), (7, -3, 8, 1, 1, 50, 0.950, "DRC"),
    (7, -4, 8, 16, 4, 50, 0.950, "DRC"), (7, -5, 8, 16, 4, 50, 0.950, "DRC"),
    (7, -6, 8, 16, 4, 100, 0.990, "DRC"),
    (3, -2, 8, 1, 1, 100, 0.990, "ALL"),
    (7, 0, 16, 16, 4, 100, 0.990, "DRC"), (7, -1, 16, 16, 4, 100, 0.990, "DRC"),
    (7, -2, 16, 1, 1, 100, 0.990, "DRC"), (7, -3, 16, 16, 4, 100, 0.990, "DRC"),
    (7, -4, 16, 16, 4, 100, 0.990, "DRC"), (7, -5, 16, 16, 4, 100, 0.990, "DRC"),
    (7, -6, 16, 16, 4, 100, 0.990, "DRC"),
    (3, 0, 8, 1, 1, 100, 0.990, "ALL"),
    (7, 0, 32, 32, 8, 100, 0.999, "DRC"), (7, -1, 32, 1, 1, 100, 0.999, "DRC"),
    (7, -2, 32, 32, 8, 100, 0.999, "DRC"), (7, -3, 32, 32, 8, 100, 0.999, "DRC"),
    (7, -4, 32, 32, 8, 100, 0.999, "DRC"), (7, -5, 32, 32, 8, 100, 0.999, "DRC"),
    (7, -6, 32, 32, 8, 100, 0.999, "DRC"),
    (3, -1, 8, 1, 1, 100, 0.999, "ALL"),
    (7, 0, 64, 1, 1, 100, 0.999, "DRC"), (7, -1, 64, 64, 16, 100, 0.999, "DRC"),
    (7, -2, 64, 64, 16, 100, 0.999, "DRC"), (7, -3, 64, 64, 16, 100, 0.999, "DRC"),
    (7, -4, 64, 64, 16, 100, 0.999, "DRC"), (7, -5, 64, 64, 16, 100, 0.999, "DRC"),
    (7, -6, 64, 64, 16, 100, 0.999, "DRC"),
]


def route_flexdr(
    g: CapacityGrid,
    nets: Sequence[NetInput],
    profile,
    GC: int,
    *,
    seed_routes: Optional[Mapping[str, Sequence[Cell]]] = None,
    seed_edges: Optional[Mapping[str, Sequence[Edge]]] = None,
    width_um: float = 0.0,
    wire_clear_um: float = 0.0,
    via_clear_um: float = 0.0,
    supply: Sequence[str] = (),
    obstacles: Set[Cell] = frozenset(),
    shape_base: float = 8.0,
    marker_base: float = 8.0,
    schedule: Optional[Sequence] = None,
    verbose: bool = False,
) -> RouteResult:
    """FlexDR detailed routing (spec section 5): complete initial routing, then
    the strategy() schedule of worker-box passes (checkerboard), stopping at 0
    markers. No global negotiation loop."""
    ordered = sorted(nets, key=lambda n: n.net)
    termsets = {n.net: _terminal_cellsets(g, n) for n in ordered}
    wire_halo, via_halo = _halos(g, width_um, wire_clear_um, via_clear_um)
    prl_halo, prl_len, dir_by_li = _prl_params(g, profile, width_um)
    ngx, ngy = _gcell_extent(g, GC)
    full_corr = {(gx, gy) for gx in range(ngx) for gy in range(ngy)}
    # O(1) per-cell pad-owner index for the maze hot path. pad_cells is FROZEN
    # during FlexDR (FlexPA mutated it before this call; the worker uses BoxCost
    # for per-box keep-outs, never pad_cells), so invert it ONCE here. _wire_ok
    # then does an O(1) owner lookup instead of an O(num_nets) scan -- that scan
    # was ~48% of route_flexdr time. (OpenROAD per-node-flag model; byte-parity
    # vs the scan is guarded by tests/unit/test_flexdr.py.)
    g.pad_owner = _invert_pad_cells(g.pad_cells)
    _attach_rust_grid(g)   # optional byte-parity Rust kernel (no-op if absent)

    def gc(rt=None, eg=None):
        return flexgc_lite(g, rt if rt is not None else routes,
                           eg if eg is not None else edges, wire_halo, via_halo,
                           prl_halo=prl_halo, prl_len=prl_len, dir_by_li=dir_by_li)

    # 1. complete initial routing: seed first, then route each remaining net
    #    within its grt GUIDE corridor (distributes nets across channel capacity,
    #    like OpenROAD FlexDR routing within grt guides) -> far fewer initial
    #    overlaps -> convergent. Falls back to the whole grid if grt is
    #    unavailable (e.g. profile=None in unit tests).
    # TA-0 baseline instrumentation: time the greedy seed phase + the initial
    # markers it leaves (the count FlexTA must drive down). All under `verbose`
    # -> zero routing behaviour change (the oracle runs verbose=False).
    import time as _ta0
    _seed_t0 = _ta0.time()
    routes: Dict[str, List[Cell]] = {k: sorted(v) for k, v in (seed_routes or {}).items() if v}
    edges: Dict[str, List[Edge]] = {k: list(v) for k, v in (seed_edges or {}).items()}
    placed_fp: Set[Cell] = set()
    for nm, cells in routes.items():
        placed_fp |= _net_footprint(g, cells, edges.get(nm, []), wire_halo, via_halo)
    todo = sorted((n for n in ordered if not routes.get(n.net)),
                  key=lambda n: (-len(termsets[n.net]), n.net))
    # FlexTA (track assignment) seed: replace the greedy net-by-net seed with a
    # track-assigned one for the nets it can handle (those with a usable grt
    # guide); the rest fall through to the greedy loop below. Opt-in via
    # FLEXDR_TA=1 (the greedy seed stays the default + fallback) -> the no-TA path
    # is byte-identical.
    import os as _os_ta
    if _os_ta.environ.get("FLEXDR_TA") == "1" and profile is not None:
        try:
            from klink.routing.backends.pnr_multilayer.pnr_flexta import flexta_seed
            guides = _grt_guides(g, ordered, termsets, GC, profile)
            if guides:
                # This engine: confine FlexTA backbones to the dedicated SIGNAL layers
                # (clean of device terminals) by filtering dir_by_li to them; pin
                # access still vias down to terminals on the full grid stack. The
                # frozen single-stack engine (signal_layers == routing_layers) ->
                # _ta_dir == dir_by_li, no-op.
                _sig = set(profile.signal_routing_layers())
                _ta_dir = {li: d for li, d in dir_by_li.items()
                           if g.layers[li] in _sig}
                ta_r, ta_e, handled = flexta_seed(
                    g, ordered, profile, GC, guides, termsets, _ta_dir,
                    wire_halo, verbose=verbose)
                # Stage D (D1): FlexTA supplies the track-assigned BACKBONE; each
                # pin's AP is then connected to it by a SHORT cross-layer access
                # (OpenROAD FlexDR: AP = routeNet terminal, maze connects it to the
                # net's existing routing, addApPathSegs writes the access pathseg).
                # Grounded measurement: the backbone is ~3 cells from every pin on
                # SOME layer, so we connect the AP to the NEAREST backbone within a
                # SMALL region (radius R gcells) and let box_maze drop a VIA -- not
                # the old full-corridor greedy stub that detoured far on the pin's
                # own layer (D0: avg 180 / max 987 stub cells, 60% of markers).
                ta_nets = [n for n in ordered if n.net in handled]
                ta_corr = (_grt_guide_corridors(g, ta_nets, termsets, GC, profile)
                           or {}) if ta_nets else {}
                _R = 2   # gcell radius of the AP access region (covers the p90=6-cell

                def _ap_region(ap_set):
                    gs = set()
                    for (ix, iy, _li) in ap_set:
                        gx, gy = ix // GC, iy // GC
                        for dx in range(-_R, _R + 1):
                            for dy in range(-_R, _R + 1):
                                x, y = gx + dx, gy + dy
                                if 0 <= x < ngx and 0 <= y < ngy:
                                    gs.add((x, y))
                    return gs

                for n in sorted(ta_nets, key=lambda n: n.net):
                    backbone = ta_r.get(n.net)
                    if not backbone or routes.get(n.net):
                        continue
                    tree = set(backbone)
                    tree_e = list(ta_e.get(n.net, []))
                    ok = True
                    for ap_set in termsets[n.net]:
                        if tree & ap_set:        # AP already on the backbone
                            continue
                        bc = BoxCost(route_shape=set(placed_fp), gg_drc=shape_base)
                        starts = sorted(tree)
                        path = box_maze(g, n.net, starts, ap_set, bc,
                                        _ap_region(ap_set), GC, via_halo)
                        if path is None:         # widen: net corridor, then whole grid
                            path = box_maze(g, n.net, starts, ap_set, bc,
                                            ta_corr.get(n.net) or full_corr, GC, via_halo)
                        if path is None:
                            path = box_maze(g, n.net, starts, ap_set, BoxCost(),
                                            full_corr, GC, via_halo)
                        if path is None:
                            ok = False
                            break
                        tree_e.extend(zip(path, path[1:]))
                        tree.update(path)
                    # The tight per-pin connect can leave DISJOINT FlexTA backbone
                    # pieces unlinked (ap_i reaches piece A, ap_j reaches piece B) ->
                    # LVS open. Verify all terminals land in ONE component; if not,
                    # fall back to the proven full-corridor connect for THIS net
                    # (keeps the short-via win where the backbone is already linked).
                    if ok and not _terms_connected(tree_e, termsets[n.net]):
                        bc = BoxCost(route_shape=set(placed_fp), gg_drc=shape_base)
                        tg = [set(backbone)] + list(termsets[n.net])
                        routed = _connect_targets(g, n.net, tg, bc,
                                                  ta_corr.get(n.net) or full_corr, GC, via_halo)
                        if routed is None:
                            routed = _connect_targets(g, n.net, tg, bc, full_corr, GC, via_halo)
                        if routed is None:
                            ok = False
                        else:
                            cells, e = routed
                            tree = set(backbone) | set(cells)
                            tree_e = list(ta_e.get(n.net, [])) + e
                    if not ok:
                        continue                 # leave this net for the greedy loop
                    routes[n.net] = sorted(tree)
                    edges[n.net] = tree_e
                    placed_fp |= _net_footprint(g, routes[n.net], edges[n.net],
                                                wire_halo, via_halo)
                todo = [n for n in todo if not routes.get(n.net)]
        except Exception as _ta_err:    # never let TA break the route; fall back
            if verbose:
                import traceback as _tb
                print(f"  flexta: disabled ({_ta_err!r}); greedy seed", flush=True)
                if _os_ta.environ.get("FLEXDR_TA_DEBUG"):
                    _tb.print_exc()
    corridors = None
    if profile is not None:
        corridors = _grt_guide_corridors(g, todo, termsets, GC, profile)
    for n in todo:
        corr = (corridors or {}).get(n.net) or full_corr
        bc = BoxCost(route_shape=set(placed_fp), gg_drc=shape_base)
        routed = _connect_targets(g, n.net, termsets[n.net], bc, corr, GC, via_halo)
        if routed is None and corr is not full_corr:        # widen to whole grid
            routed = _connect_targets(g, n.net, termsets[n.net], bc, full_corr, GC, via_halo)
        if routed is None:
            routed = _connect_targets(g, n.net, termsets[n.net], BoxCost(), full_corr, GC, via_halo)
        if routed is None:
            return RouteResult(False, routes, 0,
                               ({"type": "unroutable", "net": n.net}, ), edges)
        cells, e = routed
        routes[n.net] = sorted(cells)
        edges[n.net] = e
        placed_fp |= _net_footprint(g, cells, e, wire_halo, via_halo)

    # Stage 3b: persistent occupancy now tracks the global signal routes -> worker
    # boxes read routeShape from it (occ) instead of rebuilding every foreign net's
    # footprint per reroute. No-op (occ stays unready) without the Rust kernel.
    _init_rust_occ(g, routes, edges, wire_halo, via_halo, supply=supply)
    # Stage 3c: persistent routes store -> worker box_markers reads global routes
    # in place (no per-box full marshal). Kept in sync below on each committed box.
    _init_rust_routes(g, routes, edges)
    _seed_dt = _ta0.time() - _seed_t0
    if verbose:
        # initial markers = flexgc on the greedy seed (== the first-pass markers
        # below); this is the 855/1827 number FlexTA targets.
        print(f"  flexdr seed: {len(routes)} nets routed, {_seed_dt:.1f}s, "
              f"initial markers {len(gc())}", flush=True)
    _work_t0 = _ta0.time()
    _rg = getattr(g, "_rust_grid", None)
    # Stage 3c: when the Rust grid + occ + routes store are all live, the whole
    # FlexDR worker runs in Rust and each checkerboard batch is routed in PARALLEL
    # (Grid.route_boxes); otherwise route_flexdr falls back to the pure-Python
    # route_box. Worker count auto-detected (os.cpu_count()), override FLEXDR_THREADS
    # (=1 -> serial Rust); clamped to [1, batch size] in Rust.
    import os as _os
    _worker_on = (_rg is not None and bool(getattr(g, "_routes_ready", False))
                  and bool(getattr(g, "_occ_ready", False)))
    _n_threads = int(_os.environ.get("FLEXDR_THREADS", _os.cpu_count() or 1))
    _vertical = None
    if _worker_on:
        _rg.set_termsets([(_net_id(g, nm), [_cells_i(t) for t in ts])
                          for nm, ts in termsets.items()])
        _rg.set_worker_consts([_net_id(g, nm) for nm in supply], _cells_i(obstacles))
        # worker queue is canonical-sorted by net NAME (3b-1); net ids aren't
        # name-ordered, so give Rust each net's rank in name-sorted order.
        _rank = {nm: i for i, nm in enumerate(sorted(g._net_id))}
        _rg.set_name_rank([(nid, _rank[nm]) for nm, nid in g._net_id.items()])
        _vertical = [bool((dir_by_li or {}).get(li, "H") == "V")
                     for li in range(len(g.layers))]

    sched = list(schedule) if schedule is not None else _STRATEGY
    import time as _t
    for pi, row in enumerate(sched):
        size, off, m_end, drc_m, mk_m, fx_m, decay, mode = row
        size = min(size, max(ngx, ngy, 1))
        markers = gc()
        if not markers:
            if verbose:
                print(f"  flexdr worker: {pi} passes, {_ta0.time()-_work_t0:.1f}s "
                      f"(seed {_seed_dt:.1f}s)", flush=True)
            return RouteResult(True, routes, pi + 1, (), edges)
        gg_drc = drc_m * shape_base
        gg_marker = mk_m * marker_base
        gg_fixed = fx_m * shape_base
        t0 = _t.time()
        for batch in checkerboard_batches(worker_boxes(ngx, ngy, size, off)):
            if _worker_on:
                # Run the whole checkerboard batch in parallel (boxes are region-
                # disjoint incl DRC halo) -> Rust serial-merges deltas in box order
                # (deterministic == serial). Fold the composed per-net result into
                # routes/edges + keep the Rust store in sync for the next batch.
                boxes_ij = [(b.gx0, b.gy0, b.gx1, b.gy1) for b in batch]
                deltas = _rg.route_boxes(boxes_ij, GC, wire_halo, via_halo,
                                         mode == "ALL", m_end, gg_drc, gg_marker,
                                         gg_fixed, decay, prl_halo, prl_len, _vertical,
                                         _n_threads)
                for nid, cells, eds in deltas:
                    nm = g._id_to_net[nid]
                    routes[nm] = cells
                    edges[nm] = eds
                    _rg.routes_update(nid, cells, eds)
                continue
            for box in batch:
                out = route_box(g, box, GC, routes, edges, termsets, wire_halo, via_halo,
                                ripup_mode=mode, maze_end_iter=m_end, gg_drc=gg_drc,
                                gg_marker=gg_marker, gg_fixed=gg_fixed, marker_decay=decay,
                                supply=supply, obstacles=obstacles,
                                prl_halo=prl_halo, prl_len=prl_len, dir_by_li=dir_by_li)
                if out is not None:
                    routes.update(out[0])
                    edges.update(out[1])
        if verbose:
            now = len(gc())
            print(f"  flexdr pass {pi+1} {mode} size={size} off={off} mEnd={m_end}: "
                  f"markers {len(markers)}->{now} {_t.time()-t0:.1f}s", flush=True)

    markers = gc()
    if not markers:
        if verbose:
            print(f"  flexdr worker: {len(sched)} passes, {_ta0.time()-_work_t0:.1f}s "
                  f"(seed {_seed_dt:.1f}s)", flush=True)
        return RouteResult(True, routes, len(sched), (), edges)
    inv = sorted({s for m in markers for s in m.sources})
    return RouteResult(False, routes, len(sched),
                       ({"type": "flexdr_incomplete", "marker_nets": inv,
                         "detail": "did not reach 0 markers"}, ), edges)


# --- shared helpers ----------------------------------------------------------

def _augmented_grid(g: CapacityGrid, blocked: Set[Cell],
                    extra_pads: Optional[Mapping[int, Mapping[str, Set[Tuple[int, int]]]]] = None
                    ) -> CapacityGrid:
    """A grid COPY with ``blocked`` added to wire_blocked_all (hard keep-out) and
    ``extra_pads`` merged into pad_cells (per-owner keep-outs, e.g. protecting a
    reroute's boundary PINS). Layer-keyed -> agnostic to layer count/DRC rules."""
    new_wba = {li: set(cells) for li, cells in g.wire_blocked_all.items()}
    for (ix, iy, layer) in blocked:
        new_wba.setdefault(layer, set()).add((ix, iy))
    if not extra_pads:
        return dataclasses.replace(g, wire_blocked_all=new_wba,
                                   pad_owner=_invert_pad_cells(g.pad_cells))
    new_pad = {li: {o: set(s) for o, s in owners.items()} for li, owners in g.pad_cells.items()}
    for li, owners in extra_pads.items():
        d = new_pad.setdefault(li, {})
        for owner, cells in owners.items():
            d.setdefault(owner, set()).update(cells)
    return dataclasses.replace(g, wire_blocked_all=new_wba, pad_cells=new_pad,
                               pad_owner=_invert_pad_cells(new_pad))


_MULTI_OWNER = "\x00__multi_owner__\x00"  # cell owned by 2+ nets -> blocked for ALL


def _invert_pad_cells(pad_cells):
    """{layer: {owner: {cell}}} -> {layer: {cell: owner}} so _wire_ok does an
    O(1) per-cell owner lookup on the maze hot path (OpenROAD per-node-flag model)
    instead of an O(num_nets) scan of every net's pad set.

    A cell owned by 2+ nets is blocked for EVERY net (the scan returns False for
    any net, since some foreign owner claims it), so it is marked with the
    _MULTI_OWNER sentinel -- which never equals a real net name, so _wire_ok's
    ``owner == net`` is always False -> blocked. Single-owner cells keep their
    owner. This keeps the inverse BYTE-EQUIVALENT to the scan even when device
    pads and PDN keep-outs overlap on the coarse grid (without the sentinel a
    foreign net could route into a shared VDD/GND pad cell -> the add4/alu4
    VDD<->signal LVS short)."""
    out: Dict[int, Dict[Tuple[int, int], str]] = {}
    for li, owners in pad_cells.items():
        d: Dict[Tuple[int, int], str] = {}
        for o, cells in owners.items():
            for c in cells:
                cur = d.get(c)
                d[c] = o if cur is None else (cur if cur == o else _MULTI_OWNER)
        out[li] = d
    return out


def _net_footprint(g, cells, edge_list, wire_halo, via_halo) -> Set[Cell]:
    via = _via_cells(edge_list)
    out: Set[Cell] = set()
    for c in cells:
        out.update(_footprint(g, c, via_halo if c in via else wire_halo))
    return out


def _terms_connected(edge_list, term_sets) -> bool:
    """True if EVERY terminal cell-set shares ONE connected component over
    ``edge_list`` -- a quick LVS-open guard for the Stage-D per-pin connect (the
    tight regions can leave disjoint backbone pieces unlinked)."""
    parent: Dict[Cell, Cell] = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in edge_list:
        parent[find(a)] = find(b)
    roots = set()
    for tset in term_sets:
        rep = next((find(c) for c in tset if c in parent), None)
        if rep is None:
            return False        # this terminal is not on the routed tree at all
        roots.add(rep)
    return len(roots) <= 1


def _gcell_extent(g: CapacityGrid, GC: int) -> Tuple[int, int]:
    return ((g.nx + GC - 1) // GC, (g.ny + GC - 1) // GC)
