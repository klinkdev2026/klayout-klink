from collections import defaultdict, deque

import pytest

klink_pathfinder_rs = pytest.importorskip("klink_pathfinder_rs")

if not hasattr(klink_pathfinder_rs, "route_bounded"):
    pytest.skip("installed klink_pathfinder_rs lacks route_bounded", allow_module_level=True)

from klink.routing.backends.negotiated.bounded_router import _python_route_bounded, route_bounded
from klink.routing.grid.capacity_grid import NetInput, ViaRule, _terminal_cellsets, build_capacity_grid


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


def _crossing_bundle(pairs=4):
    size = 2 * pairs + 7
    g = _grid(size, size)
    nets = []
    for i in range(pairs):
        y = 3 + 2 * i
        x = 3 + 2 * i
        nets.append(_net(f"H{i:02d}", (0, y, "M1/0"), (size - 1, y, "M1/0")))
        nets.append(_net(f"V{i:02d}", (x, 0, "M1/0"), (x, size - 1, "M1/0")))
    return g, nets


def _bottleneck():
    gaps = {1, 2, 3}
    wall = {(4, y) for y in range(5) if y not in gaps}
    return _grid(9, 5, layers=("M1/0",), blocked=((0, wall),)), [
        _net("N0", (0, 1, "M1/0"), (8, 1, "M1/0")),
        _net("N1", (0, 2, "M1/0"), (8, 2, "M1/0")),
        _net("N2", (0, 3, "M1/0"), (8, 3, "M1/0")),
    ]


def _spacing_bundle(pairs=4, spacing_cells=5):
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


def _assert_bounded_equivalent(g, nets, kwargs):
    py = _python_route_bounded(g, nets, **kwargs)
    rs = route_bounded(g, nets, **kwargs)

    assert rs.ok == py.ok
    assert rs.ok, rs.problems
    assert _shared_cells(rs) == {}
    for net in nets:
        cells = set(rs.routes[net.net])
        assert _connected(cells, rs.edges[net.net])
        for terminals in _terminal_cellsets(g, net):
            assert cells & terminals


def test_rust_bounded_equivalent_for_crossing_bundle():
    g, nets = _crossing_bundle()
    _assert_bounded_equivalent(
        g,
        nets,
        {"max_iters": 80, "pres0": 0.1, "growth": 2.0, "margin_cells": 4},
    )


def test_rust_bounded_equivalent_for_spacing_footprints():
    g, nets = _spacing_bundle()
    _assert_bounded_equivalent(
        g,
        nets,
        {
            "width_um": 5.0,
            "wire_clear_um": 2.0,
            "via_clear_um": 0.0,
            "max_iters": 120,
            "pres0": 0.1,
            "growth": 2.0,
            "margin_cells": 4,
        },
    )


def test_rust_bounded_equivalent_for_bottleneck_capacity():
    g, nets = _bottleneck()
    _assert_bounded_equivalent(g, nets, {"max_iters": 60, "margin_cells": 2})


def test_rust_bounded_deterministic_same_process():
    g, nets = _crossing_bundle(4)
    kwargs = {"max_iters": 80, "pres0": 0.1, "growth": 2.0, "margin_cells": 4}

    first = route_bounded(g, nets, **kwargs)
    second = route_bounded(g, nets, **kwargs)

    assert first.ok and second.ok
    assert first.routes == second.routes
    assert first.edges == second.edges


def _pad_keepout_grid():
    # Net "B" owns a vertical wall of pad cells (col 4, rows 3-5) on M1; net
    # "A" must route across and detour AROUND B's pad. This is the
    # owner-aware keep-out (capacity_grid._wire_ok foreign-pad rule) that
    # Rust route_bounded used to ignore -> 533 signal-PDN overlaps on add4.
    via = ViaRule("M1/0", "M2/0", "v12", (0.0, 0.0), 2.0)
    g = build_capacity_grid(
        layers=("M1/0", "M2/0"),
        bbox_um=(0.0, 0.0, 8.0, 8.0),
        pitch_um=1.0,
        channel_boxes_um=[],
        pad_boxes_by_layer={"M1/0": [("B", (4.0, 3.0, 4.0, 5.0))]},
        device_body_boxes_um=[],
        via_rules=[via],
        via_footprint_um=0.0,
    )
    return g, [_net("A", (0, 4, "M1/0"), (8, 4, "M1/0"))]


def _foreign_pad_violations(g, result):
    bad = []
    for net, cells in result.routes.items():
        for (ix, iy, layer) in cells:
            for owner, pad in g.pad_cells.get(layer, {}).items():
                if owner != net and (ix, iy) in pad:
                    bad.append((net, owner, ix, iy, layer))
    return bad


def test_rust_bounded_honors_foreign_pad_keepout():
    # Regression for the documented Rust route_bounded pad_cells bug. The
    # other equivalence tests all use empty pad_boxes_by_layer, so this is
    # the only one that exercises the foreign-pad keep-out path.
    g, nets = _pad_keepout_grid()
    pad_cells = g.pad_cells[0]["B"]
    assert (4, 4) in pad_cells, "pad wall must straddle A's straight path"

    kwargs = {"max_iters": 80, "margin_cells": 6}
    py = _python_route_bounded(g, nets, **kwargs)
    rs = route_bounded(g, nets, **kwargs)

    assert py.ok and rs.ok, (py.problems, rs.problems)
    assert _foreign_pad_violations(g, py) == [], "python golden entered foreign pad"
    assert _foreign_pad_violations(g, rs) == [], "rust route_bounded ignored pad_cells"
    assert rs.routes == py.routes
