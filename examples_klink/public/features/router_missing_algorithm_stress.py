"""Stress cells for router algorithms klink has not fully absorbed yet.

These cells intentionally target missing capabilities inspired by the local
KlayoutClaw/Klayout-Router references:

- bus / Steiner tree routing
- ordered-loop candidate assignment
- graduated obstacle damping

The script builds the fixtures, runs the current tapered hybrid cell router,
and prints compact diagnostics.  Passing all cells is not expected yet; the
point is to keep the missing algorithm boundaries concrete.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.geom.geometry import crossing_pairs, route_hits_bboxes
from klink.routing.backends.geometric.tapered_segments import _pair_ports_by_net_tokens, route_tapered_hybrid_cell, route_tapered_hybrid_many


PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
ROUTE_LAYER = "12/0"
M1 = (1, 0)
KEEPOUT = (900, 0)
LABEL = (997, 99)

CELLS = [
    "ROUTER_EXT_01_STEINER_BUS",
    "ROUTER_EXT_02_ORDERED_LOOP",
    "ROUTER_EXT_03_DAMPING_CORRIDOR",
]


def _ignore_not_found(fn):
    try:
        return fn()
    except KLinkServerError:
        return None


def reset_cell(client, cell):
    _ignore_not_found(lambda: client.cell_delete(cell, recursive=True))
    client.cell_create(cell)


def ensure_layers(client):
    for layer, datatype, name in [
        (*M1, "M1"),
        (*KEEPOUT, "KEEPOUT"),
        (*LABEL, "LABELS"),
        (999, 1, "ANCHORS"),
        (999, 99, "PORTS"),
        (12, 0, "HYBRID"),
    ]:
        client.layer_ensure(layer, datatype, name=f"KLINK_{name}")
    client.call("port.set_layer", {"layer": PORT_LAYER})
    client.call("anchor.set_layer", {"layer": ANCHOR_LAYER})


def text(client, cell, value, xy, size=4.0):
    client.shape_insert_text(cell, value, layer=LABEL[0], datatype=LABEL[1], position_um=xy, size_um=size)


def box(client, cell, bbox, layer=M1):
    client.shape_insert_box(cell, layer=layer[0], datatype=layer[1], bbox_um=bbox)


def port(client, cell, name, xy, ori, *, net="", width=4.0, port_type="electrical"):
    return client.call("port.mark", {
        "cell": cell,
        "layer": PORT_LAYER,
        "name": name,
        "center_um": xy,
        "orientation": ori,
        "width_um": width,
        "port_type": port_type,
        "net": net,
        "target_layer": ROUTE_LAYER,
        "show_label": True,
    })


def build_steiner_bus(client):
    cell = "ROUTER_EXT_01_STEINER_BUS"
    reset_cell(client, cell)
    text(client, cell, "01: one bus source to three same-net sinks needs Steiner/tree routing", [-20, 90])
    port(client, cell, "ROOT", [0, 0], 0, net="bus", width=5.0)
    for idx, y in enumerate([-45, 0, 45]):
        port(client, cell, f"SINK{idx}", [130, y], 180, net="bus", width=5.0)
        box(client, cell, [126, y - 4, 134, y + 4])


def build_ordered_loop(client):
    cell = "ROUTER_EXT_02_ORDERED_LOOP"
    reset_cell(client, cell)
    text(client, cell, "02: loop-shaped demands choose candidate pads; needs ordered-loop pairing", [-110, 125])
    demands = [
        ("NORTH", "l0", [0, 45], 90),
        ("EAST", "l1", [45, 0], 0),
        ("SOUTH", "l2", [0, -45], 270),
        ("WEST", "l3", [-45, 0], 180),
    ]
    for name, net, xy, ori in demands:
        port(client, cell, name, xy, ori, net=net, width=4.0)
    candidates = [
        ("PAD_NW", [-100, 90], 180),
        ("PAD_NE", [100, 90], 0),
        ("PAD_SE", [100, -90], 0),
        ("PAD_SW", [-100, -90], 180),
        ("PAD_N", [0, 115], 90),
        ("PAD_S", [0, -115], 270),
    ]
    for name, xy, ori in candidates:
        port(client, cell, name, xy, ori, net="", width=4.0, port_type="candidate_sink")
        box(client, cell, [xy[0] - 3, xy[1] - 3, xy[0] + 3, xy[1] + 3])


def build_damping_corridor(client):
    cell = "ROUTER_EXT_03_DAMPING_CORRIDOR"
    reset_cell(client, cell)
    text(client, cell, "03: narrow obstacle field; hard shortest path lacks graduated damping", [-20, 120])
    port(client, cell, "A", [0, 0], 0, net="damp", width=5.0)
    port(client, cell, "B", [180, 0], 180, net="damp", width=5.0)
    # Alternating obstacles create legal passages but no soft penalty field.
    for idx, x in enumerate([35, 70, 105, 140]):
        if idx % 2 == 0:
            box(client, cell, [x - 8, -8, x + 8, 70], layer=KEEPOUT)
        else:
            box(client, cell, [x - 8, -70, x + 8, 8], layer=KEEPOUT)


def _collect_obstacles(client, cell):
    info = client.call("layout.info", {"verbosity": "summary"})
    dbu = float(info["dbu"])
    query = client.call("shape.query", {
        "cell": cell,
        "layers": ["900/0"],
        "kinds": ["boxes", "polygons", "paths"],
        "limit": 5000,
    })
    return [[float(v) * dbu for v in shape["bbox_dbu"]] for shape in query.get("shapes", []) if shape.get("bbox_dbu")]


def diagnose_cell(client, cell):
    ports = client.call("port.list", {"cell": cell, "layer": PORT_LAYER, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": ANCHOR_LAYER, "sort": "id"}).get("anchors", [])
    pairs = _pair_ports_by_net_tokens(ports)
    obstacles = _collect_obstacles(client, cell)
    planned = route_tapered_hybrid_many(pairs, anchors=anchors, spacing_um=8.0, obstacle_bboxes=obstacles)
    crossings = crossing_pairs(planned.get("routes", []))
    result = route_tapered_hybrid_cell(client, cell, spacing_um=8.0, clear=True, obstacle_layers=["900/0"])
    route_summaries = []
    for route in planned.get("routes", []):
        hits = route_hits_bboxes(route.get("points_um", []), obstacles, max(route.get("source_width_um", 1), route.get("target_width_um", 1)))
        route_summaries.append({
            "net": route.get("net"),
            "source": route.get("source"),
            "target": route.get("target"),
            "points": route.get("points_um"),
            "obstacle_hits": len(hits),
        })
    return {
        "cell": cell,
        "ports": len(ports),
        "candidate_sinks": sum(1 for p in ports if str(p.get("port_type", "")).lower() == "candidate_sink"),
        "pairs": [(p.get("net"), p.get("source", {}).get("name"), p.get("target", {}).get("name"), p.get("assignment")) for p in pairs],
        "obstacles": len(obstacles),
        "planned_ok": planned.get("ok"),
        "planned_errors": planned.get("errors"),
        "route_count": planned.get("route_count"),
        "crossings": len(crossings),
        "routes": route_summaries,
        "write_ok": result.get("ok"),
        "write_errors": result.get("errors"),
        "write_planning_errors": result.get("planning_errors"),
        "write_groups": [
            {
                "layer": g.get("route_layer"),
                "ok": g.get("ok"),
                "routes": g.get("route_count"),
                "errors": g.get("errors"),
                "obstacle_hits": len(g.get("obstacle_hits", []) or []),
                "write": None if g.get("write") is None else {
                    "paths": g["write"].get("paths"),
                    "patches": g["write"].get("patches"),
                    "polygons": g["write"].get("polygons"),
                    "deleted": g["write"].get("deleted"),
                },
            }
            for g in result.get("groups", [])
        ],
    }


def main():
    with KLinkClient().connect() as client:
        ensure_layers(client)
        build_steiner_bus(client)
        build_ordered_loop(client)
        build_damping_corridor(client)
        diagnostics = [diagnose_cell(client, cell) for cell in CELLS]
        client.call("view.show_cell", {"cell": CELLS[0], "zoom_fit": True})
        for item in diagnostics:
            print(item)


if __name__ == "__main__":
    main()
