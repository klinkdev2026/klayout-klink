"""Optional damping/soft-clearance routing backends.

These are explicit quality backends. They do not change the default pairwise,
polygon, or Steiner routers.
"""

from __future__ import annotations

from typing import Sequence

from klink.routing.geom.geometric import route_points_geometric
from klink.routing.geom.path_quality import simplify_route_points
from klink.routing.geom.planner import collect_obstacle_bboxes
from klink.routing.backends.geometric.steiner import _net_sort_key, _ports_by_net, plan_rectilinear_steiner_tree, route_steiner_cell
from klink.routing.backends.geometric.tapered import route_tapered_polygon_cell
from klink.routing.backends.geometric.tapered_segments import (
    _pair_ports_by_net_tokens,
    _unsupported_multi_port_net_errors,
    commit_tapered_hybrid_many,
    route_tapered_hybrid_many,
)
from klink.routing.geom.writeback import commit_routes


def _zero_ports_warning(port_layer: str, port_count: int) -> list[dict]:
    if port_count:
        return []
    return [{
        "type": "zero_ports",
        "port_layer": port_layer,
        "port_count": 0,
        "message": f"zero ports found on layer {port_layer}",
    }]


def route_damped_segment_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    spacing_um: float = 20.0,
    angle_mode: str = "manhattan",
    damping_distance_um: float = 10.0,
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route pairwise nets with tapered hybrid output and soft clearance."""

    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": anchor_layer, "sort": "id"}).get("anchors", [])
    obstacle_layers = list(obstacle_layers or [])
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)
    unsupported_net_errors = _unsupported_multi_port_net_errors(ports)
    pairs = _pair_ports_by_net_tokens(ports)
    warnings = _zero_ports_warning(port_layer, len(ports))

    by_layer: dict[str, list[dict]] = {}
    for pair in pairs:
        by_layer.setdefault(str(pair.get("route_layer") or "10/0"), []).append(pair)

    groups = []
    ok = not unsupported_net_errors
    for route_layer in sorted(by_layer):
        planned = route_tapered_hybrid_many(
            by_layer[route_layer],
            anchors=anchors,
            spacing_um=spacing_um,
            angle_mode=angle_mode,
            safe_distance_um=float(damping_distance_um),
            obstacle_bboxes=obstacle_bboxes,
        )
        write = None
        if planned["ok"]:
            write = commit_tapered_hybrid_many(client, cell, planned, route_layer=route_layer, clear=clear)
        else:
            ok = False
        groups.append({
            "route_layer": route_layer,
            "ok": planned["ok"],
            "route_count": planned["route_count"],
            "lane_reports": planned["lane_reports"],
            "sibling_overlaps": planned["sibling_overlaps"],
            "obstacle_hits": planned.get("obstacle_hits", []),
            "errors": planned["errors"],
            "write": write,
        })

    return {
        "ok": ok,
        "backend": "damped_segment_cell",
        "cell": cell,
        "port_count": len(ports),
        "anchor_count": len(anchors),
        "pair_count": len(pairs),
        "angle_mode": angle_mode,
        "damping_distance_um": float(damping_distance_um),
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "planning_errors": unsupported_net_errors,
        "warnings": warnings,
        "errors": [e["message"] for e in unsupported_net_errors],
        "groups": groups,
    }


def route_damped_polygon_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    route_layer: str | None = None,
    spacing_um: float = 20.0,
    angle_mode: str = "manhattan",
    damping_distance_um: float = 10.0,
    corner_style: str = "miter",
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route pairwise nets with continuous polygon output and soft clearance."""

    result = route_tapered_polygon_cell(
        client,
        cell,
        port_layer=port_layer,
        anchor_layer=anchor_layer,
        route_layer=route_layer,
        spacing_um=spacing_um,
        angle_mode=angle_mode,
        safe_distance_um=float(damping_distance_um),
        corner_style=corner_style,  # type: ignore[arg-type]
        clear=clear,
        obstacle_layers=obstacle_layers,
    )
    return {
        **result,
        "backend": "damped_polygon_cell",
        "damping_distance_um": float(damping_distance_um),
    }


def _damp_route_points(
    route: dict,
    obstacle_bboxes: Sequence[Sequence[float]],
    *,
    damping_distance_um: float,
    angle_mode: str,
) -> dict:
    points = route.get("points_um") or []
    if len(points) < 2 or not obstacle_bboxes:
        return dict(route)
    width = float(route.get("width_um", 1.0) or 1.0)
    routed = []
    for start, end in zip(points, points[1:]):
        try:
            leg = route_points_geometric(
                start,
                end,
                obstacle_bboxes=obstacle_bboxes,
                route_width_um=width,
                safe_distance_um=float(damping_distance_um),
                angle_mode="manhattan" if angle_mode == "any" else angle_mode,  # type: ignore[arg-type]
            )
        except ValueError as exc:
            if "endpoint is inside an expanded obstacle" not in str(exc):
                raise
            leg = route_points_geometric(
                start,
                end,
                obstacle_bboxes=obstacle_bboxes,
                route_width_um=width,
                safe_distance_um=0.0,
                angle_mode="manhattan" if angle_mode == "any" else angle_mode,  # type: ignore[arg-type]
            )
        if routed:
            routed.extend(leg[1:])
        else:
            routed.extend(leg)
    routed = simplify_route_points(routed, width_um=width)
    return {**route, "points_um": routed, "backend": "damped_steiner_rectilinear"}


def route_damped_steiner_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    route_layer: str | None = None,
    root_ports: dict[str, str] | None = None,
    angle_mode: str = "manhattan",
    damping_distance_um: float = 10.0,
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route multi-terminal nets with Steiner topology and damped legs."""

    obstacle_layers = list(obstacle_layers or [])
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)
    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": anchor_layer, "sort": "id"}).get("anchors", [])
    root_ports = dict(root_ports or {})
    by_net = _ports_by_net(ports)
    warnings = _zero_ports_warning(port_layer, len(ports))

    planned_groups = []
    for net in sorted(by_net, key=_net_sort_key):
        members = by_net[net]
        if len(members) <= 2:
            continue
        planned_groups.append(plan_rectilinear_steiner_tree(
            members,
            net=net,
            anchors=anchors,
            root_name=root_ports.get(net),
            route_layer=route_layer,
            obstacle_bboxes=obstacle_bboxes,
            safe_distance_um=float(damping_distance_um),
            angle_mode=angle_mode,
        ))
    planned = {
        "ok": bool(planned_groups),
        "backend": "steiner_cell",
        "cell": cell,
        "port_count": len(ports),
        "anchor_count": len(anchors),
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "groups": planned_groups,
        "warnings": warnings,
        "errors": [] if planned_groups else ["no multi-terminal nets found"],
    }

    ok = True
    groups = []
    for group in planned.get("groups", []):
        damped_routes = [
            _damp_route_points(
                route,
                obstacle_bboxes,
                damping_distance_um=float(damping_distance_um),
                angle_mode=angle_mode,
            )
            for route in group.get("routes", [])
        ]
        obstacle_hits = []
        from klink.routing.geom.geometry import route_hits_bboxes

        for route in damped_routes:
            for hit in route_hits_bboxes(route.get("points_um", []), obstacle_bboxes, float(route.get("width_um", 1.0))):
                obstacle_hits.append({**hit, "route_id": route.get("route_id"), "net": group.get("net")})
        errors = []
        if obstacle_hits:
            errors.append("damped steiner route hits obstacle")
        group_ok = not errors
        if not group_ok:
            ok = False
        write = None
        if group_ok:
            write = commit_routes(
                client,
                cell,
                damped_routes,
                route_layer=group.get("route_layer") or route_layer or "10/0",
                clear=clear,
            )
        groups.append({
            **group,
            "backend": "damped_steiner",
            "ok": group_ok,
            "routes": damped_routes,
            "route_count": len(damped_routes),
            "obstacle_hits": obstacle_hits,
            "errors": errors,
            "write": write,
        })

    return {
        **planned,
        "ok": ok,
        "backend": "damped_steiner_cell",
        "angle_mode": angle_mode,
        "damping_distance_um": float(damping_distance_um),
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "groups": groups,
        "warnings": warnings,
        "errors": [],
    }
