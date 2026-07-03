from __future__ import annotations

from klink.routing.core.intent import build_route_intent
from klink.routing.geom.planner import plan_routes_from_intent


def _port(name, net, center, orientation, width=4.0, port_type="electrical"):
    return {
        "name": name,
        "net": net,
        "center_um": list(center),
        "orientation": orientation,
        "width_um": width,
        "target_layer": "10/0",
        "port_type": port_type,
    }


def test_plan_simple_two_port_route_preserves_launch_stubs():
    intent = build_route_intent([
        _port("A", "sig", [20, 5], 0),
        _port("B", "sig", [100, 5], 180),
    ])

    result = plan_routes_from_intent(intent)

    assert result["ok"] is True
    route = result["routes"][0]
    assert route["points_um"][0] == [20.0, 5.0]
    assert route["points_um"][1] == [28.0, 5.0]
    assert route["points_um"][-2][0] == 92.0
    assert route["width_um"] == 4.0


def test_plan_waypoint_route_passes_waypoint_anchor():
    ports = [
        _port("A", "sig", [18, 5], 0),
        _port("B", "sig", [100, 5], 180),
    ]
    anchors = [
        {
            "id": "WP1",
            "kind": "waypoint_region",
            "net": "sig",
            "center_um": [60, 40],
            "width_um": 12,
            "height_um": 10,
        }
    ]
    intent = build_route_intent(ports, anchors)

    result = plan_routes_from_intent(intent)

    assert result["ok"] is True
    assert [60.0, 40.0] in result["routes"][0]["points_um"]


def test_geometric_plan_waypoint_route_passes_waypoint_anchor():
    ports = [
        _port("A", "sig", [18, 5], 0),
        _port("B", "sig", [100, 5], 180),
    ]
    anchors = [
        {
            "id": "WP1",
            "kind": "waypoint_region",
            "net": "sig",
            "center_um": [60, 40],
            "width_um": 12,
            "height_um": 10,
        }
    ]
    intent = build_route_intent(ports, anchors)

    result = plan_routes_from_intent(intent, router_backend="geometric")

    assert result["ok"] is True
    route = result["routes"][0]
    assert route["backend"] == "geometric_visibility_router"
    assert [60.0, 40.0] in route["points_um"]


def test_plan_obstacle_route_uses_bend_anchor_to_avoid_keepout():
    ports = [
        _port("A", "sig", [18, 5], 0),
        _port("B", "sig", [120, 5], 180),
    ]
    anchors = [
        {
            "id": "BEND_ABOVE",
            "kind": "bend_region",
            "net": "sig",
            "center_um": [69, 42],
            "radius_um": 6,
        }
    ]
    intent = build_route_intent(ports, anchors)

    result = plan_routes_from_intent(
        intent,
        obstacle_bboxes=[[52, -18, 86, 28]],
        obstacle_layers=["900/0"],
    )

    assert result["ok"] is True
    assert result["obstacle_hits"] == []
    route = result["routes"][0]
    assert [48.0, 5.0] in route["points_um"]
    assert [48.0, 42.0] in route["points_um"]
    assert [69.0, 42.0] in route["points_um"]
    assert [69.0, 36.0] in route["points_um"]
    assert [90.0, 36.0] in route["points_um"]
    assert [90.0, 5.0] in route["points_um"]


def test_geometric_plan_obstacle_route_uses_bend_anchor_and_avoids_keepout():
    ports = [
        _port("A", "sig", [18, 5], 0),
        _port("B", "sig", [120, 5], 180),
    ]
    anchors = [
        {
            "id": "BEND_ABOVE",
            "kind": "bend_region",
            "net": "sig",
            "center_um": [69, 42],
            "radius_um": 6,
        }
    ]
    intent = build_route_intent(ports, anchors)

    result = plan_routes_from_intent(
        intent,
        obstacle_bboxes=[[52, -18, 86, 28]],
        obstacle_layers=["900/0"],
        router_backend="geometric",
        safe_distance_um=0,
    )

    assert result["ok"] is True
    route = result["routes"][0]
    assert route["backend"] == "geometric_visibility_router"
    assert route["obstacle_hits"] == []
    assert [69.0, 36.0] in route["points_um"]
    assert [69.0, 42.0] in route["points_um"]
    assert [75.0, 42.0] in route["points_um"]
    approach_idx = route["points_um"].index([69.0, 36.0])
    center_idx = route["points_um"].index([69.0, 42.0])
    exit_idx = route["points_um"].index([75.0, 42.0])
    assert approach_idx + 1 == center_idx
    assert center_idx + 1 == exit_idx


def test_geometric_plan_orders_corridor_and_bend_by_route_progress_without_self_overlap():
    ports = [
        _port("A", "sig", [18, 5], 0),
        _port("B", "sig", [120, 5], 180),
    ]
    anchors = [
        {
            "id": "BEND_ABOVE",
            "kind": "bend_region",
            "net": "sig",
            "center_um": [69, 42],
            "radius_um": 6,
            "priority": 10,
        },
        {
            "id": "CORRIDOR_LEFT",
            "kind": "corridor",
            "net": "sig",
            "center_um": [38.5, 2.21],
            "width_um": 20,
            "path_points": "-7.824,7.415;6.576,-4.795;13.626,-4.325",
            "priority": 0,
        },
    ]
    intent = build_route_intent(ports, anchors)

    result = plan_routes_from_intent(
        intent,
        obstacle_bboxes=[
            [59.86, 3.85, 79.99, 30.84],
            [20.31, 25.35, 47.36, 59.69],
            [98.43, 27.46, 118.56, 54.45],
        ],
        obstacle_layers=["900/0"],
        router_backend="geometric",
    )

    assert result["ok"] is True
    assert result["self_crossings"] == []
    route = result["routes"][0]
    assert route["self_crossings"] == []
    corridor_entry = [30.676000000000002, 9.625]
    corridor_exit = [52.126, -2.115]
    bend_approach = [69.0, 36.0]
    bend_center = [69.0, 42.0]
    bend_exit = [75.0, 42.0]
    for point in [corridor_entry, corridor_exit, bend_approach, bend_center, bend_exit]:
        assert point in route["points_um"]
    assert route["points_um"].index(corridor_entry) < route["points_um"].index(corridor_exit)
    assert route["points_um"].index(corridor_exit) < route["points_um"].index(bend_approach)
    assert route["points_um"].index(bend_approach) + 1 == route["points_um"].index(bend_center)
    assert route["points_um"].index(bend_center) + 1 == route["points_um"].index(bend_exit)


def test_plan_obstacle_cell_without_obstacle_layers_uses_bend_anchor_only():
    ports = [
        _port("A", "sig", [18, 5], 0),
        _port("B", "sig", [120, 5], 180),
    ]
    anchors = [
        {
            "id": "BEND_ABOVE",
            "kind": "bend_region",
            "net": "sig",
            "center_um": [69, 42],
            "radius_um": 6,
        }
    ]
    intent = build_route_intent(ports, anchors)

    result = plan_routes_from_intent(intent)

    assert result["ok"] is True
    assert result["obstacle_hits"] == []
    route = result["routes"][0]
    assert [69.0, 42.0] in route["points_um"]
    assert [48.0, 42.0] not in route["points_um"]


def test_plan_fanout_corridor_routes_are_lane_split_and_non_crossing():
    ports = [
        _port("IN0", "sig0", [14, 10], 0, width=3),
        _port("IN1", "sig1", [14, 24], 0, width=3),
        _port("IN2", "sig2", [14, 38], 0, width=3),
        _port("IN3", "sig3", [14, 52], 0, width=3),
        _port("PAD0", "", [110, 0], 180, width=5, port_type="candidate_sink"),
        _port("PAD1", "", [110, 14], 180, width=5, port_type="candidate_sink"),
        _port("PAD2", "", [110, 28], 180, width=5, port_type="candidate_sink"),
        _port("PAD3", "", [110, 42], 180, width=5, port_type="candidate_sink"),
    ]
    anchors = [
        {
            "id": "LOWER",
            "kind": "corridor",
            "net": "sig0,sig1",
            "center_um": [45, 17],
            "width_um": 8,
            "path_points": "-15,-1;10,0;15,1",
        },
        {
            "id": "UPPER",
            "kind": "corridor",
            "net": "sig2,sig3",
            "center_um": [75, 49.5],
            "width_um": 8,
            "path_points": "-9,-0.5;6,0.5;9,-1",
        },
    ]
    intent = build_route_intent(ports, anchors, cell="FANOUT")

    result = plan_routes_from_intent(intent)

    assert result["ok"] is True
    assert result["crossings"] == []
    routes = result["routes"]
    assert [r["target"] for r in routes] == ["PAD0", "PAD1", "PAD2", "PAD3"]
    assert [r["anchors"] for r in routes] == [["LOWER"], ["LOWER"], ["UPPER"], ["UPPER"]]
    assert routes[0]["lane_offset_um"] < routes[1]["lane_offset_um"]
