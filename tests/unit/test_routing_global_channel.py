from __future__ import annotations

from klink.routing.backends.geometric.global_channel import (
    assign_corridors_by_capacity,
    pair_ports_with_obstacle_cost,
    route_global_channel_cell,
    route_tapered_hybrid_many_with_frozen_paths,
)


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


def _corridor(anchor_id, y, *, net, choice_group=""):
    return {
        "id": anchor_id,
        "kind": "corridor",
        "net": net,
        "label": f"choice_group={choice_group}" if choice_group else anchor_id,
        "center_um": [90.0, float(y)],
        "path_points": "-55,0;55,0",
        "width_um": 44.0,
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


def test_capacity_assignment_splits_nets_across_equivalent_corridors():
    nets = [f"n{i}" for i in range(6)]
    pairs = []
    for idx, net in enumerate(nets):
        y = -45 + idx * 18
        pairs.append({
            "net": net,
            "source": _port(f"L{idx}", net, [0, y], 0, width=5.0),
            "target": _port(f"R{idx}", net, [180, y], 180, width=5.0),
            "route_layer": "12/0",
        })
    anchors = [
        _corridor("UPPER", 36, net=",".join(nets), choice_group="BUS"),
        _corridor("LOWER", -36, net=",".join(nets), choice_group="BUS"),
    ]

    result = assign_corridors_by_capacity(pairs, anchors, spacing_um=8.0)

    by_corridor = {}
    for item in result["assignments"]:
        by_corridor.setdefault(item["corridor_id"], []).append(item["net"])
    assert set(by_corridor) == {"UPPER", "LOWER"}
    assert sum(len(v) for v in by_corridor.values()) == 6
    assert result["errors"] == []


def test_plain_corridor_is_not_treated_as_optional_choice():
    pairs = [{
        "net": "n0",
        "source": _port("L0", "n0", [0, 0], 0, width=5.0),
        "target": _port("R0", "n0", [180, 0], 180, width=5.0),
        "route_layer": "12/0",
    }]
    anchors = [
        _corridor("UPPER", 36, net="n0"),
        _corridor("LOWER", -36, net="n0"),
    ]

    result = assign_corridors_by_capacity(pairs, anchors, spacing_um=8.0)

    assert result["assignments"] == []
    assert [a["id"] for a in result["anchors"]] == ["UPPER", "LOWER"]


def test_candidate_assignment_uses_obstacle_aware_route_cost():
    demand = _port("S0", "n0", [0, 0], 0)
    near = _port("NEAR", "", [70, 0], 180, port_type="candidate_sink")
    far = _port("FAR", "", [130, 38], 180, port_type="candidate_sink")

    result = pair_ports_with_obstacle_cost(
        [demand, near, far],
        obstacle_bboxes=[[58, -12, 82, 12]],
        angle_mode="manhattan",
        safe_distance_um=0.0,
    )

    assert result["pairs"][0]["target"]["name"] == "FAR"
    assert result["pairs"][0]["assignment"] == "candidate_sink_obstacle_cost"


def test_frozen_path_router_separates_parallel_unequal_width_ports():
    pairs = []
    for idx, (y0, y1, width_a, width_b) in enumerate([
        (0, 0, 10.0, 3.0),
        (18, 18, 3.0, 8.0),
        (36, 36, 6.0, 4.0),
    ]):
        net = f"n{idx}"
        pairs.append({
            "net": net,
            "source": _port(f"L{idx}", net, [0, y0], 0, width=width_a),
            "target": _port(f"R{idx}", net, [120, y1], 180, width=width_b),
            "route_layer": "12/0",
        })

    result = route_tapered_hybrid_many_with_frozen_paths(
        pairs,
        spacing_um=8.0,
        angle_mode="manhattan",
        obstacle_bboxes=[],
    )

    assert result["ok"] is True
    assert result["route_count"] == 3
    assert result["sibling_overlaps"] == []
    assert result["routes"][0]["source_width_um"] == 10.0
    assert result["routes"][0]["target_width_um"] == 3.0


def test_frozen_path_router_rejects_physically_overlapping_port_pitch():
    pairs = []
    for idx, (y0, y1, width_a, width_b) in enumerate([
        (0, 0, 10.0, 3.0),
        (6, 6, 3.0, 8.0),
    ]):
        net = f"n{idx}"
        pairs.append({
            "net": net,
            "source": _port(f"L{idx}", net, [0, y0], 0, width=width_a),
            "target": _port(f"R{idx}", net, [120, y1], 180, width=width_b),
            "route_layer": "12/0",
        })

    result = route_tapered_hybrid_many_with_frozen_paths(
        pairs,
        spacing_um=8.0,
        angle_mode="manhattan",
        obstacle_bboxes=[],
    )

    assert result["ok"] is False
    assert result["sibling_overlaps"]


def test_frozen_path_router_searches_route_order_for_candidate_bundle():
    pairs = [
        {
            "net": "n0",
            "source": _port("S0", "n0", [0, -36], 0),
            "target": _port("FAR2", "", [155, -14], 180, port_type="candidate_sink"),
            "route_layer": "12/0",
            "assignment_cost_um": 193.0,
        },
        {
            "net": "n1",
            "source": _port("S1", "n1", [0, -12], 0),
            "target": _port("NEAR1", "", [92, -14], 180, port_type="candidate_sink"),
            "route_layer": "12/0",
            "assignment_cost_um": 94.0,
        },
        {
            "net": "n2",
            "source": _port("S2", "n2", [0, 12], 0),
            "target": _port("NEAR2", "", [92, 14], 180, port_type="candidate_sink"),
            "route_layer": "12/0",
            "assignment_cost_um": 94.0,
        },
        {
            "net": "n3",
            "source": _port("S3", "n3", [0, 36], 0),
            "target": _port("FAR1", "", [155, 14], 180, port_type="candidate_sink"),
            "route_layer": "12/0",
            "assignment_cost_um": 193.0,
        },
    ]

    result = route_tapered_hybrid_many_with_frozen_paths(
        pairs,
        spacing_um=8.0,
        angle_mode="manhattan",
        obstacle_bboxes=[
            [45, -60, 82, -22],
            [45, 22, 82, 60],
            [95, -18, 125, 18],
        ],
    )

    assert result["ok"] is True
    assert result["route_count"] == 4
    assert result["route_order"] != ["n0", "n1", "n2", "n3"]
    assert result["route_order_attempts"] > 1
    assert result["sibling_overlaps"] == []
    assert result["obstacle_hits"] == []


def test_global_channel_cell_routes_capacity_split_fixture():
    nets = [f"n{i}" for i in range(6)]
    ports = []
    for idx, net in enumerate(nets):
        y = -45 + idx * 18
        ports.append(_port(f"L{idx}", net, [0, y], 0, width=5.0))
        ports.append(_port(f"R{idx}", net, [180, y], 180, width=5.0))
    anchors = [
        _corridor("UPPER", 36, net=",".join(nets), choice_group="BUS"),
        _corridor("LOWER", -36, net=",".join(nets), choice_group="BUS"),
    ]
    client = _Client(ports, anchors=anchors)

    result = route_global_channel_cell(
        client,
        "TOP",
        spacing_um=8.0,
        obstacle_layers=[],
    )

    assert result["ok"] is True
    assert result["backend"] == "global_channel_cell"
    assert len({item["corridor_id"] for item in result["corridor_assignment"]}) == 2
    assert result["groups"][0]["route_count"] == 6
    assert client.paths
