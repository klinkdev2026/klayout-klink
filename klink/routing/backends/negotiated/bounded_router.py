"""Bounded negotiated router (lab).

Same correctness model as the stable pure-Python `pathfinder.route_negotiated`
(reuses its helpers by import: _wire_ok legality, _clear-style footprint
congestion pricing, present/history negotiation). The ONLY change is a SCALE
lever: each net's A* search is confined to its terminal bounding box grown by a
margin, so exploration is O(net span) instead of O(whole grid). Correctness is
unchanged because legality (_wire_ok) and congestion footprints are identical;
we only refuse to wander outside the net's window.

Imports only STABLE modules. Does not touch Codex-churned files.
"""
from __future__ import annotations

from collections import defaultdict
from heapq import heappop, heappush
from typing import DefaultDict, Dict, List, Optional, Sequence, Set, Tuple

from klink.routing.grid.capacity_grid import (
    Cell, CapacityGrid, NetInput, RouteResult, _terminal_cellsets, _wire_ok,
)
from klink.routing.grid.pathfinder import (
    _congestion, _occupancy, _remove_net, _halos, _legal_wire, _heuristic,
    _reconstruct, _hot_resource, _share, _resource_footprint, _neighbors,
)


def route_bounded(
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
    margin_cells: int = 12,
    verbose: bool = False,
) -> RouteResult:
    try:
        import klink_pathfinder_rs
    except ImportError:
        return _python_route_bounded(
            g,
            nets,
            width_um=width_um,
            wire_clear_um=wire_clear_um,
            via_clear_um=via_clear_um,
            max_iters=max_iters,
            pres0=pres0,
            growth=growth,
            hist_fac=hist_fac,
            margin_cells=margin_cells,
            verbose=verbose,
        )
    if not hasattr(klink_pathfinder_rs, "route_bounded"):
        return _python_route_bounded(
            g,
            nets,
            width_um=width_um,
            wire_clear_um=wire_clear_um,
            via_clear_um=via_clear_um,
            max_iters=max_iters,
            pres0=pres0,
            growth=growth,
            hist_fac=hist_fac,
            margin_cells=margin_cells,
            verbose=verbose,
        )
    payload = _bounded_payload(g, nets, width_um, wire_clear_um, via_clear_um, max_iters, pres0, growth, hist_fac, margin_cells)
    raw = klink_pathfinder_rs.route_bounded(payload)
    return _bounded_result(raw)


def _bounded_payload(
    g: CapacityGrid,
    nets: Sequence[NetInput],
    width_um: float,
    wire_clear_um: float,
    via_clear_um: float,
    max_iters: int,
    pres0: float,
    growth: float,
    hist_fac: float,
    margin_cells: int,
) -> dict:
    from klink.routing.backends.negotiated.negotiated import _pathfinder_payload

    payload = _pathfinder_payload(g, nets, width_um, wire_clear_um, via_clear_um, max_iters, pres0, growth, hist_fac)
    payload["params"]["margin_cells"] = margin_cells
    return payload


def _bounded_result(raw: dict) -> RouteResult:
    from klink.routing.backends.negotiated.negotiated import _pathfinder_result

    return _pathfinder_result(raw)


def _python_route_bounded(
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
    margin_cells: int = 12,
    verbose: bool = False,
) -> RouteResult:
    ordered = sorted(nets, key=lambda n: n.net)
    termsets = {n.net: _terminal_cellsets(g, n) for n in ordered}
    stuck: DefaultDict[str, int] = defaultdict(int)   # consecutive iters a net stays congested
    routes: Dict[str, List[Cell]] = {}
    edges: Dict[str, List[Tuple[Cell, Cell]]] = {}
    history: DefaultDict[Cell, float] = defaultdict(float)
    directed_history: DefaultDict[Tuple[str, Cell], float] = defaultdict(float)
    streak: DefaultDict[Cell, int] = defaultdict(int)
    pres = pres0
    wire_halo, via_halo = _halos(g, width_um, wire_clear_um, via_clear_um)

    import time as _t
    by_name = {n.net: n for n in ordered}
    to_route = [n.net for n in ordered]          # first pass: route everything
    for it in range(max_iters):
        _t0 = _t.time()
        occ = _occupancy(routes)
        for nm in to_route:                       # PathFinder/FastRoute: only re-route involved nets
            net = by_name[nm]
            _remove_net(occ, nm)
            # OpenROAD-style enlarge: a net stuck in congestion gets a wider
            # search window each time, so it can find a detour outside its
            # initial guide box; after enough strikes it routes on the full grid.
            grow = margin_cells + stuck[nm] * margin_cells
            bbox = _net_bbox(termsets[nm], g, grow)
            routed = _route_tree(g, net, termsets[nm], occ, history,
                                 directed_history, pres, wire_halo, via_halo, bbox)
            if routed is None:
                return RouteResult(False, routes, it + 1,
                                   ({"type": "unroutable", "net": nm,
                                     "detail": "terminal enclosed by keep-outs or bbox too tight"},), edges)
            net_cells, net_edges = routed
            routes[nm] = sorted(net_cells)
            edges[nm] = net_edges
            for cell in net_cells:
                occ[cell].add(nm)

        congestion, involved, owners_by_cell = _congestion(g, routes, edges, wire_halo, via_halo, termsets)
        if verbose:
            print(f"   iter {it+1}: rerouted={len(to_route)} congestion_cells={len(congestion)} "
                  f"involved_nets={len(involved)} pres={pres:.1f} {(_t.time()-_t0):.1f}s", flush=True)
        if not congestion:
            return RouteResult(True, routes, it + 1, (), edges)
        for nm in involved:                       # stagnation strike -> wider window next time
            stuck[nm] += 1
        to_route = sorted(involved)               # next pass: rip up only the congested nets
        for cell in congestion:
            streak[cell] += 1
            accel = max(1, streak[cell])
            history[cell] += hist_fac
            owners = sorted(owners_by_cell[cell])
            for loser in owners[1:]:
                directed_history[(loser, cell)] += hist_fac * max(4, 2 * len(owners)) * accel
        pres *= growth

    congestion, involved, _ = _congestion(g, routes, edges, wire_halo, via_halo, termsets)
    return RouteResult(False, routes, max_iters,
                       ({"type": "congestion", "nets": sorted(involved),
                         "detail": "did not converge; raise max_iters/margin or give more space"},), edges)


def _net_bbox(terminals, g, margin):
    xs, ys = [], []
    for tset in terminals:
        for ix, iy, _l in tset:
            xs.append(ix); ys.append(iy)
    if not xs:
        return (0, 0, g.nx - 1, g.ny - 1)
    return (max(0, min(xs) - margin), max(0, min(ys) - margin),
            min(g.nx - 1, max(xs) + margin), min(g.ny - 1, max(ys) + margin))


def _route_tree(g, net, terminals, occ, history, directed_history, pres, wire_halo, via_halo, bbox):
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
        path = _astar(g, net.net, starts, target, occ, history, directed_history, pres, wire_halo, via_halo, bbox)
        if path is None:
            return None
        for a, b in zip(path, path[1:]):
            edges.append((a, b))
        tree.update(path)
        tree.update(target & set(path))
    return tree, edges


def _astar(g, net, starts, goals, occ, history, directed_history, pres, wire_halo, via_halo, bbox):
    legal_goals = {c for c in goals if _legal_wire(g, c, net)}
    if not starts or not legal_goals:
        return None
    bx0, by0, bx1, by1 = bbox
    # grow the window to also contain the start cells (tree may extend out)
    for sx, sy, _l in starts:
        bx0 = min(bx0, sx); by0 = min(by0, sy); bx1 = max(bx1, sx); by1 = max(by1, sy)

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
                if nxt not in legal_goals and not (bx0 <= nxt[0] <= bx1 and by0 <= nxt[1] <= by1):
                    continue
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
