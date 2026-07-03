from collections import defaultdict, deque

import pytest

pytest.importorskip("klink_pathfinder_rs")

from klink.routing.grid.capacity_grid import NetInput, ViaRule, _terminal_cellsets, build_capacity_grid
from klink.routing.backends.negotiated.negotiated import route_negotiated as route_rust_or_fallback
from klink.routing.grid.pathfinder import route_negotiated as route_python


def _net(name, *points):
    return NetInput(name, [(float(x), float(y), layer) for x, y, layer in points])


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


def _crossing_bundle(pairs=10):
    size = 2 * pairs + 7
    via = ViaRule("M1/0", "M2/0", "v12", (0.0, 0.0), 2.0)
    g = build_capacity_grid(
        layers=("M1/0", "M2/0"),
        bbox_um=(0.0, 0.0, float(size - 1), float(size - 1)),
        pitch_um=1.0,
        channel_boxes_um=[],
        pad_boxes_by_layer={},
        device_body_boxes_um=[],
        via_rules=[via],
        via_footprint_um=0.0,
    )
    nets = []
    for i in range(pairs):
        y = 3 + 2 * i
        x = 3 + 2 * i
        nets.append(_net(f"H{i:02d}", (0, y, "M1/0"), (size - 1, y, "M1/0")))
        nets.append(_net(f"V{i:02d}", (x, 0, "M1/0"), (x, size - 1, "M1/0")))
    nets.append(_net("TREE", (1, 1, "M1/0"), (size - 2, 1, "M1/0"), (size - 2, size - 2, "M1/0")))
    return g, nets


def _forced_congestion():
    return _grid(9, 9), [
        _net("A", (0, 4, "M1/0"), (8, 4, "M1/0")),
        _net("B", (4, 0, "M1/0"), (4, 8, "M1/0")),
    ], {"max_iters": 40, "pres0": 0.1, "growth": 2.0}


def _bottleneck():
    gaps = {1, 2, 3}
    wall = {(4, y) for y in range(5) if y not in gaps}
    return _grid(9, 5, layers=("M1/0",), blocked=((0, wall),)), [
        _net("N0", (0, 1, "M1/0"), (8, 1, "M1/0")),
        _net("N1", (0, 2, "M1/0"), (8, 2, "M1/0")),
        _net("N2", (0, 3, "M1/0"), (8, 3, "M1/0")),
    ], {"max_iters": 40}


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


def _assert_functionally_equivalent(g, nets, kwargs):
    py = route_python(g, nets, **kwargs)
    rs = route_rust_or_fallback(g, nets, **kwargs)

    assert rs.ok == py.ok
    assert rs.ok, rs.problems
    assert _shared_cells(rs) == {}
    for net in nets:
        cells = set(rs.routes[net.net])
        assert _connected(cells, rs.edges[net.net])
        for terminals in _terminal_cellsets(g, net):
            assert cells & terminals


def test_rust_equivalent_for_forced_congestion():
    g, nets, kwargs = _forced_congestion()
    _assert_functionally_equivalent(g, nets, kwargs)


def test_rust_equivalent_for_bottleneck_capacity():
    g, nets, kwargs = _bottleneck()
    _assert_functionally_equivalent(g, nets, kwargs)


def test_rust_equivalent_for_full_adder_scale_congestion():
    g, nets = _crossing_bundle()
    _assert_functionally_equivalent(g, nets, {"max_iters": 80, "pres0": 0.1, "growth": 2.0})


def test_rust_equivalent_for_spacing_monotonic_sweep():
    for spacing_cells in range(3, 9):
        g, nets = _spacing_bundle(pairs=4, spacing_cells=spacing_cells)
        _assert_functionally_equivalent(
            g,
            nets,
            {
                "width_um": 5.0,
                "wire_clear_um": 2.0,
                "via_clear_um": 0.0,
                "max_iters": 140,
                "pres0": 0.1,
                "growth": 2.0,
            },
        )


def test_rust_deterministic_same_process():
    g, nets = _crossing_bundle(6)
    kwargs = {"max_iters": 80, "pres0": 0.1, "growth": 2.0}

    first = route_rust_or_fallback(g, nets, **kwargs)
    second = route_rust_or_fallback(g, nets, **kwargs)

    assert first.ok and second.ok
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
