from __future__ import annotations

from klink.routing.core.intent import build_route_intent
from klink.routing.core.validation import validate_route_intent


def _port(name, net, x=0, y=0, *, port_type="electrical"):
    return {
        "name": name,
        "net": net,
        "port_type": port_type,
        "center_um": [x, y],
        "orientation": 0,
        "width_um": 4.0,
        "target_layer": "10/0",
    }


def test_validate_simple_two_port_route_recommends_simple_router():
    intent = build_route_intent([
        _port("A", "sig", 0, 0),
        _port("B", "sig", 10, 0),
    ])

    result = validate_route_intent(intent)

    assert result["routable"] is True
    assert result["recommended_backend"] == "simple_route_router"
    assert result["errors"] == []


def test_validate_obstacle_profile_is_explicit():
    intent = build_route_intent([
        _port("A", "sig", 0, 0),
        _port("B", "sig", 10, 0),
    ])

    missing = validate_route_intent(intent, require_obstacle_layers=True)
    with_obstacle = validate_route_intent(
        intent,
        obstacle_layers=["900/0"],
        require_obstacle_layers=True,
    )

    assert missing["routable"] is False
    assert [e["code"] for e in missing["errors"]] == ["OBSTACLE_LAYERS_REQUIRED"]
    assert with_obstacle["routable"] is True
    assert with_obstacle["recommended_backend"] == "obstacle_aware_router"
    assert with_obstacle["obstacle_layers"] == ["900/0"]


def test_validate_fanout_corridor_assignment_requires_bound_corridor_per_demand():
    ports = [
        _port("IN0", "sig0"),
        _port("IN1", "sig1"),
        _port("PAD0", "", port_type="candidate_sink"),
        _port("PAD1", "", port_type="candidate_sink"),
    ]
    anchors = [
        {
            "id": "LOWER",
            "kind": "corridor",
            "net": "sig0,sig1",
            "center_um": [0, 0],
            "width_um": 8.0,
            "path_points": "-5,0;5,0",
        }
    ]
    intent = build_route_intent(ports, anchors, cell="F")

    result = validate_route_intent(intent)

    assert result["routable"] is True
    assert result["recommended_backend"] == "corridor_lane_router"
    assert result["errors"] == []


def test_validate_fanout_corridor_missing_net_binding_is_not_routable():
    ports = [
        _port("IN0", "sig0"),
        _port("PAD0", "", port_type="candidate_sink"),
    ]
    anchors = [
        {
            "id": "C",
            "kind": "corridor",
            "net": "",
            "center_um": [0, 0],
            "width_um": 8.0,
            "path_points": "-5,0;5,0",
        }
    ]
    intent = build_route_intent(ports, anchors, cell="F")

    result = validate_route_intent(intent)

    assert result["routable"] is False
    assert "DEMAND_MISSING_CORRIDOR" in [e["code"] for e in result["errors"]]
    assert "CORRIDOR_NETLESS" in [w["code"] for w in result["warnings"]]


def test_validate_edge_port_requires_slide_edge():
    bad_edge = _port("A", "sig")
    bad_edge["access_mode"] = "edge"
    bad_edge["slide_allowed"] = True
    bad_edge["slide_edge"] = ""
    intent = build_route_intent([bad_edge, _port("B", "sig")])

    result = validate_route_intent(intent)

    assert result["routable"] is False
    assert "EDGE_PORT_MISSING_SLIDE_EDGE" in [e["code"] for e in result["errors"]]
