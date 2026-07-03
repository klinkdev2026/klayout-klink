from collections import defaultdict, deque
import random

from klink.routing.grid.global_router import route_global


def _net(name, pins):
    return {"net": name, "pins": pins}


def _edge_endpoints(edge):
    kind, x, y = edge
    if kind == "H":
        return (x, y), (x + 1, y)
    return (x, y), (x, y + 1)


def _usage(routes):
    out = defaultdict(int)
    for edges in routes.values():
        for edge in edges:
            out[tuple(edge)] += 1
    return out


def _connected_to_pins(edges, pins):
    graph = defaultdict(set)
    for edge in edges:
        a, b = _edge_endpoints(tuple(edge))
        graph[a].add(b)
        graph[b].add(a)
    start = pins[0]
    seen = {start}
    queue = deque([start])
    while queue:
        point = queue.popleft()
        for nxt in graph[point]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return all(pin in seen for pin in pins)


def _assert_no_overflow(result, cap_h=1, cap_v=1):
    for edge, used in _usage(result["routes"]).items():
        kind = edge[0]
        cap = cap_h if kind == "H" else cap_v
        assert used <= cap, (edge, used, cap)


def test_capacity_respected_when_feasible():
    nets = [
        _net("A", [(0, 0), (9, 0)]),
        _net("B", [(0, 1), (9, 1)]),
        _net("C", [(0, 2), (9, 2)]),
    ]

    result = route_global(10, 4, 1, 1, {}, nets)

    assert result["ok"], result["problems"]
    assert result["overflow"] == 0
    _assert_no_overflow(result)


def test_infeasible_reports_congestion():
    nets = [
        _net("A", [(0, 0), (5, 0)]),
        _net("B", [(0, 0), (5, 0)]),
    ]

    result = route_global(6, 1, 1, 1, {}, nets, max_iters=8)

    assert not result["ok"]
    assert result["overflow"] > 0
    assert result["problems"][0]["type"] == "congestion"
    assert "increase coarse capacity" in result["problems"][0]["detail"]


def test_deterministic_same_input():
    nets = [
        _net("A", [(0, 0), (7, 4)]),
        _net("B", [(0, 4), (7, 0)]),
        _net("C", [(2, 0), (2, 4)]),
    ]

    first = route_global(8, 5, 2, 2, {}, nets, max_iters=20)
    second = route_global(8, 5, 2, 2, {}, nets, max_iters=20)

    assert first == second


def test_order_independent():
    base = [
        _net("N0", [(0, 0), (10, 0)]),
        _net("N1", [(0, 1), (10, 1)]),
        _net("N2", [(0, 2), (10, 2)]),
        _net("N3", [(2, 0), (2, 5)]),
        _net("N4", [(5, 0), (5, 5)]),
        _net("N5", [(8, 0), (8, 5)]),
    ]
    signatures = set()
    rng = random.Random(11)
    for _ in range(8):
        nets = list(base)
        rng.shuffle(nets)
        result = route_global(11, 6, 2, 2, {}, nets, max_iters=30)
        assert result["ok"], result["problems"]
        signatures.add(tuple((name, tuple(edges)) for name, edges in sorted(result["routes"].items())))
    assert len(signatures) == 1


def test_multiterminal_net_is_connected_tree():
    pins = [(0, 0), (8, 0), (8, 6), (2, 5)]
    result = route_global(9, 7, 1, 1, {}, [_net("TREE", pins)])

    assert result["ok"], result["problems"]
    assert _connected_to_pins(result["routes"]["TREE"], pins)
