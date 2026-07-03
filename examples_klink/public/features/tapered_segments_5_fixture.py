"""Build fixture cells for tapered hybrid router examples.

This file intentionally does not run the router.  It only creates ports,
anchors, keepouts, and labels.  Router logic belongs in
``tapered_segments_5_router.py`` and in ``klink.routing``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.errors import KLinkServerError


CELLS = [
    "TAPERED_SEG_FIX_01_STRAIGHT",
    "TAPERED_SEG_FIX_02_WAYPOINT",
    "TAPERED_SEG_FIX_03_EDGE_SLIDE",
    "TAPERED_SEG_FIX_04_OBSTACLE",
    "TAPERED_SEG_FIX_05_FANOUT",
]

PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
M1 = (1, 0)
KEEPOUT = (900, 0)
LABEL = (997, 99)


def _ignore_not_found(fn):
    try:
        return fn()
    except KLinkServerError:
        return None


def reset_cell(c, name):
    _ignore_not_found(lambda: c.cell_delete(name, recursive=True))
    c.cell_create(name)


def ensure_layers(c):
    for layer, datatype, name in [
        (*M1, "M1"),
        (*KEEPOUT, "KEEPOUT"),
        (*LABEL, "LABELS"),
        (999, 1, "ANCHORS"),
        (999, 99, "PORTS"),
        (12, 0, "HYBRID"),
    ]:
        c.layer_ensure(layer, datatype, name=f"KLINK_{name}")
    c.call("anchor.set_layer", {"layer": ANCHOR_LAYER})
    c.call("port.set_layer", {"layer": PORT_LAYER})


def text(c, cell, value, xy, size=3.0):
    c.shape_insert_text(cell, value, layer=LABEL[0], datatype=LABEL[1], position_um=xy, size_um=size)


def box(c, cell, bbox, layer=M1):
    c.shape_insert_box(cell, layer=layer[0], datatype=layer[1], bbox_um=bbox)


def port(c, cell, name, xy, ori, *, net, width, port_type="electrical", access_mode="point", slide_allowed=False, slide_edge=""):
    return c.call("port.mark", {
        "cell": cell,
        "layer": PORT_LAYER,
        "name": name,
        "center_um": xy,
        "orientation": ori,
        "width_um": width,
        "port_type": port_type,
        "net": net,
        "target_layer": "12/0",
        "show_label": True,
        "access_mode": access_mode,
        "slide_allowed": slide_allowed,
        "slide_edge": slide_edge,
    })


def anchor(c, cell, anchor_id, xy, kind, *, net, label="", radius=5.0, width=10.0, height=10.0, path_points="", priority=0):
    return c.call("anchor.mark", {
        "cell": cell,
        "layer": ANCHOR_LAYER,
        "id": anchor_id,
        "center_um": xy,
        "kind": kind,
        "mode": "flexible",
        "net": net,
        "label": label,
        "show_label": True,
        "required": True,
        "priority": priority,
        "radius_um": radius,
        "width_um": width,
        "height_um": height,
        "path_points": path_points,
    })


def build_01(c):
    cell = "TAPERED_SEG_FIX_01_STRAIGHT"
    reset_cell(c, cell)
    text(c, cell, "01 fixture: straight", [0, 24])
    box(c, cell, [0, 0, 20, 10])
    box(c, cell, [100, 0, 120, 10])
    port(c, cell, "A", [20, 5], 0, net="s", width=5.0)
    port(c, cell, "B", [100, 5], 180, net="s", width=2.0)


def build_02(c):
    cell = "TAPERED_SEG_FIX_02_WAYPOINT"
    reset_cell(c, cell)
    text(c, cell, "02 fixture: waypoint", [0, 64])
    box(c, cell, [0, 0, 18, 10])
    box(c, cell, [100, 0, 118, 10])
    port(c, cell, "A", [18, 5], 0, net="w", width=5.0)
    port(c, cell, "B", [100, 5], 180, net="w", width=2.0)
    anchor(c, cell, "WP1", [60, 40], "waypoint_region", net="w", label="via", width=12, height=10)


def build_03(c):
    cell = "TAPERED_SEG_FIX_03_EDGE_SLIDE"
    reset_cell(c, cell)
    text(c, cell, "03 fixture: edge slide", [0, 84])
    box(c, cell, [20, 20, 140, 40])
    box(c, cell, [150, 66, 170, 78])
    port(c, cell, "A_EDGE", [80, 40], 90, net="e", width=6.0, access_mode="edge", slide_allowed=True, slide_edge="20000,40000,140000,40000")
    port(c, cell, "B", [150, 72], 180, net="e", width=3.0)
    anchor(c, cell, "EXIT", [80, 54], "waypoint_region", net="e", label="x", width=12, height=8)


def build_04(c):
    cell = "TAPERED_SEG_FIX_04_OBSTACLE"
    reset_cell(c, cell)
    text(c, cell, "04 fixture: bend anchor over keepout", [0, 58])
    box(c, cell, [0, 0, 18, 10])
    box(c, cell, [120, 0, 138, 10])
    box(c, cell, [52, -18, 86, 28], layer=KEEPOUT)
    text(c, cell, "KEEP_OUT", [55, 31], 2.5)
    port(c, cell, "A", [18, 5], 0, net="o", width=5.0)
    port(c, cell, "B", [120, 5], 180, net="o", width=2.0)
    anchor(c, cell, "BEND", [69, 42], "bend_region", net="o", label="^", radius=21, priority=10)


def build_05(c):
    cell = "TAPERED_SEG_FIX_05_FANOUT"
    reset_cell(c, cell)
    text(c, cell, "05 fixture: 4 demands choose 4 of 6 candidate pads", [-8, 112])
    for i, y in enumerate([10, 24, 38, 52]):
        box(c, cell, [0, y - 3, 14, y + 3])
        port(c, cell, f"IN{i}", [14, y], 0, net=f"sig{i}", width=3.0)
    for i, y in enumerate([-18, 0, 14, 42, 56, 82]):
        box(c, cell, [110, y - 5, 132, y + 5])
        port(c, cell, f"PAD{i}", [110, y], 180, net="", width=8.0, port_type="candidate_sink")
    anchor(c, cell, "LO", [45, 17], "corridor", net="sig0,sig1", label="lo", width=24.0, path_points="-15,-1;10,0;15,1")
    anchor(c, cell, "HI", [75, 49.5], "corridor", net="sig2,sig3", label="hi", width=24.0, path_points="-9,-0.5;6,0.5;9,-1")


def main():
    with KLinkClient().connect() as c:
        ensure_layers(c)
        for builder in [build_01, build_02, build_03, build_04, build_05]:
            builder(c)
        c.show_cell(CELLS[0], zoom_fit=True)
        print("Fixture cells:")
        for cell in CELLS:
            ports = c.call("port.list", {"cell": cell, "layer": PORT_LAYER, "sort": "name"})
            anchors = c.call("anchor.list", {"cell": cell, "layer": ANCHOR_LAYER, "sort": "id"})
            print(f"  {cell:<32} ports={ports['count']:>2} anchors={anchors['count']:>2}")


if __name__ == "__main__":
    main()
