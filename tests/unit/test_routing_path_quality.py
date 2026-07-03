from __future__ import annotations

import math

from klink.routing.geom.path_quality import simplify_route_points
from klink.routing.backends.geometric.steiner import plan_rectilinear_steiner_tree


def _length(points):
    return sum(
        math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
        for a, b in zip(points, points[1:])
    )


def _port(name, net, center, orientation, *, width=4.0, port_type="electrical"):
    return {
        "name": name,
        "net": net,
        "center_um": [float(center[0]), float(center[1])],
        "orientation": float(orientation),
        "width_um": float(width),
        "port_type": port_type,
        "target_layer": "12/0",
    }


def test_update_24_spur_is_pruned_with_numeric_reduction():
    before = [
        [0.01, 16.99],
        [0.01, 6.99],
        [0.01, 13.03],
        [39.72, 13.03],
        [39.72, 6.99],
    ]

    after = simplify_route_points(before, width_um=0.42)

    assert after == [
        [0.01, 16.99],
        [0.01, 13.03],
        [39.72, 13.03],
        [39.72, 6.99],
    ]
    assert (len(before) - 1) - (len(after) - 1) == 1
    assert round(_length(before) - _length(after), 2) == 12.08
    assert after[0] == before[0]
    assert after[-1] == before[-1]


def test_table_cases_cover_noop_zero_length_collinear_and_repeated_spurs():
    cases = [
        (
            [[0.0, 0.0], [10.0, 0.0]],
            [[0.0, 0.0], [10.0, 0.0]],
        ),
        (
            [[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]],
            [[0.0, 0.0], [10.0, 0.0]],
        ),
        (
            [[0.0, 0.0], [0.0, 0.0], [5.0, 0.0], [5.0, 0.0], [10.0, 0.0]],
            [[0.0, 0.0], [10.0, 0.0]],
        ),
        (
            [[0.0, 10.0], [0.0, 0.0], [0.0, 6.0], [0.0, 2.0], [0.0, 8.0]],
            [[0.0, 10.0], [0.0, 8.0]],
        ),
        (
            [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [10.0, 5.0]],
            [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [10.0, 5.0]],
        ),
    ]

    for before, expected in cases:
        assert simplify_route_points(before, width_um=1.0) == expected


def test_ambiguous_or_malformed_inputs_are_returned_unchanged_without_exception():
    ambiguous = [[0.0, 0.0], [10.0, 0.0], [-5.0, 0.0]]
    malformed = [[0.0, 0.0], [1.0], [2.0, 0.0]]

    assert simplify_route_points(ambiguous, width_um=1.0) == ambiguous
    assert simplify_route_points(malformed, width_um=1.0) == malformed
    assert simplify_route_points([[0.0, 0.0], [1.0, 0.0]], width_um=0.0) == [[0.0, 0.0], [1.0, 0.0]]


def test_simplify_route_points_is_idempotent_and_deterministic():
    points = [[0.0, 10.0], [0.0, 0.0], [0.0, 6.0], [8.0, 6.0], [12.0, 6.0]]

    first = simplify_route_points(points, width_um=2.0)
    second = simplify_route_points(points, width_um=2.0)
    third = simplify_route_points(first, width_um=2.0)

    assert first == second
    assert third == first


def test_steiner_planning_applies_collinear_merge_with_endpoint_equivalence():
    ports = [
        _port("ROOT", "bus", [0, 0], 0, width=9.0, port_type="root"),
        _port("SINK0", "bus", [130, -45], 180, width=3.0),
        _port("SINK1", "bus", [130, 0], 180, width=5.0),
        _port("SINK2", "bus", [130, 45], 180, width=7.0),
    ]

    result = plan_rectilinear_steiner_tree(ports, net="bus", root_name="ROOT")

    trunk = next(route for route in result["routes"] if route["kind"] == "trunk")
    root_branch = next(route for route in result["routes"] if route["kind"] == "branch" and route["source"] == "ROOT")
    assert trunk["points_um"] == [[118.0, -45.0], [118.0, 45.0]]
    assert root_branch["points_um"] == [[0.0, 0.0], [118.0, 0.0]]
    assert trunk["points_um"][0] == [118.0, -45.0]
    assert trunk["points_um"][-1] == [118.0, 45.0]
    assert root_branch["points_um"][0] == [0.0, 0.0]
    assert root_branch["points_um"][-1] == [118.0, 0.0]
    assert 2 - (len(trunk["points_um"]) - 1) == 1
    assert round(90.0 - _length(trunk["points_um"]), 6) == 0.0
    assert 2 - (len(root_branch["points_um"]) - 1) == 1
    assert round(118.0 - _length(root_branch["points_um"]), 6) == 0.0
