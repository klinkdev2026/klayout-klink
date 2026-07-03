"""Hierarchical router (OpenROAD-faithful global -> detailed).

OpenROAD splits routing into a COARSE global route (grt/FastRoute: gcell-edge
capacity, overflow/history, rip-up) that emits per-net GUIDES, and a DETAILED
route (drt) that only works INSIDE each net's guide. The flat negotiated router
(bounded_router) is correct but mixes both levels in one fine-grid A*, so global
congestion is paid on the whole fine grid -> it walls at CPU scale.

This module restores the hierarchy WITHOUT changing the validated legality:
  1. gcell_capacity + route_global  -> per-net gcell guide (coarse congestion
     resolved cheaply here; reuses klink's FastRoute-style global_router).
  2. per net, negotiated A* on the FINE grid but CONFINED to its guide corridor
     (gcells from the guide, grown by halo), reusing the golden legality:
     _wire_ok (foreign device pads + channel keep-outs) + _clear footprint
     spacing via present/history negotiation. Only nets whose corridors overlap
     ever contend -> detailed work is local -> it scales.
  3. enlarge = a stuck net's corridor grows by one ring of neighbour gcells
     (the worker-box enlarge analogue), not the whole grid.

Legality/cost identical to pathfinder.py; geometry stays LVS-faithful (the same
RouteResult feeds cell_realize / lvs_check unchanged).
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
from klink.routing.grid.gcell import gcell_capacity
from klink.routing.grid.global_router import route_global

GCell = Tuple[int, int]


def route_hier(
    g: CapacityGrid,
    nets: Sequence[NetInput],
    profile,
    *,
    GC: int = 10,
    halo: int = 1,
    width_um: float = 0.0,
    wire_clear_um: float = 0.0,
    via_clear_um: float = 0.0,
    max_iters: int = 80,
    pres0: float = 0.5,
    growth: float = 1.8,
    hist_fac: float = 1.0,
    global_max_iters: int = 200,
    seed_routes=None,
    seed_edges=None,
    verbose: bool = False,
) -> RouteResult:
    ordered = sorted(nets, key=lambda n: n.net)
    termsets = {n.net: _terminal_cellsets(g, n) for n in ordered}

    # --- 1. coarse global route -> per-net gcell guide -----------------------
    cap_h, cap_v = gcell_capacity(g, GC, profile)
    ngy = len(cap_h)
    ngx = len(cap_h[0]) if ngy else (len(cap_v[0]) if cap_v else 1)

    def gcell_of(cell: Cell) -> GCell:
        return (min(cell[0] // GC, ngx - 1), min(cell[1] // GC, ngy - 1))

    global_nets = []
    for n in ordered:
        pins = sorted({gcell_of(c) for tset in termsets[n.net] for c in tset})
        global_nets.append({"net": n.net, "pins": pins})
    gr = route_global(ngx, ngy, cap_h, cap_v, {}, global_nets, max_iters=global_max_iters)
    if verbose:
        print(f"   global: ok={gr.get('ok')} ngrid={ngx}x{ngy} overflow={gr.get('overflow')}", flush=True)

    # corridor gcells per net (from global guide edges); fall back to the net
    # bounding gcell box if a net has no global route (degenerate/1-pin).
    base_corr: Dict[str, Set[GCell]] = {}
    routes_g = gr.get("routes", {}) if isinstance(gr, dict) else {}
    for n in ordered:
        cells: Set[GCell] = set(gn for gn in (gcell_of(c) for tset in termsets[n.net] for c in tset))
        for edge in routes_g.get(n.net, ()):  # edge = (kind, x, y)
            _k, x, y = edge
            cells.add((x, y))
            if _k == "H":
                cells.add((min(x + 1, ngx - 1), y))
            else:
                cells.add((x, min(y + 1, ngy - 1)))
        base_corr[n.net] = _grow_gcells(cells, halo, ngx, ngy)

    # --- 2. detailed negotiated route, each net confined to its corridor -----
    by_name = {n.net: n for n in ordered}
    stuck: DefaultDict[str, int] = defaultdict(int)
    # Optional FlexTA seed: warm-start from a partial track-assignment so the
    # negotiated loop only has to finish the contended/unrouted nets (the
    # "option 1" TA -> golden-completion bridge). Seeded nets that stay clean
    # are never re-ripped (to_route starts as just the unrouted nets); the
    # standard congestion step still lets negotiation displace any blocker.
    routes: Dict[str, List[Cell]] = {nm: sorted(c) for nm, c in (seed_routes or {}).items()}
    edges: Dict[str, List[Tuple[Cell, Cell]]] = {nm: list(e) for nm, e in (seed_edges or {}).items()}
    history: DefaultDict[Cell, float] = defaultdict(float)
    directed_history: DefaultDict[Tuple[str, Cell], float] = defaultdict(float)
    pres = pres0
    wire_halo, via_halo = _halos(g, width_um, wire_clear_um, via_clear_um)
    to_route = [n.net for n in ordered if n.net not in routes] or [n.net for n in ordered]

    import time as _t
    for it in range(max_iters):
        _t0 = _t.time()
        occ = _occupancy(routes)
        for nm in to_route:
            net = by_name[nm]
            _remove_net(occ, nm)
            corr = _enlarge(base_corr[nm], stuck[nm], ngx, ngy)
            routed = _route_tree(g, net, termsets[nm], occ, history, directed_history,
                                 pres, wire_halo, via_halo, corr, GC)
            if routed is None:
                return RouteResult(False, routes, it + 1,
                                   ({"type": "unroutable", "net": nm,
                                     "detail": "terminal enclosed by keep-outs or corridor too tight"},), edges)
            net_cells, net_edges = routed
            routes[nm] = sorted(net_cells)
            edges[nm] = net_edges
            for cell in net_cells:
                occ[cell].add(nm)

        congestion, involved, owners_by_cell = _congestion(g, routes, edges, wire_halo, via_halo, termsets)
        if verbose:
            print(f"   iter {it+1}: rerouted={len(to_route)} congestion_cells={len(congestion)} "
                  f"involved={len(involved)} pres={pres:.1f} {(_t.time()-_t0):.1f}s", flush=True)
        if not congestion:
            return RouteResult(True, routes, it + 1, (), edges)
        for cell in congestion:
            history[cell] += hist_fac
            owners = sorted(owners_by_cell[cell])
            for loser in owners[1:]:
                directed_history[(loser, cell)] += hist_fac * max(4, 2 * len(owners))
        for nm in involved:
            stuck[nm] += 1
        to_route = sorted(involved)
        pres *= growth

    congestion, involved, _ = _congestion(g, routes, edges, wire_halo, via_halo, termsets)
    return RouteResult(False, routes, max_iters,
                       ({"type": "congestion", "nets": sorted(involved),
                         "detail": "did not converge; raise max_iters/halo or give more space"},), edges)


def _grow_gcells(cells: Set[GCell], halo: int, ngx: int, ngy: int) -> Set[GCell]:
    out = set(cells)
    for _ in range(max(0, halo)):
        ring = set()
        for (x, y) in out:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < ngx and 0 <= ny < ngy:
                    ring.add((nx, ny))
        out |= ring
    return out


def _enlarge(corr: Set[GCell], strikes: int, ngx: int, ngy: int) -> Set[GCell]:
    return corr if strikes <= 0 else _grow_gcells(corr, strikes, ngx, ngy)


def _route_tree(g, net, terminals, occ, history, directed_history, pres, wire_halo, via_halo, corr, GC):
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
        path = _astar(g, net.net, starts, target, occ, history, directed_history,
                      pres, wire_halo, via_halo, corr, GC)
        if path is None:
            return None
        for a, b in zip(path, path[1:]):
            edges.append((a, b))
        tree.update(path)
        tree.update(target & set(path))
    return tree, edges


def _astar(g, net, starts, goals, occ, history, directed_history, pres, wire_halo, via_halo, corr, GC):
    legal_goals = {c for c in goals if _legal_wire(g, c, net)}
    if not starts or not legal_goals:
        return None
    # allow the corridor plus the gcells of start/goal cells (terminals may sit
    # just outside the guide); membership test is by gcell.
    allowed = set(corr)
    for c in list(starts) + list(legal_goals):
        allowed.add((c[0] // GC, c[1] // GC))

    def in_corr(cell):
        return (cell[0] // GC, cell[1] // GC) in allowed

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
                if nxt not in legal_goals and not in_corr(nxt):
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
