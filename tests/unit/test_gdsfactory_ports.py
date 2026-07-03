from __future__ import annotations

import pytest

from klink.routing.backends.gdsfactory.gdsfactory_ports import select_gdsfactory_port_groups


def _port(name, center, orientation, *, net=""):
    return {
        "name": name,
        "center_um": list(center),
        "orientation": orientation,
        "width_um": 0.5,
        "target_layer": "10/0",
        "port_type": "optical",
        "net": net,
    }


def test_select_groups_by_prefix_and_axis_order():
    ports = [
        _port("IN1", [0, 10], 0),
        _port("IN0", [0, 0], 0),
        _port("OUT1", [100, 20], 180),
        _port("OUT0", [100, 5], 180),
    ]

    left, right = select_gdsfactory_port_groups(
        ports,
        source_prefix="IN",
        target_prefix="OUT",
        pair_by="axis",
    )

    assert [p["name"] for p in left] == ["IN0", "IN1"]
    assert [p["name"] for p in right] == ["OUT0", "OUT1"]


def test_default_selection_routes_all_two_port_nets():
    ports = [
        _port("random_a", [0, 0], 0, net="sig0"),
        _port("anything", [100, 5], 180, net="sig0"),
        _port("foo", [0, 10], 0, net="sig1"),
        _port("bar", [100, 15], 180, net="sig1"),
    ]

    left, right = select_gdsfactory_port_groups(ports)

    assert [p["net"] for p in left] == ["sig0", "sig1"]
    assert [p["net"] for p in right] == ["sig0", "sig1"]


def test_prefix_groups_default_to_net_pairing_not_name_pairing():
    ports = [
        _port("left_a", [0, 0], 0, net="sig0"),
        _port("left_b", [0, 10], 0, net="sig1"),
        _port("right_x", [100, 15], 180, net="sig1"),
        _port("right_y", [100, 5], 180, net="sig0"),
    ]

    left, right = select_gdsfactory_port_groups(
        ports,
        source_prefix="left",
        target_prefix="right",
        pair_by="net",
    )

    assert [(a["name"], b["name"]) for a, b in zip(left, right)] == [
        ("left_a", "right_y"),
        ("left_b", "right_x"),
    ]


def test_select_all_two_port_nets_rejects_multidrop_without_topology():
    ports = [
        _port("A", [0, 0], 0, net="bus"),
        _port("B", [100, 0], 180, net="bus"),
        _port("C", [100, 10], 180, net="bus"),
    ]

    with pytest.raises(ValueError, match="multi-port nets need explicit topology"):
        select_gdsfactory_port_groups(ports, all_two_port_nets=True)


def test_select_multidrop_rejected_for_photonic_backend():
    ports = [
        _port("ROOT", [0, 0], 0, net="bus"),
        _port("B", [100, 0], 180, net="bus"),
        _port("C", [100, 10], 180, net="bus"),
    ]

    with pytest.raises(ValueError, match="Insert an explicit splitter"):
        select_gdsfactory_port_groups(
            ports,
            multidrop_net="bus",
            root="ROOT",
        )
