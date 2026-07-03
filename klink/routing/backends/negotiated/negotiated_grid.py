"""Negotiated multilayer routing (F3) — PathFinder on grid edges.

F2 (feature_grid_3d) routes ONE net on a multilayer grid with other
nets as hard obstacles, which greedy-sequentially cannot solve mutual
contention (cyclic displacement; STATUS Update 53: half-adder stalls at
12/13).  F3 lets all nets share the grid: an edge used by more than its
capacity is OVERUSED, priced by PathFinder present + history cost, and
the congested nets rip-up/reroute over bounded iterations until no edge
is overused — or it fails honestly naming the saturated edges.

Pure and offline like F1/F2: no KLayout, no router-backend imports
beyond the F2 grid builder.  Device obstacles are hard (baked into each
net's grid, with that net's own terminal pads excluded by the caller);
OTHER nets are soft, expressed only as edge usage cost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from klink.routing.grid.feature_grid_3d import (
    MultilayerGrid,
    Node3D,
    ViaSpec,
    build_multilayer_grid,
)

EdgeKey = Tuple[Node3D, Node3D]


def _edge_key(a: Node3D, b: Node3D) -> EdgeKey:
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True)
class NetRouteInput:
    net: str
    start_layer: str
    goal_layer: str
    start_um: Tuple[float, float]
    goal_um: Tuple[float, float]
    terminals_by_layer: Mapping[str, Sequence[Mapping[str, Any]]]
    obstacles_by_layer: Mapping[str, Sequence[Sequence[float]]]
    foreign_terminals_nm: Tuple[Tuple[int, int], ...] = ()
    # legs of the same electrical net share a group: siblings are NOT
    # foreign to each other (touching is correct, not a short) and do not
    # penalize each other in congestion. Empty -> the leg is its own net.
    group: str = ""


def _manhattan(a: Node3D, b: Node3D) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _box_overlap(a, b) -> bool:
    return (min(a[2], b[2]) > max(a[0], b[0])
            and min(a[3], b[3]) > max(a[1], b[1]))


def _edge_box_nm(node: Node3D, nb: Node3D, half_w_nm: int):
    """Wire footprint of one grid edge (same layer), inflated to the
    routed half-width."""
    return (min(node[0], nb[0]) - half_w_nm, min(node[1], nb[1]) - half_w_nm,
            max(node[0], nb[0]) + half_w_nm, max(node[1], nb[1]) + half_w_nm)


def _route_one(
    grid: MultilayerGrid,
    start: Node3D,
    goal: Node3D,
    *,
    usage: Mapping[EdgeKey, set],
    history: Mapping[EdgeKey, float],
    pres_fac: float,
    own_net: str,
    halos_by_layer: Optional[Mapping[str, Sequence[Tuple[Tuple[int, int, int, int], str]]]] = None,
    half_w_nm: int = 0,
) -> Optional[List[Node3D]]:
    """A* where an edge's cost is the PathFinder product:
    base * (1 + history) * (1 + pres_fac * (others_using + halo_hits)).
    Overlap is ALLOWED (priced, not blocked) so contention is
    negotiated, not greedily frozen.  ``halos_by_layer`` are OTHER nets'
    spacing halos (segment boxes inflated by width/2 + routing spacing);
    an edge whose wire enters a halo pays the spacing penalty, steering
    different nets apart.  Deterministic tie-break by node order."""
    if start not in grid.adjacency or goal not in grid.adjacency:
        return None
    halos_by_layer = halos_by_layer or {}
    heap: List[Tuple[float, int, Node3D]] = [(float(_manhattan(start, goal)), 0, start)]
    came: Dict[Node3D, Node3D] = {}
    best: Dict[Node3D, float] = {start: 0.0}
    seq = 0
    seen: set = set()
    while heap:
        _, _, node = heappop(heap)
        if node in seen:
            continue
        seen.add(node)
        if node == goal:
            path = [goal]
            while path[-1] in came:
                path.append(came[path[-1]])
            path.reverse()
            return path
        base_so_far = best[node]
        for nb, base_cost, _kind in grid.adjacency[node]:
            ek = _edge_key(node, nb)
            others = len(usage.get(ek, set()) - {own_net})
            halo_hits = 0
            if half_w_nm and node[2] == nb[2]:   # wire edge on one layer
                halos = halos_by_layer.get(node[2], ())
                if halos:
                    ebox = _edge_box_nm(node, nb, half_w_nm)
                    # only OTHER groups' halos steer this net away; a
                    # net's own siblings may run close (same electrical node)
                    halo_hits = sum(1 for (h, g) in halos
                                    if g != own_net and _box_overlap(ebox, h))
            hist = history.get(ek, 0.0)
            step = base_cost * (1.0 + hist) * (1.0 + pres_fac * (others + halo_hits))
            new_cost = base_so_far + step
            if new_cost < best.get(nb, float("inf")) - 1e-9:
                best[nb] = new_cost
                came[nb] = node
                seq += 1
                priority = new_cost + _manhattan(nb, goal)
                heappush(heap, (priority, seq, nb))
    return None


def _q(v: float) -> int:
    return int(round(v * 1000))


def _shared_basis_nm(
    nets: Sequence["NetRouteInput"],
    channel_pitch_um: float,
    channel_margin_um: float,
) -> Tuple[List[int], List[int]]:
    """OpenROAD tracks-in-channels (lesson 66): ONE line basis shared by
    every net so their grids share edges (present/history negotiation can
    fire) and channel tracks at the routing pitch give parallel room.
    Union of every net's terminal coords + obstacle edges, plus a uniform
    track lattice at ``channel_pitch_um`` spanning the (margin-expanded)
    bbox.  Pitch/margin are tunable process parameters (no hardcoding)."""
    xs: set = set()
    ys: set = set()
    for n in nets:
        for terms in n.terminals_by_layer.values():
            for t in terms:
                p = t.get("point_um")
                if p is not None and len(p) == 2:
                    xs.add(_q(p[0])); ys.add(_q(p[1]))
        for obs in n.obstacles_by_layer.values():
            for b in obs:
                if len(b) == 4:
                    xs.add(_q(b[0])); xs.add(_q(b[2]))
                    ys.add(_q(b[1])); ys.add(_q(b[3]))
    if channel_pitch_um and channel_pitch_um > 0 and xs and ys:
        pitch = _q(channel_pitch_um)
        margin = _q(channel_margin_um)
        x0, x1 = min(xs) - margin, max(xs) + margin
        y0, y1 = min(ys) - margin, max(ys) + margin
        x = x0
        while x <= x1:
            xs.add(x); x += pitch
        y = y0
        while y <= y1:
            ys.add(y); y += pitch
    return sorted(xs), sorted(ys)


def _group_of(n: "NetRouteInput") -> str:
    return n.group or n.net


def _foreign_pads_by_layer(
    nets: Sequence["NetRouteInput"],
    own_group: str,
    half_w_nm: int,
    spacing_nm: int,
) -> Dict[str, List[Tuple[float, float, float, float]]]:
    """Other nets' terminal pads as hard obstacles for ``own_group`` (a
    net must not run its wire through a foreign pad — that IS a short).
    Sibling legs (same group) are skipped: a net touching its own pads is
    correct.  Each pad is inflated to the wire half-width + routing
    spacing so the routed net is also kept clear of foreign pads."""
    pad = half_w_nm + spacing_nm
    out: Dict[str, List[Tuple[float, float, float, float]]] = defaultdict(list)
    for n in nets:
        if _group_of(n) == own_group:
            continue
        for layer, terms in n.terminals_by_layer.items():
            for t in terms:
                p = t.get("point_um")
                if p is None or len(p) != 2:
                    continue
                cx, cy = _q(p[0]), _q(p[1])
                out[layer].append(((cx - pad) / 1000.0, (cy - pad) / 1000.0,
                                   (cx + pad) / 1000.0, (cy + pad) / 1000.0))
    return out


@dataclass(frozen=True)
class NegotiatedResult:
    ok: bool
    routes: Mapping[str, List[Node3D]]
    iterations: int
    overused_edges: Tuple[EdgeKey, ...]
    problems: Tuple[Mapping[str, Any], ...]


def negotiated_route(
    nets: Sequence[NetRouteInput],
    *,
    layers: Sequence[str],
    vias: Sequence[ViaSpec],
    width_um: float,
    min_spacing_um: float,
    edge_capacity: int = 1,
    max_iters: int = 8,
    pres0: float = 0.5,
    growth: float = 1.6,
    hist_fac: float = 1.0,
    routing_spacing_um: float = 0.0,
    channel_pitch_um: float = 0.0,
    channel_margin_um: float = 0.0,
    pad_clearance_um: Optional[float] = None,
    via_forbidden_boxes_um: Sequence[Sequence[float]] = (),
) -> NegotiatedResult:
    """Route all nets on a SHARED multilayer grid with PathFinder
    negotiation.  ``channel_pitch_um`` (>0) overlays OpenROAD-style
    routing tracks at the routing pitch across the bbox so nets have
    parallel room (lesson 66); all nets then build on ONE shared line
    basis so their grids share edges and present/history congestion
    actually fires.  Other nets' terminal pads are hard obstacles for a
    net (a wire through a foreign pad is a short, lesson 65); same-layer
    spacing is enforced by halos when ``routing_spacing_um`` > 0."""
    half_w_nm = _q(width_um / 2.0)
    spacing_nm = _q(routing_spacing_um)
    # foreign device PADS are fixed and densely packed; a net must be able
    # to approach a neighbor's pad closely, so pad clearance is DECOUPLED
    # from wire-wire spacing.  Wire spacing (>= via_footprint/2) keeps
    # foreign wires out of via footprints; pad clearance only stops a wire
    # crossing a foreign pad.  Defaults to routing spacing for back-compat.
    pad_clear_nm = _q(pad_clearance_um) if pad_clearance_um is not None else spacing_nm
    shared_x, shared_y = _shared_basis_nm(nets, channel_pitch_um, channel_margin_um)
    grp_of: Dict[str, str] = {n.net: _group_of(n) for n in nets}
    grids: Dict[str, MultilayerGrid] = {}
    endpoints: Dict[str, Tuple[Node3D, Node3D]] = {}
    for n in nets:
        own_group = _group_of(n)
        # foreign pads (other groups' terminals) become hard obstacles so
        # a net cannot route its wire over another net's pad (a short);
        # sibling legs (same group) are not foreign
        foreign_pads = _foreign_pads_by_layer(nets, own_group, half_w_nm, pad_clear_nm)
        obs_by_layer = {
            lyr: list(n.obstacles_by_layer.get(lyr, [])) + foreign_pads.get(lyr, [])
            for lyr in layers}
        # foreign terminal points (nm) for via-landing-swallow gating
        foreign_pts = list(n.foreign_terminals_nm)
        for m in nets:
            if _group_of(m) == own_group:
                continue
            for terms in m.terminals_by_layer.values():
                for t in terms:
                    p = t.get("point_um")
                    if p is not None and len(p) == 2:
                        foreign_pts.append((_q(p[0]), _q(p[1])))
        grids[n.net] = build_multilayer_grid(
            layers=layers, terminals_by_layer=n.terminals_by_layer,
            obstacles_by_layer=obs_by_layer, vias=vias,
            width_um=width_um, min_spacing_um=min_spacing_um,
            foreign_terminals_nm=foreign_pts,
            extra_x_lines_nm=shared_x, extra_y_lines_nm=shared_y,
            via_forbidden_boxes_um=via_forbidden_boxes_um)
        endpoints[n.net] = (
            (_q(n.start_um[0]), _q(n.start_um[1]), n.start_layer),
            (_q(n.goal_um[0]), _q(n.goal_um[1]), n.goal_layer))

    history: Dict[EdgeKey, float] = defaultdict(float)
    order = [n.net for n in nets]
    pres_fac = pres0
    last_overused: List[EdgeKey] = []

    for it in range(max_iters):
        usage: Dict[EdgeKey, set] = defaultdict(set)
        routes: Dict[str, List[Node3D]] = {}
        # per-leg wire edges (for spacing checks), tagged with the net
        # GROUP, and a growing halo field also tagged by group
        net_edges: Dict[str, List[Tuple[str, str, Node3D, Node3D]]] = {}
        halos_by_layer: Dict[str, List[Tuple[Tuple[int, int, int, int], str]]] = defaultdict(list)
        failed: Optional[str] = None
        for net in order:
            grp = grp_of[net]
            start, goal = endpoints[net]
            path = _route_one(
                grids[net], start, goal, usage=usage, history=history,
                pres_fac=pres_fac, own_net=grp,
                halos_by_layer=halos_by_layer if spacing_nm else None,
                half_w_nm=half_w_nm if spacing_nm else 0)
            if path is None:
                failed = net
                break
            routes[net] = path
            wedges = []
            for a, b in zip(path, path[1:]):
                usage[_edge_key(a, b)].add(grp)
                if a[2] == b[2]:                     # wire edge on one layer
                    wedges.append((grp, a[2], a, b))
                    if spacing_nm:
                        halos_by_layer[a[2]].append(
                            (_edge_box_nm(a, b, half_w_nm + spacing_nm), grp))
            net_edges[net] = wedges
        if failed is not None:
            return NegotiatedResult(
                ok=False, routes=routes, iterations=it + 1,
                overused_edges=(),
                problems=({"type": "no_path", "net": failed,
                           "message": "no grid path even with overlap "
                           "allowed; the net is hard-blocked by device "
                           "geometry, not contention"},))
        overused = sorted(ek for ek, who in usage.items()
                          if len(who) > edge_capacity)
        # spacing violations: different nets' wire edges closer than the
        # routing spacing on the same layer (the parasitic-too-close
        # problem the user flagged). Recorded as contended edge keys.
        spacing_bad: set = set()
        congestion: Dict[str, int] = defaultdict(int)
        if spacing_nm:
            # items already tagged by GROUP: a spacing violation between
            # two SIBLING legs (same group) is not a short (same node)
            items = [(grp, lyr, a, b)
                     for we in net_edges.values() for (grp, lyr, a, b) in we]
            for i in range(len(items)):
                ni, li, ai, bi = items[i]
                box_i = _edge_box_nm(ai, bi, half_w_nm + spacing_nm)
                for j in range(i + 1, len(items)):
                    nj, lj, aj, bj = items[j]
                    if ni == nj or li != lj:
                        continue
                    if _box_overlap(box_i, _edge_box_nm(aj, bj, half_w_nm)):
                        spacing_bad.add(_edge_key(ai, bi))
                        spacing_bad.add(_edge_key(aj, bj))
                        congestion[ni] += 1
                        congestion[nj] += 1
        if not overused and not spacing_bad:
            return NegotiatedResult(ok=True, routes=routes,
                                    iterations=it + 1,
                                    overused_edges=(), problems=())
        contended = sorted(set(overused) | spacing_bad)
        last_overused = contended
        for ek in contended:
            history[ek] += hist_fac
        pres_fac *= growth
        # reorder: most-contended groups route first next round
        for ek in overused:
            for grp in usage[ek]:
                congestion[grp] += 1
        order = sorted(order, key=lambda n: (-congestion.get(grp_of[n], 0), n))

    return NegotiatedResult(
        ok=False, routes={}, iterations=max_iters,
        overused_edges=tuple(last_overused),
        problems=({"type": "not_converged",
                   "message": "negotiation did not clear all overuse in "
                   f"{max_iters} iterations; {len(last_overused)} edges "
                   "remain contended — placement may be too tight "
                   "(raise pitch) or needs more routing layers",
                   "overused_edge_count": len(last_overused)},))
