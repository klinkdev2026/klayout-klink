"""Showcase fixtures for router capabilities beyond the current backends.

This script is intentionally a negative/aspirational example:

- it creates generic Port/Anchor/Obstacle fixtures
- it calls existing klink MCP routing tools mechanically
- it prints why the current backend is insufficient

No fixture-specific routing coordinates are hidden in this file.  The point is
to make "stronger router" concrete: each cell is a layout that needs a global
decision the current local/corridor/Steiner backends do not make yet.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.mcp.bridge import KLinkMCPBridge


PORT_LAYER = "999/99"
ANCHOR_LAYER = "999/1"
ROUTE_LAYER = "12/0"
ALT_ROUTE_LAYER = "13/0"
VIA_LAYER = "14/0"
PAD = (1, 0)
KEEP = (900, 0)
LABEL = (997, 99)

CELLS = [
    "ROUTER_FUTURE_01_CORRIDOR_CAPACITY_SPLIT",
    "ROUTER_FUTURE_02_OBSTACLE_AWARE_ASSIGNMENT",
    "ROUTER_FUTURE_03_OBSTACLE_AWARE_STEINER",
    "ROUTER_FUTURE_04_MULTILAYER_ESCAPE",
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
        (12, 0, "ROUTE_M1"),
        (13, 0, "ROUTE_M2"),
        (14, 0, "ROUTE_VIA"),
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


def port(client, cell, name, xy, ori, *, net, width=4.0, target_layer=ROUTE_LAYER, port_type="electrical"):
    client.call("port.mark", {
        "cell": cell,
        "layer": PORT_LAYER,
        "name": name,
        "center_um": xy,
        "orientation": ori,
        "width_um": width,
        "port_type": port_type,
        "net": net,
        "target_layer": target_layer,
        "show_label": True,
    })


def anchor(client, cell, anchor_id, xy, kind, *, net, path_points="", width=30.0, radius=10.0, priority=0, label=None):
    client.call("anchor.mark", {
        "cell": cell,
        "layer": ANCHOR_LAYER,
        "id": anchor_id,
        "center_um": xy,
        "kind": kind,
        "net": net,
        "label": label if label is not None else anchor_id,
        "radius_um": radius,
        "width_um": width,
        "height_um": width,
        "path_points": path_points,
        "priority": priority,
    })


def build_corridor_capacity_split(client):
    """Needs global load balancing across explicitly alternative corridors.

    Plain corridors remain required paths.  These two anchors are explicitly
    marked as alternatives using label token choice_group=BUS.
    """
    cell = "ROUTER_FUTURE_01_CORRIDOR_CAPACITY_SPLIT"
    reset_cell(client, cell)
    text(client, cell, "01: six nets, two optional channels marked choice_group=BUS", [-40, 105])
    nets = [f"n{i}" for i in range(6)]
    for idx, net in enumerate(nets):
        y = -45 + idx * 18
        port(client, cell, f"L{idx}", [0, y], 0, net=net, width=5.0)
        port(client, cell, f"R{idx}", [180, y], 180, net=net, width=5.0)
    allow = ",".join(nets)
    anchor(client, cell, "UPPER", [90, 36], "corridor", net=allow, path_points="-55,0;55,0", width=44.0, priority=0, label="UPPER choice_group=BUS")
    anchor(client, cell, "LOWER", [90, -36], "corridor", net=allow, path_points="-55,0;55,0", width=44.0, priority=1, label="LOWER choice_group=BUS")


def build_obstacle_aware_assignment(client):
    """Needs candidate assignment based on routed cost, not geometric proximity.

    The near candidates sit behind keepout slots.  A stronger router should run
    matching on estimated route cost with obstacles and then route the bundle.
    """
    cell = "ROUTER_FUTURE_02_OBSTACLE_AWARE_ASSIGNMENT"
    reset_cell(client, cell)
    text(client, cell, "02: candidate pads require obstacle-aware global assignment", [-55, 110])
    for idx, y in enumerate([-36, -12, 12, 36]):
        port(client, cell, f"S{idx}", [0, y], 0, net=f"n{idx}", width=4.0)
    for idx, y in enumerate([-42, -14, 14, 42]):
        port(client, cell, f"NEAR{idx}", [92, y], 180, net="", width=4.0, port_type="candidate_sink")
        port(client, cell, f"FAR{idx}", [155, -y], 180, net="", width=4.0, port_type="candidate_sink")
    box(client, cell, [45, -60, 82, -22], layer=KEEP)
    box(client, cell, [45, 22, 82, 60], layer=KEEP)
    box(client, cell, [95, -18, 125, 18], layer=KEEP)


def build_obstacle_aware_steiner(client):
    """Needs global obstacle-aware Steiner topology, not a fixed trunk axis."""
    cell = "ROUTER_FUTURE_03_OBSTACLE_AWARE_STEINER"
    reset_cell(client, cell)
    text(client, cell, "03: same-net tree with blocked trunk; needs obstacle-aware Steiner topology", [-50, 120])
    port(client, cell, "ROOT", [0, 0], 0, net="bus", width=8.0, port_type="root")
    for idx, (x, y, w) in enumerate([(160, -48, 4.0), (160, 0, 5.0), (160, 48, 6.0)]):
        port(client, cell, f"S{idx}", [x, y], 180, net="bus", width=w)
    anchor(client, cell, "TRUNK_HINT", [100, 0], "corridor", net="bus", path_points="0,-60;0,60", width=30.0)
    box(client, cell, [86, -20, 114, 20], layer=KEEP)
    box(client, cell, [54, -75, 76, -20], layer=KEEP)
    box(client, cell, [124, 20, 146, 75], layer=KEEP)


def build_multilayer_escape(client):
    """Needs layer assignment and vias; current self-owned routers are single-layer."""
    cell = "ROUTER_FUTURE_04_MULTILAYER_ESCAPE"
    reset_cell(client, cell)
    text(client, cell, "04: single-layer wall; stronger router needs via/layer switching", [-35, 100])
    for idx, y in enumerate([-24, 0, 24]):
        port(client, cell, f"L{idx}", [0, y], 0, net=f"m{idx}", width=4.0, target_layer=ROUTE_LAYER)
        port(client, cell, f"R{idx}", [160, y], 180, net=f"m{idx}", width=4.0, target_layer=ROUTE_LAYER)
    box(client, cell, [62, -70, 98, 70], layer=KEEP)
    anchor(client, cell, "ESCAPE_HINT", [80, 0], "waypoint_region", net="m0,m1,m2", width=30.0)
    text(client, cell, "Expected future behavior: use M2 bridge + vias over the wall", [20, -88], size=3.2)


def _call_tool(bridge, name, arguments):
    result = bridge.call_tool(name, arguments)
    if result.get("isError"):
        return {"ok": False, "errors": [result["content"][0]["text"]]}
    return json.loads(result["content"][0]["text"])


def summarize(result):
    groups = result.get("groups", [])
    return {
        "ok": result.get("ok"),
        "backend": result.get("backend"),
        "errors": result.get("errors"),
        "groups": [
            {
                "ok": group.get("ok"),
                "routes": group.get("route_count"),
                "errors": group.get("errors"),
                "planning_errors": group.get("planning_errors"),
                "obstacle_hits": len(group.get("obstacle_hits", []) or []),
                "lane_reports": group.get("lane_reports"),
            }
            for group in groups
        ],
    }


def quality_gate(diagnostics):
    checks = []
    expected_success = [
        "01_new_global_channel",
        "02_new_global_channel",
        "03_current_damped_steiner",
        "04_multilayer_escape",
    ]
    expected_failure = [
        "01_current_tapered_hybrid",
        "02_current_damped_segment",
        "04_current_tapered_hybrid",
    ]
    for key in expected_success:
        result = diagnostics.get(key) or {}
        group_errors = [
            err
            for group in result.get("groups", [])
            for err in (group.get("errors") or [])
        ]
        checks.append({
            "name": key,
            "expected": "success",
            "passed": bool(result.get("ok")) and not group_errors,
            "errors": [*(result.get("errors") or []), *group_errors],
        })
    for key in expected_failure:
        result = diagnostics.get(key) or {}
        checks.append({
            "name": key,
            "expected": "failure",
            "passed": not bool(result.get("ok")),
            "errors": result.get("errors") or [],
        })
    return {
        "ok": all(item["passed"] for item in checks),
        "checks": checks,
    }


def main():
    with KLinkClient().connect() as client:
        ensure_layers(client)
        build_corridor_capacity_split(client)
        build_obstacle_aware_assignment(client)
        build_obstacle_aware_steiner(client)
        build_multilayer_escape(client)

        bridge = KLinkMCPBridge(profiles=["basic"])
        diagnostics = {
            "01_current_tapered_hybrid": summarize(_call_tool(bridge, "routing.tapered_hybrid_cell", {
                "cell": CELLS[0],
                "route_layer": ROUTE_LAYER,
                "spacing_um": 8.0,
                "obstacle_layers": ["900/0"],
            })),
            "01_new_global_channel": summarize(_call_tool(bridge, "routing.global_channel_cell", {
                "cell": CELLS[0],
                "spacing_um": 8.0,
                "angle_mode": "manhattan",
                "obstacle_layers": ["900/0"],
            })),
            "02_current_damped_segment": summarize(_call_tool(bridge, "routing.damped_segment_cell", {
                "cell": CELLS[1],
                "route_layer": ROUTE_LAYER,
                "spacing_um": 8.0,
                "obstacle_layers": ["900/0"],
                "damping_distance_um": 8.0,
            })),
            "02_new_global_channel": summarize(_call_tool(bridge, "routing.global_channel_cell", {
                "cell": CELLS[1],
                "spacing_um": 8.0,
                "angle_mode": "manhattan",
                "safe_distance_um": 0.0,
                "obstacle_layers": ["900/0"],
            })),
            "03_current_damped_steiner": summarize(_call_tool(bridge, "routing.damped_steiner_cell", {
                "cell": CELLS[2],
                "route_layer": ROUTE_LAYER,
                "obstacle_layers": ["900/0"],
                "damping_distance_um": 8.0,
                "root_ports": {"bus": "ROOT"},
            })),
            "04_current_tapered_hybrid": summarize(_call_tool(bridge, "routing.tapered_hybrid_cell", {
                "cell": CELLS[3],
                "route_layer": ROUTE_LAYER,
                "spacing_um": 8.0,
                "obstacle_layers": ["900/0"],
            })),
            "04_multilayer_escape": _call_tool(bridge, "routing.multilayer_escape_cell", {
                "cell": CELLS[3],
                "route_layer": ROUTE_LAYER,
                "bridge_layer": ALT_ROUTE_LAYER,
                "via_layer": VIA_LAYER,
                "spacing_um": 8.0,
                "obstacle_layers": ["900/0"],
            }),
        }
        bridge.close()
        client.call("view.show_cell", {"cell": CELLS[0], "zoom_fit": True})

    gate = quality_gate(diagnostics)
    print(json.dumps({
        "cells": CELLS,
        "what_stronger_means": [
            "global channel/corridor capacity assignment, not first-matching-corridor greedy routing",
            "obstacle-aware candidate sink matching, not nearest/order-only assignment",
            "obstacle-aware Steiner topology with trunk placement decided by cost",
            "multi-layer escape with vias and bridge-layer wall crossing",
        ],
        "quality_gate": gate,
        "diagnostics": diagnostics,
    }, indent=2, ensure_ascii=False))
    if not gate["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
