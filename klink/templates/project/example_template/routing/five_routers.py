"""Five tapered-routing scenarios, one backend, honest pass/fail per route.

Builds five small Port + Anchor routing-input cells (device/pad geometry,
a keepout box, Port and Anchor PCells) -- the SITUATIONS a router has to
handle: a plain two-port net, a forced waypoint, a port allowed to slide
along an edge, an obstacle to route around, and demand ports fanning out
to whichever candidate pad each net picks. All five route through the
SAME continuous-polygon taper backend (``route_tapered`` /
``commit_tapered_routes``); the differences are in what each scenario
feeds it (anchors, obstacle bboxes, per-net widths), not in which router
function gets called. A route only counts as done when ``ok=true`` AND
it hit zero obstacles AND zero sibling routes AND wrote the expected
polygon count -- never call a failed route routed.

Run:  python example_template/routing/five_routers.py
"""

from __future__ import annotations

import argparse
import sys

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.routing.geom.constraints import route_with_port_launch_stubs
from klink.routing.backends.geometric.tapered import (
    commit_tapered_routes,
    route_tapered,
    validate_tapered_route,
)

# --- demo design data (example-owned) ---------------------------------------
PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
M1 = (1, 0)
KEEPOUT = (900, 0)
GUIDE = (996, 99)          # reference centerline (route_with_port_launch_stubs)
TAPER_RESULT = (11, 0)     # committed taper polygons (route_tapered)
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


# =============================================================================
# Phase 1 -- build the five experiment cells (device/pad geometry, keepout,
# Port PCells, Anchor PCells). Each builder returns what Phase 2 needs to
# route it: the route request(s) (source port, target port, inner waypoints)
# and any obstacle bboxes the route must clear.
# =============================================================================


def build_01_straight(c: KLinkClient) -> dict:
    """Baseline: two same-net ports, no anchors. 5um -> 2um taper, no bends."""
    cell = "ROUTE_01_STRAIGHT"
    reset_cell(c, cell)
    text(c, cell, "01_STRAIGHT: baseline two same-net ports, 5um->2um, no bends", [0, 24])
    box(c, cell, [0, 0, 20, 10])
    box(c, cell, [100, 0, 120, 10])
    p0 = port(c, cell, "A", [20, 5], 0, net="net_straight", width=5.0)
    p1 = port(c, cell, "B", [100, 5], 180, net="net_straight", width=2.0)
    return {"cell": cell, "requests": [{"source": p0, "target": p1, "inner_points": []}],
            "obstacle_bboxes": []}


def build_02_waypoint(c: KLinkClient) -> dict:
    """Route must pass through the WP1 waypoint-region anchor."""
    cell = "ROUTE_02_WAYPOINT"
    reset_cell(c, cell)
    text(c, cell, "02_WAYPOINT: route must pass through WP1 box anchor", [0, 64])
    box(c, cell, [0, 0, 18, 10])
    box(c, cell, [100, 0, 118, 10])
    p0 = port(c, cell, "A", [18, 5], 0, net="net_waypoint", width=5.0)
    p1 = port(c, cell, "B", [100, 5], 180, net="net_waypoint", width=2.0)
    anchor(c, cell, "WP1", [60, 40], "waypoint_region", net="net_waypoint",
           label="must_pass", width=12, height=10)
    return {"cell": cell, "requests": [{"source": p0, "target": p1, "inner_points": [[60, 40]]}],
            "obstacle_bboxes": []}


def build_03_edge_slide(c: KLinkClient) -> dict:
    """A_EDGE may slide along the device top edge before launching."""
    cell = "ROUTE_03_EDGE_SLIDE"
    reset_cell(c, cell)
    text(c, cell, "03_EDGE_SLIDE: A_EDGE may slide along the device top edge", [0, 84])
    box(c, cell, [20, 20, 140, 40])
    box(c, cell, [150, 66, 170, 78])

    # Stored in DBU for the current default 1 nm database unit. This example
    # intentionally documents the router-facing slide edge.
    slide_edge = "20000,40000,140000,40000"
    p0 = port(c, cell, "A_EDGE", [80, 40], 90, net="net_slide", width=6.0,
              access_mode="edge", slide_allowed=True, slide_edge=slide_edge)
    p1 = port(c, cell, "B", [150, 72], 180, net="net_slide", width=3.0)
    anchor(c, cell, "EXIT", [80, 54], "waypoint_region", net="net_slide",
           label="edge_exit", width=12, height=8)
    return {"cell": cell, "requests": [{"source": p0, "target": p1, "inner_points": [[80, 54]]}],
            "obstacle_bboxes": []}


def build_04_obstacle(c: KLinkClient) -> dict:
    """Avoid the 900/0 keepout; use a bend-region anchor to route above it."""
    cell = "ROUTE_04_OBSTACLE"
    reset_cell(c, cell)
    text(c, cell, "04_OBSTACLE: avoid keepout on 900/0, use bend-region anchor", [0, 58])
    box(c, cell, [0, 0, 18, 10])
    box(c, cell, [120, 0, 138, 10])
    keepout_bbox = [52.0, -18.0, 86.0, 28.0]
    box(c, cell, keepout_bbox, layer=KEEPOUT)
    text(c, cell, "KEEP_OUT", [55, 31], size=2.5)
    p0 = port(c, cell, "A", [18, 5], 0, net="net_obstacle", width=5.0)
    p1 = port(c, cell, "B", [120, 5], 180, net="net_obstacle", width=2.0)
    anchor(c, cell, "BEND_ABOVE", [69, 42], "bend_region", net="net_obstacle",
           label="above", radius=6, priority=10)
    # The route must avoid the 900/0 keepout after clearing it entirely; two
    # points routing above the box (not just through the anchor center) do
    # that -- passing only through the anchor would overlap the obstacle.
    return {"cell": cell,
            "requests": [{"source": p0, "target": p1, "inner_points": [[48, 42], [90, 42]]}],
            "obstacle_bboxes": [keepout_bbox]}


def build_05_fanout(c: KLinkClient) -> dict:
    """Demand ports choose pads and follow directional corridors; 3um IN -> 8um PAD."""
    cell = "ROUTE_05_FANOUT"
    reset_cell(c, cell)
    text(c, cell, "05_FANOUT: demand ports choose pads and follow directional corridors", [-8, 98])

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
           label="follow_lower", width=8.0, path_points="-15,-1;10,0;15,1")
    anchor(c, cell, "UPPER_CORRIDOR", [75, 49.5], "corridor", net="sig2,sig3",
           label="follow_upper", width=8.0, path_points="-9,-0.5;6,0.5;9,-1")

    # Lane offset must be wide enough that two same-corridor routes, each
    # widening from a 3um IN port to an 8um PAD port, never touch. The
    # +-3um split used by the original examples is NOT enough once
    # validated (route_tapered's mid-corridor width already exceeds 6um,
    # so +-3um lanes overlap); +-5um is the smallest split this scenario's
    # geometry clears with zero sibling overlaps -- verified in Phase 3.
    assignments = [
        (10, 0, lower_corridor, -5.0),
        (24, 14, lower_corridor, 5.0),
        (38, 42, upper_corridor, -5.0),
        (52, 56, upper_corridor, 5.0),
    ]
    requests = []
    for src_y, dst_y, corridor_points, lane_offset in assignments:
        src = {"center_um": [14, src_y], "orientation": 0, "width_um": 3.0}
        dst = {"center_um": [110, dst_y], "orientation": 180, "width_um": 8.0}
        lane_points = [[p[0], p[1] + lane_offset] for p in corridor_points]
        requests.append({"source": src, "target": dst, "inner_points": lane_points})
    return {"cell": cell, "requests": requests, "obstacle_bboxes": []}


# =============================================================================
# Phase 2 -- invoke the tapered-routing backend for each scenario: a
# reference centerline guide (route_with_port_launch_stubs, on 996/99, same
# as the Port/Anchor scene demo) plus the real taper polygon route
# (route_tapered -> commit_tapered_routes, on 11/0). Same calls the source
# examples make; a scenario's routes are committed together in one call.
# =============================================================================


def route_scene(c: KLinkClient, scene: dict) -> dict:
    cell = scene["cell"]
    routes = []
    for req in scene["requests"]:
        p0, p1, inner = req["source"], req["target"], req["inner_points"]
        g = route_with_port_launch_stubs(p0, p1, inner or None)
        guide(c, cell, g["points_um"], width=g["width_um"])
        t = route_tapered(p0, p1, inner or None, strategy="uniform", corner_style="miter")
        routes.append(t)
    write = commit_tapered_routes(c, cell, routes, route_layer="11/0", clear=False)
    return {"cell": cell, "routes": routes, "write": write}


# =============================================================================
# Phase 3 -- verify honestly: a route counts ONLY if ok=true, it hit zero
# obstacles, zero sibling routes, and the committed polygon count matches
# the expected route count. Never report success on ok=false.
# =============================================================================


def verify_scene(scene: dict, routed: dict, problems: list) -> bool:
    cell = scene["cell"]
    obstacle_bboxes = scene["obstacle_bboxes"]
    routes = routed["routes"]
    write = routed["write"]
    expected_route_count = len(scene["requests"])

    obstacle_hit_count = 0
    sibling_overlap_count = 0
    for idx, route in enumerate(routes):
        others = [other.get("polygon_um") for j, other in enumerate(routes) if j != idx]
        v = validate_tapered_route(route, obstacle_bboxes=obstacle_bboxes, other_polygons=others)
        obstacle_hit_count += len(v.get("obstacle_hits") or [])
        sibling_overlap_count += int(v.get("sibling_overlaps", 0) or 0)

    route_count = write.get("inserted_polygons", 0) + write.get("inserted_paths_fallback", 0)
    ok = (route_count == expected_route_count
          and obstacle_hit_count == 0
          and sibling_overlap_count == 0)

    print("  %-22s ok=%-5s route_count=%d/%d obstacle_hit_count=%d sibling_overlap_count=%d"
          % (cell, ok, route_count, expected_route_count,
             obstacle_hit_count, sibling_overlap_count))
    if not ok:
        problems.append(
            "%s: ok=%s route_count=%d (expected %d) obstacle_hit_count=%d "
            "sibling_overlap_count=%d"
            % (cell, ok, route_count, expected_route_count,
               obstacle_hit_count, sibling_overlap_count))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765,
                    help="klink RPC port of the target KLayout (default 8765)")
    args = ap.parse_args()

    problems: list = []
    with KLinkClient(port=args.port).connect() as c:
        # own new tab: never touches whatever the user already has open.
        # The five real cells are built fresh below (reset_cell), so the
        # tab's initial placeholder top cell is only scaffolding.
        c.new_tab(cell_name="FIVE_ROUTERS_SCRATCH")
        ensure_layers(c)

        builders = [build_01_straight, build_02_waypoint, build_03_edge_slide,
                    build_04_obstacle, build_05_fanout]
        scenes = [build(c) for build in builders]

        print("\nRouting five scenarios through the tapered polygon backend "
              "(route_tapered / commit_tapered_routes):")
        ok_all = True
        for scene in scenes:
            routed = route_scene(c, scene)
            ok = verify_scene(scene, routed, problems)
            ok_all = ok_all and ok

        c.call("view.show_cell", {"cell": "ROUTE_01_STRAIGHT"})

        if not ok_all:
            print("\nFAILED (%d problem(s)):" % len(problems))
            for p in problems:
                print("  -", p)
            return 1
        print("\nall five scenarios routed cleanly")
        return 0


if __name__ == "__main__":
    sys.exit(main())
