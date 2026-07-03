from __future__ import annotations

from klink.routing.geom.constraints import port_launch_point, route_with_port_launch_stubs


def test_port_launch_leaves_center_along_orientation_with_port_width_scale():
    port = {
        "name": "A",
        "center_um": [20.0, 5.0],
        "orientation": 0.0,
        "width_um": 4.0,
    }

    assert port_launch_point(port) == [28.0, 5.0]


def test_route_skeleton_preserves_source_and_target_port_launches():
    source = {
        "name": "A",
        "center_um": [20.0, 5.0],
        "orientation": 0.0,
        "width_um": 4.0,
    }
    target = {
        "name": "B",
        "center_um": [100.0, 5.0],
        "orientation": 180.0,
        "width_um": 4.0,
    }

    route = route_with_port_launch_stubs(source, target, [[60.0, 40.0]])

    assert route["points_um"][0] == [20.0, 5.0]
    assert route["points_um"][1] == [28.0, 5.0]
    assert route["points_um"][-2][0] == 92.0
    assert round(route["points_um"][-2][1], 6) == 5.0
    assert route["points_um"][-1] == [100.0, 5.0]
    assert route["width_um"] == 4.0
