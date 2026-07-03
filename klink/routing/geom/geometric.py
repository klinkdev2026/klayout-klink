"""Geometry-first router core for klink Port routes.

This module owns path search logic.  Examples and CLI scripts should call this
through routing APIs instead of embedding routing decisions locally.
"""

from __future__ import annotations

import heapq
import math
from typing import Literal, Sequence

from klink.routing.geom.constraints import route_with_port_launch_stubs
from klink.routing.geom.geometry import expand_bbox, route_hits_bboxes, segment_intersects_rect, self_crossings


Point = list[float]
BBox = list[float]
AngleMode = Literal["manhattan", "fortyfive"]


def _point_key(point: Sequence[float], ndigits: int = 9) -> tuple[float, float]:
    return (round(float(point[0]), ndigits), round(float(point[1]), ndigits))


def _clean_float(value: float, eps: float = 1e-9) -> float:
    value = float(value)
    return 0.0 if abs(value) <= eps else value


def _clean_point(point: Sequence[float]) -> Point:
    return [_clean_float(float(point[0])), _clean_float(float(point[1]))]


def _same_point(a: Sequence[float], b: Sequence[float], eps: float = 1e-9) -> bool:
    return abs(float(a[0]) - float(b[0])) <= eps and abs(float(a[1]) - float(b[1])) <= eps


def _point_in_bbox(point: Sequence[float], bbox: Sequence[float], eps: float = 1e-9) -> bool:
    return (
        float(bbox[0]) - eps <= float(point[0]) <= float(bbox[2]) + eps
        and float(bbox[1]) - eps <= float(point[1]) <= float(bbox[3]) + eps
    )


def _segment_clear(a: Sequence[float], b: Sequence[float], bboxes: Sequence[Sequence[float]]) -> bool:
    if _same_point(a, b):
        return False
    segment = ([float(a[0]), float(a[1])], [float(b[0]), float(b[1])])
    return not any(segment_intersects_rect(segment, bbox) for bbox in bboxes)


def _angle_allowed(a: Sequence[float], b: Sequence[float], angle_mode: AngleMode) -> bool:
    dx = abs(float(b[0]) - float(a[0]))
    dy = abs(float(b[1]) - float(a[1]))
    eps = 1e-9
    if dx <= eps and dy <= eps:
        return False
    if dx <= eps or dy <= eps:
        return True
    if angle_mode == "fortyfive" and abs(dx - dy) <= eps:
        return True
    return False


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def expand_obstacles_for_route(
    obstacle_bboxes: Sequence[Sequence[float]],
    *,
    route_width_um: float,
    safe_distance_um: float = 0.0,
) -> list[BBox]:
    """Return hard-block rectangles expanded by route half-width and spacing."""
    margin = max(0.0, float(route_width_um) / 2.0 + float(safe_distance_um))
    return [expand_bbox(bbox, margin) for bbox in obstacle_bboxes]


def _candidate_nodes(
    start: Sequence[float],
    end: Sequence[float],
    blocked_bboxes: Sequence[Sequence[float]],
    *,
    jog_um: float,
) -> list[Point]:
    xs = {float(start[0]), float(end[0])}
    ys = {float(start[1]), float(end[1])}
    points = [[float(start[0]), float(start[1])], [float(end[0]), float(end[1])]]
    jog = max(float(jog_um), 1e-6)
    for bbox in blocked_bboxes:
        x1, y1, x2, y2 = [float(v) for v in bbox]
        left = x1 - jog
        right = x2 + jog
        bottom = y1 - jog
        top = y2 + jog
        xs.update([left, right])
        ys.update([bottom, top])
        points.extend([[left, bottom], [left, top], [right, bottom], [right, top]])

    for x in xs:
        for y in ys:
            points.append([x, y])

    unique: dict[tuple[float, float], Point] = {}
    for point in points:
        if any(_point_in_bbox(point, bbox) for bbox in blocked_bboxes):
            continue
        unique[_point_key(point)] = [float(point[0]), float(point[1])]
    return list(unique.values())


def _collinear_group_clear_pairs(
    indices: list[int],
    nodes: Sequence[Point],
    bboxes: Sequence[Sequence[float]],
    sort_coord: int,
) -> list[tuple[int, int]]:
    """Clear (i, j) pairs within one collinear node group.

    The group is sorted along the line; consecutive gap segments exactly tile
    any longer in-group segment, so a long segment is clear iff every gap it
    spans is clear.  This keeps the edge set identical to checking each pair
    with _segment_clear while doing only k-1 obstacle checks per group.
    """
    if len(indices) < 2:
        return []
    order = sorted(indices, key=lambda idx: nodes[idx][sort_coord])
    gap_clear = [
        _segment_clear(nodes[order[k]], nodes[order[k + 1]], bboxes)
        for k in range(len(order) - 1)
    ]
    # prefix[k] = number of blocked gaps among gap_clear[:k]
    prefix = [0]
    for clear in gap_clear:
        prefix.append(prefix[-1] + (0 if clear else 1))
    pairs: list[tuple[int, int]] = []
    for a_pos in range(len(order) - 1):
        for b_pos in range(a_pos + 1, len(order)):
            if prefix[b_pos] - prefix[a_pos] == 0:
                i, j = order[a_pos], order[b_pos]
                pairs.append((i, j) if i < j else (j, i))
    return pairs


def _axis_aligned_adjacency(
    nodes: Sequence[Point],
    blocked_bboxes: Sequence[Sequence[float]],
    angle_mode: AngleMode,
) -> list[list[tuple[int, float]]]:
    """Fast equivalent of the brute-force O(V^2 * R) adjacency build.

    _angle_allowed only ever admits axis-aligned segments (plus exact 45-degree
    diagonals in fortyfive mode), so edges are enumerated per collinear group
    instead of over all node pairs.  Grouping uses 1e-9-rounded keys, matching
    the eps used by _angle_allowed; obstacle prefilters use an exact reject
    that mirrors segment_intersects_rect's strict bbox test with a 1e-6
    safety margin, so the resulting edge set and ordering are identical.
    """
    groups_v: dict[float, list[int]] = {}
    groups_h: dict[float, list[int]] = {}
    for idx, point in enumerate(nodes):
        groups_v.setdefault(round(point[0], 9), []).append(idx)
        groups_h.setdefault(round(point[1], 9), []).append(idx)

    pairs: list[tuple[int, int]] = []
    for x_key, indices in groups_v.items():
        col_bboxes = [
            bbox for bbox in blocked_bboxes
            if float(bbox[0]) <= x_key + 1e-6 and float(bbox[2]) >= x_key - 1e-6
        ]
        pairs.extend(_collinear_group_clear_pairs(indices, nodes, col_bboxes, sort_coord=1))
    for y_key, indices in groups_h.items():
        row_bboxes = [
            bbox for bbox in blocked_bboxes
            if float(bbox[1]) <= y_key + 1e-6 and float(bbox[3]) >= y_key - 1e-6
        ]
        pairs.extend(_collinear_group_clear_pairs(indices, nodes, row_bboxes, sort_coord=0))
    if angle_mode == "fortyfive":
        groups_d1: dict[float, list[int]] = {}
        groups_d2: dict[float, list[int]] = {}
        for idx, point in enumerate(nodes):
            groups_d1.setdefault(round(point[0] - point[1], 9), []).append(idx)
            groups_d2.setdefault(round(point[0] + point[1], 9), []).append(idx)
        for groups in (groups_d1, groups_d2):
            for indices in groups.values():
                pairs.extend(_collinear_group_clear_pairs(indices, nodes, blocked_bboxes, sort_coord=0))

    adjacency: list[list[tuple[int, float]]] = [[] for _ in nodes]
    # Same (i, j) append order as the original double loop so Dijkstra
    # tie-breaking (and therefore the returned path) is unchanged.
    for i, j in sorted(set(pairs)):
        cost = _distance(nodes[i], nodes[j])
        adjacency[i].append((j, cost))
        adjacency[j].append((i, cost))
    return adjacency


def _shortest_visibility_path(
    start: Sequence[float],
    end: Sequence[float],
    blocked_bboxes: Sequence[Sequence[float]],
    *,
    angle_mode: AngleMode,
    jog_um: float,
) -> list[Point]:
    nodes = _candidate_nodes(start, end, blocked_bboxes, jog_um=jog_um)
    start_key = _point_key(start)
    end_key = _point_key(end)
    index_by_key = {_point_key(point): idx for idx, point in enumerate(nodes)}
    if start_key not in index_by_key or end_key not in index_by_key:
        # name the endpoint AND the offending bbox — a context-free
        # error cost a whole diagnosis round on the half-adder build
        details = []
        for label, key, pt in (("start", start_key, start),
                               ("end", end_key, end)):
            if key in index_by_key:
                continue
            hits = [tuple(round(float(v), 3) for v in bbox)
                    for bbox in blocked_bboxes
                    if float(bbox[0]) <= pt[0] <= float(bbox[2])
                    and float(bbox[1]) <= pt[1] <= float(bbox[3])]
            details.append(
                "%s (%.3f, %.3f) inside expanded obstacle(s) %s"
                % (label, pt[0], pt[1], hits or "<expansion margin>"))
        raise ValueError(
            "route endpoint is inside an expanded obstacle: "
            + "; ".join(details))

    adjacency = _axis_aligned_adjacency(nodes, blocked_bboxes, angle_mode)

    start_idx = index_by_key[start_key]
    end_idx = index_by_key[end_key]
    distances = [math.inf] * len(nodes)
    previous: list[int | None] = [None] * len(nodes)
    distances[start_idx] = 0.0
    queue: list[tuple[float, int]] = [(0.0, start_idx)]
    while queue:
        dist, idx = heapq.heappop(queue)
        if dist > distances[idx]:
            continue
        if idx == end_idx:
            break
        for next_idx, edge_cost in adjacency[idx]:
            next_dist = dist + edge_cost
            if next_dist < distances[next_idx]:
                distances[next_idx] = next_dist
                previous[next_idx] = idx
                heapq.heappush(queue, (next_dist, next_idx))

    if math.isinf(distances[end_idx]):
        raise ValueError("no visibility path found")

    path_indices = []
    cursor: int | None = end_idx
    while cursor is not None:
        path_indices.append(cursor)
        cursor = previous[cursor]
    path_indices.reverse()
    return [_clean_point(nodes[idx]) for idx in path_indices]


def _is_collinear(a: Sequence[float], b: Sequence[float], c: Sequence[float], eps: float = 1e-9) -> bool:
    return abs(
        (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1]))
        - (float(b[1]) - float(a[1])) * (float(c[0]) - float(a[0]))
    ) <= eps


def _compress_path(
    points: Sequence[Sequence[float]],
    *,
    protected_points: Sequence[Sequence[float]] | None = None,
) -> list[Point]:
    protected = {_point_key(point) for point in (protected_points or [])}
    cleaned: list[Point] = []
    for point in points:
        p = _clean_point(point)
        if cleaned and _same_point(cleaned[-1], p):
            continue
        cleaned.append(p)
    if len(cleaned) <= 2:
        return cleaned
    result = [cleaned[0]]
    for idx in range(1, len(cleaned) - 1):
        if _point_key(cleaned[idx]) in protected:
            result.append(cleaned[idx])
            continue
        if _is_collinear(result[-1], cleaned[idx], cleaned[idx + 1]):
            continue
        result.append(cleaned[idx])
    result.append(cleaned[-1])
    return result


def route_points_geometric(
    start: Sequence[float],
    end: Sequence[float],
    *,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
    route_width_um: float,
    safe_distance_um: float = 0.0,
    angle_mode: AngleMode = "manhattan",
) -> list[Point]:
    """Route between two geometric points with a visibility graph."""
    obstacle_bboxes = list(obstacle_bboxes or [])
    blocked = expand_obstacles_for_route(
        obstacle_bboxes,
        route_width_um=route_width_um,
        safe_distance_um=safe_distance_um,
    )
    jog = max(float(route_width_um), float(safe_distance_um), 1.0)
    return _compress_path(
        _shortest_visibility_path(
            start,
            end,
            blocked,
            angle_mode=angle_mode,
            jog_um=jog,
        )
    )


def _route_through_points(
    points: Sequence[Sequence[float]],
    *,
    obstacle_bboxes: Sequence[Sequence[float]],
    route_width_um: float,
    safe_distance_um: float,
    angle_mode: AngleMode,
    protected_points: Sequence[Sequence[float]] | None = None,
) -> list[Point]:
    if len(points) < 2:
        return [[float(p[0]), float(p[1])] for p in points]
    routed: list[Point] = []
    for start, end in zip(points, points[1:]):
        leg = route_points_geometric(
            start,
            end,
            obstacle_bboxes=obstacle_bboxes,
            route_width_um=route_width_um,
            safe_distance_um=safe_distance_um,
            angle_mode=angle_mode,
        )
        if routed:
            routed.extend(leg[1:])
        else:
            routed.extend(leg)
    return _compress_path(routed, protected_points=protected_points)


def _route_through_groups(
    groups: Sequence[Sequence[Sequence[float]]],
    *,
    obstacle_bboxes: Sequence[Sequence[float]],
    route_width_um: float,
    safe_distance_um: float,
    angle_mode: AngleMode,
    protected_points: Sequence[Sequence[float]] | None = None,
) -> list[Point]:
    cleaned_groups = [[_clean_point(point) for point in group] for group in groups if group]
    if not cleaned_groups:
        return []
    routed: list[Point] = list(cleaned_groups[0])
    for group in cleaned_groups[1:]:
        leg = route_points_geometric(
            routed[-1],
            group[0],
            obstacle_bboxes=obstacle_bboxes,
            route_width_um=route_width_um,
            safe_distance_um=safe_distance_um,
            angle_mode=angle_mode,
        )
        routed.extend(leg[1:])
        routed.extend(group[1:])
    return _compress_path(routed, protected_points=protected_points)


def route_segment_bboxes(points: Sequence[Sequence[float]]) -> list[BBox]:
    """Return one bbox per route segment for freeze-as-obstacle routing."""
    bboxes: list[BBox] = []
    pts = [[float(p[0]), float(p[1])] for p in points]
    for a, b in zip(pts, pts[1:]):
        if _same_point(a, b):
            continue
        bboxes.append([
            min(a[0], b[0]),
            min(a[1], b[1]),
            max(a[0], b[0]),
            max(a[1], b[1]),
        ])
    return bboxes


def route_two_port_geometric(
    source: dict,
    target: dict,
    *,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
    required_points: Sequence[Sequence[float]] | None = None,
    required_point_groups: Sequence[Sequence[Sequence[float]]] | None = None,
    frozen_route_bboxes: Sequence[Sequence[float]] | None = None,
    safe_distance_um: float = 0.0,
    angle_mode: AngleMode = "manhattan",
    launch_length_um: float | None = None,
) -> dict:
    """Route one two-port net with a visibility graph over expanded bboxes."""
    obstacle_bboxes = list(obstacle_bboxes or [])
    frozen_route_bboxes = list(frozen_route_bboxes or [])
    all_obstacles = obstacle_bboxes + frozen_route_bboxes
    skeleton = route_with_port_launch_stubs(source, target, launch_length_um=launch_length_um)
    width = float(skeleton["width_um"])
    source_launch = skeleton["source_launch_um"]
    target_launch = skeleton["target_launch_um"]
    blocked = expand_obstacles_for_route(
        all_obstacles,
        route_width_um=width,
        safe_distance_um=safe_distance_um,
    )
    if required_point_groups is not None:
        groups = [[source_launch], *required_point_groups, [target_launch]]
        required = [_clean_point(point) for group in required_point_groups for point in group]
        inner = _route_through_groups(
            groups,
            obstacle_bboxes=all_obstacles,
            route_width_um=width,
            safe_distance_um=safe_distance_um,
            angle_mode=angle_mode,
            protected_points=required,
        )
    else:
        required = [_clean_point(point) for point in (required_points or [])]
        inner = _route_through_points(
            [source_launch, *required, target_launch],
            obstacle_bboxes=all_obstacles,
            route_width_um=width,
            safe_distance_um=safe_distance_um,
            angle_mode=angle_mode,
            protected_points=required,
        )
    route = route_with_port_launch_stubs(
        source,
        target,
        _compress_path(inner[1:-1], protected_points=required),
        launch_length_um=launch_length_um,
    )
    route["points_um"] = [_clean_point(point) for point in route["points_um"]]
    route["source_launch_um"] = _clean_point(route["source_launch_um"])
    route["target_launch_um"] = _clean_point(route["target_launch_um"])
    route.update(
        {
            "backend": "geometric_visibility_router",
            "expanded_obstacle_bboxes_um": blocked,
            "required_points_um": required,
            "frozen_route_bboxes_um": [list(map(float, bbox)) for bbox in frozen_route_bboxes],
            "safe_distance_um": float(safe_distance_um),
            "angle_mode": angle_mode,
            "obstacle_hits": route_hits_bboxes(route["points_um"], obstacle_bboxes, width),
            "frozen_route_hits": route_hits_bboxes(route["points_um"], frozen_route_bboxes, width),
            "self_crossings": self_crossings(route["points_um"]),
        }
    )
    return route


def route_many_two_port_geometric(
    pairs: Sequence[tuple[dict, dict]],
    *,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
    required_points_by_net: dict[str, Sequence[Sequence[float]]] | None = None,
    safe_distance_um: float = 0.0,
    path_safe_distance_um: float | None = None,
    angle_mode: AngleMode = "manhattan",
) -> list[dict]:
    """Route many two-port pairs sequentially, freezing completed routes."""
    routes: list[dict] = []
    frozen: list[BBox] = []
    required_points_by_net = dict(required_points_by_net or {})
    for source, target in pairs:
        net = str(source.get("net") or target.get("net") or "")
        route = route_two_port_geometric(
            source,
            target,
            obstacle_bboxes=obstacle_bboxes,
            required_points=required_points_by_net.get(net, []),
            frozen_route_bboxes=frozen,
            safe_distance_um=safe_distance_um,
            angle_mode=angle_mode,
        )
        route["net"] = net
        route["source"] = source.get("name", "")
        route["target"] = target.get("name", "")
        route["route_id"] = "route_%s" % net if net else "route_%s_%s" % (route["source"], route["target"])
        routes.append(route)
        freeze_margin = float(path_safe_distance_um) if path_safe_distance_um is not None else float(safe_distance_um)
        frozen.extend(expand_bbox(bbox, freeze_margin) for bbox in route_segment_bboxes(route["points_um"]))
    return routes
