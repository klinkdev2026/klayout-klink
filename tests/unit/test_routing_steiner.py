from __future__ import annotations

import pytest

from klink.routing.backends.geometric.steiner import plan_rectilinear_steiner_tree, route_steiner_cell


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


def test_rectilinear_steiner_tree_uses_launch_geometry_without_fixture_priors():
    ports = [
        _port("ROOT", "bus", [0, 0], 0, width=9.0),
        _port("SINK0", "bus", [130, -45], 180, width=3.0),
        _port("SINK1", "bus", [130, 0], 180, width=5.0),
        _port("SINK2", "bus", [130, 45], 180, width=7.0),
    ]

    result = plan_rectilinear_steiner_tree(ports, net="bus", root_name="ROOT")

    assert result["ok"] is True
    assert result["root"] == "ROOT"
    assert result["trunk_axis"] == "vertical"
    trunk = next(route for route in result["routes"] if route["kind"] == "trunk")
    # Path-quality pass removes the redundant collinear trunk midpoint at y=0.
    assert trunk["points_um"] == [[118.0, -45.0], [118.0, 45.0]]
    assert trunk["width_um"] == 9.0
    branches = {route["source"]: route["points_um"] for route in result["routes"] if route["kind"] == "branch"}
    widths = {route["source"]: route["width_um"] for route in result["routes"] if route["kind"] == "branch"}
    # Path-quality pass removes redundant collinear launch points on branches.
    assert branches["ROOT"] == [[0.0, 0.0], [118.0, 0.0]]
    assert branches["SINK0"] == [[130.0, -45.0], [118.0, -45.0]]
    assert widths == {"ROOT": 9.0, "SINK0": 3.0, "SINK1": 5.0, "SINK2": 7.0}


def test_rectilinear_steiner_tree_can_choose_horizontal_trunk():
    ports = [
        _port("ROOT", "bus", [0, 0], 90),
        _port("SINK0", "bus", [-45, 130], 270),
        _port("SINK1", "bus", [0, 130], 270),
        _port("SINK2", "bus", [45, 130], 270),
    ]

    result = plan_rectilinear_steiner_tree(ports, net="bus", root_name="ROOT")

    assert result["trunk_axis"] == "horizontal"
    trunk = next(route for route in result["routes"] if route["kind"] == "trunk")
    # Path-quality pass removes the redundant collinear trunk midpoint at x=0.
    assert trunk["points_um"] == [[-45.0, 122.0], [45.0, 122.0]]


def test_rectilinear_steiner_dedupes_collinear_sibling_path_covered_by_trunk():
    ports = [
        _port("ROOT", "bus", [0, 10], 90, width=5.0, port_type="root"),
        _port("MID", "bus", [0, 30], 270, width=5.0),
        _port("SIDE", "bus", [100, 40], 180, width=5.0),
    ]

    result = plan_rectilinear_steiner_tree(ports, net="bus", root_name="ROOT")

    assert result["route_count"] == 3
    routes_by_id = {route["route_id"]: route for route in result["routes"]}
    assert "steiner_bus_MID" not in routes_by_id
    trunk_points = routes_by_id["steiner_bus_trunk"]["points_um"]
    assert [coord for point in trunk_points for coord in point] == pytest.approx([0.0, 20.0, 0.0, 40.0])


def test_rectilinear_steiner_requires_multi_terminal_net():
    with pytest.raises(ValueError, match="at least three ports"):
        plan_rectilinear_steiner_tree(
            [_port("A", "n0", [0, 0], 0), _port("B", "n0", [10, 0], 180)],
            net="n0",
        )


def test_steiner_waypoint_and_bend_anchors_are_included_on_trunk():
    ports = [
        _port("ROOT", "bus", [0, 0], 0),
        _port("SINK0", "bus", [130, -45], 180),
        _port("SINK1", "bus", [130, 45], 180),
    ]
    anchors = [
        {"id": "WP", "kind": "waypoint_region", "net": "bus", "center_um": [120, -15]},
        {"id": "BEND", "kind": "bend_region", "net": "bus", "center_um": [120, 15]},
    ]

    result = plan_rectilinear_steiner_tree(ports, net="bus", anchors=anchors, root_name="ROOT")

    trunk = next(route for route in result["routes"] if route["kind"] == "trunk")
    assert [120.0, -15.0] in trunk["points_um"]
    assert [120.0, 15.0] in trunk["points_um"]
    assert trunk["anchors"] == ["WP", "BEND"]


def test_steiner_corridor_anchor_defines_shared_trunk_path():
    ports = [
        _port("ROOT", "bus", [0, 0], 0),
        _port("SINK0", "bus", [130, -45], 180),
        _port("SINK1", "bus", [130, 45], 180),
    ]
    anchors = [{
        "id": "COR",
        "kind": "corridor",
        "net": "bus",
        "center_um": [80, 0],
        "path_points": "0,-60;0,60",
    }]

    result = plan_rectilinear_steiner_tree(ports, net="bus", anchors=anchors, root_name="ROOT")

    trunk = next(route for route in result["routes"] if route["kind"] == "trunk")
    assert trunk["points_um"] == [[80.0, -60.0], [80.0, 60.0]]
    branches = {route["source"]: route["points_um"] for route in result["routes"] if route["kind"] == "branch"}
    assert branches["ROOT"][-1] == [80.0, 0.0]
    assert branches["SINK0"][-1] == [80.0, -45.0]


class _SteinerClient:
    def __init__(self, ports, anchors=None):
        self.ports = ports
        self.anchors = list(anchors or [])
        self.paths = []
        self.deleted = []

    def call(self, name, arguments):
        if name == "port.list":
            return {"ports": list(self.ports)}
        if name == "anchor.list":
            return {"anchors": list(self.anchors)}
        raise AssertionError(f"unexpected call: {name}")

    def layout_info(self):
        return {"dbu": 0.001}

    def shape_query(self, cell, *, layers, kinds, limit):
        return {"shapes": []}

    def layer_ensure(self, layer, datatype, name=None):
        return {"ok": True}

    def shape_delete(self, cell, *, layers, kinds, limit):
        self.deleted.append((cell, tuple(layers)))
        return {"deleted": 0}

    def shape_insert_path(self, cell, *, layer, datatype, points_um, width_um, begin_ext_um, end_ext_um, round_ends):
        self.paths.append({
            "cell": cell,
            "layer": layer,
            "datatype": datatype,
            "points_um": points_um,
            "width_um": width_um,
        })
        return {"ok": True}


def test_steiner_cell_routes_only_multi_terminal_nets():
    ports = [
        _port("ROOT", "bus", [0, 0], 0, port_type="root"),
        _port("SINK0", "bus", [130, -45], 180),
        _port("SINK1", "bus", [130, 0], 180),
        _port("A", "pair", [0, 100], 0),
        _port("B", "pair", [130, 100], 180),
    ]
    client = _SteinerClient(ports)

    result = route_steiner_cell(client, "TOP", obstacle_layers=[])

    assert result["ok"] is True
    assert len(result["groups"]) == 1
    assert result["groups"][0]["net"] == "bus"
    assert result["groups"][0]["write"]["inserted"] == 4
    assert len(client.paths) == 4


def test_steiner_cell_fails_explicitly_without_multi_terminal_net():
    client = _SteinerClient([
        _port("A", "pair", [0, 100], 0),
        _port("B", "pair", [130, 100], 180),
    ])

    result = route_steiner_cell(client, "TOP", obstacle_layers=[])

    assert result["ok"] is False
    assert result["errors"] == ["no multi-terminal nets found"]


def test_steiner_cell_zero_ports_warns_with_layer_and_count():
    client = _SteinerClient([])

    result = route_steiner_cell(client, "TOP", port_layer="998/98", obstacle_layers=[])

    assert result["ok"] is True
    assert result["port_count"] == 0
    assert result["groups"] == []
    assert result["warnings"] == [{
        "type": "zero_ports",
        "port_layer": "998/98",
        "port_count": 0,
        "message": "zero ports found on layer 998/98",
    }]
