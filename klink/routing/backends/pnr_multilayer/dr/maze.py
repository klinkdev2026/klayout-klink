"""Python-first FlexDR maze state on TrackGrid (Stage T5, increment C).

The detailed-route maze ported onto the non-uniform `TrackGrid` (T1), Python-first
(NO Rust), using the increment-A geometry + increment-B legality adapter. Design:
see the mapping table in this module's docstring, group (C). Scope (binding):

* net-id, per-node occupancy, an A* `box_maze`, a checkerboard tile schedule, and a
  rip-up/retry pass loop -- the maze SURFACES the worker needs to route on TrackGrid.
* consumes the T4 seed (segments + vias) as the initial routed geometry and resolves
  the overlaps it leaves.
* **DRC / G4 stays OUT** -- the only hard blockers are the B legality predicates
  (`wire_ok` / `via_ok` / blocked planar edges). Inter-net OVERLAP (two nets on one node)
  is an occupancy conflict the maze resolves, NOT a DRC spacing rule.
* no spacing/PRL/min-area, no Rust, no byte-parity claim (new grid).

Generic: layers/dirs/via-ladder come from the grid; nothing process-specific in the kernel.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

Node = Tuple[int, int, int]            # (xi, yi, zi)
Box = Tuple[int, int, int, int]        # (xi_lo, yi_lo, xi_hi, yi_hi) inclusive maze-idx box
_BLOCK = 255                           # saturated fixed-shape cost == hard obstacle (legality.BLOCK)
_INF = float("inf")


def checkerboard_tiles(nx: int, ny: int, size: int) -> List[Tuple[Box, int]]:
    """Two-colour checkerboard tiling of the gcell grid into `size`-wide boxes
    (FlexDR worker-box schedule). Colour = (tx+ty)&1; routing one colour at a time is
    the parallel-safe order (we stay single-threaded in C, but keep the schedule
    faithful + deterministic). Returns [(box, colour), ...] colour-0 tiles first."""
    tiles: List[Tuple[Box, int]] = []
    for color in (0, 1):
        ty = 0
        for y0 in range(0, max(1, ny), size):
            tx = 0
            for x0 in range(0, max(1, nx), size):
                if (tx + ty) & 1 == color:
                    box = (x0, y0, min(x0 + size - 1, nx - 1), min(y0 + size - 1, ny - 1))
                    tiles.append((box, color))
                tx += 1
            ty += 1
    return tiles


def nodes_from_t4(segments: Sequence[dict], vias: Sequence[dict]) -> List[Node]:
    """T4 seed (assigned segments + vias) -> the set of TrackGrid nodes it occupies.
    Assumes the T4 along/track indices are TrackGrid maze indices (tracks_per_gcell=1
    aligned, as in the T5-A round-trip)."""
    out: List[Node] = []
    for s in segments:
        perp, b, e, z = s["track_coord"], s["along_begin"], s["along_end"], s["layer"]
        for k in range(b, e + 1):
            out.append((k, perp, z) if s["is_h"] else (perp, k, z))
    for v in vias:
        out.append((v["gx"], v["gy"], v["z_lo"]))
        out.append((v["gx"], v["gy"], v["z_hi"]))
    return list(dict.fromkeys(out))


@dataclass
class RouteState:
    ok: bool
    passes: int
    routes: Dict[str, List[Node]] = field(default_factory=dict)
    overlap_count: int = 0


class TrackMaze:
    """Maze state over a TrackGrid via the geometry+legality adapter."""

    def __init__(self, grid, adapter, *, via_cost: float = 2.0, occ_penalty: float = 50.0,
                 marker_weight: float = 8.0, planar_layers=None, portals=None,
                 portal_window=None, jog_cost: float = 1.0):
        self.g = grid
        self.a = adapter
        # cached Node SoA arrays + legality (the A* hot path indexes these directly by the
        # flat maze-index, so it never recomputes get_idx -- OpenROAD's flat-array trick).
        _n = grid.nodes
        self._mc = _n["mc_planar"]
        self._edge_E, self._edge_N = _n["edge_E"], _n["edge_N"]
        self._blk_E, self._blk_N = _n["blocked_E"], _n["blocked_N"]
        self._fsc_h, self._fsc_v = _n["fsc_planar_h"], _n["fsc_planar_v"]
        self._fsc_via = _n["fsc_via"]
        self._pad_owner = adapter._pad_owner     # {(zi, xi, yi): owner} (foreign-pad legality)
        self.via_cost = via_cost
        self.occ_penalty = occ_penalty
        self.marker_weight = marker_weight
        # extra cost for a planar hop on a terminal/PDN layer (a portal jog): keeps the
        # maze on vias-straight-up by default, jogging only when the pin column is blocked.
        self.jog_cost = jog_cost
        # SPACING-AWARE occupancy: a foreign net within this many tracks PERPENDICULAR to
        # the wire (same layer) counts as "in the way", so the maze keeps parallel runs
        # apart and is BORN low-DRC (no PRL to grind out later). 0 = exact-cell only.
        self.spacing_halo = 0
        # z-indices where a terminal-layer planar JOG is never allowed (the PDN rail/strap
        # layers) -- pins are reached by via-THROUGH these, never a planar jog, so a jog can
        # never land a signal on a GND/VDD rail (the LVS short). Via traversal still allowed.
        self.no_jog_layers: Set[int] = set()
        # if set, planar (backbone) routing is confined to these z-indices (clean
        # signal/terminal separation); terminals on other layers are still reached by
        # the via ladder. None => planar allowed on every layer.
        self.planar_layers = set(planar_layers) if planar_layers is not None else None
        # per-net AP via-access PORTAL columns {net: {(xi, yi), ...}}: the ONLY (x,y)
        # where a via may descend onto a terminal/PDN layer (signal<->signal vias are
        # free). Off-portal, terminal layers are untouchable -> signals never short the
        # PDN. None/empty => no portal restriction.
        self._portals = {k: set(v) for k, v in (portals or {}).items()}
        # per-net AP-LOCAL portal WINDOW {net: {(xi, yi), ...}}: the only (x,y) where a
        # net may make a planar hop (a "portal jog") on a terminal/PDN layer -- a small
        # box around each AP, so a blocked pin column can sidestep to a via-clear column
        # WITHOUT free planar signal routing on terminal layers (bounded, owner-allowed).
        self._window = {k: set(v) for k, v in (portal_window or {}).items()}
        self.routes: Dict[str, List[Node]] = {}
        self.edges: Dict[str, List[Tuple[Node, Node]]] = {}   # ACTUAL routed moves
        self.occ: Dict[Node, Set[str]] = defaultdict(set)
        self._ids: Dict[str, int] = {}
        self._by_id: Dict[int, str] = {}
        self.unrouted: List[str] = []
        # instrumentation: A* node expansions (pops) total + per route_net call, reroutes
        self.expanded = 0
        self.net_expansions: List[int] = []
        self.reroutes = 0
        self.use_corridor = True                 # bound initial routing to a local corridor
        self.use_rust = False                    # drive initial routing via klink_trackmaze_rs
        self.rust_ovlp_passes = 0                 # >0: also resolve overlaps in Rust (the 3b win)
        self.corridors = None                    # {net: set[gcell]} T3 guide corridors (bound A*)
        self.gcell = 1                           # gcell size (cell = (x//gcell, y//gcell))
        self._corridor = None                    # current net's corridor gcell set (or None)

    # --- net id (mirrors _net_id / _id_to_net) ------------------------------
    def net_id(self, net: str) -> int:
        if net not in self._ids:
            i = len(self._ids)
            self._ids[net] = i
            self._by_id[i] = net
        return self._ids[net]

    # --- occupancy ----------------------------------------------------------
    def add_route(self, net: str, nodes: Sequence[Node], edges=None) -> None:
        self.net_id(net)
        self.routes[net] = list(nodes)
        # ACTUAL routed moves (not adjacency-reconstructed): so the drawn geometry and
        # the terminal-layer audit reflect only real maze hops. Seed routes (no edges
        # supplied) fall back to adjacency since they are pre-built geometry.
        self.edges[net] = list(edges) if edges is not None else _adj_edges(nodes)
        for n in nodes:
            self.occ[n].add(net)

    def remove_route(self, net: str) -> None:
        for n in self.routes.get(net, ()):
            s = self.occ.get(n)
            if s:
                s.discard(net)
                if not s:
                    del self.occ[n]
        self.routes.pop(net, None)
        self.edges.pop(net, None)

    def overlaps_nodes(self, box: Optional[Box] = None) -> Set[Node]:
        out = set()
        for n, who in self.occ.items():
            if len(who) > 1 and (box is None or _in_box(n, box)):
                out.add(n)
        return out

    # --- maze ---------------------------------------------------------------
    def _wire_ok_idx(self, idx: int, xi: int, yi: int, zi: int, net: str) -> bool:
        """wire_ok against a PRECOMPUTED flat index (no get_idx). Faithful to the adapter's
        wire_ok: fixed-metal node => only the owning net (or all-net channel block)."""
        if self._fsc_h[idx] >= _BLOCK or self._fsc_v[idx] >= _BLOCK:
            owner = self._pad_owner.get((zi, xi, yi))
            return owner is not None and owner == net
        return True

    def _neighbors(self, node: Node, net: str, box: Optional[Box], allow_jog: bool = True,
                   nidx: Optional[int] = None):
        """Yield (neighbour, neighbour_idx, step). Planar neighbours are reached by index
        ARITHMETIC from ``nidx`` (E/W = +-E_stride, N/S = +-N_stride) -- no get_idx per
        neighbour; only vias (z-flip) recompute. Yields the flat index so the caller reuses
        it for marker cost (the FlexGridGraph flat-array hot path)."""
        xi, yi, zi = node
        g = self.g
        if nidx is None:
            nidx = g.get_idx(xi, yi, zi)
        sig = self.planar_layers
        window = self._window.get(net, _EMPTY) if allow_jog else _EMPTY
        if zi in self.no_jog_layers:             # never a planar jog on a PDN rail/strap layer
            window = _EMPTY
        on_term = sig is not None and zi not in sig
        nx, ny = g._nx, g._ny
        is_h = g._zh[zi]
        es = 1 if is_h else ny                   # E/W flat stride
        ns = nx if is_h else 1                   # N/S flat stride
        eE, eN, bE, bN = self._edge_E, self._edge_N, self._blk_E, self._blk_N
        wok = self._wire_ok_idx
        cgc, G = self._corridor, self.gcell    # T3 guide corridor: planar move must stay in-band
        if box is None:
            x0, y0, x1, y1 = 0, 0, nx - 1, ny - 1
        else:
            x0, y0, x1, y1 = box
        if (sig is None) or (zi in sig) or ((xi, yi) in window):
            cost = self.jog_cost if on_term else 1.0
            yg = yi // G
            # E
            if xi < x1 and (not on_term or (xi + 1, yi) in window) and eE[nidx] and not bE[nidx] \
                    and (cgc is None or ((xi + 1) // G, yg) in cgc):
                ti = nidx + es
                if wok(ti, xi + 1, yi, zi, net):
                    yield (xi + 1, yi, zi), ti, cost
            # W
            if xi > x0 and (not on_term or (xi - 1, yi) in window) \
                    and (cgc is None or ((xi - 1) // G, yg) in cgc):
                ei = nidx - es
                if eE[ei] and not bE[ei] and wok(ei, xi - 1, yi, zi, net):
                    yield (xi - 1, yi, zi), ei, cost
            # N
            if yi < y1 and (not on_term or (xi, yi + 1) in window) and eN[nidx] and not bN[nidx] \
                    and (cgc is None or (xi // G, (yi + 1) // G) in cgc):
                ti = nidx + ns
                if wok(ti, xi, yi + 1, zi, net):
                    yield (xi, yi + 1, zi), ti, cost
            # S
            if yi > y0 and (not on_term or (xi, yi - 1) in window) \
                    and (cgc is None or (xi // G, (yi - 1) // G) in cgc):
                ei = nidx - ns
                if eN[ei] and not bN[ei] and wok(ei, xi, yi - 1, zi, net):
                    yield (xi, yi - 1, zi), ei, cost
        # via U/D (z flips the planar layout -> recompute the index)
        on_portal = (xi, yi) in self._portals.get(net, ())
        in_window = (xi, yi) in window
        fsc_via = self._fsc_via
        for nzi in (zi + 1, zi - 1):
            if 0 <= nzi < g._nz and (min(zi, nzi), max(zi, nzi)) in g.via_z_pairs:
                if sig is not None and (zi not in sig or nzi not in sig) \
                        and not (on_portal or in_window):
                    continue
                vidx = g.get_idx(xi, yi, nzi)
                if (on_portal or fsc_via[vidx] < _BLOCK) and wok(vidx, xi, yi, nzi, net):
                    yield (xi, yi, nzi), vidx, self.via_cost

    def _foreign(self, node: Node, net: str) -> bool:
        occ_get = self.occ.get                   # local-bind the hot dict lookup
        h = self.spacing_halo
        if h:                                    # spacing-aware: scan perpendicular band
            xi, yi, zi = node
            perp_y = self.g._zh[zi]              # H wire runs along x -> parallels differ in y
            for d in range(-h, h + 1):
                who = occ_get((xi, yi + d, zi) if perp_y else (xi + d, yi, zi))
                if who:
                    for o in who:                # explicit loop + early return > any(gen)
                        if o != net:
                            return True
            return False
        who = occ_get(node)
        if who:
            for o in who:
                if o != net:
                    return True
        return False

    def add_marker(self, node: Node) -> None:
        """Raise the DRC marker cost on a node (FlexDR marker-driven rip-up). The maze
        then steers wires off it. Saturating +10 per hit (T1 `mc_planar`)."""
        if self.g.is_valid(*node):
            self.g.add_marker_planar(self.g.get_idx(*node))

    def _marker_cost(self, node: Node) -> float:
        return self._mc[self.g.get_idx(node[0], node[1], node[2])] * self.marker_weight

    def _astar(self, net: str, sources: Set[Node], dest: Node,
               box: Optional[Box], hard_avoid: bool = False,
               allow_jog: bool = True) -> Optional[List[Node]]:
        # hot loop: local-bind everything; reuse the neighbour flat index for marker cost.
        dx, dy, dz = dest
        heap: List[Tuple[float, float, Node]] = []
        best: Dict[Node, float] = {}
        came: Dict[Node, Node] = {}
        push, pop = heappush, heappop
        mc, mw, occ_pen = self._mc, self.marker_weight, self.occ_penalty
        foreign, nbrs, gidx = self._foreign, self._neighbors, self.g.get_idx
        for s in sources:
            best[s] = 0.0
            push(heap, (abs(s[0] - dx) + abs(s[1] - dy) + abs(s[2] - dz), 0.0, s))
        pops = 0
        while heap:
            _, cost, node = pop(heap)
            if node == dest:
                self.expanded += pops
                path = [node]
                while path[-1] in came:
                    path.append(came[path[-1]])
                return path[::-1]
            if cost > best.get(node, _INF) + 1e-12:
                continue
            pops += 1
            nidx = gidx(node[0], node[1], node[2])
            for nb, nb_idx, step in nbrs(node, net, box, allow_jog, nidx):
                ext = mc[nb_idx] * mw
                if foreign(nb, net):
                    if hard_avoid:
                        continue           # never overlap a foreign net (no short)
                    ext += occ_pen
                nc = cost + step + ext
                if nc + 1e-12 < best.get(nb, _INF):
                    best[nb] = nc
                    came[nb] = node
                    push(heap, (nc + abs(nb[0] - dx) + abs(nb[1] - dy) + abs(nb[2] - dz), nc, nb))
        self.expanded += pops
        return None

    def route_net(self, net: str, terminals: Sequence[Node],
                  box: Optional[Box] = None, hard_avoid: bool = False,
                  allow_jog: bool = True, seed_tree: Optional[Set[Node]] = None):
        """Return (nodes, edges) connecting the terminals, or None if unroutable.
        ``edges`` are the ACTUAL maze hops. ``hard_avoid`` forbids overlapping any foreign
        net. ``seed_tree`` (the net's existing track-assigned backbone) is the starting
        tree -- each terminal is then connected TO the backbone (pin access), so the T4
        track-assigned spread is preserved while pins are stitched in."""
        terms = list(dict.fromkeys(terminals))
        if not terms:
            return [], []
        self.reroutes += 1
        _exp0 = self.expanded
        tree: Set[Node] = set(seed_tree or ())
        remaining = list(terms)
        if not tree:
            tree.add(remaining.pop(0))
        edges: List[Tuple[Node, Node]] = []
        for t in remaining:
            if t in tree:
                continue
            p = self._astar(net, set(tree), t, box, hard_avoid, allow_jog)
            if p is None:
                self.net_expansions.append(self.expanded - _exp0)
                return None
            edges.extend(zip(p, p[1:]))
            tree |= set(p)
            tree.add(t)
        self.net_expansions.append(self.expanded - _exp0)
        return sorted(tree), edges

    def _bbox(self, nodes: Sequence[Node], margin: int) -> Box:
        xs = [n[0] for n in nodes]
        ys = [n[1] for n in nodes]
        return (max(0, min(xs) - margin), max(0, min(ys) - margin),
                min(self.g._nx - 1, max(xs) + margin), min(self.g._ny - 1, max(ys) + margin))

    def route_net_corridor(self, net: str, terminals: Sequence[Node], *,
                           seed_tree: Optional[Set[Node]] = None,
                           margins: Sequence = (8, 24, 64), hard_avoid: bool = False,
                           allow_jog: bool = True):
        """Route within a LOCAL corridor (bbox of terminals + seed, grown by ``margin``),
        with DETERMINISTIC growth and a final capped fallback. A local net never searches
        the whole grid -- only a genuinely cross-die net reaches the whole-grid fallback
        (so routability is preserved, correctness unchanged, big grids don't explode)."""
        terms = list(terminals) + (list(seed_tree) if seed_tree else [])
        if not terms:
            return [], []
        for mgn in margins:
            res = self.route_net(net, terminals, box=self._bbox(terms, mgn),
                                 hard_avoid=hard_avoid, allow_jog=allow_jog, seed_tree=seed_tree)
            if res is not None:
                return res
        # capped fallback (bbox + whole-grid-span margin): only reached when the tight
        # corridors all fail -- a deterministic, guaranteed-reachable last resort.
        cap = max(self.g._nx, self.g._ny)
        return self.route_net(net, terminals, box=self._bbox(terms, cap),
                              hard_avoid=hard_avoid, allow_jog=allow_jog, seed_tree=seed_tree)

    def route_net_bounded(self, net: str, terminals: Sequence[Node],
                          margins: Sequence = (8, 24, 64, None), hard_avoid: bool = False):
        """Route ``net`` bounding the A* to a box around its terminals, enlarging the
        box on failure and finally falling back to the whole grid. This localizes the
        search (the cost is whole-grid A*) WITHOUT changing the result -- the last
        margin (None) is the unbounded route, so correctness is identical, only faster
        for the common local net. (Faithful to FlexDR's increasing worker-box schedule.)
        ``hard_avoid`` forbids overlapping any foreign net (so a reroute never INTRODUCES
        a short) -- bounded, so it stays cheap unlike a whole-grid hard route."""
        terms = [t for t in terminals]
        if not terms:
            return [], []
        for m in margins:
            box = None if m is None else self._bbox(terms, m)
            res = self.route_net(net, terms, box, hard_avoid=hard_avoid)
            if res is not None:
                return res
        return None

    def _connected(self, net: str, net_terminals: Sequence[Node]) -> bool:
        """True iff every pin of ``net`` lies in one connected component of its edges
        (the no-open guarantee). Empty/<=1 pin is trivially connected."""
        pins = [p for p in net_terminals]
        if len(pins) <= 1:
            return True
        adj: Dict[Node, Set[Node]] = defaultdict(set)
        for (a, b) in self.edges.get(net, ()):
            adj[a].add(b)
            adj[b].add(a)
        seen = {pins[0]}
        stk = [pins[0]]
        while stk:
            n = stk.pop()
            for mn in adj[n]:
                if mn not in seen:
                    seen.add(mn)
                    stk.append(mn)
        return all(p in seen for p in pins)

    def route_box(self, net: str, ext: Box, net_terminals: Sequence[Node],
                  hard_avoid: bool = True) -> bool:
        """Worker-box LOCAL rip-up + reroute of ``net``'s in-``ext`` segment.

        Faithful FlexDR worker: only the edges fully inside ``ext`` are ripped; everything
        else (the net's geometry outside, and the edges crossing the box boundary) is
        FIXED. The **boundary anchors** = the inside endpoints of the boundary-crossing
        edges + the net's pins inside ``ext``; the local reroute must reconnect ALL of
        them within ``ext``. **Accept-or-revert**: if the reroute fails or the net is not
        fully connected afterwards, the original geometry is restored -- so a box can
        never open the net. Returns True iff the box changed the net."""
        edges = list(self.edges.get(net) or _adj_edges(self.routes.get(net, [])))
        rip = [e for e in edges if _in_box(e[0], ext) and _in_box(e[1], ext)]
        if not rip:
            return False
        keep = [e for e in edges if not (_in_box(e[0], ext) and _in_box(e[1], ext))]
        anchors: Set[Node] = set()
        for (a, b) in keep:                       # crossing edge -> its INSIDE endpoint
            if _in_box(a, ext) != _in_box(b, ext):
                anchors.add(a if _in_box(a, ext) else b)
        for p in net_terminals:                   # pins inside the box are anchors too
            if _in_box(p, ext):
                anchors.add(p)
        keep_nodes = {n for e in keep for n in e}

        saved_r = list(self.routes.get(net, []))
        saved_e = list(self.edges.get(net, []))
        self.remove_route(net)

        if len(anchors) <= 1:
            # nothing to reconnect inside; just drop the ripped in-box wire
            new_nodes, new_edges = sorted(keep_nodes), list(keep)
        else:
            # reconnect within ext: never jog onto a terminal/PDN layer (allow_jog=False ->
            # no GND short). ``hard_avoid`` True = never overlap (clean); False = a soft
            # "kick" that may transiently overlap to escape a deadlock (resolved next pass).
            res = self.route_net(net, sorted(anchors), box=ext, hard_avoid=hard_avoid,
                                 allow_jog=False)
            if res is None:
                self.add_route(net, saved_r, saved_e)
                return False
            rnodes, redges = res
            new_nodes = sorted(keep_nodes | set(rnodes))
            new_edges = list(keep) + list(redges)
        self.add_route(net, new_nodes, new_edges)
        if not self._connected(net, net_terminals):
            self.remove_route(net)
            self.add_route(net, saved_r, saved_e)   # REVERT -> no open, ever
            return False
        return True

    # --- seed + pass loop ---------------------------------------------------
    def consume_seed(self, seed: Mapping[str, Sequence[Node]], seed_edges=None) -> None:
        for net, nodes in seed.items():
            legal = [n for n in nodes if self.a.wire_ok(n[0], n[1], n[2], net)]
            ls = set(legal)
            e = None
            if seed_edges and net in seed_edges:
                e = [(a, b) for (a, b) in seed_edges[net] if a in ls and b in ls]
            self.add_route(net, legal, e)

    def route_all(self, terminals: Mapping[str, Sequence[Node]], *,
                  seed: Optional[Mapping[str, Sequence[Node]]] = None,
                  seed_edges=None, max_passes: int = 30, tile: int = 4) -> RouteState:
        if seed:
            self.consume_seed(seed, seed_edges)
        # ensure every net connects its pins. A seeded net keeps its track-assigned
        # backbone and only stitches pins to it (seed_tree); an unseeded net routes fresh.
        self.unrouted = []
        if self.use_rust:                        # Rust kernel for the per-net A* (seed already
            from .rust_bridge import rust_initial_route   # consumed); Python fallback otherwise
            rust_initial_route(self, terminals)
        else:
            cmap = self.corridors
            for net in sorted(terminals):
                existing = list(self.routes.get(net, []))
                if existing and self._connected(net, terminals[net]):
                    continue
                st = set(existing) or None
                # bound the search to this net's T3 guide corridor; if no in-corridor path
                # exists, retry UNBOUNDED (the clean detailed router is the fallback, scope #4)
                self._corridor = cmap.get(net) if cmap else None
                res = (self.route_net_corridor(net, terminals[net], seed_tree=st)
                       if self.use_corridor else self.route_net(net, terminals[net], seed_tree=st))
                if res is None and self._corridor is not None:
                    self._corridor = None
                    res = self.route_net(net, terminals[net], seed_tree=st)
                self._corridor = None
                if res is None:
                    if not existing:
                        self.unrouted.append(net)
                    continue
                nodes = sorted(set(existing) | set(res[0]))
                edges = list(self.edges.get(net, [])) + list(res[1])
                self.add_route(net, nodes, edges)

        # checkerboard rip-up/retry until no overlaps
        for pass_i in range(1, max_passes + 1):
            if not self.overlaps_nodes():
                return RouteState(True, pass_i - 1, dict(self.routes), 0)
            progressed = False
            for box, _color in checkerboard_tiles(self.g.nx, self.g.ny, tile):
                ov = self.overlaps_nodes(box)
                if not ov:
                    continue
                # rip up the higher-id net on each overlapped node, reroute avoiding others
                fix: Set[str] = set()
                for n in ov:
                    nets_here = sorted(self.occ[n], key=self.net_id)
                    fix.add(nets_here[-1])      # deterministic: higher net id yields
                for net in sorted(fix, key=self.net_id):
                    self.remove_route(net)
                    res = self.route_net_bounded(net, terminals[net])
                    if res is not None:
                        self.add_route(net, res[0], res[1])
                        progressed = True
                    else:
                        self.add_route(net, [], [])
            if not progressed:
                break
        return RouteState(not self.overlaps_nodes(),
                          max_passes, dict(self.routes), len(self.overlaps_nodes()))


_EMPTY: frozenset = frozenset()


def _adj_edges(nodes: Sequence[Node]) -> List[Tuple[Node, Node]]:
    """Edges by adjacency over a node set (for seed geometry only)."""
    s = set(nodes)
    out = []
    for (x, y, z) in nodes:
        for nb in ((x + 1, y, z), (x, y + 1, z), (x, y, z + 1)):
            if nb in s:
                out.append(((x, y, z), nb))
    return out


def _in_box(n: Node, box: Box) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= n[0] <= x1 and y0 <= n[1] <= y1
