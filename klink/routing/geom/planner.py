"""MVP routing planner for Port/Anchor examples.

This module intentionally implements conservative route skeletons.  It is not
the final cost-grid autorouter; it establishes the semantic contract that every
backend must preserve.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from klink.routing.geom.constraints import route_with_port_launch_stubs
from klink.routing.geom.geometric import route_two_port_geometric
from klink.routing.geom.geometry import crossing_pairs, parse_relative_path, route_hits_bboxes, self_crossings
from klink.routing.core.intent import collect_route_intent
from klink.routing.core.validation import validate_route_intent


def _name_key(item: dict) -> str:
    return str(item.get("name") or item.get("id") or "")


def _center_y(item: dict) -> float:
    return float((item.get("center_um") or [0.0, 0.0])[1])


def _route_id_for_ports(source: dict, target: dict) -> str:
    net = str(source.get("net") or target.get("net") or "")
    if net:
        return "route_%s" % net
    return "route_%s_%s" % (_name_key(source), _name_key(target))


def _anchor_center(anchor: dict) -> list[float]:
    center = anchor.get("center_um") or [0.0, 0.0]
    return [float(center[0]), float(center[1])]


def _bend_region_points(anchor: dict, obstacle_bboxes: list[list[float]]) -> list[list[float]]:
    center = _anchor_center(anchor)
    radius = float(anchor.get("radius_um", 0.0) or 0.0)
    if radius <= 0.0:
        radius = max(float(anchor.get("width_um", 0.0) or 0.0), float(anchor.get("height_um", 0.0) or 0.0), 2.0) / 2.0
    if radius <= 0.0:
        radius = 2.0

    if not obstacle_bboxes:
        return [center]

    obs_cy = sum((bbox[1] + bbox[3]) / 2.0 for bbox in obstacle_bboxes) / len(obstacle_bboxes)
    # Put the second bend point inside the bend region, toward the obstacle
    # side.  This makes the anchor mean "turn here", not merely "use this y".
    sign = -1.0 if center[1] >= obs_cy else 1.0
    return [center, [center[0], center[1] + sign * radius]]


def _obstacle_avoid_points(source: dict, target: dict, anchors: list[dict], obstacle_bboxes: list[list[float]]) -> list[list[float]]:
    bend_anchors = [a for a in anchors if a.get("kind") == "bend_region"]
    if not obstacle_bboxes or not bend_anchors:
        points = []
        for anchor in anchors:
            if anchor.get("kind") == "waypoint_region":
                points.append(_anchor_center(anchor))
            elif anchor.get("kind") == "bend_region":
                points.extend(_bend_region_points(anchor, obstacle_bboxes))
        return points

    bend = bend_anchors[0]
    center = _anchor_center(bend)
    y = center[1]
    width = min(float(source.get("width_um", 1.0)), float(target.get("width_um", 1.0)))
    margin = max(width / 2.0, 1.0)
    left = min(b[0] for b in obstacle_bboxes) - margin * 2.0
    right = max(b[2] for b in obstacle_bboxes) + margin * 2.0
    bend_points = _bend_region_points(bend, obstacle_bboxes)
    exit_y = bend_points[-1][1]
    source_y = float((source.get("center_um") or [0.0, 0.0])[1])
    target_y = float((target.get("center_um") or [0.0, 0.0])[1])
    return [
        [left, source_y],
        [left, y],
        *bend_points,
        [right, exit_y],
        [right, target_y],
    ]


def _plan_two_port_request(request: dict, obstacle_bboxes: list[list[float]]) -> dict:
    ports = list(request.get("ports", []))
    source = request.get("source") or ports[0]
    target = request.get("target") or ports[1]
    anchors = list(request.get("anchors", []))
    waypoints = [_anchor_center(a) for a in anchors if a.get("kind") == "waypoint_region"]
    if obstacle_bboxes:
        waypoints.extend(_obstacle_avoid_points(source, target, anchors, obstacle_bboxes))
    else:
        waypoints.extend(_anchor_center(a) for a in anchors if a.get("kind") == "bend_region")
    route = route_with_port_launch_stubs(source, target, waypoints)
    route.update(
        {
            "route_id": request.get("route_id") or _route_id_for_ports(source, target),
            "net": request.get("net", ""),
            "source": source.get("name", ""),
            "target": target.get("name", ""),
            "backend": "obstacle_aware_router" if obstacle_bboxes else "simple_route_router",
            "anchors": [a.get("id") for a in anchors],
        }
    )
    return route


def _bend_region_geometric_points(source: dict, target: dict, anchor: dict) -> list[list[float]]:
    center = _anchor_center(anchor)
    radius = float(anchor.get("radius_um", 0.0) or 0.0)
    if radius <= 0.0:
        radius = max(float(anchor.get("width_um", 0.0) or 0.0), float(anchor.get("height_um", 0.0) or 0.0), 2.0) / 2.0
    if radius <= 0.0:
        radius = 2.0
    target_center = target.get("center_um") or center
    source_center = source.get("center_um") or center
    if abs(float(target_center[0]) - center[0]) >= abs(float(target_center[1]) - center[1]):
        sign = 1.0 if float(target_center[0]) >= center[0] else -1.0
        if abs(float(target_center[0]) - center[0]) < 1e-9:
            sign = -1.0 if float(source_center[0]) >= center[0] else 1.0
        approach_sign = -1.0 if float(source_center[1]) <= center[1] else 1.0
        if abs(float(source_center[1]) - center[1]) < 1e-9:
            approach_sign = -1.0
        approach_point = [center[0], center[1] + approach_sign * radius]
        exit_point = [center[0] + sign * radius, center[1]]
    else:
        sign = 1.0 if float(target_center[1]) >= center[1] else -1.0
        if abs(float(target_center[1]) - center[1]) < 1e-9:
            sign = -1.0 if float(source_center[1]) >= center[1] else 1.0
        approach_sign = -1.0 if float(source_center[0]) <= center[0] else 1.0
        if abs(float(source_center[0]) - center[0]) < 1e-9:
            approach_sign = -1.0
        approach_point = [center[0] + approach_sign * radius, center[1]]
        exit_point = [center[0], center[1] + sign * radius]
    return [approach_point, center, exit_point]


def _required_points_for_geometric_router(
    source: dict,
    target: dict,
    anchors: list[dict],
) -> list[list[float]]:
    points: list[list[float]] = []
    for anchor in anchors:
        kind = anchor.get("kind")
        if kind == "waypoint_region":
            points.append(_anchor_center(anchor))
        elif kind == "bend_region":
            points.extend(_bend_region_geometric_points(source, target, anchor))
        elif kind == "corridor":
            points.extend(parse_relative_path(anchor.get("center_um", [0.0, 0.0]), anchor.get("path_points", "")))
    return points


def _project_point(point: list[float], start: list[float], end: list[float]) -> float:
    vx = end[0] - start[0]
    vy = end[1] - start[1]
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return 0.0
    return ((point[0] - start[0]) * vx + (point[1] - start[1]) * vy) / denom


def _required_point_groups_for_geometric_router(
    source: dict,
    target: dict,
    anchors: list[dict],
) -> list[list[list[float]]]:
    source_center = [float(v) for v in source.get("center_um", [0.0, 0.0])]
    target_center = [float(v) for v in target.get("center_um", [0.0, 0.0])]
    groups: list[tuple[float, str, list[list[float]]]] = []
    for anchor in anchors:
        kind = anchor.get("kind")
        if kind == "waypoint_region":
            group = [_anchor_center(anchor)]
        elif kind == "bend_region":
            group = _bend_region_geometric_points(source, target, anchor)
        elif kind == "corridor":
            path = parse_relative_path(anchor.get("center_um", [0.0, 0.0]), anchor.get("path_points", ""))
            # CorridorAnchor is a band/direction constraint, not a demand to
            # trace the exact centerline.  Use entrance/exit gates as hard
            # ordering constraints; later cost/damping can prefer staying near
            # the centerline without forcing it.
            group = [path[0], path[-1]] if len(path) >= 2 else path
        else:
            continue
        if not group:
            continue
        progress = min(_project_point(point, source_center, target_center) for point in group)
        groups.append((progress, str(anchor.get("id") or ""), group))
    return [group for _progress, _anchor_id, group in sorted(groups, key=lambda item: (item[0], item[1]))]


def _plan_two_port_geometric_request(
    request: dict,
    obstacle_bboxes: list[list[float]],
    *,
    safe_distance_um: float,
    angle_mode: str,
) -> dict:
    ports = list(request.get("ports", []))
    source = request.get("source") or ports[0]
    target = request.get("target") or ports[1]
    anchors = list(request.get("anchors", []))
    route = route_two_port_geometric(
        source,
        target,
        obstacle_bboxes=obstacle_bboxes,
        required_point_groups=_required_point_groups_for_geometric_router(source, target, anchors),
        safe_distance_um=safe_distance_um,
        angle_mode=angle_mode,  # type: ignore[arg-type]
    )
    route.update(
        {
            "route_id": request.get("route_id") or _route_id_for_ports(source, target),
            "net": request.get("net", ""),
            "source": source.get("name", ""),
            "target": target.get("name", ""),
            "anchors": [a.get("id") for a in anchors],
        }
    )
    return route


def _choose_candidate_sinks(demands: list[dict], candidates: list[dict]) -> list[tuple[dict, dict]]:
    ordered_demands = sorted(demands, key=lambda p: (_center_y(p), _name_key(p)))
    ordered_candidates = sorted(candidates, key=lambda p: (_center_y(p), _name_key(p)))
    return list(zip(ordered_demands, ordered_candidates[: len(ordered_demands)]))


def _lane_offsets(count: int, spacing: float) -> list[float]:
    if count <= 1:
        return [0.0]
    center = (count - 1) / 2.0
    return [(i - center) * spacing for i in range(count)]


def _plan_assignment_request(request: dict) -> list[dict]:
    pairs = _choose_candidate_sinks(
        list(request.get("demands", [])),
        list(request.get("candidate_sinks", [])),
    )
    anchors_by_demand = request.get("anchors_by_demand", {})
    by_corridor: dict[str, list[tuple[dict, dict, dict]]] = defaultdict(list)
    direct_pairs: list[tuple[dict, dict]] = []
    for demand, candidate in pairs:
        anchors = [a for a in anchors_by_demand.get(_name_key(demand), []) if a.get("kind") == "corridor"]
        if anchors:
            by_corridor[str(anchors[0].get("id", ""))].append((demand, candidate, anchors[0]))
        else:
            direct_pairs.append((demand, candidate))

    routes: list[dict] = []
    for demand, candidate in direct_pairs:
        route = route_with_port_launch_stubs(demand, candidate)
        route.update(
            {
                "route_id": _route_id_for_ports(demand, candidate),
                "net": demand.get("net", ""),
                "source": demand.get("name", ""),
                "target": candidate.get("name", ""),
                "backend": "assignment_router",
                "anchors": [],
            }
        )
        routes.append(route)

    for items in by_corridor.values():
        items = sorted(items, key=lambda item: (_center_y(item[0]), _center_y(item[1])))
        corridor = items[0][2]
        base = parse_relative_path(corridor.get("center_um", [0.0, 0.0]), corridor.get("path_points", ""))
        width = float(corridor.get("width_um", 0.0) or 0.0)
        spacing = max(1.0, width / max(len(items), 1) / 2.0)
        for (demand, candidate, _corridor), offset in zip(items, _lane_offsets(len(items), spacing)):
            lane = [[p[0], p[1] + offset] for p in base]
            route = route_with_port_launch_stubs(demand, candidate, lane)
            route.update(
                {
                    "route_id": _route_id_for_ports(demand, candidate),
                    "net": demand.get("net", ""),
                    "source": demand.get("name", ""),
                    "target": candidate.get("name", ""),
                    "backend": "corridor_lane_router",
                    "anchors": [corridor.get("id")],
                    "lane_offset_um": offset,
                }
            )
            routes.append(route)
    return sorted(routes, key=lambda r: str(r.get("source", "")))


def plan_routes_from_intent(
    intent: dict,
    *,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
    obstacle_layers: list[str] | None = None,
    router_backend: str = "semantic",
    safe_distance_um: float = 0.0,
    angle_mode: str = "manhattan",
) -> dict:
    obstacle_bboxes = [list(map(float, bbox)) for bbox in (obstacle_bboxes or [])]
    validation = validate_route_intent(intent, obstacle_layers=obstacle_layers or [])
    if not validation["routable"]:
        return {"ok": False, "validation": validation, "routes": [], "errors": validation["errors"]}

    routes: list[dict] = []
    for request in intent.get("route_requests", []):
        if router_backend == "geometric":
            routes.append(
                _plan_two_port_geometric_request(
                    request,
                    obstacle_bboxes,
                    safe_distance_um=safe_distance_um,
                    angle_mode=angle_mode,
                )
            )
        else:
            routes.append(_plan_two_port_request(request, obstacle_bboxes))
    for request in intent.get("assignment_requests", []):
        routes.extend(_plan_assignment_request(request))

    crossings = crossing_pairs(routes)
    obstacle_hits = []
    route_self_crossings = []
    for route in routes:
        hits = route_hits_bboxes(route.get("points_um", []), obstacle_bboxes, float(route.get("width_um", 0.0)))
        for hit in hits:
            hit["route_id"] = route.get("route_id", "")
        obstacle_hits.extend(hits)
        for crossing in route.get("self_crossings") or self_crossings(route.get("points_um", [])):
            crossing["route_id"] = route.get("route_id", "")
            route_self_crossings.append(crossing)

    ok = not crossings and not obstacle_hits and not route_self_crossings
    return {
        "ok": ok,
        "validation": validation,
        "routes": routes,
        "crossings": crossings,
        "self_crossings": route_self_crossings,
        "obstacle_hits": obstacle_hits,
        "errors": [],
    }


def _shape_bbox_um(shape: dict, dbu: float) -> list[float] | None:
    bbox = shape.get("bbox_dbu")
    if not bbox:
        return None
    return [float(v) * dbu for v in bbox]


def collect_obstacle_bboxes(client, cell: str, obstacle_layers: list[str]) -> list[list[float]]:
    if not obstacle_layers:
        return []
    dbu = float(client.layout_info().get("dbu", 0.001))
    query = client.shape_query(cell, layers=obstacle_layers, kinds=["boxes", "polygons", "paths"], limit=5000)
    bboxes: list[list[float]] = []
    for shape in query.get("shapes", []):
        bbox = _shape_bbox_um(shape, dbu)
        if bbox:
            bboxes.append(bbox)
    return bboxes


def plan_routes_for_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    obstacle_layers: list[str] | None = None,
) -> dict:
    obstacle_layers = list(obstacle_layers or [])
    intent = collect_route_intent(client, cell, port_layer=port_layer, anchor_layer=anchor_layer)
    obstacles = collect_obstacle_bboxes(client, cell, obstacle_layers)
    return plan_routes_from_intent(intent, obstacle_bboxes=obstacles, obstacle_layers=obstacle_layers)
