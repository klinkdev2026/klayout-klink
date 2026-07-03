"""Pure Python PathFinder-style negotiated routing on CapacityGrid.

Algorithm note: adapted from OpenROAD grt/FastRoute (BSD-3-Clause) at
``OpenROAD src/grt/src/fastroute``. The borrowed
ideas are the global-router level loop only: rip up a net's old usage
before rerouting it, run heap-based maze search over grid neighbours and
same-coordinate via transitions, price congestion with present/history
costs, and increase history only for resources still in overflow. This
module does not call or depend on OpenROAD; it projects those ideas onto
klink's cell-based ``CapacityGrid`` legality model.
"""

from __future__ import annotations

from collections import defaultdict
from heapq import heappop, heappush
from math import ceil
from typing import DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from klink.routing.grid.capacity_grid import (
    Cell,
    CapacityGrid,
    NetInput,
    RouteResult,
    _STEPS,
    _terminal_cellsets,
    _wire_ok,
)


def route_negotiated(
    g: CapacityGrid,
    nets: Sequence[NetInput],
    *,
    width_um: float = 0.0,
    wire_clear_um: float = 0.0,
    via_clear_um: float = 0.0,
    max_iters: int = 80,
    pres0: float = 0.5,
    growth: float = 1.8,
    hist_fac: float = 1.0,
) -> RouteResult:
    """Route nets with PathFinder negotiated congestion costs.

    Other nets are priced through present/history costs, never treated as
    hard obstacles. Hard exclusions remain capacity-grid legality only:
    channel keep-outs, foreign device pads, and forbidden via landing cells.
    """

    ordered = sorted(nets, key=lambda n: n.net)
    termsets = {n.net: _terminal_cellsets(g, n) for n in ordered}
    routes: Dict[str, List[Cell]] = {}
    edges: Dict[str, List[Tuple[Cell, Cell]]] = {}
    history: DefaultDict[Cell, float] = defaultdict(float)
    directed_history: DefaultDict[Tuple[str, Cell], float] = defaultdict(float)
    congestion_streak: DefaultDict[Cell, int] = defaultdict(int)
    pres = pres0
    wire_halo, via_halo = _halos(g, width_um, wire_clear_um, via_clear_um)

    for it in range(max_iters):
        occ = _occupancy(routes)
        for net in ordered:
            _remove_net(occ, net.net)
            routed = _route_tree(
                g,
                net,
                termsets[net.net],
                occ,
                history,
                directed_history,
                pres,
                wire_halo,
                via_halo,
            )
            if routed is None:
                return RouteResult(
                    False,
                    routes,
                    it + 1,
                    (
                        {
                            "type": "unroutable",
                            "net": net.net,
                            "detail": (
                                "terminal of net %r is enclosed by hard keep-outs; "
                                "placement must leave an escape on some layer"
                            )
                            % net.net,
                        },
                    ),
                    edges,
                )
            net_cells, net_edges = routed
            routes[net.net] = sorted(net_cells)
            edges[net.net] = net_edges
            for cell in net_cells:
                occ[cell].add(net.net)

        congestion, involved, owners_by_cell = _congestion(g, routes, edges, wire_halo, via_halo, termsets)
        if not congestion:
            return RouteResult(True, routes, it + 1, (), edges)
        for cell in congestion:
            congestion_streak[cell] += 1
            accel = max(1, congestion_streak[cell])
            history[cell] += hist_fac
            owners = sorted(owners_by_cell[cell])
            for loser in owners[1:]:
                directed_history[(loser, cell)] += hist_fac * max(4, 2 * len(owners)) * accel
        pres *= growth

    congestion, involved, _owners_by_cell = _congestion(g, routes, edges, wire_halo, via_halo, termsets)
    return RouteResult(
        False,
        routes,
        max_iters,
        (
            {
                "type": "congestion",
                "nets": sorted(involved),
                "detail": "did not converge in %d iters; give more routing space/layers or raise max_iters"
                % max_iters,
            },
        ),
        edges,
    )


def _route_tree(
    g: CapacityGrid,
    net: NetInput,
    terminals: Sequence[Set[Cell]],
    occ: Mapping[Cell, Set[str]],
    history: Mapping[Cell, float],
    directed_history: Mapping[Tuple[str, Cell], float],
    pres: float,
    wire_halo: int,
    via_halo: int,
) -> Optional[Tuple[Set[Cell], List[Tuple[Cell, Cell]]]]:
    if not terminals:
        return set(), []
    cleaned = [set(t) for t in terminals if t]
    if not cleaned:
        return set(), []
    for tset in cleaned:
        if not any(_legal_wire(g, cell, net.net) for cell in tset):
            return None

    tree: Set[Cell] = set(cleaned[0])
    edges: List[Tuple[Cell, Cell]] = []
    for target in cleaned[1:]:
        if tree & target:
            tree |= target
            continue
        starts = sorted(c for c in tree if _legal_wire(g, c, net.net))
        path = _astar(g, net.net, starts, target, occ, history, directed_history, pres, wire_halo, via_halo)
        if path is None:
            return None
        for a, b in zip(path, path[1:]):
            edges.append((a, b))
        tree.update(path)
        tree.update(target & set(path))
    return tree, edges


def _astar(
    g: CapacityGrid,
    net: str,
    starts: Sequence[Cell],
    goals: Set[Cell],
    occ: Mapping[Cell, Set[str]],
    history: Mapping[Cell, float],
    directed_history: Mapping[Tuple[str, Cell], float],
    pres: float,
    wire_halo: int,
    via_halo: int,
) -> Optional[List[Cell]]:
    legal_goals = {c for c in goals if _legal_wire(g, c, net)}
    if not starts or not legal_goals:
        return None

    for avoid_hot in (True, False):
        best: Dict[Cell, float] = {}
        came: Dict[Cell, Cell] = {}
        heap: List[Tuple[float, float, Cell]] = []
        for start in starts:
            best[start] = 0.0
            heappush(heap, (_heuristic(start, legal_goals), 0.0, start))

        done: Set[Cell] = set()
        while heap:
            _, cost, cell = heappop(heap)
            if cell in done:
                continue
            done.add(cell)
            if cell in legal_goals:
                return _reconstruct(came, cell)
            for nxt, base_cost, extra_cost, halo in _neighbors(g, cell, net, via_halo):
                step_halo = halo if nxt[2] != cell[2] else wire_halo
                history_resource = _resource_footprint(g, cell, nxt, 2 * wire_halo, 2 * step_halo)
                if avoid_hot and nxt not in legal_goals and _hot_resource(history_resource, net, history, directed_history):
                    continue
                share = _share(history_resource, net, occ)
                hist = sum(history.get(c, 0.0) + directed_history.get((net, c), 0.0) for c in history_resource)
                step = base_cost * (1.0 + hist) * (1.0 + pres * share)
                new_cost = cost + step + extra_cost
                if new_cost + 1e-12 < best.get(nxt, float("inf")):
                    best[nxt] = new_cost
                    came[nxt] = cell
                    heappush(heap, (new_cost + _heuristic(nxt, legal_goals), new_cost, nxt))
    return None


def _hot_resource(
    resource: Iterable[Cell],
    net: str,
    history: Mapping[Cell, float],
    directed_history: Mapping[Tuple[str, Cell], float],
) -> bool:
    return any(
        history.get(c, 0.0) >= 16.0 or directed_history.get((net, c), 0.0) >= 16.0
        for c in resource
    )


def _neighbors(g: CapacityGrid, cell: Cell, net: str, via_halo: int) -> Iterable[Tuple[Cell, float, float, int]]:
    ix, iy, layer = cell
    in_bounds = g.in_bounds
    for dx, dy in _STEPS:
        nx, ny = ix + dx, iy + dy
        if in_bounds(nx, ny) and _wire_ok(g, nx, ny, layer, net):
            yield (nx, ny, layer), 1.0, 0.0, 0
    # TODO: 3D needs per-layer-pair via blocking; CapacityGrid currently
    # exposes via_blocked as a flat (ix, iy) keep-out shared by all via rules.
    if (ix, iy) in g.via_blocked:
        return
    # Hoist the current-cell legality (invariant across via rules) out of the
    # loop -- the old code re-checked _wire_ok(layer) per rule. Via index is
    # precomputed/cached (was sorted + layers.index per call). Order + content
    # identical to before -> byte-parity (verified vs flexdr_oracle).
    if not _wire_ok(g, ix, iy, layer, net):
        return
    for a, b, cost in g.via_index():
        other = b if layer == a else a if layer == b else None
        if other is None:
            continue
        if _wire_ok(g, ix, iy, other, net):
            yield (ix, iy, other), 1.0, cost, via_halo


def _congestion(
    g: CapacityGrid,
    routes: Mapping[str, Sequence[Cell]],
    edges: Mapping[str, Sequence[Tuple[Cell, Cell]]],
    wire_halo: int,
    via_halo: int,
    terminals: Optional[Mapping[str, Sequence[Set[Cell]]]] = None,
) -> Tuple[Set[Cell], Set[str], dict[Cell, Set[str]]]:
    cover: DefaultDict[Cell, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for net, cells in routes.items():
        via_cells = _via_cells(edges.get(net, ()))
        terminal_cells = set().union(*terminals.get(net, ())) if terminals else set()
        for cell in cells:
            halo = via_halo if cell in via_cells else wire_halo
            kind = "terminal" if cell in terminal_cells else "route"
            for covered in _footprint(g, cell, halo):
                cover[covered][net].add(kind)
    owners_by_cell = {
        cell: set(owners)
        for cell, owners in cover.items()
        if sum(1 for kinds in owners.values() if "route" in kinds) >= 2
    }
    bad = set(owners_by_cell)
    involved: Set[str] = set()
    for owners in owners_by_cell.values():
        involved.update(owners)
    return bad, involved, owners_by_cell


def _via_cells(edge_list: Sequence[Tuple[Cell, Cell]]) -> Set[Cell]:
    out: Set[Cell] = set()
    for a, b in edge_list:
        if a[0] == b[0] and a[1] == b[1] and a[2] != b[2]:
            out.add(a)
            out.add(b)
    return out


def _share(cells: Iterable[Cell], net: str, occ: Mapping[Cell, Set[str]]) -> int:
    total = 0
    for covered in cells:
        total += len(occ.get(covered, set()) - {net})
    return total


def _resource_footprint(g: CapacityGrid, prev: Cell, cell: Cell, wire_halo: int, via_halo: int) -> Tuple[Cell, ...]:
    if prev[0] == cell[0] and prev[1] == cell[1] and prev[2] != cell[2]:
        return tuple(_footprint(g, prev, via_halo)) + tuple(_footprint(g, cell, via_halo))
    return tuple(_footprint(g, cell, wire_halo))


def _footprint(g: CapacityGrid, cell: Cell, halo: int) -> Iterable[Cell]:
    ix, iy, layer = cell
    for dx in range(-halo, halo + 1):
        for dy in range(-halo, halo + 1):
            nx, ny = ix + dx, iy + dy
            if g.in_bounds(nx, ny):
                yield (nx, ny, layer)


def _occupancy(routes: Mapping[str, Sequence[Cell]]) -> DefaultDict[Cell, Set[str]]:
    occ: DefaultDict[Cell, Set[str]] = defaultdict(set)
    for net, cells in routes.items():
        for cell in cells:
            occ[cell].add(net)
    return occ


def _remove_net(occ: Mapping[Cell, Set[str]], net: str) -> None:
    empty = []
    for cell, owners in occ.items():
        owners.discard(net)
        if not owners:
            empty.append(cell)
    for cell in empty:
        del occ[cell]


def _halos(g: CapacityGrid, width_um: float, wire_clear_um: float, via_clear_um: float) -> Tuple[int, int]:
    pitch_um = g.pitch_nm / 1000.0
    if pitch_um <= 0:
        return 0, 0
    wire_halo = max(0, ceil((wire_clear_um + width_um - 1e-6) / pitch_um) - 1)
    # A halo reserves cells whose occupancy by ANOTHER net would VIOLATE spacing.
    # A neighbour at d cells has centre separation d*pitch; the violation test is
    #   d*pitch < own_half + neighbour_half + spacing   (edge-to-edge < spacing).
    # The largest violating d is ceil(threshold/pitch) - 1 (the `-1` is the
    # boundary: a shape exactly `spacing` away passes). The via formula MUST use
    # this same boundary as wire_halo -- a via PAD of width W has the identical
    # keep-out as a wire of width W; dropping the `-1` over-reserved a full ring
    # of DRC-clean cells, flagging unclearable false markers (FlexGC non-faithful).
    fp_half = max((max(vr.footprint_um) / 2.0 for vr in g.via_rules), default=0.0)
    nbr_half = max(width_um / 2.0, fp_half)          # worst neighbour = via pad
    via_space = max(wire_clear_um, via_clear_um)     # pad is metal -> metal spacing
    via_halo = max(0, ceil((fp_half + nbr_half + via_space - 1e-6) / pitch_um) - 1)
    return wire_halo, via_halo


def _legal_wire(g: CapacityGrid, cell: Cell, net: str) -> bool:
    return g.in_bounds(cell[0], cell[1]) and _wire_ok(g, cell[0], cell[1], cell[2], net)


def _heuristic(cell: Cell, goals: Set[Cell]) -> float:
    ix, iy, layer = cell
    return float(min(abs(ix - gx) + abs(iy - gy) + (0 if layer == gl else 2) for gx, gy, gl in goals))


def _reconstruct(came: Mapping[Cell, Cell], end: Cell) -> List[Cell]:
    path = [end]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    return path
