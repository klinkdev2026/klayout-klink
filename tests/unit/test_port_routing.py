from __future__ import annotations

from klink.routing.geom.port import match_ports, port_to_gf, sort_ports_clockwise


def test_match_ports_by_name():
    ports_a = [
        {"name": "E0", "center_um": [0, 0]},
        {"name": "E1", "center_um": [10, 0]},
    ]
    ports_b = [
        {"name": "E1", "center_um": [100, 0]},
        {"name": "E0", "center_um": [200, 0]},
    ]

    pairs = match_ports(ports_a, ports_b, strategy="name")

    assert [(a["name"], b["name"]) for a, b in pairs] == [("E0", "E0"), ("E1", "E1")]


def test_match_ports_by_distance():
    ports_a = [
        {"name": "A0", "center_um": [0, 0]},
        {"name": "A1", "center_um": [100, 0]},
    ]
    ports_b = [
        {"name": "B0", "center_um": [3, 0]},
        {"name": "B1", "center_um": [103, 0]},
    ]

    pairs = match_ports(ports_a, ports_b, strategy="distance")

    assert [(a["name"], b["name"]) for a, b in pairs] == [("A0", "B0"), ("A1", "B1")]


def test_match_ports_by_net_allows_one_to_many():
    ports_a = [{"name": "A", "net": "VDD", "center_um": [0, 0]}]
    ports_b = [
        {"name": "B0", "net": "VDD", "center_um": [1, 0]},
        {"name": "B1", "net": "VDD", "center_um": [2, 0]},
        {"name": "B2", "net": "GND", "center_um": [3, 0]},
    ]

    pairs = match_ports(ports_a, ports_b, strategy="net")

    assert [(a["name"], b["name"]) for a, b in pairs] == [("A", "B0"), ("A", "B1")]


def test_match_ports_clockwise_pairs_sorted_ports():
    ports_a = [
        {"name": "east", "center_um": [1, 0]},
        {"name": "north", "center_um": [0, 1]},
        {"name": "west", "center_um": [-1, 0]},
        {"name": "south", "center_um": [0, -1]},
    ]
    ports_b = [
        {"name": "E", "center_um": [10, 0]},
        {"name": "N", "center_um": [0, 10]},
        {"name": "W", "center_um": [-10, 0]},
        {"name": "S", "center_um": [0, -10]},
    ]

    pairs = match_ports(ports_a, ports_b, strategy="clockwise")

    assert [(a["name"], b["name"]) for a, b in pairs] == [
        ("west", "W"),
        ("north", "N"),
        ("east", "E"),
        ("south", "S"),
    ]


def test_sort_ports_clockwise_is_deterministic_for_cardinal_points():
    ports = [
        {"name": "north", "center_um": [0, 1]},
        {"name": "west", "center_um": [-1, 0]},
        {"name": "south", "center_um": [0, -1]},
        {"name": "east", "center_um": [1, 0]},
    ]

    sorted_ports = sort_ports_clockwise(ports)

    assert [p["name"] for p in sorted_ports] == ["west", "north", "east", "south"]


def test_port_to_gf_without_gdsfactory_is_non_fatal():
    port = {
        "name": "E0",
        "center_um": [1.0, 2.0],
        "orientation": 0,
        "width_um": 0.5,
        "target_layer": "1/0",
    }

    # In environments with gdsfactory installed this returns a gf.Port;
    # otherwise it returns None. Either behavior is acceptable here; the
    # test documents that the optional dependency path must not raise.
    result = port_to_gf(port)

    assert result is None or getattr(result, "name", None) == "E0"


def test_port_to_gf_preserves_port_type_when_available():
    port = {
        "name": "E0",
        "center_um": [1.0, 2.0],
        "orientation": 0,
        "width_um": 0.5,
        "target_layer": "10/0",
        "port_type": "electrical",
    }

    result = port_to_gf(port)

    if result is not None:
        assert getattr(result, "port_type", None) == "electrical"
        assert str(getattr(result, "layer_info", "")) == "10/0"
