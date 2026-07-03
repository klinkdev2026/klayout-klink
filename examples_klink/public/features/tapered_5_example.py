"""Build five tapered-router experiment cells (TAPERED_01 ~ TAPERED_05).

Continuous polygon taper version: uses ``route_tapered()`` + polygon for all
cases.  See ``tapered_segments_5_example.py`` for the discrete-path variant.

Run with KLayout open and the klink plugin loaded:

  python examples_klink/tapered_5_example.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.geom.constraints import route_with_port_launch_stubs
from klink.routing.backends.geometric.tapered import (
    commit_tapered_routes,
    route_tapered,
    validate_tapered_route,
)

CELLS = [
    "TAPERED_01_STRAIGHT",
    "TAPERED_02_WAYPOINT",
    "TAPERED_03_EDGE_SLIDE",
    "TAPERED_04_OBSTACLE",
    "TAPERED_05_FANOUT",
]

PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
M1 = (1, 0)
KEEPOUT = (900, 0)
GUIDE = (996, 99)
TAPER_RESULT = (11, 0)
LABEL = (997, 99)


def _ignore_not_found(fn):
    try:
        return fn()
    except KLinkServerError:
        return None


def reset_cell(c: KLinkClient, name: str) -> None:
    _ignore_not_found(lambda: c.cell_delete(name, recursive=True))
    c.cell_create(name)


def ensure_layers(c: KLinkClient) -> None:
    c.layer_ensure(*M1, name="M1_DEVICE_OR_PAD")
    c.layer_ensure(*KEEPOUT, name="KLINK_ROUTE_KEEPOUT")
    c.layer_ensure(*GUIDE, name="KLINK_EXPECTED_ROUTE_GUIDE")
    c.layer_ensure(*TAPER_RESULT, name="KLINK_TAPERED_RESULT")
    c.layer_ensure(*LABEL, name="KLINK_EXAMPLE_LABELS")
    c.layer_ensure(999, 1, name="KLINK_ANCHORS")
    c.layer_ensure(999, 99, name="KLINK_PORTS")
    c.call("anchor.set_layer", {"layer": ANCHOR_LAYER})
    c.call("port.set_layer", {"layer": PORT_LAYER})


def text(c: KLinkClient, cell: str, value: str, xy: list[float], size: float = 3.0) -> None:
    c.shape_insert_text(cell, value, layer=LABEL[0], datatype=LABEL[1],
                        position_um=xy, size_um=size)


def box(c: KLinkClient, cell: str, bbox: list[float], layer=M1) -> None:
    c.shape_insert_box(cell, layer=layer[0], datatype=layer[1], bbox_um=bbox)


def guide(c: KLinkClient, cell: str, points: list[list[float]], width: float = 0.7) -> None:
    c.shape_insert_path(cell, layer=GUIDE[0], datatype=GUIDE[1],
                        points_um=points, width_um=width,
                        begin_ext_um=width / 2.0, end_ext_um=width / 2.0,
                        round_ends=False)


def port(c: KLinkClient, cell: str, name: str, xy: list[float], orientation: float,
         *, net: str, width: float = 4.0, port_type: str = "electrical",
         access_mode: str = "point", slide_allowed: bool = False,
         slide_edge: str = "") -> dict:
    return c.call("port.mark", {
        "cell": cell, "layer": PORT_LAYER, "name": name,
        "center_um": xy, "orientation": orientation, "width_um": width,
        "port_type": port_type, "net": net, "target_layer": "10/0",
        "show_label": True, "access_mode": access_mode,
        "slide_allowed": slide_allowed, "slide_edge": slide_edge,
    })


def anchor(c: KLinkClient, cell: str, anchor_id: str, xy: list[float], kind: str,
           *, net: str, label: str = "", radius: float = 5.0, width: float = 10.0,
           height: float = 10.0, path_points: str = "", priority: int = 0) -> None:
    c.call("anchor.mark", {
        "cell": cell, "layer": ANCHOR_LAYER, "id": anchor_id,
        "center_um": xy, "kind": kind, "mode": "flexible", "net": net,
        "label": label, "show_label": True, "required": True,
        "priority": priority, "radius_um": radius, "width_um": width,
        "height_um": height, "path_points": path_points,
    })


# ======================================================================
# Cell builders — all use polygon (route_tapered)
# ======================================================================


def build_01_straight(c: KLinkClient) -> None:
    """Source=5um, Target=2um — straight connection → trapezoid polygon."""
    cell = "TAPERED_01_STRAIGHT"
    reset_cell(c, cell)
    text(c, cell, "01_STRAIGHT: 5um→2um polygon trapezoid, no bends", [0, 24])

    box(c, cell, [0, 0, 20, 10])
    box(c, cell, [100, 0, 120, 10])
    p0 = port(c, cell, "A", [20, 5], 0, net="net_straight", width=5.0)
    p1 = port(c, cell, "B", [100, 5], 180, net="net_straight", width=2.0)

    r = route_with_port_launch_stubs(p0, p1)
    guide(c, cell, r["points_um"], width=r["width_um"])

    t = route_tapered(p0, p1, strategy="uniform", corner_style="miter")
    commit_tapered_routes(c, cell, [t], route_layer="11/0", clear=False)
    print(f"  {cell}: polygon trapezoid widths={[round(w,2) for w in t['widths_um']]}")


def build_02_waypoint(c: KLinkClient) -> None:
    """Source=5um, Target=2um, waypoint → polygon with bend narrowing."""
    cell = "TAPERED_02_WAYPOINT"
    reset_cell(c, cell)
    text(c, cell, "02_WAYPOINT: 5um→2um via waypoint → polygon", [0, 64])

    box(c, cell, [0, 0, 18, 10])
    box(c, cell, [100, 0, 118, 10])
    p0 = port(c, cell, "A", [18, 5], 0, net="net_waypoint", width=5.0)
    p1 = port(c, cell, "B", [100, 5], 180, net="net_waypoint", width=2.0)
    anchor(c, cell, "WP1", [60, 40], "waypoint_region", net="net_waypoint",
           label="must_pass", width=12, height=10)

    r = route_with_port_launch_stubs(p0, p1, [[60, 40]])
    guide(c, cell, r["points_um"], width=r["width_um"])

    t = route_tapered(p0, p1, [[60, 40]], strategy="uniform", corner_style="miter")
    commit_tapered_routes(c, cell, [t], route_layer="11/0", clear=False)
    print(f"  {cell}: bends={t['num_bends']} ratios={t['per_bend_ratios']}")


def build_03_edge_slide(c: KLinkClient) -> None:
    """Source=6um edge-slide, Target=3um → polygon."""
    cell = "TAPERED_03_EDGE_SLIDE"
    reset_cell(c, cell)
    text(c, cell, "03_EDGE_SLIDE: 6um edge→3um → polygon", [0, 84])

    box(c, cell, [20, 20, 140, 40])
    box(c, cell, [150, 66, 170, 78])

    slide_edge = "20000,40000,140000,40000"
    p0 = port(c, cell, "A_EDGE", [80, 40], 90, net="net_slide", width=6.0,
              access_mode="edge", slide_allowed=True, slide_edge=slide_edge)
    p1 = port(c, cell, "B", [150, 72], 180, net="net_slide", width=3.0)
    anchor(c, cell, "EXIT", [80, 54], "waypoint_region", net="net_slide",
           label="edge_exit", width=12, height=8)

    r = route_with_port_launch_stubs(p0, p1, [[80, 54]])
    guide(c, cell, r["points_um"], width=r["width_um"])

    t = route_tapered(p0, p1, [[80, 54]], strategy="uniform", corner_style="miter")
    commit_tapered_routes(c, cell, [t], route_layer="11/0", clear=False)
    print(f"  {cell}: bends={t['num_bends']} ratios={t['per_bend_ratios']}")


def build_04_obstacle(c: KLinkClient) -> None:
    """Source=5um, Target=2um — route ABOVE keepout via bend anchor → polygon."""
    cell = "TAPERED_04_OBSTACLE"
    reset_cell(c, cell)
    text(c, cell, "04_OBSTACLE: 5um→2um over obstacle → polygon", [0, 58])

    box(c, cell, [0, 0, 18, 10])
    box(c, cell, [120, 0, 138, 10])
    box(c, cell, [52, -18, 86, 28], layer=KEEPOUT)
    text(c, cell, "KEEP_OUT", [55, 31], size=2.5)

    p0 = port(c, cell, "A", [18, 5], 0, net="net_obstacle", width=5.0)
    p1 = port(c, cell, "B", [120, 5], 180, net="net_obstacle", width=2.0)
    anchor(c, cell, "BEND_ABOVE", [69, 42], "bend_region", net="net_obstacle",
           label="above", radius=6, priority=10)

    r = route_with_port_launch_stubs(p0, p1, [[48, 42], [90, 42]])
    guide(c, cell, r["points_um"], width=r["width_um"])

    t = route_tapered(p0, p1, [[48, 42], [90, 42]], strategy="uniform", corner_style="miter")
    commit_tapered_routes(c, cell, [t], route_layer="11/0", clear=False)

    keepout_bboxes = [[52.0, -18.0, 86.0, 28.0]]
    v = validate_tapered_route(t, obstacle_bboxes=keepout_bboxes)
    print(f"  {cell}: bends={t['num_bends']} ratios={t['per_bend_ratios']} valid={v['valid']}")
    if not v["valid"]:
        print(f"    WARNING: {v['errors']}")


def build_05_fanout(c: KLinkClient) -> None:
    """Demand 3um, PAD 8um — fanout → polygon (widening)."""
    cell = "TAPERED_05_FANOUT"
    reset_cell(c, cell)
    text(c, cell, "05_FANOUT: IN 3um→PAD 8um via corridors → polygon", [-8, 98])

    for i, y in enumerate([10, 24, 38, 52]):
        box(c, cell, [0, y - 3, 14, y + 3])
        port(c, cell, f"IN{i}", [14, y], 0, net=f"sig{i}", width=3.0)

    for i, y in enumerate([0, 14, 28, 42, 56, 70]):
        box(c, cell, [110, y - 5, 132, y + 5])
        port(c, cell, f"PAD{i}", [110, y], 180, net="", width=8.0,
             port_type="candidate_sink")

    lower_corridor = [[30, 16], [55, 17], [60, 18]]
    upper_corridor = [[66, 49], [81, 50], [84, 48.5]]
    anchor(c, cell, "LOWER_CORRIDOR", [45, 17], "corridor", net="sig0,sig1",
           label="follow_lower", width=8.0,
           path_points="-15,-1;10,0;15,1")
    anchor(c, cell, "UPPER_CORRIDOR", [75, 49.5], "corridor", net="sig2,sig3",
           label="follow_upper", width=8.0,
           path_points="-9,-0.5;6,0.5;9,-1")

    assignments = [
        (10, 0, lower_corridor, -3.0),
        (24, 14, lower_corridor, 3.0),
        (38, 42, upper_corridor, -3.0),
        (52, 56, upper_corridor, 3.0),
    ]
    for src_y, dst_y, corridor_points, lane_offset in assignments:
        src = {"center_um": [14, src_y], "orientation": 0, "width_um": 3.0}
        dst = {"center_um": [110, dst_y], "orientation": 180, "width_um": 8.0}
        lane_points = [[p[0], p[1] + lane_offset] for p in corridor_points]
        r = route_with_port_launch_stubs(src, dst, lane_points)
        guide(c, cell, r["points_um"], width=r["width_um"])

    for src_y, dst_y, corridor_points, lane_offset in assignments:
        src = {"center_um": [14, src_y], "orientation": 0, "width_um": 3.0}
        dst = {"center_um": [110, dst_y], "orientation": 180, "width_um": 8.0}
        lane_points = [[p[0], p[1] + lane_offset] for p in corridor_points]
        t = route_tapered(src, dst, lane_points, strategy="uniform", corner_style="miter")
        commit_tapered_routes(c, cell, [t], route_layer="11/0", clear=False)

    print(f"  {cell}: 4 polygon routes with 3um→8um widening")


# ======================================================================
# Corner style demo (polygon)
# ======================================================================


def draw_corner_style_demo(c: KLinkClient) -> None:
    """Extra cell: same polygon route with miter / bevel / round comparison."""
    cell = "TAPERED_CORNER_DEMO"
    reset_cell(c, cell)
    text(c, cell, "CORNER_DEMO: polygon miter / bevel / round", [0, 70])

    box(c, cell, [0, 0, 18, 10])
    box(c, cell, [100, 0, 118, 10])
    p0 = port(c, cell, "A", [18, 5], 0, net="demo", width=8.0)
    p1 = port(c, cell, "B", [100, 5], 180, net="demo", width=3.0)
    anchor(c, cell, "WP1", [60, 40], "waypoint_region", net="demo",
           label="via", width=12, height=10)

    inner = [[60, 40]]
    for style, layer_str in [("miter", "12/0"), ("bevel", "13/0"), ("round", "14/0")]:
        c.layer_ensure(int(layer_str.split("/")[0]), int(layer_str.split("/")[1]),
                       name=f"KLINK_CORNER_{style.upper()}")
        t = route_tapered(p0, p1, inner, strategy="uniform",
                          corner_style=style,
                          miter_limit=2.0, arc_points=8)
        commit_tapered_routes(c, cell, [t], route_layer=layer_str, clear=False)
        text(c, cell, style, [105, 55 - 8 * ["miter", "bevel", "round"].index(style)],
             size=2.5)

    print(f"  {cell}: polygon miter(12/0) bevel(13/0) round(14/0)")


# ======================================================================
# Main
# ======================================================================


def print_summary(c: KLinkClient) -> None:
    print("\nTapered routing experiment cells (polygon):")
    for cell_name in CELLS:
        ports = c.call("port.list", {"cell": cell_name, "layer": PORT_LAYER, "sort": "name"})
        anchors = c.call("anchor.list", {"cell": cell_name, "layer": ANCHOR_LAYER, "sort": "id"})
        print(f"  {cell_name:<28} ports={ports['count']:>2} anchors={anchors['count']:>2}")


def main() -> None:
    c = KLinkClient().connect()
    ensure_layers(c)

    build_01_straight(c)
    build_02_waypoint(c)
    build_03_edge_slide(c)
    build_04_obstacle(c)
    build_05_fanout(c)
    draw_corner_style_demo(c)

    print_summary(c)

    c.show_cell("TAPERED_01_STRAIGHT", zoom_fit=True)
    screenshot_path = Path(__file__).with_name("tapered_5_example_screenshot.png")
    c.screenshot(mode="path", width_px=1200, height_px=800, path=str(screenshot_path))
    print(f"\nScreenshot saved: {screenshot_path}")
    c.close()


if __name__ == "__main__":
    main()
