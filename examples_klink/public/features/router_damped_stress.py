"""Standalone stress fixtures for explicit damping routing backends.

The example only creates geometry intent: Ports, Anchors, and obstacle boxes.
Routing decisions live in klink.routing.backends.geometric.damped.
"""

from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.mcp.bridge import KLinkMCPBridge


PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
ROUTE_LAYER = "12/0"
LABEL = (997, 99)
PAD = (1, 0)
KEEP = (900, 0)

CELLS = [
    "ROUTER_DAMPED_01_SEGMENT",
    "ROUTER_DAMPED_02_POLYGON",
    "ROUTER_DAMPED_03_STEINER",
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
        (*PAD, "PADS"),
        (*KEEP, "KEEPOUT"),
        (12, 0, "DAMPED_ROUTE"),
        (*LABEL, "LABELS"),
        (999, 1, "ANCHORS"),
        (999, 99, "PORTS"),
    ]:
        client.layer_ensure(layer, datatype, name=f"KLINK_{name}")
    client.call("port.set_layer", {"layer": PORT_LAYER})
    client.call("anchor.set_layer", {"layer": ANCHOR_LAYER})


def text(client, cell, value, xy, size=4.0):
    client.shape_insert_text(cell, value, layer=LABEL[0], datatype=LABEL[1], position_um=xy, size_um=size)


def box(client, cell, bbox, layer=PAD):
    client.shape_insert_box(cell, layer=layer[0], datatype=layer[1], bbox_um=bbox)


def port(client, cell, name, xy, ori, *, net, width=4.0, port_type="electrical"):
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


def anchor(client, cell, anchor_id, xy, kind, *, net, path_points="", width=30.0, radius=10.0):
    return client.call("anchor.mark", {
        "cell": cell,
        "layer": ANCHOR_LAYER,
        "id": anchor_id,
        "center_um": xy,
        "kind": kind,
        "net": net,
        "label": anchor_id,
        "radius_um": radius,
        "width_um": width,
        "height_um": width,
        "path_points": path_points,
    })


def build_segment(client):
    cell = "ROUTER_DAMPED_01_SEGMENT"
    reset_cell(client, cell)
    text(client, cell, "01: damped segment backend avoids keepout halo", [-20, 70])
    port(client, cell, "A", [0, 0], 0, net="sig", width=4.0)
    port(client, cell, "B", [120, 0], 180, net="sig", width=4.0)
    box(client, cell, [48, -8, 72, 8], layer=KEEP)


def build_polygon(client):
    cell = "ROUTER_DAMPED_02_POLYGON"
    reset_cell(client, cell)
    text(client, cell, "02: damped polygon backend with waypoint anchor", [-20, 80])
    port(client, cell, "A", [0, 0], 0, net="sig", width=10.0)
    port(client, cell, "B", [120, 0], 180, net="sig", width=3.0)
    anchor(client, cell, "WP", [60, 38], "waypoint_region", net="sig", width=12.0)
    box(client, cell, [48, -8, 72, 8], layer=KEEP)


def build_steiner(client):
    cell = "ROUTER_DAMPED_03_STEINER"
    reset_cell(client, cell)
    text(client, cell, "03: damped Steiner backend with corridor trunk", [-20, 110])
    port(client, cell, "ROOT", [0, 0], 0, net="bus", width=9.0, port_type="root")
    for idx, (x, y, width) in enumerate([(130, -40, 3.0), (130, 0, 5.0), (130, 40, 7.0)]):
        port(client, cell, f"S{idx}", [x, y], 180, net="bus", width=width)
    anchor(client, cell, "COR", [105, 0], "corridor", net="bus", path_points="0,-55;0,55", width=30.0)
    box(client, cell, [54, -12, 74, 12], layer=KEEP)


def summarize(result):
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "cell": result.get("cell"),
        "damping_distance_um": result.get("damping_distance_um"),
        "groups": [
            {
                "route_layer": group.get("route_layer"),
                "net": group.get("net"),
                "ok": group.get("ok"),
                "route_count": group.get("route_count"),
                "obstacle_hits": len(group.get("obstacle_hits", []) or []),
                "write": None if group.get("write") is None else {
                    "inserted": group["write"].get("inserted"),
                    "paths": group["write"].get("paths"),
                    "polygons": group["write"].get("polygons"),
                    "inserted_polygons": group["write"].get("inserted_polygons"),
                },
                "errors": group.get("errors"),
            }
            for group in result.get("groups", [])
        ],
        "errors": result.get("errors"),
    }


def call_mcp_tool(bridge, name, arguments):
    result = bridge.call_tool(name, arguments)
    if result.get("isError"):
        return {"ok": False, "backend": name, "errors": [result["content"][0]["text"]]}
    return json.loads(result["content"][0]["text"])


def main():
    with KLinkClient().connect() as client:
        ensure_layers(client)
        build_segment(client)
        build_polygon(client)
        build_steiner(client)
        bridge = KLinkMCPBridge(profiles=["basic"])
        diagnostics = [
            call_mcp_tool(bridge, "routing.damped_segment_cell", {
                "cell": CELLS[0],
                "obstacle_layers": ["900/0"],
                "damping_distance_um": 10.0,
            }),
            call_mcp_tool(bridge, "routing.damped_polygon_cell", {
                "cell": CELLS[1],
                "obstacle_layers": ["900/0"],
                "damping_distance_um": 10.0,
            }),
            call_mcp_tool(bridge, "routing.damped_steiner_cell", {
                "cell": CELLS[2],
                "obstacle_layers": ["900/0"],
                "damping_distance_um": 10.0,
                "root_ports": {"bus": "ROOT"},
            }),
        ]
        bridge.close()
        client.call("view.show_cell", {"cell": CELLS[0], "zoom_fit": True})
        for item in diagnostics:
            print(summarize(item))


if __name__ == "__main__":
    main()
