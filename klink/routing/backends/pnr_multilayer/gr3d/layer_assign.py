"""3D global route / layer assignment (Stage T3, scoped).

Faithful (scoped) to OpenROAD FastRoute 3D
(`OpenROAD src/grt/src/fastroute/src/maze3D.cpp`). See the mapping table in this
module's docstring first (data/cost/transition model + scope).

The job: lift the 2D global route (per-net gcell H/V edges) onto LAYERS so each guide
segment carries a layer of its own direction, paying a via at every layer change. A
negotiated 3D maze (PathFinder, the same congestion shape as our 2D `global_router`,
lifted to (x,y,z)) does the assignment; congestion SPREADS same-direction demand across
the parallel same-direction signal layers (the measured lever -- one H layer = a
bottleneck; two balanced H layers clear). Pins are anchored LOW at their access-point
terminal layer; the backbone routes HIGH on the clean signal stack and vias down.

Faithful cost (getMazeRouteCost3D, maze3D:680): wire step = base 1.0 + congestion;
via step = via_cost + congestion. Direction legality: an H gcell-edge is legal ONLY on
an H-direction layer, a V edge ONLY on a V layer (this is what forces a via at every
H<->V bend). Timing/slack-aware ordering and via resistance are DEFERRED (named, §2 of
the doc), not invented.

Pure, offline, generic: the stack (layers, directions, via ladder, which layers are
signal vs terminal, tracks per gcell) is DATA from `ProcessProfile`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

# A 3D edge id. Planar: (z, "H"|"V", ex, ey). Via: (z_lo, "U", x, y) bridging z_lo/z_lo+1.
Edge3D = Tuple[int, str, int, int]
Node = Tuple[int, int, int]            # (gx, gy, z)


@dataclass(frozen=True)
class LayerStack:
    """The routing stack as DATA. ``z`` indexes layers low->high.

    * ``dirs[z]`` = "H" | "V" preferred direction of layer z.
    * ``signal`` = z-indices that carry signal BACKBONES (planar runs allowed).
    * ``terminal`` = z-indices where pins/APs live (via-only: no planar backbone, the
      clean-separation rule -- signals never run on a terminal layer).
    * ``via_pairs`` = adjacent (z, z+1) the via ladder bridges.
    * ``tracks_per_gcell`` = planar edge capacity per layer (gcell spans this many
      tracks); via capacity is generous (vias are not the congestion driver in T3).
    """

    dirs: Tuple[str, ...]
    signal: frozenset
    terminal: frozenset
    via_pairs: frozenset
    tracks_per_gcell: int = 1
    via_capacity: int = 1000

    @property
    def nz(self) -> int:
        return len(self.dirs)

    def planar_ok(self, z: int, kind: str) -> bool:
        """A planar move of orientation ``kind`` is legal on layer z iff z is a signal
        layer and its direction matches (H edge on H layer, V edge on V layer)."""
        return z in self.signal and self.dirs[z] == kind


def stack_from_profile(profile) -> LayerStack:
    """Derive the stack from a ``ProcessProfile`` (generic, no hardcode).

    Signal layers = ``profile.signal_routing_layers()`` (the dedicated clean stack when
    set; else all routing layers). Terminal layers = gate/sd layers. Via ladder =
    adjacent routing-layer index pairs that a profile via bridges.
    """
    layers = list(profile.routing_layers)
    li = {L: i for i, L in enumerate(layers)}
    dirs = tuple(profile.layer_direction(L) for L in layers)
    signal = frozenset(li[L] for L in profile.signal_routing_layers() if L in li)
    terminal = frozenset(
        li[L] for L in (profile.gate_layer, profile.sd_layer) if L in li
    )
    via_pairs = set()
    for lo, _cut, up in profile.vias:
        if lo in li and up in li:
            a, b = sorted((li[lo], li[up]))
            if b == a + 1:
                via_pairs.add((a, b))
    return LayerStack(dirs=dirs, signal=signal, terminal=terminal,
                      via_pairs=frozenset(via_pairs))


@dataclass
class AssignResult:
    ok: bool
    guides: Dict[str, List[Edge3D]]    # net -> 3D edges (planar + via)
    overflow: int
    iters: int
    used_planar_layers: Dict[str, Set[int]] = field(default_factory=dict)  # net -> {z}
    problems: Tuple[dict, ...] = ()

    def vias(self, net: str) -> List[Edge3D]:
        return [e for e in self.guides.get(net, ()) if e[1] == "U"]

    def planar(self, net: str) -> List[Edge3D]:
        return [e for e in self.guides.get(net, ()) if e[1] in ("H", "V")]


def _cap(stack: LayerStack, e: Edge3D) -> int:
    z, kind, _x, _y = e
    if kind == "U":
        return stack.via_capacity
    return stack.tracks_per_gcell if stack.planar_ok(z, kind) else 0


def _step_cost(stack: LayerStack, e: Edge3D, usage, history, pres, via_cost) -> float:
    base = via_cost if e[1] == "U" else 1.0
    cap = max(1, _cap(stack, e))
    over = max(0, usage.get(e, 0) + 1 - cap)
    return base + history.get(e, 0.0) + pres * over * over


def _neighbors(stack: LayerStack, node: Node, nx: int, ny: int,
               corridor: Optional[Set[Tuple[int, int]]]):
    """Yield (next_node, edge3d). Planar moves only on matching signal layers and (when
    a corridor is given) only within it; via moves along the ladder."""
    x, y, z = node
    # planar
    if stack.planar_ok(z, "H"):
        if x + 1 < nx and (corridor is None or (x + 1, y) in corridor):
            yield (x + 1, y, z), (z, "H", x, y)
        if x - 1 >= 0 and (corridor is None or (x - 1, y) in corridor):
            yield (x - 1, y, z), (z, "H", x - 1, y)
    if stack.planar_ok(z, "V"):
        if y + 1 < ny and (corridor is None or (x, y + 1) in corridor):
            yield (x, y + 1, z), (z, "V", x, y)
        if y - 1 >= 0 and (corridor is None or (x, y - 1) in corridor):
            yield (x, y - 1, z), (z, "V", x, y - 1)
    # via ladder
    if (z, z + 1) in stack.via_pairs:
        yield (x, y, z + 1), (z, "U", x, y)
    if (z - 1, z) in stack.via_pairs:
        yield (x, y, z - 1), (z - 1, "U", x, y)


def _maze3d(stack, nx, ny, starts, goal, usage, history, pres, via_cost, corridor):
    gx, gy, gz = goal

    def h(n: Node) -> float:
        return abs(n[0] - gx) + abs(n[1] - gy) + abs(n[2] - gz)

    heap: List[Tuple[float, float, Node]] = []
    best: Dict[Node, float] = {}
    came: Dict[Node, Tuple[Node, Edge3D]] = {}
    for s in starts:
        best[s] = 0.0
        heappush(heap, (h(s), 0.0, s))
    while heap:
        _, cost, node = heappop(heap)
        if node == goal:
            edges: List[Edge3D] = []
            cur = node
            while cur in came:
                prev, e = came[cur]
                edges.append(e)
                cur = prev
            edges.reverse()
            return _root(came, node), edges
        if cost > best.get(node, float("inf")) + 1e-12:
            continue
        for nxt, e in _neighbors(stack, node, nx, ny, corridor):
            if _cap(stack, e) <= 0:
                continue
            nc = cost + _step_cost(stack, e, usage, history, pres, via_cost)
            if nc + 1e-12 < best.get(nxt, float("inf")):
                best[nxt] = nc
                came[nxt] = (node, e)
                heappush(heap, (nc + h(nxt), nc, nxt))
    return None


def _root(came, end):
    cur = end
    while cur in came:
        cur = came[cur][0]
    return cur


def _nodes_of(edges: Sequence[Edge3D], seed: Node) -> Set[Node]:
    """Reconstruct the set of nodes a 3D edge list touches (for the connected tree)."""
    nodes: Set[Node] = {seed}
    for (z, kind, ex, ey) in edges:
        if kind == "H":
            nodes.add((ex, ey, z))
            nodes.add((ex + 1, ey, z))
        elif kind == "V":
            nodes.add((ex, ey, z))
            nodes.add((ex, ey + 1, z))
        else:  # via z<->z+1
            nodes.add((ex, ey, z))
            nodes.add((ex, ey, z + 1))
    return nodes


def assign_layers(
    nx: int,
    ny: int,
    stack: LayerStack,
    nets: Sequence[Mapping[str, object]],
    *,
    corridors: Optional[Mapping[str, Set[Tuple[int, int]]]] = None,
    via_cost: float = 2.0,
    max_iters: int = 40,
    pres0: float = 0.5,
    growth: float = 1.6,
    hist_fac: float = 1.0,
) -> AssignResult:
    """Assign layers to the 2D route by a negotiated 3D maze.

    ``nets`` = ``[{"net": name, "terminals": [(gx,gy,z_term), ...]}]``: each terminal is
    a gcell at a layer (the AP's terminal layer, from T2). Connects every net's
    terminals through the signal stack + via ladder, spreading congestion across
    same-direction signal layers. Returns layer-resolved guides for FlexTA (T4).
    """
    nets = sorted(nets, key=lambda n: str(n["net"]))
    history: Dict[Edge3D, float] = defaultdict(float)
    order = [str(n["net"]) for n in nets]
    by_name = {str(n["net"]): n for n in nets}
    pres = pres0

    def route_net(name, usage) -> Optional[List[Edge3D]]:
        terms = [tuple(t) for t in by_name[name]["terminals"]]
        corridor = corridors.get(name) if corridors else None
        if not terms:
            return []
        tree: Set[Node] = {terms[0]}
        all_edges: List[Edge3D] = []
        for goal in terms[1:]:
            if goal in tree:
                continue
            res = _maze3d(stack, nx, ny, sorted(tree), goal, usage,
                          history, pres, via_cost, corridor)
            if res is None:
                return None
            _src, edges = res
            all_edges.extend(edges)
            tree |= _nodes_of(edges, _src)
            tree.add(goal)
        return list(dict.fromkeys(all_edges))

    routes: Dict[str, List[Edge3D]] = {}
    usage: Dict[Edge3D, int] = defaultdict(int)
    for name in order:
        edges = route_net(name, usage)
        if edges is None:
            return AssignResult(False, routes, 0, 0,
                                problems=({"type": "unroutable", "net": name},))
        routes[name] = edges
        for e in edges:
            usage[e] += 1

    for it in range(max_iters + 1):
        overflow = {e for e, u in usage.items() if u > _cap(stack, e)}
        if not overflow:
            used = {n: {e[0] for e in es if e[1] in ("H", "V")}
                    for n, es in routes.items()}
            return AssignResult(True, routes, 0, it + 1, used_planar_layers=used)
        if it == max_iters:
            break
        for e in overflow:
            history[e] += hist_fac * (1.0 + max(0, usage[e] - _cap(stack, e)))
        involved = sorted({n for n, es in routes.items()
                           if any(e in overflow for e in es)})
        pres *= growth
        for name in involved:
            for e in routes[name]:
                usage[e] -= 1
                if usage[e] == 0:
                    del usage[e]
            edges = route_net(name, usage)
            if edges is None:
                return AssignResult(False, routes, 0, it + 1,
                                    problems=({"type": "unroutable", "net": name},))
            routes[name] = edges
            for e in edges:
                usage[e] += 1

    total_over = sum(max(0, usage[e] - _cap(stack, e))
                     for e in usage if usage[e] > _cap(stack, e))
    used = {n: {e[0] for e in es if e[1] in ("H", "V")} for n, es in routes.items()}
    return AssignResult(False, routes, total_over, max_iters,
                        used_planar_layers=used,
                        problems=({"type": "congestion", "overflow": total_over},))
