"""Build five hybrid tapered-router experiment cells (TAPERED_SEG_01 ~ 05).

Hybrid = angle-dependent pulled-back paths + miter polygon patches at bends.
Short segments fall back to continuous polygon.  See tapered_5_example.py for
the full continuous-polygon variant.

Run: python examples_klink/tapered_segments_5_example.py
"""

from __future__ import annotations
import os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.geom.constraints import route_with_port_launch_stubs
from klink.routing.backends.geometric.tapered_segments import commit_tapered_hybrid, route_tapered_hybrid

CELLS = ["TAPERED_SEG_01_STRAIGHT", "TAPERED_SEG_02_WAYPOINT",
         "TAPERED_SEG_03_EDGE_SLIDE", "TAPERED_SEG_04_OBSTACLE",
         "TAPERED_SEG_05_FANOUT"]

PORT_LAYER, ANCHOR_LAYER = "999/99", "999/1"
M1, KEEPOUT = (1, 0), (900, 0)
GUIDE, SEG_RESULT, LABEL = (996, 99), (12, 0), (997, 99)


def _ignore_not_found(fn):
    try: return fn()
    except KLinkServerError: return None

def reset_cell(c, name):
    _ignore_not_found(lambda: c.cell_delete(name, recursive=True))
    c.cell_create(name)

def ensure_layers(c):
    for l, d, n in [(*M1, "M1"), (*KEEPOUT, "KEEPOUT"), (*GUIDE, "GUIDE"),
                     (*SEG_RESULT, "HYBRID"), (*LABEL, "LABELS"),
                     (999, 1, "ANCHORS"), (999, 99, "PORTS")]:
        c.layer_ensure(l, d, name=f"KLINK_{n}")
    c.call("anchor.set_layer", {"layer": ANCHOR_LAYER})
    c.call("port.set_layer", {"layer": PORT_LAYER})

def text(c, cell, v, xy, s=3.0):
    c.shape_insert_text(cell, v, layer=LABEL[0], datatype=LABEL[1], position_um=xy, size_um=s)

def box(c, cell, b, layer=M1):
    c.shape_insert_box(cell, layer=layer[0], datatype=layer[1], bbox_um=b)

def guide(c, cell, pts, w=0.7):
    c.shape_insert_path(cell, layer=GUIDE[0], datatype=GUIDE[1], points_um=pts, width_um=w,
                        begin_ext_um=w/2, end_ext_um=w/2, round_ends=False)

def port(c, cell, name, xy, ori, *, net, width=4.0, port_type="electrical",
         access_mode="point", slide_allowed=False, slide_edge=""):
    return c.call("port.mark", {"cell": cell, "layer": PORT_LAYER, "name": name,
        "center_um": xy, "orientation": ori, "width_um": width, "port_type": port_type,
        "net": net, "target_layer": "10/0", "show_label": True,
        "access_mode": access_mode, "slide_allowed": slide_allowed, "slide_edge": slide_edge})

def anchor(c, cell, aid, xy, kind, *, net, label="", radius=5.0, width=10.0,
           height=10.0, path_points="", priority=0):
    c.call("anchor.mark", {"cell": cell, "layer": ANCHOR_LAYER, "id": aid,
        "center_um": xy, "kind": kind, "mode": "flexible", "net": net, "label": label,
        "show_label": True, "required": True, "priority": priority,
        "radius_um": radius, "width_um": width, "height_um": height, "path_points": path_points})


def corridor_lane_offsets(width_um, spacing_um=4.0):
    """Two-lane corridor offsets sized from the widest route envelope."""
    pitch = float(width_um) + float(spacing_um)
    return (-pitch / 2.0, pitch / 2.0)


def build_01(c):
    cell = "TAPERED_SEG_01_STRAIGHT"
    reset_cell(c, cell); text(c, cell, "01: 5um→2um straight — hybrid", [0, 24])
    box(c, cell, [0,0,20,10]); box(c, cell, [100,0,120,10])
    p0 = port(c, cell, "A", [20,5], 0, net="s", width=5.0)
    p1 = port(c, cell, "B", [100,5], 180, net="s", width=2.0)
    guide(c, cell, route_with_port_launch_stubs(p0, p1)["points_um"])
    t = route_tapered_hybrid(p0, p1, strategy="uniform")
    r = commit_tapered_hybrid(c, cell, t, route_layer="12/0", clear=False)
    print(f"  {cell}: {r['paths']}p {r['patches']}patch {r['polygons']}poly")


def build_02(c):
    cell = "TAPERED_SEG_02_WAYPOINT"
    reset_cell(c, cell); text(c, cell, "02: 5um→2um via waypoint — hybrid", [0, 64])
    box(c, cell, [0,0,18,10]); box(c, cell, [100,0,118,10])
    p0 = port(c, cell, "A", [18,5], 0, net="w", width=5.0)
    p1 = port(c, cell, "B", [100,5], 180, net="w", width=2.0)
    anchor(c, cell, "WP1", [60,40], "waypoint_region", net="w", label="via", width=12, height=10)
    guide(c, cell, route_with_port_launch_stubs(p0, p1, [[60,40]])["points_um"])
    t = route_tapered_hybrid(p0, p1, [[60,40]], strategy="uniform")
    r = commit_tapered_hybrid(c, cell, t, route_layer="12/0", clear=False)
    print(f"  {cell}: {r['paths']}p {r['patches']}patch {r['polygons']}poly")


def build_03(c):
    cell = "TAPERED_SEG_03_EDGE_SLIDE"
    reset_cell(c, cell); text(c, cell, "03: 6um edge→3um — hybrid", [0, 84])
    box(c, cell, [20,20,140,40]); box(c, cell, [150,66,170,78])
    p0 = port(c, cell, "A_EDGE", [80,40], 90, net="e", width=6.0,
              access_mode="edge", slide_allowed=True, slide_edge="20000,40000,140000,40000")
    p1 = port(c, cell, "B", [150,72], 180, net="e", width=3.0)
    anchor(c, cell, "EXIT", [80,54], "waypoint_region", net="e", label="x", width=12, height=8)
    guide(c, cell, route_with_port_launch_stubs(p0, p1, [[80,54]])["points_um"])
    t = route_tapered_hybrid(p0, p1, [[80,54]], strategy="uniform")
    r = commit_tapered_hybrid(c, cell, t, route_layer="12/0", clear=False)
    print(f"  {cell}: {r['paths']}p {r['patches']}patch {r['polygons']}poly")


def build_04(c):
    cell = "TAPERED_SEG_04_OBSTACLE"
    reset_cell(c, cell); text(c, cell, "04: 5um→2um over keepout — hybrid", [0, 58])
    box(c, cell, [0,0,18,10]); box(c, cell, [120,0,138,10])
    box(c, cell, [52,-18,86,28], layer=KEEPOUT); text(c, cell, "KEEP_OUT", [55,31], 2.5)
    p0 = port(c, cell, "A", [18,5], 0, net="o", width=5.0)
    p1 = port(c, cell, "B", [120,5], 180, net="o", width=2.0)
    anchor(c, cell, "BEND", [69,42], "bend_region", net="o", label="^", radius=6, priority=10)
    guide(c, cell, route_with_port_launch_stubs(p0, p1, [[48,42],[90,42]])["points_um"])
    t = route_tapered_hybrid(p0, p1, [[48,42],[90,42]], strategy="uniform")
    r = commit_tapered_hybrid(c, cell, t, route_layer="12/0", clear=False)
    print(f"  {cell}: {r['paths']}p {r['patches']}patch {r['polygons']}poly")


def build_05(c):
    cell = "TAPERED_SEG_05_FANOUT"
    reset_cell(c, cell); text(c, cell, "05: IN 3um→PAD 8um widening — hybrid", [-8, 98])
    for i, y in enumerate([10,24,38,52]):
        box(c, cell, [0,y-3,14,y+3])
        port(c, cell, f"IN{i}", [14,y], 0, net=f"sig{i}", width=3.0)
    for i, y in enumerate([0,14,28,42,56,70]):
        box(c, cell, [110,y-5,132,y+5])
        port(c, cell, f"PAD{i}", [110,y], 180, net="", width=8.0, port_type="candidate_sink")
    lo, hi = [[30,16],[55,17],[60,18]], [[66,49],[81,50],[84,48.5]]
    anchor(c, cell, "LO", [45,17], "corridor", net="sig0,sig1", label="lo", width=8.0,
           path_points="-15,-1;10,0;15,1")
    anchor(c, cell, "HI", [75,49.5], "corridor", net="sig2,sig3", label="hi", width=8.0,
           path_points="-9,-0.5;6,0.5;9,-1")
    lo_a, lo_b = corridor_lane_offsets(8.0)
    hi_a, hi_b = corridor_lane_offsets(8.0)
    fanout_routes = [(10,0,lo,lo_a),(24,14,lo,lo_b),(38,42,hi,hi_a),(52,56,hi,hi_b)]
    for sy, dy, cor, loff in fanout_routes:
        src = {"center_um":[14,sy],"orientation":0,"width_um":3.0}
        dst = {"center_um":[110,dy],"orientation":180,"width_um":8.0}
        lp = [[p[0],p[1]+loff] for p in cor]
        guide(c, cell, route_with_port_launch_stubs(src, dst, lp)["points_um"])
    for sy, dy, cor, loff in fanout_routes:
        src = {"center_um":[14,sy],"orientation":0,"width_um":3.0}
        dst = {"center_um":[110,dy],"orientation":180,"width_um":8.0}
        lp = [[p[0],p[1]+loff] for p in cor]
        t = route_tapered_hybrid(src, dst, lp, strategy="uniform")
        commit_tapered_hybrid(c, cell, t, route_layer="12/0", clear=False)
    print(f"  {cell}: 4 routes 3um→8um")


def main():
    c = KLinkClient().connect()
    ensure_layers(c)
    build_01(c); build_02(c); build_03(c); build_04(c); build_05(c)
    print("\nHybrid cells:")
    for cn in CELLS:
        ports = c.call("port.list", {"cell": cn, "layer": PORT_LAYER, "sort": "name"})
        anchors = c.call("anchor.list", {"cell": cn, "layer": ANCHOR_LAYER, "sort": "id"})
        print(f"  {cn:<28} ports={ports['count']:>2} anchors={anchors['count']:>2}")
    c.show_cell("TAPERED_SEG_01_STRAIGHT", zoom_fit=True)
    p = Path(__file__).with_name("tapered_segments_5_example_screenshot.png")
    c.screenshot(mode="path", width_px=1200, height_px=800, path=str(p))
    print(f"\nScreenshot: {p}"); c.close()

if __name__ == "__main__":
    main()
