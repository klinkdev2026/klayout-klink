"""Coarse gcell-edge global router.

This module is the Python entry point for router v2.  It uses the Rust
``klink_global_rs`` kernel when available and keeps a small pure-Python
reference fallback for no-wheel platforms and unit tests.
"""

from __future__ import annotations

from collections import defaultdict
from heapq import heappop, heappush
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

Edge = Tuple[str, int, int]
Pin = Tuple[int, int]


def route_global(
    nx: int,
    ny: int,
    cap_h: int | Sequence[Sequence[int]] = 1,
    cap_v: int | Sequence[Sequence[int]] = 1,
    blockages: Optional[Mapping[str, Iterable[Tuple[int, int]]]] = None,
    nets: Sequence[Mapping[str, object]] = (),
    *,
    max_iters: int = 80,
) -> Dict[str, object]:
    """Route ``nets`` on a coarse H/V edge grid.

    ``cap_h`` is indexed as ``cap_h[y][x]`` for edge ``("H", x, y)`` between
    ``(x, y)`` and ``(x + 1, y)``. ``cap_v`` is indexed as ``cap_v[y][x]`` for
    edge ``("V", x, y)`` between ``(x, y)`` and ``(x, y + 1)``.
    """

    payload = {
        "nx": nx,
        "ny": ny,
        "cap_h": cap_h if isinstance(cap_h, int) else _normalize_cap(cap_h, ny, max(0, nx - 1)),
        "cap_v": cap_v if isinstance(cap_v, int) else _normalize_cap(cap_v, max(0, ny - 1), nx),
        "blockages": _normalize_blockages(blockages or {}),
        "nets": _normalize_nets(nets),
        "max_iters": max_iters,
    }
    try:
        from klink_global_rs import route_global as _route_global_rs
    except Exception:
        return _route_global_py(payload)
    return _route_global_rs(payload)


def _route_global_py(payload: Mapping[str, object]) -> Dict[str, object]:
    nx = int(payload["nx"])
    ny = int(payload["ny"])
    cap_h = _normalize_cap(payload["cap_h"], ny, max(0, nx - 1))
    cap_v = _normalize_cap(payload["cap_v"], max(0, ny - 1), nx)
    blocked_h = {tuple(e) for e in payload["blockages"].get("H", [])}
    blocked_v = {tuple(e) for e in payload["blockages"].get("V", [])}
    for x, y in blocked_h:
        if 0 <= y < ny and 0 <= x < max(0, nx - 1):
            cap_h[y][x] = 0
    for x, y in blocked_v:
        if 0 <= y < max(0, ny - 1) and 0 <= x < nx:
            cap_v[y][x] = 0

    nets = sorted(payload["nets"], key=lambda n: n["net"])
    max_iters = int(payload["max_iters"])
    history: Dict[Edge, float] = defaultdict(float)
    routes: Dict[str, List[Edge]] = {}
    usage: Dict[Edge, int] = defaultdict(int)

    for net in nets:
        edges = _route_net(nx, ny, cap_h, cap_v, net["pins"], usage, history, 1, 0, pattern_only=True)
        if edges is None:
            return _fail(routes, usage, cap_h, cap_v, 0, "unroutable", [net["net"]])
        routes[net["net"]] = sorted(edges)
        _add_usage(usage, edges, 1)

    for it in range(max_iters + 1):
        overflow = _overflow(usage, cap_h, cap_v)
        if not overflow:
            return {"ok": True, "routes": routes, "overflow": 0, "iters": it, "problems": []}
        if it == max_iters:
            involved = sorted(_nets_on_edges(routes, overflow))
            return _fail(routes, usage, cap_h, cap_v, it, "congestion", involved)
        for edge in overflow:
            history[edge] += 1.0 + max(0, usage[edge] - _cap(edge, cap_h, cap_v))
        involved = _nets_on_edges(routes, overflow)
        for name in sorted(involved):
            _add_usage(usage, routes[name], -1)
            net = next(n for n in nets if n["net"] == name)
            edges = _route_net(
                nx, ny, cap_h, cap_v, net["pins"], usage, history, it + 2, it + 1, pattern_only=False
            )
            if edges is None:
                return _fail(routes, usage, cap_h, cap_v, it + 1, "unroutable", [name])
            routes[name] = sorted(edges)
            _add_usage(usage, edges, 1)

    return _fail(routes, usage, cap_h, cap_v, max_iters, "congestion", [])


def _route_net(nx, ny, cap_h, cap_v, pins, usage, history, pres, enlarge, pattern_only):
    pins = sorted({(int(x), int(y)) for x, y in pins})
    if not pins:
        return []
    tree = {pins[0]}
    edges: List[Edge] = []
    for target in pins[1:]:
        if target in tree:
            continue
        routed = _pattern_or_maze(
            nx, ny, cap_h, cap_v, tree, target, usage, history, pres, enlarge, pins, pattern_only
        )
        if routed is None:
            return None
        source, path = routed
        edges.extend(path)
        tree.update(_points_from_edges(path, source))
        tree.add(target)
    return list(dict.fromkeys(edges))


def _pattern_or_maze(nx, ny, cap_h, cap_v, tree, target, usage, history, pres, enlarge, pins, pattern_only):
    candidates = []
    for source in sorted(tree):
        for hv in (True, False):
            path = _pattern_path(source, target, hv)
            if all(_edge_ok(e, cap_h, cap_v) for e in path):
                cost = sum(_edge_cost(e, usage, history, cap_h, cap_v, pres) for e in path)
                candidates.append((cost, source, path))
    if candidates:
        best = min(candidates, key=lambda item: (item[0], item[2]))
        if pattern_only:
            return best[1], best[2]
        if not any(usage.get(edge, 0) + 1 > _cap(edge, cap_h, cap_v) for edge in best[2]):
            return best[1], best[2]
        maze = _maze(nx, ny, cap_h, cap_v, tree, target, usage, history, pres, enlarge, pins)
        if maze is None:
            return best[1], best[2]
        maze_source, maze_path = maze
        maze_cost = sum(_edge_cost(e, usage, history, cap_h, cap_v, pres) for e in maze_path)
        winner = min(best, (maze_cost, maze_source, maze_path), key=lambda item: (item[0], item[2]))
        return winner[1], winner[2]
    return _maze(nx, ny, cap_h, cap_v, tree, target, usage, history, pres, enlarge, pins)


def _maze(nx, ny, cap_h, cap_v, starts, target, usage, history, pres, enlarge, pins):
    xs = [p[0] for p in pins] + [target[0]]
    ys = [p[1] for p in pins] + [target[1]]
    xmin, xmax = max(0, min(xs) - enlarge - 2), min(nx - 1, max(xs) + enlarge + 2)
    ymin, ymax = max(0, min(ys) - enlarge - 2), min(ny - 1, max(ys) + enlarge + 2)
    starts = sorted(p for p in starts if xmin <= p[0] <= xmax and ymin <= p[1] <= ymax)
    if not starts:
        return None
    heap = []
    best = {}
    came = {}
    for s in starts:
        best[s] = 0.0
        heappush(heap, (_dist(s, target), 0.0, s))
    while heap:
        _, cost, point = heappop(heap)
        if point == target:
            return _maze_source(came, point), _reconstruct_edges(came, point)
        if cost > best.get(point, float("inf")) + 1e-12:
            continue
        for nxt, edge in _neighbors(point, nx, ny):
            if not (xmin <= nxt[0] <= xmax and ymin <= nxt[1] <= ymax):
                continue
            if not _edge_ok(edge, cap_h, cap_v):
                continue
            new_cost = cost + _edge_cost(edge, usage, history, cap_h, cap_v, pres)
            if new_cost + 1e-12 < best.get(nxt, float("inf")):
                best[nxt] = new_cost
                came[nxt] = (point, edge)
                heappush(heap, (new_cost + _dist(nxt, target), new_cost, nxt))
    return None


def _pattern_path(a: Pin, b: Pin, horizontal_first: bool) -> List[Edge]:
    x1, y1 = a
    x2, y2 = b
    edges = []
    if horizontal_first:
        edges.extend(_h_edges(x1, x2, y1))
        edges.extend(_v_edges(x2, y1, y2))
    else:
        edges.extend(_v_edges(x1, y1, y2))
        edges.extend(_h_edges(x1, x2, y2))
    return edges


def _h_edges(x1, x2, y):
    if x1 < x2:
        return [("H", x, y) for x in range(x1, x2)]
    if x1 > x2:
        return [("H", x, y) for x in range(x1 - 1, x2 - 1, -1)]
    return []


def _v_edges(x, y1, y2):
    if y1 < y2:
        return [("V", x, y) for y in range(y1, y2)]
    if y1 > y2:
        return [("V", x, y) for y in range(y1 - 1, y2 - 1, -1)]
    return []


def _neighbors(point, nx, ny):
    x, y = point
    if x + 1 < nx:
        yield (x + 1, y), ("H", x, y)
    if x > 0:
        yield (x - 1, y), ("H", x - 1, y)
    if y + 1 < ny:
        yield (x, y + 1), ("V", x, y)
    if y > 0:
        yield (x, y - 1), ("V", x, y - 1)


def _edge_cost(edge, usage, history, cap_h, cap_v, pres):
    cap = max(1, _cap(edge, cap_h, cap_v))
    over = max(0, usage.get(edge, 0) + 1 - cap)
    return 1.0 + history.get(edge, 0.0) + pres * over * over


def _edge_ok(edge, cap_h, cap_v):
    return _cap(edge, cap_h, cap_v) > 0


def _cap(edge, cap_h, cap_v):
    kind, x, y = edge
    if y < 0 or x < 0:
        return 0
    if kind == "H" and (y >= len(cap_h) or x >= len(cap_h[y])):
        return 0
    if kind == "V" and (y >= len(cap_v) or x >= len(cap_v[y])):
        return 0
    return cap_h[y][x] if kind == "H" else cap_v[y][x]


def _add_usage(usage, edges, delta):
    for edge in edges:
        usage[edge] += delta
        if usage[edge] == 0:
            del usage[edge]


def _overflow(usage, cap_h, cap_v):
    return {edge for edge, used in usage.items() if used > _cap(edge, cap_h, cap_v)}


def _nets_on_edges(routes, edges):
    return {net for net, route in routes.items() if any(edge in edges for edge in route)}


def _fail(routes, usage, cap_h, cap_v, iters, kind, nets):
    overflow = _overflow(usage, cap_h, cap_v)
    return {
        "ok": False,
        "routes": routes,
        "overflow": sum(max(0, usage[e] - _cap(e, cap_h, cap_v)) for e in overflow),
        "iters": iters,
        "problems": [{"type": kind, "nets": sorted(nets), "detail": "increase coarse capacity or routing area"}],
    }


def _points_from_edges(edges, start):
    points = {start}
    x, y = start
    for kind, ex, ey in edges:
        if kind == "H":
            x = ex + 1 if x == ex else ex
        else:
            y = ey + 1 if y == ey else ey
        points.add((x, y))
    return points


def _reconstruct_edges(came, end):
    edges = []
    cur = end
    while cur in came:
        prev, edge = came[cur]
        edges.append(edge)
        cur = prev
    edges.reverse()
    return edges


def _maze_source(came, end):
    cur = end
    while cur in came:
        cur = came[cur][0]
    return cur


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _normalize_cap(cap, rows, cols):
    if isinstance(cap, int):
        return [[cap for _ in range(cols)] for _ in range(rows)]
    return [[int(v) for v in row] for row in cap]


def _normalize_blockages(blockages):
    return {
        "H": sorted((int(x), int(y)) for x, y in blockages.get("H", ())),
        "V": sorted((int(x), int(y)) for x, y in blockages.get("V", ())),
    }


def _normalize_nets(nets):
    out = []
    for net in nets:
        out.append({"net": str(net["net"]), "pins": sorted((int(x), int(y)) for x, y in net["pins"])})
    return out
