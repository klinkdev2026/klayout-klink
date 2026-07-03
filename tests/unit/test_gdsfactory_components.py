from __future__ import annotations

import pytest


pytest.importorskip("gdsfactory")

from klink.routing.backends.gdsfactory.gdsfactory_components import gdsfactory_component_marker_to_shapes_and_ports


def test_gdsfactory_component_marker_exports_shapes_and_ports():
    result = gdsfactory_component_marker_to_shapes_and_ports(
        {
            "id": "SPL",
            "component": "mmi1x2",
            "center_um": [55, 0],
            "rotation": 0,
            "port_nets": {"o1": "net_in", "o2": "net_out0", "o3": "net_out1"},
        },
        target_layer="10/0",
    )

    assert result["shape_items"]
    assert [(p["name"], p["net"]) for p in result["ports"]] == [
        ("SPL.o1", "net_in"),
        ("SPL.o2", "net_out0"),
        ("SPL.o3", "net_out1"),
    ]
    assert result["ports"][0]["center_um"] == [45.0, 0.0]


def test_gdsfactory_component_marker_rejects_unknown_component():
    with pytest.raises(ValueError, match="unknown gdsfactory component"):
        gdsfactory_component_marker_to_shapes_and_ports({"component": "does_not_exist"})
