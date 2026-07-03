"""Pure multilayer feature-grid routing (F2).

Lifts F1's single-layer Hanan graph to 3D: one plane per conductor
layer (built on a SHARED line basis so via points align), inter-plane
via-edges where a declared via cell can legally sit, and deterministic
A* over (x, y, layer) nodes.  Vias fall out as traversed via-edges; the
router knows the cost and legality of a layer change at search time
(not "route then insert vias then fix").

Pure and offline like F1: no KLayout, no router-backend imports.  Inputs
are plain per-layer terminal/obstacle data plus via descriptors; the
live connect_nets integration is a separate main-lane step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from klink.routing.grid.feature_grid import (
    FeatureGridError,
    NM_PER_UM,
    build_feature_grid,
)

Node3D = Tuple[int, int, str]          # (x_nm, y_nm, layer)
Point = Tuple[float, float]


@dataclass(frozen=True)
class ViaSpec:
    """A declared inter-layer transition (from the process stack)."""
    a: str                              # conductor layer "L/D"
    b: str                              # conductor layer "L/D"
    cell: str                           # via cell name (placed on use)
    footprint_um: Tuple[float, float]   # via landing size (w, h)
    cost_um: float = 0.0                # extra cost to discourage gratuitous vias


@dataclass(frozen=True)
class MultilayerGrid:
    nodes: Tuple[Node3D, ...]
    adjacency: Mapping[Node3D, Tuple[Tuple[Node3D, int, str], ...]]
    # rejected via candidates carry an instructive reason (binding,
    # user review): (x_nm, y_nm, a, b, reason)
    rejected_vias: Tuple[Tuple[int, int, str, str, str], ...]


def _q(v: float) -> int:
    return int(round(v * NM_PER_UM))


def _all_lines_nm(
    terminals_by_layer: Mapping[str, Sequence[Mapping[str, Any]]],
    obstacles_by_layer: Mapping[str, Sequence[Sequence[float]]],
) -> Tuple[List[int], List[int]]:
    """Shared line basis: union of every layer's terminal and obstacle
    feature lines, so a node at (x, y) exists on every plane and via
    points align."""
    xs: set[int] = set()
    ys: set[int] = set()
    for terms in terminals_by_layer.values():
        for t in terms:
            p = t.get("point_um")
            if isinstance(p, Sequence) and len(p) == 2:
                xs.add(_q(p[0])); ys.add(_q(p[1]))
    for obs in obstacles_by_layer.values():
        for b in obs:
            if isinstance(b, Sequence) and len(b) == 4:
                xs.add(_q(b[0])); xs.add(_q(b[2]))
                ys.add(_q(b[1])); ys.add(_q(b[3]))
    return sorted(xs), sorted(ys)


def _footprint_clear(
    cx_nm: int, cy_nm: int, fp_um: Tuple[float, float],
    obstacle_bboxes: Sequence[Sequence[float]],
) -> bool:
    hw = _q(fp_um[0] / 2.0)
    hh = _q(fp_um[1] / 2.0)
    fx1, fy1, fx2, fy2 = cx_nm - hw, cy_nm - hh, cx_nm + hw, cy_nm + hh
    for b in obstacle_bboxes:
        bx1, by1, bx2, by2 = (_q(b[0]), _q(b[1]), _q(b[2]), _q(b[3]))
        if min(fx2, bx2) > max(fx1, bx1) and min(fy2, by2) > max(fy1, by1):
            return False
    return True


def _footprint_swallows_terminal(
    cx_nm: int, cy_nm: int, fp_um: Tuple[float, float],
    terminal_points_nm: Sequence[Tuple[int, int]],
) -> bool:
    # terminal_points_nm are FOREIGN (other nets') terminals by
    # contract — never the routed net's own — so a coincident one is a
    # genuine swallow, not skipped.
    hw = _q(fp_um[0] / 2.0)
    hh = _q(fp_um[1] / 2.0)
    for tx, ty in terminal_points_nm:
        if abs(tx - cx_nm) < hw and abs(ty - cy_nm) < hh:
            return True
    return False


def _footprint_overlaps_forbidden(
    cx_nm: int, cy_nm: int, fp_um: Tuple[float, float],
    forbidden_nm: Sequence[Tuple[int, int, int, int]],
) -> bool:
    """True if a via footprint at (cx,cy) overlaps any via-forbidden box
    (device body/channel).  A via must never sit on a device."""
    hw = _q(fp_um[0] / 2.0)
    hh = _q(fp_um[1] / 2.0)
    fx1, fy1, fx2, fy2 = cx_nm - hw, cy_nm - hh, cx_nm + hw, cy_nm + hh
    for (bx1, by1, bx2, by2) in forbidden_nm:
        if min(fx2, bx2) > max(fx1, bx1) and min(fy2, by2) > max(fy1, by1):
            return True
    return False


def build_multilayer_grid(
    *,
    layers: Sequence[str],
    terminals_by_layer: Mapping[str, Sequence[Mapping[str, Any]]],
    obstacles_by_layer: Mapping[str, Sequence[Sequence[float]]],
    vias: Sequence[ViaSpec],
    width_um: float,
    min_spacing_um: float,
    foreign_terminals_nm: Sequence[Tuple[int, int]] = (),
    extra_x_lines_nm: Sequence[int] = (),
    extra_y_lines_nm: Sequence[int] = (),
    via_forbidden_boxes_um: Sequence[Sequence[float]] = (),
) -> MultilayerGrid:
    """Build the 3D routing graph.  ``foreign_terminals_nm`` are
    other-net terminal points a via landing must not swallow.
    ``extra_x_lines_nm`` / ``extra_y_lines_nm`` are caller-supplied grid
    lines (channel tracks + a shared cross-net line basis) merged into
    every plane's line basis (lesson 66).  ``via_forbidden_boxes_um`` are
    regions (e.g. whole device bodies) where a via may NEVER land even
    though wires may pass — a via on a device shorts/destroys it
    (lesson 67); this is DISTINCT from wire obstacles (e.g. the channel),
    which block wires too."""
    if not layers:
        raise FeatureGridError("at least one conductor layer required")
    bx, by = _all_lines_nm(terminals_by_layer, obstacles_by_layer)
    ex = sorted(set(bx) | set(extra_x_lines_nm))
    ey = sorted(set(by) | set(extra_y_lines_nm))

    per_layer: Dict[str, Any] = {}
    node_set: set[Node3D] = set()
    adjacency: Dict[Node3D, List[Tuple[Node3D, int, str]]] = {}
    for layer in layers:
        terms = list(terminals_by_layer.get(layer, []))
        obs = list(obstacles_by_layer.get(layer, []))
        # build this plane on the shared line basis; needs >=2 terminals
        # for F1's contract, so seed with the shared lines and a dummy
        # pair only when the layer has none of its own (escape/transit
        # layer). We pass the layer's real terminals; if <2, synthesize
        # two corner anchors on the shared basis so the plane still grids.
        seed = terms if len(terms) >= 2 else _corner_anchors(ex, ey)
        grid = build_feature_grid(
            seed, obs, width_um=width_um, min_spacing_um=min_spacing_um,
            extra_x_lines_nm=ex, extra_y_lines_nm=ey)
        per_layer[layer] = grid
        for (x, y) in grid.nodes:
            n = (x, y, layer)
            node_set.add(n)
            adjacency.setdefault(n, [])
        for a, b, cost in grid.edges:
            na = (a[0], a[1], layer)
            nb = (b[0], b[1], layer)
            adjacency[na].append((nb, cost, "wire"))
            adjacency[nb].append((na, cost, "wire"))

    # via-edges between the two planes of each declared via
    rejected: List[Tuple[int, int, str, str, str]] = []
    foreign = list(foreign_terminals_nm)
    forbid = [(_q(b[0]), _q(b[1]), _q(b[2]), _q(b[3]))
              for b in via_forbidden_boxes_um if len(b) == 4]
    for via in vias:
        if via.a not in per_layer or via.b not in per_layer:
            continue
        obs_a = list(obstacles_by_layer.get(via.a, []))
        obs_b = list(obstacles_by_layer.get(via.b, []))
        via_cost = _q(via.cost_um) + 1  # >0 so a via is never "free"
        common = sorted(
            {(x, y) for (x, y, lyr) in node_set if lyr == via.a}
            & {(x, y) for (x, y, lyr) in node_set if lyr == via.b})
        for (x, y) in common:
            if not _footprint_clear(x, y, via.footprint_um, obs_a):
                rejected.append((x, y, via.a, via.b,
                                 "landing not clear on layer " + via.a))
                continue
            if not _footprint_clear(x, y, via.footprint_um, obs_b):
                rejected.append((x, y, via.a, via.b,
                                 "landing not clear on layer " + via.b))
                continue
            if _footprint_swallows_terminal(x, y, via.footprint_um, foreign):
                rejected.append((x, y, via.a, via.b,
                                 "via landing would swallow a foreign terminal"))
                continue
            if _footprint_overlaps_forbidden(x, y, via.footprint_um, forbid):
                rejected.append((x, y, via.a, via.b,
                                 "via landing on a device keep-out (no via on a "
                                 "device body/channel — it shorts/destroys it)"))
                continue
            na = (x, y, via.a)
            nb = (x, y, via.b)
            adjacency[na].append((nb, via_cost, "via:" + via.cell))
            adjacency[nb].append((na, via_cost, "via:" + via.cell))

    nodes = tuple(sorted(node_set))
    frozen = {n: tuple(sorted(adjacency.get(n, []),
                              key=lambda it: (it[0], it[1], it[2])))
              for n in nodes}
    return MultilayerGrid(nodes=nodes, adjacency=frozen,
                          rejected_vias=tuple(rejected))


def _corner_anchors(ex: Sequence[int], ey: Sequence[int]) -> List[Mapping[str, Any]]:
    if not ex or not ey:
        raise FeatureGridError(
            "a transit layer with no terminals needs a non-empty shared "
            "line basis to grid")
    return [{"name": "_anchor0", "point_um": (ex[0] / NM_PER_UM, ey[0] / NM_PER_UM)},
            {"name": "_anchor1", "point_um": (ex[-1] / NM_PER_UM, ey[-1] / NM_PER_UM)}]


def _manhattan3(a: Node3D, b: Node3D) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shortest_path_3d(
    grid: MultilayerGrid, start: Node3D, goal: Node3D
) -> List[Node3D] | None:
    """Deterministic A* over (x, y, layer); heuristic ignores layer
    (admissible: a via only adds cost)."""
    if start not in grid.adjacency or goal not in grid.adjacency:
        return None
    heap: List[Tuple[int, int, Node3D]] = [(_manhattan3(start, goal), 0, start)]
    came: Dict[Node3D, Node3D] = {}
    best: Dict[Node3D, int] = {start: 0}
    seen: set[Node3D] = set()
    while heap:
        _, cost, node = heappop(heap)
        if node in seen:
            continue
        seen.add(node)
        if node == goal:
            path = [goal]
            while path[-1] in came:
                path.append(came[path[-1]])
            path.reverse()
            return path
        for nb, ec, _kind in grid.adjacency[node]:
            nc = cost + ec
            if nc < best.get(nb, 10**30):
                best[nb] = nc
                came[nb] = node
                heappush(heap, (nc + _manhattan3(nb, goal), nc, nb))
    return None


def route_net_multilayer(
    *,
    start_layer: str,
    goal_layer: str,
    start_um: Point,
    goal_um: Point,
    layers: Sequence[str],
    terminals_by_layer: Mapping[str, Sequence[Mapping[str, Any]]],
    obstacles_by_layer: Mapping[str, Sequence[Sequence[float]]],
    vias: Sequence[ViaSpec],
    width_um: float,
    min_spacing_um: float,
    foreign_terminals_nm: Sequence[Tuple[int, int]] = (),
) -> Dict[str, Any]:
    """Route one two-terminal net that may cross layers via declared
    vias.  Returns per-layer segments + via positions, or problems."""
    grid = build_multilayer_grid(
        layers=layers, terminals_by_layer=terminals_by_layer,
        obstacles_by_layer=obstacles_by_layer, vias=vias,
        width_um=width_um, min_spacing_um=min_spacing_um,
        foreign_terminals_nm=foreign_terminals_nm)
    start = (_q(start_um[0]), _q(start_um[1]), start_layer)
    goal = (_q(goal_um[0]), _q(goal_um[1]), goal_layer)
    path = shortest_path_3d(grid, start, goal)
    if path is None:
        return {"problems": [{
            "type": "no_path",
            "message": "no multilayer feature-grid path between terminals",
            "start": [start_um[0], start_um[1], start_layer],
            "goal": [goal_um[0], goal_um[1], goal_layer],
            "rejected_via_count": len(grid.rejected_vias),
        }]}
    # split into per-layer polylines + via transitions
    segments: List[Dict[str, Any]] = []
    vias_used: List[Dict[str, Any]] = []
    run: List[Node3D] = [path[0]]
    for prev, cur in zip(path, path[1:]):
        if cur[2] != prev[2]:                 # a via step (same x,y)
            if len(run) >= 2:
                segments.append(_run_to_segment(run))
            vias_used.append({"point_um": [prev[0] / NM_PER_UM,
                                           prev[1] / NM_PER_UM],
                              "from": prev[2], "to": cur[2]})
            run = [cur]
        else:
            run.append(cur)
    if len(run) >= 2:
        segments.append(_run_to_segment(run))
    length = sum(s["length_um"] for s in segments)
    # layers_used reflects every layer the PATH visits — including a
    # layer reached by a via with zero in-plane wire (the via lands at
    # the terminal), which emits no segment but is genuinely used
    return {"segments": segments, "vias": vias_used,
            "length_um": round(length, 6),
            "layers_used": sorted({n[2] for n in path})}


def _run_to_segment(run: Sequence[Node3D]) -> Dict[str, Any]:
    layer = run[0][2]
    pts = [(n[0] / NM_PER_UM, n[1] / NM_PER_UM) for n in run]
    # collinear compress
    compact = [pts[0]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        if not (a[0] == b[0] == c[0] or a[1] == b[1] == c[1]):
            compact.append(b)
    compact.append(pts[-1])
    length = sum(abs(p2[0] - p1[0]) + abs(p2[1] - p1[1])
                 for p1, p2 in zip(compact, compact[1:]))
    return {"layer": layer, "points_um": compact, "length_um": length}
