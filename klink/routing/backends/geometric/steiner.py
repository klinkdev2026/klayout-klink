"""Multi-terminal rectilinear tree routing for klink Ports.

This module owns topology routing for nets with more than two ports.  It does
not know about examples, cell names, layer names, or fixture coordinates.
"""

from __future__ import annotations

from statistics import median
from typing import Sequence

from klink.routing.geom.constraints import port_launch_point, port_launch_width
from klink.routing.geom.geometry import parse_relative_path, route_hits_bboxes
from klink.routing.geom.path_quality import simplify_route_points
from klink.routing.geom.planner import collect_obstacle_bboxes
from klink.routing.geom.writeback import commit_routes


Point = list[float]


def _net_tokens(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _net_sort_key(net: str) -> tuple[str, int, str]:
    prefix = "".join(ch for ch in str(net) if not ch.isdigit())
    digits = "".join(ch for ch in str(net) if ch.isdigit())
    return (prefix, int(digits) if digits else -1, str(net))


def _port_name(port: dict) -> str:
    return str(port.get("name") or port.get("id") or "")


def _port_center(port: dict) -> Point:
    center = port.get("center_um") or [0.0, 0.0]
    return [float(center[0]), float(center[1])]


def _obstacle_safe_launch_point(port: dict, obstacle_bboxes: Sequence[Sequence[float]]) -> Point:
    center = _port_center(port)
    width = float(port_launch_width(port) or 1.0)
    if not obstacle_bboxes:
        return port_launch_point(port)
    for length in [max(width * 2.0, width, 1.0), width, width / 2.0, 1.0, 0.0]:
        launch = port_launch_point(port, length_um=length)
        if not route_hits_bboxes([center, launch], obstacle_bboxes, width):
            return launch
    return port_launch_point(port)


def _same_point(a: Sequence[float], b: Sequence[float], eps: float = 1e-9) -> bool:
    return abs(float(a[0]) - float(b[0])) <= eps and abs(float(a[1]) - float(b[1])) <= eps


def _dedupe_path(points: Sequence[Sequence[float]]) -> list[Point]:
    out: list[Point] = []
    for point in points:
        p = [float(point[0]), float(point[1])]
        if not out or not _same_point(out[-1], p):
            out.append(p)
    return out


def _simplify_planned_route(route: dict) -> dict:
    width = float(route.get("width_um", 1.0) or 1.0)
    return {**route, "points_um": simplify_route_points(route.get("points_um") or [], width_um=width)}


def _segments(points: Sequence[Sequence[float]]) -> list[tuple[Point, Point]]:
    return [([float(a[0]), float(a[1])], [float(b[0]), float(b[1])]) for a, b in zip(points, points[1:])]


def _axis_overlap_length(a0: float, a1: float, b0: float, b1: float) -> float:
    lo = max(min(a0, a1), min(b0, b1))
    hi = min(max(a0, a1), max(b0, b1))
    return max(0.0, hi - lo)


def _segment_covered_by(candidate: tuple[Point, Point], route: dict, width_um: float, eps: float = 1e-9) -> bool:
    a, b = candidate
    length = abs(b[0] - a[0]) + abs(b[1] - a[1])
    if length <= eps:
        return True
    covered = 0.0
    for c, d in _segments(route.get("points_um") or []):
        if abs(a[0] - b[0]) <= eps and abs(c[0] - d[0]) <= eps and abs(a[0] - c[0]) <= eps:
            covered += _axis_overlap_length(a[1], b[1], c[1], d[1])
        elif abs(a[1] - b[1]) <= eps and abs(c[1] - d[1]) <= eps and abs(a[1] - c[1]) <= eps:
            covered += _axis_overlap_length(a[0], b[0], c[0], d[0])
    return covered >= length - eps and covered > width_um + eps


def _dedupe_same_net_collinear_siblings(routes: Sequence[dict]) -> list[dict]:
    kept: list[dict] = []
    for index, route in enumerate(routes):
        if route.get("kind") != "branch":
            kept.append(route)
            continue
        candidate_segments = _segments(route.get("points_um") or [])
        if not candidate_segments:
            continue
        width = float(route.get("width_um", 1.0) or 1.0)
        covering_routes = [
            other for other_index, other in enumerate(routes)
            if other_index != index
            and other.get("kind") == "trunk"
            and other.get("net") == route.get("net")
            and other.get("route_layer") == route.get("route_layer")
        ]
        if covering_routes and all(
            any(_segment_covered_by(segment, other, width) for other in covering_routes)
            for segment in candidate_segments
        ):
            continue
        kept.append(route)
    return kept


def _choose_root(ports: Sequence[dict], root_name: str | None = None) -> dict:
    if root_name:
        for port in ports:
            if _port_name(port) == root_name:
                return port
        raise ValueError(f"root port {root_name!r} not found")
    typed = [
        port for port in ports
        if str(port.get("port_type") or "").lower() in {"root", "source"}
    ]
    if typed:
        return sorted(typed, key=_port_name)[0]
    return sorted(ports, key=_port_name)[0]


def _route_layer_for_net(net: str, ports: Sequence[dict], fallback: str = "10/0") -> str:
    for port in ports:
        layers = port.get("target_layers_by_net") or {}
        if isinstance(layers, dict) and layers.get(net):
            return str(layers[net])
    for port in ports:
        layer = str(port.get("target_layer") or "")
        if layer:
            return layer
    return fallback


def _ports_by_net(ports: Sequence[dict]) -> dict[str, list[dict]]:
    by_net: dict[str, list[dict]] = {}
    for port in ports:
        if str(port.get("port_type") or "").lower() == "candidate_sink":
            continue
        for net in _net_tokens(port.get("net")):
            by_net.setdefault(net, []).append(port)
    return by_net


def _anchor_nets(anchor: dict) -> set[str]:
    return {part.strip() for part in str(anchor.get("net") or "").split(",") if part.strip()}


def _anchor_applies(anchor: dict, net: str) -> bool:
    nets = _anchor_nets(anchor)
    return not nets or str(net) in nets


def _anchor_center(anchor: dict) -> Point:
    center = anchor.get("center_um") or [0.0, 0.0]
    return [float(center[0]), float(center[1])]


def _corridor_path(anchor: dict) -> list[Point]:
    path = parse_relative_path(anchor.get("center_um", [0.0, 0.0]), anchor.get("path_points", ""))
    return path or [_anchor_center(anchor)]


def _nearest_point_on_segment(point: Sequence[float], a: Sequence[float], b: Sequence[float]) -> Point:
    px, py = float(point[0]), float(point[1])
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx = bx - ax
    dy = by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return [ax, ay]
    t = ((px - ax) * dx + (py - ay) * dy) / length2
    t = max(0.0, min(1.0, t))
    return [ax + dx * t, ay + dy * t]


def _nearest_point_on_polyline(point: Sequence[float], path: Sequence[Sequence[float]]) -> Point:
    if not path:
        return [float(point[0]), float(point[1])]
    if len(path) == 1:
        return [float(path[0][0]), float(path[0][1])]
    best = _nearest_point_on_segment(point, path[0], path[1])
    best_dist2 = (best[0] - float(point[0])) ** 2 + (best[1] - float(point[1])) ** 2
    for a, b in zip(path, path[1:]):
        candidate = _nearest_point_on_segment(point, a, b)
        dist2 = (candidate[0] - float(point[0])) ** 2 + (candidate[1] - float(point[1])) ** 2
        if dist2 < best_dist2:
            best = candidate
            best_dist2 = dist2
    return best


def _sorted_trunk_points(points: Sequence[Sequence[float]], *, vertical: bool) -> list[Point]:
    if vertical:
        ordered = sorted(points, key=lambda p: (float(p[1]), float(p[0])))
    else:
        ordered = sorted(points, key=lambda p: (float(p[0]), float(p[1])))
    return _dedupe_path(ordered)


def plan_rectilinear_steiner_tree(
    ports: Sequence[dict],
    *,
    net: str,
    anchors: Sequence[dict] | None = None,
    root_name: str | None = None,
    route_layer: str | None = None,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
    safe_distance_um: float = 0.0,
    angle_mode: str = "manhattan",
) -> dict:
    """Plan one multi-terminal net as a generic rectilinear bus tree.

    The trunk axis is selected from the spread of Port launch points.  Branches
    connect each Port center to its launch point, then to the shared trunk.
    """

    terminals = [dict(port) for port in ports]
    if len(terminals) < 3:
        raise ValueError("steiner routing requires at least three ports")

    root = _choose_root(terminals, root_name)
    matching_anchors = [dict(anchor) for anchor in (anchors or []) if _anchor_applies(anchor, net)]
    corridor_anchors = [anchor for anchor in matching_anchors if str(anchor.get("kind") or "") == "corridor"]
    non_corridor_points = [
        _anchor_center(anchor)
        for anchor in matching_anchors
        if str(anchor.get("kind") or "") in {"waypoint_region", "bend_region"}
    ]
    anchor_ids = [anchor.get("id") for anchor in matching_anchors if anchor.get("id")]
    launch_by_name = {
        _port_name(port): _obstacle_safe_launch_point(port, obstacle_bboxes or [])
        for port in terminals
    }
    launches = list(launch_by_name.values())
    xs = [float(p[0]) for p in launches]
    ys = [float(p[1]) for p in launches]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    vertical_trunk = x_span >= y_span
    layer = str(route_layer or _route_layer_for_net(net, terminals))
    trunk_width = max(float(port_launch_width(port) or 1.0) for port in terminals)
    branch_width = min(float(port_launch_width(port) or 1.0) for port in terminals)

    routes: list[dict] = []
    projections: list[Point] = []
    obstacle_bboxes = list(obstacle_bboxes or [])
    if corridor_anchors:
        corridor_anchors = sorted(corridor_anchors, key=lambda a: (int(a.get("priority") or 0), str(a.get("id") or "")))
        trunk_points = []
        for anchor in corridor_anchors:
            trunk_points.extend(_corridor_path(anchor))
        trunk_points.extend(non_corridor_points)
        trunk_points = _dedupe_path(trunk_points)
        if len(trunk_points) >= 2 and obstacle_bboxes:
            from klink.routing.geom.geometric import route_points_geometric

            routed_trunk: list[Point] = []
            for start, end in zip(trunk_points, trunk_points[1:]):
                leg = route_points_geometric(
                    start,
                    end,
                    obstacle_bboxes=obstacle_bboxes,
                    route_width_um=trunk_width,
                    safe_distance_um=safe_distance_um,
                    angle_mode="manhattan" if angle_mode == "any" else angle_mode,  # type: ignore[arg-type]
                )
                if routed_trunk:
                    routed_trunk.extend(leg[1:])
                else:
                    routed_trunk.extend(leg)
            trunk_points = _dedupe_path(routed_trunk)
        for port in terminals:
            launch = launch_by_name[_port_name(port)]
            projections.append(_nearest_point_on_polyline(launch, trunk_points))
        if len(trunk_points) >= 2:
            first = trunk_points[0]
            last = trunk_points[-1]
            vertical_trunk = abs(float(last[1]) - float(first[1])) >= abs(float(last[0]) - float(first[0]))
    elif vertical_trunk:
        trunk_x = float(median(xs))
        for port in terminals:
            launch = launch_by_name[_port_name(port)]
            projections.append([trunk_x, float(launch[1])])
        trunk_points = _sorted_trunk_points([*projections, *non_corridor_points], vertical=True)
    else:
        trunk_y = float(median(ys))
        for port in terminals:
            launch = launch_by_name[_port_name(port)]
            projections.append([float(launch[0]), trunk_y])
        trunk_points = _sorted_trunk_points([*projections, *non_corridor_points], vertical=False)

    if len(trunk_points) >= 2:
        routes.append(_simplify_planned_route({
            "route_id": f"steiner_{net}_trunk",
            "backend": "steiner_rectilinear",
            "kind": "trunk",
            "net": net,
            "source": _port_name(root),
            "target": "TRUNK",
            "route_layer": layer,
            "width_um": trunk_width,
            "points_um": trunk_points,
            "anchors": anchor_ids,
        }))

    for port, projection in zip(terminals, projections):
        center = _port_center(port)
        launch = launch_by_name[_port_name(port)]
        points = _dedupe_path([center, launch, projection])
        if len(points) < 2:
            continue
        routes.append(_simplify_planned_route({
            "route_id": f"steiner_{net}_{_port_name(port)}",
            "backend": "steiner_rectilinear",
            "kind": "branch",
            "net": net,
            "source": _port_name(port),
            "target": "TRUNK",
            "route_layer": layer,
            "width_um": float(port_launch_width(port) or branch_width),
            "points_um": points,
            "anchors": anchor_ids,
        }))

    routes = _dedupe_same_net_collinear_siblings(routes)
    return {
        "ok": True,
        "backend": "steiner_rectilinear",
        "net": net,
        "root": _port_name(root),
        "port_count": len(terminals),
        "route_layer": layer,
        "trunk_axis": "vertical" if vertical_trunk else "horizontal",
        "anchors": anchor_ids,
        "routes": routes,
        "route_count": len(routes),
    }


def route_steiner_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    route_layer: str | None = None,
    root_ports: dict[str, str] | None = None,
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route all multi-terminal nets in a cell as rectilinear Steiner trees."""

    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": anchor_layer, "sort": "id"}).get("anchors", [])
    obstacle_layers = list(obstacle_layers or [])
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)
    root_ports = dict(root_ports or {})
    by_net = _ports_by_net(ports)
    warnings = []
    if not ports:
        warnings.append({
            "type": "zero_ports",
            "port_layer": port_layer,
            "port_count": 0,
            "message": f"zero ports found on layer {port_layer}",
        })

    groups: list[dict] = []
    ok = True
    for net in sorted(by_net, key=_net_sort_key):
        members = by_net[net]
        if len(members) <= 2:
            continue
        planned = plan_rectilinear_steiner_tree(
            members,
            net=net,
            anchors=anchors,
            root_name=root_ports.get(net),
            route_layer=route_layer,
        )
        obstacle_hits = []
        for route in planned["routes"]:
            for hit in route_hits_bboxes(route["points_um"], obstacle_bboxes, float(route.get("width_um", 1.0))):
                obstacle_hits.append({**hit, "route_id": route.get("route_id"), "net": net})
        errors = []
        if obstacle_hits:
            errors.append("steiner route hits obstacle")
        group_ok = not errors
        if not group_ok:
            ok = False
        write = None
        if group_ok:
            write = commit_routes(
                client,
                cell,
                planned["routes"],
                route_layer=planned["route_layer"],
                clear=clear,
            )
        groups.append({
            **planned,
            "ok": group_ok,
            "obstacle_hits": obstacle_hits,
            "errors": errors,
            "write": write,
        })

    if not groups:
        if warnings:
            return {
                "ok": True,
                "backend": "steiner_cell",
                "cell": cell,
                "port_count": len(ports),
                "anchor_count": len(anchors),
                "obstacle_layers": obstacle_layers,
                "obstacle_bboxes": obstacle_bboxes,
                "groups": [],
                "warnings": warnings,
                "errors": [],
            }
        return {
            "ok": False,
            "backend": "steiner_cell",
            "cell": cell,
            "port_count": len(ports),
            "anchor_count": len(anchors),
            "obstacle_layers": obstacle_layers,
            "obstacle_bboxes": obstacle_bboxes,
            "groups": [],
            "warnings": warnings,
            "errors": ["no multi-terminal nets found"],
        }
    return {
        "ok": ok,
        "backend": "steiner_cell",
        "cell": cell,
        "port_count": len(ports),
        "anchor_count": len(anchors),
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "groups": groups,
        "warnings": warnings,
        "errors": [],
    }
