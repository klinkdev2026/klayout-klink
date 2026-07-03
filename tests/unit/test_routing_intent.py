from __future__ import annotations

from klink.routing.core.intent import build_route_intent


def test_build_route_intent_pairs_same_net_ports_with_bound_anchors():
    ports = [
        {"name": "A", "net": "sig", "port_type": "electrical", "center_um": [0, 0]},
        {"name": "B", "net": "sig", "port_type": "electrical", "center_um": [10, 0]},
    ]
    anchors = [
        {"id": "WP1", "net": "sig", "kind": "waypoint_region", "priority": 0},
        {"id": "BEND", "net": "other", "kind": "bend_region", "priority": 0},
    ]

    intent = build_route_intent(ports, anchors, cell="C")

    assert intent["cell"] == "C"
    assert len(intent["route_requests"]) == 1
    request = intent["route_requests"][0]
    assert request["route_id"] == "route_sig"
    assert [p["name"] for p in request["ports"]] == ["A", "B"]
    assert request["source"]["name"] == "A"
    assert request["target"]["name"] == "B"
    assert [a["id"] for a in request["anchors"]] == ["WP1"]


def test_build_route_intent_accepts_comma_separated_anchor_net_allowlist():
    ports = [
        {"name": "A", "net": "sig1", "port_type": "electrical"},
        {"name": "B", "net": "sig1", "port_type": "electrical"},
    ]
    anchors = [
        {"id": "LOWER", "net": "sig0,sig1", "kind": "corridor", "priority": 0},
        {"id": "UPPER", "net": "sig2,sig3", "kind": "corridor", "priority": 0},
    ]

    intent = build_route_intent(ports, anchors)

    assert [a["id"] for a in intent["route_requests"][0]["anchors"]] == ["LOWER"]


def test_build_route_intent_keeps_netless_corridors_global():
    ports = [
        {"name": "A", "net": "sig", "port_type": "electrical"},
        {"name": "B", "net": "sig", "port_type": "electrical"},
    ]
    anchors = [
        {"id": "BUS", "net": "", "kind": "corridor", "priority": 5},
    ]

    intent = build_route_intent(ports, anchors)

    assert intent["route_requests"][0]["anchors"] == []
    assert [a["id"] for a in intent["global_anchors"]] == ["BUS"]
    assert intent["global_anchors"][0]["corridor_policy"] == "follow_centerline"
    assert intent["global_anchors"][0]["allows_crossing"] is False


def test_build_route_intent_creates_fanout_assignment_request():
    ports = [
        {"name": "IN0", "net": "sig0", "port_type": "electrical"},
        {"name": "IN1", "net": "sig1", "port_type": "electrical"},
        {"name": "PAD0", "net": "", "port_type": "candidate_sink"},
        {"name": "PAD1", "net": "", "port_type": "candidate_sink"},
    ]
    anchors = [
        {"id": "BUS", "net": "", "kind": "corridor", "priority": 0},
    ]

    intent = build_route_intent(ports, anchors, cell="FANOUT")

    assert intent["route_requests"] == []
    assert len(intent["assignment_requests"]) == 1
    assignment = intent["assignment_requests"][0]
    assert assignment["mode"] == "fanout_to_candidate_sinks"
    assert [p["name"] for p in assignment["demands"]] == ["IN0", "IN1"]
    assert [p["name"] for p in assignment["candidate_sinks"]] == ["PAD0", "PAD1"]
    assert [a["id"] for a in assignment["anchors"]] == ["BUS"]
    assert assignment["anchors"][0]["corridor_policy"] == "follow_centerline"


def test_build_route_intent_binds_fanout_corridors_to_demand_nets():
    ports = [
        {"name": "IN0", "net": "sig0", "port_type": "electrical"},
        {"name": "IN1", "net": "sig1", "port_type": "electrical"},
        {"name": "IN2", "net": "sig2", "port_type": "electrical"},
        {"name": "PAD0", "net": "", "port_type": "candidate_sink"},
    ]
    anchors = [
        {"id": "LOWER", "net": "sig0,sig1", "kind": "corridor", "priority": 0},
        {"id": "UPPER", "net": "sig2", "kind": "corridor", "priority": 0},
    ]

    intent = build_route_intent(ports, anchors, cell="FANOUT")
    assignment = intent["assignment_requests"][0]

    assert [a["id"] for a in assignment["anchors"]] == ["LOWER", "UPPER"]
    assert [a["id"] for a in assignment["anchors_by_demand"]["IN0"]] == ["LOWER"]
    assert [a["id"] for a in assignment["anchors_by_demand"]["IN1"]] == ["LOWER"]
    assert [a["id"] for a in assignment["anchors_by_demand"]["IN2"]] == ["UPPER"]
