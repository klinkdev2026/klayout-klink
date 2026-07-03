from collections import defaultdict, deque
import random

from klink.routing.grid.capacity_grid import NetInput, ViaRule, build_capacity_grid
from klink.routing.grid.pathfinder import route_negotiated


def _grid(width, height, *, layers=("M1/0", "M2/0"), blocked=()):
    via = ViaRule("M1/0", "M2/0", "v12", (0.0, 0.0), 2.0)
    g = build_capacity_grid(
        layers=layers,
        bbox_um=(0.0, 0.0, float(width - 1), float(height - 1)),
        pitch_um=1.0,
        channel_boxes_um=[],
        pad_boxes_by_layer={},
        device_body_boxes_um=[],
        via_rules=[via] if len(layers) > 1 else [],
        via_footprint_um=0.0,
    )
    for layer, cells in blocked:
        g.wire_blocked_all[layer].update(cells)
    return g


def _net(name, *points):
    return NetInput(name, [(float(x), float(y), layer) for x, y, layer in points])


def _shared_cells(result):
    owners = defaultdict(set)
    for net, cells in result.routes.items():
        for cell in cells:
            owners[cell].add(net)
    return {cell: nets for cell, nets in owners.items() if len(nets) > 1}


def _connected(cells, edges):
    if not cells:
        return True
    graph = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
        graph[b].add(a)
    start = next(iter(cells))
    seen = {start}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for nxt in graph[cell]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return set(cells) <= seen


def test_two_crossing_nets():
    g = _grid(9, 9)
    nets = [
        _net("A", (0, 4, "M1/0"), (8, 4, "M1/0")),
        _net("B", (4, 0, "M1/0"), (4, 8, "M1/0")),
    ]

    result = route_negotiated(g, nets, max_iters=40)

    assert result.ok, result.problems
    assert _shared_cells(result) == {}


def test_forced_congestion_converges():
    g = _grid(9, 9)
    nets = [
        _net("A", (0, 4, "M1/0"), (8, 4, "M1/0")),
        _net("B", (4, 0, "M1/0"), (4, 8, "M1/0")),
    ]

    result = route_negotiated(g, nets, max_iters=40, pres0=0.1, growth=2.0)

    assert result.ok, result.problems
    assert result.iterations > 1
    assert _shared_cells(result) == {}


def test_bottleneck_capacity_equals_demand():
    gaps = {1, 2, 3}
    wall = {(4, y) for y in range(5) if y not in gaps}
    g = _grid(9, 5, layers=("M1/0",), blocked=((0, wall),))
    nets = [
        _net("N0", (0, 1, "M1/0"), (8, 1, "M1/0")),
        _net("N1", (0, 2, "M1/0"), (8, 2, "M1/0")),
        _net("N2", (0, 3, "M1/0"), (8, 3, "M1/0")),
    ]

    result = route_negotiated(g, nets, max_iters=40)

    assert result.ok, result.problems
    assert _shared_cells(result) == {}
    used_gaps = {cell for cells in result.routes.values() for cell in cells if cell[0] == 4}
    assert used_gaps == {(4, 1, 0), (4, 2, 0), (4, 3, 0)}


def test_spacing_monotonic_converges_from_tight_to_loose():
    for spacing_cells in range(3, 9):
        g, nets = _spacing_bundle(pairs=4, spacing_cells=spacing_cells)
        result = route_negotiated(
            g,
            nets,
            width_um=5.0,
            wire_clear_um=2.0,
            via_clear_um=0.0,
            max_iters=140,
            pres0=0.1,
            growth=2.0,
        )
        assert result.ok, (spacing_cells, result.problems)
        assert _shared_cells(result) == {}


def test_terminal_access_overlap_is_not_route_congestion():
    g = _grid(9, 7, layers=("M1/0",))
    nets = [
        _net("A", (2, 1, "M1/0"), (8, 1, "M1/0")),
        _net("B", (2, 3, "M1/0"), (8, 5, "M1/0")),
    ]

    result = route_negotiated(g, nets, width_um=1.0, wire_clear_um=1.0, max_iters=40)

    assert result.ok, result.problems
    assert _shared_cells(result) == {}


def test_order_independent():
    base = [
        _net("N0", (0, 1, "M1/0"), (13, 1, "M1/0")),
        _net("N1", (0, 3, "M1/0"), (13, 3, "M1/0")),
        _net("N2", (0, 5, "M1/0"), (13, 5, "M1/0")),
        _net("N3", (2, 0, "M1/0"), (2, 7, "M1/0")),
        _net("N4", (6, 0, "M1/0"), (6, 7, "M1/0")),
        _net("N5", (10, 0, "M1/0"), (10, 7, "M1/0")),
        _net("N6", (1, 7, "M1/0"), (12, 0, "M1/0")),
        _net("N7", (1, 0, "M1/0"), (12, 7, "M1/0")),
    ]
    signatures = set()
    rng = random.Random(7)
    for _ in range(12):
        nets = list(base)
        rng.shuffle(nets)
        result = route_negotiated(_grid(14, 8), nets, max_iters=60)
        assert result.ok, result.problems
        assert _shared_cells(result) == {}
        signatures.add(tuple(sorted((net, tuple(cells)) for net, cells in result.routes.items())))
    assert len(signatures) == 1


def test_capacity_monotonic():
    nets = [
        _net("A", (0, 0, "M1/0"), (11, 0, "M1/0")),
        _net("B", (0, 1, "M1/0"), (11, 1, "M1/0")),
        _net("C", (0, 2, "M1/0"), (11, 2, "M1/0")),
        _net("D", (3, 0, "M1/0"), (3, 3, "M1/0")),
        _net("E", (7, 0, "M1/0"), (7, 3, "M1/0")),
    ]
    seen_success = False
    outcomes = []
    for height in range(3, 9):
        clipped = [n for n in nets if all(0 <= y < height for _, y, _ in n.access)]
        result = route_negotiated(_grid(12, height), clipped, max_iters=60)
        outcomes.append(result.ok)
        if seen_success:
            assert result.ok, outcomes
        seen_success = seen_success or result.ok
    assert seen_success, outcomes


def test_multiterminal_tree():
    g = _grid(12, 10)
    net = _net("TREE", (1, 1, "M1/0"), (10, 1, "M1/0"), (10, 8, "M1/0"), (2, 7, "M1/0"))

    result = route_negotiated(g, [net])

    assert result.ok, result.problems
    route = set(result.routes["TREE"])
    for terminals in net.terminal_cells or []:
        assert route & terminals
    for access in [(1, 1, 0), (10, 1, 0), (10, 8, 0), (2, 7, 0)]:
        assert access in route
    assert _connected(route, result.edges["TREE"])

    via12 = ViaRule("M1/0", "M2/0", "v12", (0.0, 0.0), 2.0)
    via23 = ViaRule("M2/0", "M3/0", "v23", (0.0, 0.0), 2.0)
    three_layer = build_capacity_grid(
        layers=("M1/0", "M2/0", "M3/0"),
        bbox_um=(0.0, 0.0, 8.0, 3.0),
        pitch_um=1.0,
        channel_boxes_um=[],
        pad_boxes_by_layer={},
        device_body_boxes_um=[],
        via_rules=[via12, via23],
        via_footprint_um=0.0,
    )
    stacked = _net("STACK", (0, 1, "M1/0"), (8, 1, "M3/0"))
    stacked_result = route_negotiated(three_layer, [stacked])
    assert stacked_result.ok, stacked_result.problems
    assert {cell[2] for cell in stacked_result.routes["STACK"]} == {0, 1, 2}


def test_unroutable_is_instructive():
    blocked = {
        (3, 2),
        (2, 3),
        (4, 3),
        (3, 4),
    }
    g = _grid(7, 7, blocked=((0, blocked), (1, blocked)))
    net = _net("TRAPPED", (3, 3, "M1/0"), (6, 6, "M1/0"))

    result = route_negotiated(g, [net])

    assert not result.ok
    assert result.problems[0]["type"] == "unroutable"
    assert "TRAPPED" in result.problems[0]["detail"]
    assert "leave an escape" in result.problems[0]["detail"]


def test_runs_twice_same_process():
    nets = [
        _net("A", (0, 2, "M1/0"), (8, 2, "M1/0")),
        _net("B", (4, 0, "M1/0"), (4, 5, "M1/0")),
    ]

    first = route_negotiated(_grid(9, 6), nets)
    second = route_negotiated(_grid(9, 6), nets)

    assert first.ok, first.problems
    assert second.ok, second.problems
    assert first.routes == second.routes
    assert first.edges == second.edges


def _spacing_bundle(pairs, spacing_cells):
    pitch = 5.0
    size_cells = (pairs - 1) * spacing_cells + 9
    max_um = (size_cells - 1) * pitch
    via = ViaRule("M1/0", "M2/0", "v12", (5.0, 5.0), 3.0)
    g = build_capacity_grid(
        layers=("M1/0", "M2/0"),
        bbox_um=(0.0, 0.0, max_um, max_um),
        pitch_um=pitch,
        channel_boxes_um=[],
        pad_boxes_by_layer={},
        device_body_boxes_um=[],
        via_rules=[via],
        via_footprint_um=5.0,
    )
    nets = []
    for i in range(pairs):
        y = (4 + i * spacing_cells) * pitch
        x = (4 + i * spacing_cells) * pitch
        nets.append(_net(f"H{i}", (0, y, "M1/0"), (max_um, y, "M1/0")))
        nets.append(_net(f"V{i}", (x, 0, "M1/0"), (x, max_um, "M1/0")))
    return g, nets
