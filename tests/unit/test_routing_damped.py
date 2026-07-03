from __future__ import annotations

from klink.routing.backends.geometric.damped import route_damped_polygon_cell, route_damped_segment_cell, route_damped_steiner_cell


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


class _Client:
    def __init__(self, ports, anchors=None, obstacles=None):
        self.ports = list(ports)
        self.anchors = list(anchors or [])
        self.obstacles = list(obstacles or [])
        self.paths = []
        self.polygons = []

    def call(self, name, arguments):
        if name == "port.list":
            return {"ports": list(self.ports)}
        if name == "anchor.list":
            return {"anchors": list(self.anchors)}
        raise AssertionError(f"unexpected call: {name}")

    def layout_info(self):
        return {"dbu": 0.001}

    def shape_query(self, cell, *, layers, kinds, limit):
        return {"shapes": [{"bbox_dbu": [int(v * 1000) for v in bbox]} for bbox in self.obstacles]}

    def layer_ensure(self, layer, datatype, name=None):
        return {"ok": True}

    def shape_delete(self, cell, *, layers, kinds, limit):
        return {"deleted": 0}

    def shape_insert_path(self, cell, *, layer, datatype, points_um, width_um, begin_ext_um, end_ext_um, round_ends):
        self.paths.append({"points_um": points_um, "width_um": width_um})
        return {"ok": True}

    def shape_insert_polygon(self, cell, *, layer, datatype, points_um):
        self.polygons.append(points_um)
        return {"ok": True}

    def shape_insert_many(self, cell, items, *, dry_run=False):
        if dry_run:
            return {"inserted": len(items), "dry_run": True}
        for item in items:
            if item.get("kind") == "path":
                self.paths.append({
                    "points_um": item.get("points_um"),
                    "width_um": item.get("width_um"),
                })
            elif item.get("kind") == "polygon":
                self.polygons.append(item.get("points_um"))
        return {"inserted": len(items)}


def _pair_ports():
    return [
        _port("A", "sig", [0, 0], 0, width=4.0),
        _port("B", "sig", [100, 0], 180, width=4.0),
    ]


def _path_length(points):
    return sum(
        ((float(b[0]) - float(a[0])) ** 2 + (float(b[1]) - float(a[1])) ** 2) ** 0.5
        for a, b in zip(points, points[1:])
    )


def test_damped_segment_cell_is_explicit_backend_and_routes_around_obstacle():
    client = _Client(_pair_ports(), obstacles=[[40, -5, 60, 5]])

    result = route_damped_segment_cell(
        client,
        "TOP",
        damping_distance_um=8.0,
        obstacle_layers=["900/0"],
    )

    assert result["ok"] is True
    assert result["backend"] == "damped_segment_cell"
    assert result["damping_distance_um"] == 8.0
    assert result["groups"][0]["route_count"] == 1
    assert client.paths


def test_damped_segment_head_on_aligned_ports_are_one_straight_13um_segment():
    client = _Client([
        _port("A", "MID", [0, 8.5], 90, width=5.0),
        _port("B", "MID", [0, 21.5], 270, width=5.0),
    ])

    first = route_damped_segment_cell(client, "TOP", obstacle_layers=[])
    second = route_damped_segment_cell(_Client(client.ports), "TOP", obstacle_layers=[])

    assert first == second
    assert first["ok"] is True
    assert first["groups"][0]["route_count"] == 1
    assert len(client.paths) == 1
    assert client.paths[0]["points_um"] == [[0.0, 8.5], [0.0, 21.5]]
    assert client.paths[0]["width_um"] == 5.0
    assert _path_length(client.paths[0]["points_um"]) == 13.0


def test_damped_segment_zero_ports_warns_with_layer_and_count():
    result = route_damped_segment_cell(_Client([]), "TOP", port_layer="998/98", obstacle_layers=[])

    assert result["ok"] is True
    assert result["port_count"] == 0
    assert result["groups"] == []
    assert result["warnings"] == [{
        "type": "zero_ports",
        "port_layer": "998/98",
        "port_count": 0,
        "message": "zero ports found on layer 998/98",
    }]


def test_damped_polygon_cell_is_explicit_backend_and_writes_polygon():
    client = _Client(_pair_ports(), obstacles=[[40, -5, 60, 5]])

    result = route_damped_polygon_cell(
        client,
        "TOP",
        damping_distance_um=8.0,
        obstacle_layers=["900/0"],
    )

    assert result["ok"] is True
    assert result["backend"] == "damped_polygon_cell"
    assert result["damping_distance_um"] == 8.0
    assert result["groups"][0]["route_count"] == 1
    assert len(client.polygons) == 1


def test_damped_steiner_cell_keeps_topology_backend_separate():
    ports = [
        _port("ROOT", "bus", [0, 0], 0, width=8.0, port_type="root"),
        _port("S0", "bus", [120, -40], 180, width=4.0),
        _port("S1", "bus", [120, 40], 180, width=5.0),
    ]
    client = _Client(ports, obstacles=[])

    result = route_damped_steiner_cell(
        client,
        "TOP",
        root_ports={"bus": "ROOT"},
        damping_distance_um=8.0,
        obstacle_layers=["900/0"],
    )

    assert result["ok"] is True
    assert result["backend"] == "damped_steiner_cell"
    assert result["groups"][0]["route_count"] == 4
    assert client.paths


def test_damped_steiner_zero_ports_warns_with_layer_and_count():
    result = route_damped_steiner_cell(_Client([]), "TOP", port_layer="998/98", obstacle_layers=[])

    assert result["ok"] is True
    assert result["port_count"] == 0
    assert result["groups"] == []
    assert result["warnings"] == [{
        "type": "zero_ports",
        "port_layer": "998/98",
        "port_count": 0,
        "message": "zero ports found on layer 998/98",
    }]


def test_damped_steiner_moves_corridor_trunk_around_obstacles_before_branching():
    ports = [
        _port("ROOT", "bus", [0, 0], 0, width=8.0, port_type="root"),
        _port("S0", "bus", [160, -48], 180, width=4.0),
        _port("S1", "bus", [160, 0], 180, width=5.0),
        _port("S2", "bus", [160, 48], 180, width=6.0),
    ]
    anchors = [{
        "id": "TRUNK_HINT",
        "kind": "corridor",
        "net": "bus",
        "center_um": [100, 0],
        "path_points": "0,-60;0,60",
        "width_um": 30.0,
    }]
    client = _Client(
        ports,
        anchors=anchors,
        obstacles=[
            [86, -20, 114, 20],
            [54, -75, 76, -20],
            [124, 20, 146, 75],
        ],
    )

    result = route_damped_steiner_cell(
        client,
        "TOP",
        route_layer="12/0",
        root_ports={"bus": "ROOT"},
        damping_distance_um=8.0,
        obstacle_layers=["900/0"],
    )

    assert result["ok"] is True
    assert result["groups"][0]["obstacle_hits"] == []
    trunk = next(route for route in result["groups"][0]["routes"] if route["kind"] == "trunk")
    assert [34.0, -95.0] in trunk["points_um"]
    assert len(client.paths) == 5
