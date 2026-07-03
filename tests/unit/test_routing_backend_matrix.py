from __future__ import annotations

import pytest

from klink.routing.backends.geometric.tapered import route_tapered, route_tapered_polygon_cell
from klink.routing.backends.geometric.tapered_segments import route_tapered_hybrid_many


def _port(name, net, center, orientation, *, width=4.0, port_type="electrical"):
    return {
        "name": name,
        "net": net,
        "center_um": [float(center[0]), float(center[1])],
        "orientation": float(orientation),
        "width_um": float(width),
        "port_type": port_type,
        "target_layer": "10/0",
    }


def test_first_class_taper_backends_share_launch_hairpin_contract():
    source = _port("NORTH", "n0", [0, 45], 90)
    target = _port("PAD_N", "n0", [0, 115], 90)

    polygon = route_tapered(source, target)
    hybrid = route_tapered_hybrid_many([{"net": "n0", "source": source, "target": target}])

    assert polygon["points_um"][-2] == [pytest.approx(0.0), 123.0]
    assert polygon["points_um"][-3][1] == 123.0
    assert polygon["points_um"][-3][0] != 0.0
    assert hybrid["routes"][0]["points_um"][-2] == [pytest.approx(0.0), 123.0]
    assert hybrid["routes"][0]["points_um"][-3][1] == 123.0
    assert hybrid["routes"][0]["points_um"][-3][0] != 0.0


class _PolygonCellClient:
    def __init__(self, anchors):
        self.anchors = anchors
        self.polygons = []

    def call(self, name, arguments):
        if name == "port.list":
            return {
                "ports": [
                    _port("A", "n0", [0, 0], 0),
                    _port("B", "n0", [100, 0], 180),
                ]
            }
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
        return {"deleted": 0}

    def shape_insert_polygon(self, cell, *, layer, datatype, points_um):
        self.polygons.append(points_um)
        return {"ok": True}


def test_tapered_polygon_cell_routes_pairwise_without_corridor_anchor():
    client = _PolygonCellClient(anchors=[])

    result = route_tapered_polygon_cell(client, "TOP", obstacle_layers=[])

    assert result["ok"] is True
    assert result["backend"] == "tapered_polygon_cell"
    assert result["pair_count"] == 1
    assert result["groups"][0]["write"]["inserted_polygons"] == 1
    assert len(client.polygons) == 1


def test_tapered_polygon_cell_honors_corridor_lane_splitting():
    client = _PolygonCellClient(anchors=[{
        "id": "C0",
        "kind": "corridor",
        "net": "n0",
        "center_um": [50, 0],
        "width_um": 20,
        "path_points": "-10,0;10,0",
    }])

    result = route_tapered_polygon_cell(client, "TOP", obstacle_layers=[])

    assert result["ok"] is True
    assert result["groups"][0]["lane_reports"][0]["corridor_id"] == "C0"
    assert result["groups"][0]["write"]["inserted_polygons"] == 1
    assert len(client.polygons) == 1
