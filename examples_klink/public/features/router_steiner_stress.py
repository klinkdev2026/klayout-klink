"""Standalone stress example for the klink Steiner/bus router.

This example is intentionally mechanical:

- build Port markers for multi-terminal nets
- call route_steiner_cell(...)
- print diagnostics

No route coordinates or fixture-specific routing decisions are encoded here.
The routing intelligence lives in klink.routing.backends.geometric.steiner.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.backends.geometric.steiner import route_steiner_cell


PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
ROUTE_LAYER = "12/0"
LABEL = (997, 99)
PAD = (1, 0)

CELLS = [
    "ROUTER_STEINER_01_VERTICAL_BUS",
    "ROUTER_STEINER_02_HORIZONTAL_BUS",
    "ROUTER_STEINER_03_ANCHOR_CONTRACT",
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
        (1, 0, "PADS"),
        (12, 0, "STEINER_ROUTE"),
        (997, 99, "LABELS"),
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


def port(client, cell, name, xy, ori, *, net="bus", width=5.0, port_type="electrical"):
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


def anchor(client, cell, anchor_id, xy, kind, *, net="bus", path_points="", width=20.0, radius=10.0, priority=0):
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
        "priority": priority,
    })


def build_vertical_bus(client):
    cell = "ROUTER_STEINER_01_VERTICAL_BUS"
    reset_cell(client, cell)
    text(client, cell, "01: multi-terminal net -> vertical trunk + branches", [-20, 90])
    port(client, cell, "ROOT", [0, 0], 0, port_type="root")
    for idx, y in enumerate([-45, 0, 45]):
        port(client, cell, f"SINK{idx}", [130, y], 180)
        box(client, cell, [126, y - 4, 134, y + 4])


def build_horizontal_bus(client):
    cell = "ROUTER_STEINER_02_HORIZONTAL_BUS"
    reset_cell(client, cell)
    text(client, cell, "02: multi-terminal net -> horizontal trunk + branches", [-80, 160])
    port(client, cell, "ROOT", [0, 0], 90, port_type="root")
    for idx, x in enumerate([-45, 0, 45]):
        port(client, cell, f"SINK{idx}", [x, 130], 270)
        box(client, cell, [x - 4, 126, x + 4, 134])


def build_anchor_contract(client):
    cell = "ROUTER_STEINER_03_ANCHOR_CONTRACT"
    reset_cell(client, cell)
    text(client, cell, "03: Steiner obeys waypoint, bend, and corridor anchors", [-80, 120])
    port(client, cell, "ROOT", [0, 0], 0, net="bus", width=9.0, port_type="root")
    for idx, (x, y, width) in enumerate([(135, -45, 3.0), (130, 0, 5.0), (125, 45, 7.0)]):
        port(client, cell, f"SINK{idx}", [x, y], 180, net="bus", width=width)
        box(client, cell, [x - 4, y - 4, x + 4, y + 4])
    anchor(client, cell, "COR", [80, 0], "corridor", net="bus", path_points="0,-60;0,60", width=30.0, priority=0)
    anchor(client, cell, "WP", [80, -20], "waypoint_region", net="bus", width=12.0, priority=1)
    anchor(client, cell, "BEND", [80, 20], "bend_region", net="bus", radius=10.0, priority=2)


def diagnose(result):
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "cell": result.get("cell"),
        "groups": [
            {
                "net": group.get("net"),
                "root": group.get("root"),
                "anchors": group.get("anchors"),
                "trunk_axis": group.get("trunk_axis"),
                "route_count": group.get("route_count"),
                "write": None if group.get("write") is None else {
                    "inserted": group["write"].get("inserted"),
                    "deleted": group["write"].get("deleted"),
                },
                "errors": group.get("errors"),
            }
            for group in result.get("groups", [])
        ],
        "errors": result.get("errors"),
    }


def main():
    with KLinkClient().connect() as client:
        ensure_layers(client)
        build_vertical_bus(client)
        build_horizontal_bus(client)
        build_anchor_contract(client)
        diagnostics = []
        for cell in CELLS:
            result = route_steiner_cell(
                client,
                cell,
                port_layer=PORT_LAYER,
                anchor_layer=ANCHOR_LAYER,
                route_layer=ROUTE_LAYER,
                root_ports={"bus": "ROOT"},
                obstacle_layers=[],
                clear=True,
            )
            diagnostics.append(diagnose(result))
        client.call("view.show_cell", {"cell": CELLS[0], "zoom_fit": True})
        for item in diagnostics:
            print(item)


if __name__ == "__main__":
    main()
