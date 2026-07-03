from __future__ import annotations

import pytest

from klink.routing.geom.geometric import (
    expand_obstacles_for_route,
    route_many_two_port_geometric,
    route_two_port_geometric,
)
from klink.routing.geom.geometry import crossing_pairs
from klink.routing.geom.geometry import route_hits_bboxes


def _port(name, net, center, orientation, width=4.0):
    return {
        "name": name,
        "net": net,
        "center_um": list(center),
        "orientation": orientation,
        "width_um": width,
        "target_layer": "10/0",
        "port_type": "electrical",
    }


def test_expand_obstacles_uses_half_width_and_safe_distance():
    expanded = expand_obstacles_for_route(
        [[40, -10, 60, 10]],
        route_width_um=4,
        safe_distance_um=3,
    )

    assert expanded == [[35.0, -15.0, 65.0, 15.0]]


def test_geometric_router_preserves_port_launch_and_avoids_obstacle():
    source = _port("A", "sig", [0, 0], 0)
    target = _port("B", "sig", [100, 0], 180)
    obstacle = [[40, -10, 60, 10]]

    route = route_two_port_geometric(
        source,
        target,
        obstacle_bboxes=obstacle,
        safe_distance_um=2,
        angle_mode="manhattan",
    )

    assert route["backend"] == "geometric_visibility_router"
    assert route["points_um"][0] == [0.0, 0.0]
    assert route["points_um"][1] == [8.0, 0.0]
    assert route["points_um"][-2] == [92.0, 0.0]
    assert route["points_um"][-1] == [100.0, 0.0]
    assert route["obstacle_hits"] == []
    assert route_hits_bboxes(route["points_um"], obstacle, route["width_um"]) == []
    assert any(point[1] != 0.0 for point in route["points_um"][2:-2])


def test_geometric_router_reports_endpoint_inside_expanded_obstacle():
    source = _port("A", "sig", [0, 0], 0)
    target = _port("B", "sig", [100, 0], 180)

    with pytest.raises(ValueError, match="endpoint is inside"):
        route_two_port_geometric(
            source,
            target,
            obstacle_bboxes=[[4, -3, 12, 3]],
            safe_distance_um=0,
        )


def test_geometric_router_supports_fortyfive_segments():
    source = _port("A", "sig", [0, 0], 0)
    target = _port("B", "sig", [36, 20], 180)

    route = route_two_port_geometric(
        source,
        target,
        angle_mode="fortyfive",
    )

    assert route["obstacle_hits"] == []
    segments = list(zip(route["points_um"], route["points_um"][1:]))
    assert any(
        abs(abs(b[0] - a[0]) - abs(b[1] - a[1])) < 1e-9 and a[0] != b[0]
        for a, b in segments
    )


def test_geometric_router_honors_required_points():
    source = _port("A", "sig", [0, 0], 0)
    target = _port("B", "sig", [100, 0], 180)

    route = route_two_port_geometric(
        source,
        target,
        required_points=[[50, 30]],
        angle_mode="manhattan",
    )

    assert [50.0, 30.0] in route["points_um"]
    idx = route["points_um"].index([50.0, 30.0])
    assert 1 < idx < len(route["points_um"]) - 2


def test_geometric_router_preserves_bend_center_and_side_exit_required_points():
    source = _port("A", "sig", [18, 5], 0)
    target = _port("B", "sig", [120, 5], 180)
    obstacle = [
        [65.87, -18.0, 86.0, 8.99],
        [20.31, 25.35, 47.36, 59.69],
    ]

    route = route_two_port_geometric(
        source,
        target,
        obstacle_bboxes=obstacle,
        required_points=[[69, 42], [75, 42]],
        angle_mode="manhattan",
    )

    assert [69.0, 42.0] in route["points_um"]
    assert [75.0, 42.0] in route["points_um"]
    center_idx = route["points_um"].index([69.0, 42.0])
    exit_idx = route["points_um"].index([75.0, 42.0])
    assert center_idx + 1 == exit_idx
    assert route["obstacle_hits"] == []


def test_many_geometric_routes_freeze_completed_paths_to_avoid_crossing():
    pairs = [
        (_port("A0", "h", [0, 0], 0), _port("B0", "h", [100, 0], 180)),
        (_port("A1", "v", [50, -50], 90), _port("B1", "v", [50, 50], 270)),
    ]

    routes = route_many_two_port_geometric(
        pairs,
        safe_distance_um=2,
        path_safe_distance_um=2,
        angle_mode="manhattan",
    )

    assert len(routes) == 2
    assert routes[1]["frozen_route_hits"] == []
    assert crossing_pairs(routes) == []
    assert any(point[0] != 50.0 for point in routes[1]["points_um"][2:-2])
