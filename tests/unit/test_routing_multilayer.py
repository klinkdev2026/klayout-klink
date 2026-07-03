from __future__ import annotations

from klink.routing.backends.geometric.multilayer import route_multilayer_escape_cell


def _port(name, net, center, orientation, *, width=4.0):
    return {
        "name": name,
        "net": net,
        "center_um": [float(center[0]), float(center[1])],
        "orientation": float(orientation),
        "width_um": float(width),
        "target_layer": "12/0",
    }


class _Client:
    def __init__(self, ports, obstacles=None):
        self.ports = list(ports)
        self.obstacles = list(obstacles or [])
        self.paths = []
        self.boxes = []

    def call(self, name, arguments):
        if name == "port.list":
            return {"ports": list(self.ports)}
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
        self.paths.append({"layer": layer, "datatype": datatype, "points_um": points_um, "width_um": width_um})
        return {"ok": True}

    def shape_insert_box(self, cell, *, layer, datatype, bbox_um):
        self.boxes.append({"layer": layer, "datatype": datatype, "bbox_um": bbox_um})
        return {"ok": True}


def test_multilayer_escape_uses_bridge_layer_and_vias_for_blocking_wall():
    ports = []
    for idx, y in enumerate([-24, 0, 24]):
        ports.append(_port(f"L{idx}", f"m{idx}", [0, y], 0))
        ports.append(_port(f"R{idx}", f"m{idx}", [160, y], 180))
    client = _Client(ports, obstacles=[[62, -70, 98, 70]])

    result = route_multilayer_escape_cell(
        client,
        "TOP",
        route_layer="12/0",
        bridge_layer="13/0",
        via_layer="14/0",
        spacing_um=8.0,
        obstacle_layers=["900/0"],
    )

    assert result["ok"] is True
    assert result["route_count"] == 3
    assert result["obstacle_hits"] == []
    assert result["write"]["primary_paths"] == 6
    assert result["write"]["bridge_paths"] == 3
    assert result["write"]["vias"] == 6
    assert len([p for p in client.paths if p["layer"] == 12]) == 6
    assert len([p for p in client.paths if p["layer"] == 13]) == 3
    assert len(client.boxes) == 6
