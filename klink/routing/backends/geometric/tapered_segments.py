"""Discrete per-segment tapered router.

Unlike ``klink.routing.backends.geometric.tapered`` which produces a single continuous-gradient
polygon, this module treats each centerline segment as an independent
uniform-width KLayout path.  Width steps at bend points — no gradual taper.

This is the "natural" wiring model: each straight segment has a constant
width, and every time the route turns, the path gets narrower (or wider) by
a computed ratio.

======== ============================================= ===================
Scenario  Recommended router                            Output
======== ============================================= ===================
直连       ``tapered.route_tapered()``                  梯形 polygon
折线路     ``tapered_segments.route_tapered_segments()`` 多段不同宽 path
======== ============================================= ===================

Both modes share the same pluggable taper strategies from ``tapered.py``.
"""

from __future__ import annotations

import math
from typing import Sequence

from klink.routing.backends.geometric.tapered import (
    TaperStrategy,
    _find_bend_indices,
    _resolve_strategy,
    compute_tapered_widths,
)

Point = list[float]


def _route_name(port: dict) -> str:
    return str(port.get("name") or port.get("id") or "")


def _route_net(pair: dict, source: dict, target: dict) -> str:
    return str(pair.get("net") or source.get("net") or target.get("net") or "")


def _anchor_nets(anchor: dict) -> set[str]:
    return {part.strip() for part in str(anchor.get("net") or "").split(",") if part.strip()}


def _net_tokens(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _net_sort_key(net: str) -> tuple[str, int, str]:
    prefix = "".join(ch for ch in str(net) if not ch.isdigit())
    digits = "".join(ch for ch in str(net) if ch.isdigit())
    return (prefix, int(digits) if digits else -1, str(net))


def _is_candidate_sink(port: dict) -> bool:
    return str(port.get("port_type", "")).lower() == "candidate_sink"


def _port_distance(a: dict, b: dict) -> float:
    ax, ay = [float(v) for v in (a.get("center_um") or [0.0, 0.0])]
    bx, by = [float(v) for v in (b.get("center_um") or [0.0, 0.0])]
    return math.hypot(ax - bx, ay - by)


def _orientation_towards(port: dict, target: dict) -> float:
    px, py = _point_of_port(port)
    tx, ty = _point_of_port(target)
    dx = tx - px
    dy = ty - py
    if abs(dx) >= abs(dy):
        return 0.0 if dx >= 0.0 else 180.0
    return 90.0 if dy >= 0.0 else 270.0


def _assignment_axis_key(ports: Sequence[dict]):
    if not ports:
        return lambda p: (0.0, str(p.get("name") or ""))
    xs = [float((p.get("center_um") or [0.0, 0.0])[0]) for p in ports]
    ys = [float((p.get("center_um") or [0.0, 0.0])[1]) for p in ports]
    if (max(xs) - min(xs)) > (max(ys) - min(ys)):
        return lambda p: (float((p.get("center_um") or [0.0, 0.0])[0]), str(p.get("name") or ""))
    return lambda p: (float((p.get("center_um") or [0.0, 0.0])[1]), str(p.get("name") or ""))


def _point_of_port(port: dict) -> tuple[float, float]:
    center = port.get("center_um") or [0.0, 0.0]
    return (float(center[0]), float(center[1]))


def _span_xy(ports: Sequence[dict]) -> tuple[float, float]:
    if not ports:
        return (0.0, 0.0)
    xs = [_point_of_port(p)[0] for p in ports]
    ys = [_point_of_port(p)[1] for p in ports]
    return (max(xs) - min(xs), max(ys) - min(ys))


def _is_loop_like_assignment(demands: Sequence[dict], candidates: Sequence[dict]) -> bool:
    if len(demands) < 3 or len(candidates) < len(demands):
        return False
    ddx, ddy = _span_xy(demands)
    cdx, cdy = _span_xy(candidates)
    demand_2d = min(ddx, ddy) > 0.35 * max(ddx, ddy, 1.0)
    candidate_2d = min(cdx, cdy) > 0.35 * max(cdx, cdy, 1.0)
    return demand_2d and candidate_2d


def _polar_order(ports: Sequence[dict]) -> list[dict]:
    if not ports:
        return []
    pts = [_point_of_port(p) for p in ports]
    cx = sum(x for x, _y in pts) / len(pts)
    cy = sum(y for _x, y in pts) / len(pts)
    return sorted(
        ports,
        key=lambda p: (
            math.atan2(_point_of_port(p)[1] - cy, _point_of_port(p)[0] - cx),
            str(p.get("name") or ""),
        ),
    )


def _assign_candidate_sinks_ordered_loop(demands: Sequence[dict], candidates: Sequence[dict]) -> list[tuple[dict, dict]]:
    ordered_demands = _polar_order(demands)
    ordered_candidates = _polar_order(candidates)
    demand_count = len(ordered_demands)
    candidate_count = len(ordered_candidates)
    if demand_count == 0 or candidate_count < demand_count:
        return []

    best_cost = float("inf")
    best_assignment: list[tuple[dict, dict]] = []
    for start in range(candidate_count):
        window = [ordered_candidates[(start + idx) % candidate_count] for idx in range(candidate_count)]
        inf = float("inf")
        dp = [[inf] * (candidate_count + 1) for _ in range(demand_count + 1)]
        take = [[False] * (candidate_count + 1) for _ in range(demand_count + 1)]
        for j in range(candidate_count + 1):
            dp[0][j] = 0.0
        for i in range(1, demand_count + 1):
            for j in range(1, candidate_count + 1):
                skip = dp[i][j - 1]
                use = dp[i - 1][j - 1] + _port_distance(ordered_demands[i - 1], window[j - 1])
                if use < skip:
                    dp[i][j] = use
                    take[i][j] = True
                else:
                    dp[i][j] = skip
        if dp[demand_count][candidate_count] >= best_cost:
            continue
        assignment: list[tuple[dict, dict]] = []
        i, j = demand_count, candidate_count
        while i > 0 and j > 0:
            if take[i][j]:
                assignment.append((ordered_demands[i - 1], window[j - 1]))
                i -= 1
                j -= 1
            else:
                j -= 1
        assignment.reverse()
        best_cost = dp[demand_count][candidate_count]
        best_assignment = assignment
    return best_assignment


def _assign_candidate_sinks(demands: Sequence[dict], candidates: Sequence[dict]) -> list[tuple[dict, dict, str]]:
    if _is_loop_like_assignment(demands, candidates):
        return [
            (demand, candidate, "candidate_sink_ordered_loop")
            for demand, candidate in _assign_candidate_sinks_ordered_loop(demands, candidates)
        ]

    ordered_demands = sorted(demands, key=_assignment_axis_key(demands))
    ordered_candidates = sorted(candidates, key=_assignment_axis_key(candidates))
    demand_count = len(ordered_demands)
    candidate_count = len(ordered_candidates)
    if demand_count == 0 or candidate_count == 0:
        return []
    if candidate_count < demand_count:
        ordered_demands = ordered_demands[:candidate_count]
        demand_count = candidate_count

    # Order-preserving assignment: choose a candidate subset that minimizes
    # total distance.  This matches the physical no-crossing fanout intent
    # better than greedy nearest-neighbor, which can swap adjacent pads.
    inf = float("inf")
    dp = [[inf] * (candidate_count + 1) for _ in range(demand_count + 1)]
    take = [[False] * (candidate_count + 1) for _ in range(demand_count + 1)]
    for j in range(candidate_count + 1):
        dp[0][j] = 0.0
    for i in range(1, demand_count + 1):
        for j in range(1, candidate_count + 1):
            skip = dp[i][j - 1]
            use = dp[i - 1][j - 1] + _port_distance(ordered_demands[i - 1], ordered_candidates[j - 1])
            if use < skip:
                dp[i][j] = use
                take[i][j] = True
            else:
                dp[i][j] = skip

    assignments: list[tuple[dict, dict, str]] = []
    i, j = demand_count, candidate_count
    while i > 0 and j > 0:
        if take[i][j]:
            assignments.append((ordered_demands[i - 1], ordered_candidates[j - 1], "candidate_sink_nearest"))
            i -= 1
            j -= 1
        else:
            j -= 1
    assignments.reverse()
    return assignments


def _anchor_applies(anchor: dict, net: str) -> bool:
    nets = _anchor_nets(anchor)
    return not nets or str(net) in nets


def _center(anchor: dict) -> Point:
    c = anchor.get("center_um") or [0.0, 0.0]
    return [float(c[0]), float(c[1])]


def _corridor_path(anchor: dict) -> list[Point]:
    from klink.routing.geom.geometry import parse_relative_path

    path = parse_relative_path(anchor.get("center_um", [0.0, 0.0]), anchor.get("path_points", ""))
    return path or [_center(anchor)]


def _lane_normal(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    if len(points) < 2:
        return (0.0, 1.0)
    dx = float(points[-1][0]) - float(points[0][0])
    dy = float(points[-1][1]) - float(points[0][1])
    # Corridor anchors describe a routing band.  For mostly-horizontal bands,
    # users expect lane offsets in Y; for mostly-vertical bands, in X.  Using a
    # mathematically perpendicular vector on a shallow corridor needlessly moves
    # lane points in X and can create fan-in overlaps near the exits.
    if abs(dx) >= abs(dy):
        return (0.0, 1.0)
    return (1.0, 0.0)


def _is_vertical_corridor(points: Sequence[Sequence[float]]) -> bool:
    if len(points) < 2:
        return False
    dx = abs(float(points[-1][0]) - float(points[0][0]))
    dy = abs(float(points[-1][1]) - float(points[0][1]))
    return dy > dx


def _lane_points_for_corridor(
    source: dict,
    target: dict,
    base_path: Sequence[Sequence[float]],
    nx: float,
    ny: float,
    offset: float,
    *,
    member_count: int,
) -> list[Point]:
    if _is_vertical_corridor(base_path) and member_count > 1:
        x = float(base_path[0][0]) + nx * offset
        sy = float((source.get("center_um") or [0.0, 0.0])[1])
        ty = float((target.get("center_um") or [0.0, 0.0])[1])
        return [[x, sy], [x, ty]]
    return [[p[0] + nx * offset, p[1] + ny * offset] for p in base_path]


def _point_inside_bbox(point: Sequence[float], bbox: Sequence[float], eps: float = 1e-9) -> bool:
    return (
        float(bbox[0]) - eps <= float(point[0]) <= float(bbox[2]) + eps
        and float(bbox[1]) - eps <= float(point[1]) <= float(bbox[3]) + eps
    )


def _corridor_axis_extent(points: Sequence[Sequence[float]]) -> tuple[str, float, float] | None:
    if len(points) < 2:
        return None
    x0, y0 = float(points[0][0]), float(points[0][1])
    x1, y1 = float(points[-1][0]), float(points[-1][1])
    if abs(y1 - y0) >= abs(x1 - x0):
        return ("y", min(y0, y1), max(y0, y1))
    return ("x", min(x0, x1), max(x0, x1))


def _nearest_clear_corridor_gate(
    point: Sequence[float],
    shifted_path: Sequence[Sequence[float]],
    blocked_bboxes: Sequence[Sequence[float]],
    report_bboxes: Sequence[Sequence[float]],
    *,
    route_width_um: float,
    corridor_id: object,
) -> Point:
    if not blocked_bboxes:
        return [float(point[0]), float(point[1])]
    if not any(_point_inside_bbox(point, bbox) for bbox in blocked_bboxes):
        return [float(point[0]), float(point[1])]

    extent = _corridor_axis_extent(shifted_path)
    if extent is None:
        return [float(point[0]), float(point[1])]
    axis, lo, hi = extent
    coord_index = 1 if axis == "y" else 0
    fixed_index = 0 if axis == "y" else 1
    origin = min(max(float(point[coord_index]), lo), hi)
    fixed = float(point[fixed_index])
    step = max(float(route_width_um) / 2.0, 1e-6)
    max_steps = int((hi - lo) / step) + 2

    def candidate_at(value: float) -> Point:
        if axis == "y":
            return [fixed, value]
        return [value, fixed]

    for index in range(max_steps + 1):
        deltas = [0.0] if index == 0 else [index * step, -index * step]
        candidates: list[tuple[float, Point]] = []
        for delta in deltas:
            value = origin + delta
            if lo - 1e-9 <= value <= hi + 1e-9:
                candidates.append((abs(delta), candidate_at(value)))
        for _distance, candidate in sorted(candidates, key=lambda item: (item[0], item[1][coord_index])):
            if not any(_point_inside_bbox(candidate, bbox) for bbox in blocked_bboxes):
                return candidate

    blockers = [
        [round(float(v), 3) for v in bbox]
        for bbox in report_bboxes
        if min(float(bbox[2]), hi) >= max(float(bbox[0]), lo) if axis == "x"
    ]
    if axis == "y":
        blockers = [
            [round(float(v), 3) for v in bbox]
            for bbox in report_bboxes
            if min(float(bbox[3]), hi) >= max(float(bbox[1]), lo)
        ]
    raise ValueError(
        "corridor gate slide failed: "
        f"corridor_id={corridor_id!r}, searched_extent={axis}[{lo:.3f},{hi:.3f}], "
        f"blocking_bboxes={blockers or [[round(float(v), 3) for v in bbox] for bbox in report_bboxes]}"
    )


def _slide_corridor_gates(
    lane_points: Sequence[Sequence[float]],
    base_path: Sequence[Sequence[float]],
    nx: float,
    ny: float,
    offset: float,
    obstacle_bboxes: Sequence[Sequence[float]],
    *,
    route_width_um: float,
    safe_distance_um: float,
    corridor_id: object,
) -> list[Point]:
    from klink.routing.geom.geometry import expand_bbox

    margin = max(0.0, float(route_width_um) / 2.0 + float(safe_distance_um))
    blocked = [expand_bbox(bbox, margin) for bbox in obstacle_bboxes]
    shifted_path = [[float(p[0]) + nx * offset, float(p[1]) + ny * offset] for p in base_path]
    return [
        _nearest_clear_corridor_gate(
            point,
            shifted_path,
            blocked,
            obstacle_bboxes,
            route_width_um=route_width_um,
            corridor_id=corridor_id,
        )
        for point in lane_points
    ]


def _ordered_offsets_for_corridor(base_path: Sequence[Sequence[float]], pairs: Sequence[dict], offsets: list[float]) -> list[float]:
    if not _is_vertical_corridor(base_path) or not pairs:
        return offsets
    base_x = float(base_path[0][0])
    avg_source_x = sum(float((p["source"].get("center_um") or [0.0, 0.0])[0]) for p in pairs) / len(pairs)
    avg_source_y = sum(float((p["source"].get("center_um") or [0.0, 0.0])[1]) for p in pairs) / len(pairs)
    avg_target_y = sum(float((p["target"].get("center_um") or [0.0, 0.0])[1]) for p in pairs) / len(pairs)
    if avg_source_x < base_x and avg_target_y > avg_source_y:
        return list(reversed(offsets))
    return offsets


def _auto_corridor_for_pair_group(pairs: Sequence[dict], corridor_id: str) -> dict | None:
    """Infer one neutral bus corridor from only the port geometry.

    This is deliberately generic: no cell names, net names, layer names, or
    fixture-specific coordinates.  If a caller supplies explicit anchors, those
    win.  Without anchors, a multi-net group still needs one shared planning
    structure so sibling spacing/order lives in the router, not the example.
    """
    if len(pairs) <= 1:
        return None
    sx = [float((p["source"].get("center_um") or [0.0, 0.0])[0]) for p in pairs]
    sy = [float((p["source"].get("center_um") or [0.0, 0.0])[1]) for p in pairs]
    tx = [float((p["target"].get("center_um") or [0.0, 0.0])[0]) for p in pairs]
    ty = [float((p["target"].get("center_um") or [0.0, 0.0])[1]) for p in pairs]
    avg_sx = sum(sx) / len(sx)
    avg_sy = sum(sy) / len(sy)
    avg_tx = sum(tx) / len(tx)
    avg_ty = sum(ty) / len(ty)
    dx = abs(avg_tx - avg_sx)
    dy = abs(avg_ty - avg_sy)
    nets = ",".join(str(p.get("net") or "") for p in pairs if p.get("net"))
    if dx >= dy:
        y0 = min(min(sy), min(ty))
        y1 = max(max(sy), max(ty))
        x = (avg_sx + avg_tx) / 2.0
        cy = (y0 + y1) / 2.0
        half = max((y1 - y0) / 2.0, 1.0)
        return {
            "id": corridor_id,
            "kind": "corridor",
            "net": nets,
            "center_um": [x, cy],
            "width_um": 0.0,
            "path_points": f"0,{-half:.6f};0,{half:.6f}",
            "priority": 9999,
            "auto": True,
        }
    x0 = min(min(sx), min(tx))
    x1 = max(max(sx), max(tx))
    y = (avg_sy + avg_ty) / 2.0
    cx = (x0 + x1) / 2.0
    half = max((x1 - x0) / 2.0, 1.0)
    return {
        "id": corridor_id,
        "kind": "corridor",
        "net": nets,
        "center_um": [cx, y],
        "width_um": 0.0,
        "path_points": f"{-half:.6f},0;{half:.6f},0",
        "priority": 9999,
        "auto": True,
    }


def _auto_corridors_for_pairs(pairs: Sequence[dict]) -> list[dict]:
    """Infer bus corridors for a multi-route group.

    A single vertical trunk is not enough for symmetric fan-in where lower
    routes move upward and upper routes move downward.  Split those into
    directional sub-buses so lane assignment remains inside the router instead
    of being hand-coded by examples.
    """
    if len(pairs) <= 1:
        return []
    if any(str(p.get("assignment") or "") == "candidate_sink_ordered_loop" for p in pairs):
        return []

    sx = [float((p["source"].get("center_um") or [0.0, 0.0])[0]) for p in pairs]
    sy = [float((p["source"].get("center_um") or [0.0, 0.0])[1]) for p in pairs]
    tx = [float((p["target"].get("center_um") or [0.0, 0.0])[0]) for p in pairs]
    ty = [float((p["target"].get("center_um") or [0.0, 0.0])[1]) for p in pairs]
    source_1d = min(max(sx) - min(sx), max(sy) - min(sy)) <= 0.25 * max(max(sx) - min(sx), max(sy) - min(sy), 1.0)
    target_1d = min(max(tx) - min(tx), max(ty) - min(ty)) <= 0.25 * max(max(tx) - min(tx), max(ty) - min(ty), 1.0)
    if not source_1d or not target_1d:
        return []
    avg_sx = sum(sx) / len(sx)
    avg_sy = sum(sy) / len(sy)
    avg_tx = sum(tx) / len(tx)
    avg_ty = sum(ty) / len(ty)
    dx = abs(avg_tx - avg_sx)
    dy = abs(avg_ty - avg_sy)

    groups: list[list[dict]]
    if dx >= dy:
        positive = [
            p for p in pairs
            if float((p["target"].get("center_um") or [0.0, 0.0])[1])
            >= float((p["source"].get("center_um") or [0.0, 0.0])[1])
        ]
        negative = [p for p in pairs if p not in positive]
        groups = [g for g in (positive, negative) if g]
    else:
        positive = [
            p for p in pairs
            if float((p["target"].get("center_um") or [0.0, 0.0])[0])
            >= float((p["source"].get("center_um") or [0.0, 0.0])[0])
        ]
        negative = [p for p in pairs if p not in positive]
        groups = [g for g in (positive, negative) if g]

    if len(groups) == 1:
        corridor = _auto_corridor_for_pair_group(groups[0], "AUTO_BUS")
        return [corridor] if corridor is not None else []

    corridors: list[dict] = []
    for idx, group in enumerate(groups):
        corridor = _auto_corridor_for_pair_group(group, f"AUTO_BUS_{idx}")
        if corridor is not None:
            corridors.append(corridor)
    return corridors


def _lane_offsets(count: int, pitch: float) -> list[float]:
    if count <= 1:
        return [0.0]
    center = (count - 1) / 2.0
    return [(i - center) * float(pitch) for i in range(count)]


def _corridor_capacity_issue(corridor: dict, offsets: Sequence[float], max_width: float) -> dict | None:
    if corridor.get("auto"):
        return None
    width = float(corridor.get("width_um", 0.0) or 0.0)
    if width <= 0.0 or not offsets:
        return None
    max_center_offset = max(abs(float(offset)) for offset in offsets)
    allowed_center_offset = max((width - float(max_width)) / 2.0, 0.0)
    if max_center_offset <= allowed_center_offset + 1e-9:
        return None
    return {
        "type": "corridor_capacity",
        "corridor_id": corridor.get("id"),
        "net_count": len(offsets),
        "corridor_width_um": width,
        "max_route_width_um": float(max_width),
        "max_center_offset_um": max_center_offset,
        "allowed_center_offset_um": allowed_center_offset,
        "required_width_um": 2.0 * max_center_offset + float(max_width),
    }


def _ordered_pairs_for_lane_assignment(pairs: list[dict]) -> list[dict]:
    def key(pair: dict) -> tuple[float, float, str]:
        src = pair["source"]
        tgt = pair["target"]
        sy = float((src.get("center_um") or [0.0, 0.0])[1])
        ty = float((tgt.get("center_um") or [0.0, 0.0])[1])
        return (sy, ty, str(pair.get("net") or ""))

    return sorted(pairs, key=key)


def _segment_bbox(seg: dict) -> list[float] | None:
    pts = seg.get("points_um") or []
    if len(pts) < 2:
        return None
    w = float(seg.get("width_um", 0.0) or 0.0)
    xs = [float(pt[0]) for pt in pts]
    ys = [float(pt[1]) for pt in pts]
    margin = w / 2.0
    return [min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin]


def _route_path_segments_with_width(route: dict) -> list[dict]:
    segments: list[dict] = []
    for group in route.get("groups", []):
        if group.get("kind") == "path":
            for seg in group.get("segments", []):
                pts = seg.get("points_um") or []
                if len(pts) >= 2:
                    segments.append({
                        "a": [float(pts[0][0]), float(pts[0][1])],
                        "b": [float(pts[1][0]), float(pts[1][1])],
                        "width_um": float(seg.get("width_um", 0.0) or 0.0),
                    })
        elif group.get("kind") == "polygon":
            # Polygon fallback regions are already local route bodies.  Use
            # their bbox for conservative detection by representing the
            # diagonal of the bbox as a wide segment.
            poly = group.get("polygon_um") or []
            if not poly:
                continue
            bbox = [
                min(float(p[0]) for p in poly),
                min(float(p[1]) for p in poly),
                max(float(p[0]) for p in poly),
                max(float(p[1]) for p in poly),
            ]
            segments.append({"a": [bbox[0], bbox[1]], "b": [bbox[2], bbox[3]], "width_um": 0.0})
    return segments


def _point_segment_distance(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    px, py = float(p[0]), float(p[1])
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    vx, vy = bx - ax, by - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / denom))
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(px - cx, py - cy)


def _segment_distance(a0: Sequence[float], a1: Sequence[float], b0: Sequence[float], b1: Sequence[float]) -> float:
    from klink.routing.geom.geometry import segments_intersect

    if segments_intersect((list(a0), list(a1)), (list(b0), list(b1)), allow_shared_endpoint=False):
        return 0.0
    return min(
        _point_segment_distance(a0, b0, b1),
        _point_segment_distance(a1, b0, b1),
        _point_segment_distance(b0, a0, a1),
        _point_segment_distance(b1, a0, a1),
    )


def _sibling_envelope_overlaps(routes: Sequence[dict], *, min_gap_um: float = 0.0) -> list[dict]:
    overlaps: list[dict] = []
    for i in range(len(routes)):
        segs_i = _route_path_segments_with_width(routes[i])
        for j in range(i + 1, len(routes)):
            segs_j = _route_path_segments_with_width(routes[j])
            for si, seg_i in enumerate(segs_i):
                for sj, seg_j in enumerate(segs_j):
                    threshold = (
                        float(seg_i.get("width_um", 0.0)) / 2.0
                        + float(seg_j.get("width_um", 0.0)) / 2.0
                        + float(min_gap_um)
                    )
                    distance = _segment_distance(seg_i["a"], seg_i["b"], seg_j["a"], seg_j["b"])
                    if distance < threshold:
                        overlaps.append({
                            "route_a": routes[i].get("route_id", routes[i].get("net", "")),
                            "route_b": routes[j].get("route_id", routes[j].get("net", "")),
                            "segment_a": [seg_i["a"], seg_i["b"]],
                            "segment_b": [seg_j["a"], seg_j["b"]],
                            "distance_um": distance,
                            "threshold_um": threshold,
                            "shape_a": si,
                            "shape_b": sj,
                        })
                        break
                if overlaps and overlaps[-1].get("route_a") == routes[i].get("route_id", routes[i].get("net", "")) and overlaps[-1].get("route_b") == routes[j].get("route_id", routes[j].get("net", "")):
                    break
    return overlaps


# ---------------------------------------------------------------------------
# Segment width computation
# ---------------------------------------------------------------------------


def compute_segment_widths(
    points: Sequence[Sequence[float]],
    source_width_um: float,
    target_width_um: float,
    *,
    strategy: str | TaperStrategy = "uniform",
) -> list[float]:
    """Width for each *segment* between consecutive centerline points.

    Returns N-1 values for N points.  Segment k (points[k] → points[k+1])
    gets the width at the segment's starting point.  Width steps at bends.
    """
    point_widths = compute_tapered_widths(
        points, source_width_um, target_width_um, strategy=strategy
    )
    return point_widths[:-1]


# ---------------------------------------------------------------------------
# Route builder
# ---------------------------------------------------------------------------


def route_tapered_segments(
    source: dict,
    target: dict,
    inner_points: Sequence[Sequence[float]] | None = None,
    *,
    launch_length_um: float | None = None,
    strategy: str | TaperStrategy = "uniform",
) -> dict:
    """Build a tapered route as discrete uniform-width path segments.

    Each segment is a straight line with a single ``width_um``.  Width steps
    at bend points according to the chosen *strategy*.

    Returns a dict with ``segments`` (list of ``{points_um, width_um}``),
    plus the full centerline and metadata.  Write back with
    ``commit_tapered_segments()``.
    """
    from klink.routing.geom.constraints import break_launch_hairpins, direct_head_on_route, port_launch_point, port_launch_width

    source_center = [float(v) for v in source.get("center_um", [0.0, 0.0])]
    target_center = [float(v) for v in target.get("center_um", [0.0, 0.0])]
    source_width = port_launch_width(source)
    target_width = port_launch_width(target)
    source_launch = port_launch_point(source, length_um=launch_length_um)
    target_launch = port_launch_point(target, length_um=launch_length_um)

    direct = direct_head_on_route(source, target, launch_length_um=launch_length_um)
    if direct is not None and not inner_points:
        points = direct
    else:
        points = [source_center, source_launch]
        for pt in inner_points or []:
            p = [float(pt[0]), float(pt[1])]
            if p != points[-1]:
                points.append(p)
        if target_launch != points[-1]:
            points.append(target_launch)
        if target_center != points[-1]:
            points.append(target_center)
        points = break_launch_hairpins(points, source, target)

    seg_widths = compute_segment_widths(
        points, source_width, target_width, strategy=strategy
    )
    bend_indices = _find_bend_indices(points)
    num_bends = len(bend_indices)

    # Build segments
    segments: list[dict] = []
    for k in range(len(points) - 1):
        segments.append({
            "points_um": [points[k], points[k + 1]],
            "width_um": seg_widths[k],
        })

    # Per-bend ratios for reporting
    per_bend_ratios = []
    if num_bends > 0 and source_width > 0:
        for idx in bend_indices:
            w_before = seg_widths[idx - 1] if idx > 0 else source_width
            w_after = seg_widths[idx] if idx < len(seg_widths) else target_width
            if w_before > 0:
                per_bend_ratios.append(round(w_after / w_before, 4))

    return {
        "points_um": points,
        "segments": segments,
        "segment_widths_um": seg_widths,
        "source_launch_um": source_launch,
        "target_launch_um": target_launch,
        "width_um": min(source_width, target_width),
        "source_width_um": source_width,
        "target_width_um": target_width,
        "num_bends": num_bends,
        "bend_indices": bend_indices,
        "per_bend_ratios": per_bend_ratios,
        "strategy": strategy if isinstance(strategy, str) else "custom",
        "backend": "tapered_segments",
    }


# ---------------------------------------------------------------------------
# Writeback
# ---------------------------------------------------------------------------


def commit_tapered_segments(
    client,
    cell: str,
    route: dict,
    *,
    route_layer: str = "10/0",
    clear: bool = True,
) -> dict:
    """Write a tapered-segments route to KLayout as individual paths.

    Each segment in ``route["segments"]`` becomes one ``shape_insert_path``
    with its own uniform ``width_um``.  Extensions are set to half the
    segment width so consecutive segments meet at corners.
    """
    from klink.routing.geom.geometry import parse_layer

    layer, datatype = parse_layer(route_layer)
    client.layer_ensure(layer, datatype, name="KLINK_ROUTES")

    deleted = 0
    if clear:
        deleted = int(
            client.shape_delete(
                cell, layers=[route_layer], kinds=["paths"], limit=10000,
            ).get("deleted", 0)
        )

    inserted = 0
    for seg in route.get("segments", []):
        pts = seg.get("points_um") or []
        if len(pts) < 2:
            continue
        w = float(seg.get("width_um", 1.0))
        client.shape_insert_path(
            cell,
            layer=layer,
            datatype=datatype,
            points_um=pts,
            width_um=w,
            begin_ext_um=w / 2.0,
            end_ext_um=w / 2.0,
            round_ends=False,
        )
        inserted += 1

    return {
        "cell": cell,
        "route_layer": route_layer,
        "deleted": deleted,
        "inserted_segments": inserted,
    }


# ============================================================================
# Hybrid approach: paths + polygon patches at bends
# ============================================================================
#
# Each segment is a uniform-width path.  At each bend between two good
# (long-enough) segments, a 4-vertex polygon patch bridges the two path faces
# seamlessly.  Segments that are too short for a clean path are grouped into
# polygon sections — only the tight region falls back, not the entire route.
# ============================================================================


def _perpendicular(dx: float, dy: float, sign: float) -> tuple[float, float]:
    """sign=+1 → CCW (left side), sign=-1 → CW (right side)."""
    return (sign * (-dy), sign * dx)


def _group_segments(good_flags: list[bool]) -> list[dict]:
    """Group consecutive segments into {kind: "path"|"polygon", seg_range: (start, end)}."""
    if not good_flags:
        return []
    groups = []
    i = 0
    while i < len(good_flags):
        kind = "path" if good_flags[i] else "polygon"
        j = i
        while j < len(good_flags) and (good_flags[j] == (kind == "path")):
            j += 1
        groups.append({"kind": kind, "seg_range": (i, j)})
        i = j
    return groups


def _dedupe_consecutive_points(points: list[Point], eps: float = 1e-9) -> list[Point]:
    result: list[Point] = []
    for pt in points:
        if result and math.hypot(pt[0] - result[-1][0], pt[1] - result[-1][1]) <= eps:
            continue
        result.append(pt)
    if len(result) > 1 and math.hypot(result[0][0] - result[-1][0], result[0][1] - result[-1][1]) <= eps:
        result.pop()
    return result


def _signed_polygon_area(points: Sequence[Sequence[float]]) -> float:
    area = 0.0
    for i, pt in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        area += float(pt[0]) * float(nxt[1]) - float(nxt[0]) * float(pt[1])
    return area / 2.0


def _convex_hull(points: Sequence[Sequence[float]]) -> list[Point]:
    pts = sorted({(float(p[0]), float(p[1])) for p in points})
    if len(pts) <= 1:
        return [[x, y] for x, y in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return [[x, y] for x, y in lower[:-1] + upper[:-1]]


def _miter_corner(
    points: Sequence[Sequence[float]],
    seg_widths: Sequence[float],
    bi: int,
    *,
    w_in_ov: float | None = None,
    w_out_ov: float | None = None,
    miter_limit: float = 2.0,
) -> tuple[list[Point], float, float]:
    """Compute miter corner patch at bend index *bi*.

    Geometry definition (angle < 180° on the inner side):

      Path A travels along d_in, ends near bend_pt.
      Path B travels along d_out, starts near bend_pt.

      Inner side = the concave side of the turn (where the two paths
                   would overlap if left untruncated).
      Outer side = the convex side (the pointy miter tip).

      inner_miter = intersection of the two inner-edge offset lines.
                    This is the shared corner of both truncated path faces.
                    When the turn is sharp, inner_miter lies BEHIND bend_pt
                    along d_in (and along -d_out), so each path must be
                    shortened to reach it.

      outer_miter = intersection of the two outer-edge offset lines.
                    This is the pointy tip of the miter polygon.

    The corner patch is always a quadrilateral (CCW):
        inner_miter   ← shared hinge point of both path faces
        outer_face_a  ← outer corner of path A's truncated face
        outer_miter   ← the pointy tip
        outer_face_b  ← outer corner of path B's truncated face

    Returns:
        polygon_um  : list of 4 points (CCW)
        cut_in      : how much to shorten path A from its end (positive = shorten)
        cut_out     : how much to shorten path B from its start (positive = shorten)
    """
    from klink.routing.backends.geometric.tapered import _segment_direction

    d_in  = _segment_direction(points[bi - 1], points[bi])
    d_out = _segment_direction(points[bi],     points[bi + 1])
    w_in  = w_in_ov  if w_in_ov  is not None else float(seg_widths[bi - 1])
    w_out = w_out_ov if w_out_ov is not None else float(seg_widths[bi])
    bx, by = float(points[bi][0]), float(points[bi][1])

    # cross > 0 → left turn;  cross < 0 → right turn
    cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]

    # Perpendicular unit normals pointing to each side.
    # _perpendicular(dx, dy, +1) = left normal  = (-dy,  dx)
    # _perpendicular(dx, dy, -1) = right normal = ( dy, -dx)
    # Inner side is the concave (overlap) side.
    # _perpendicular(d, +1) = left, _perpendicular(d, -1) = right.
    # Left turn  → inner = left  side of both paths
    # Right turn → inner = right side of both paths
    if cross >= 0:  # left turn: inner=left(+1), outer=right(-1)
        inner_sign =  1.0
        outer_sign = -1.0
    else:           # right turn: inner=right(-1), outer=left(+1)
        inner_sign = -1.0
        outer_sign =  1.0
    in_sign_a = in_sign_b = inner_sign
    out_sign_a = out_sign_b = outer_sign

    # Inner edge offset lines.
    in_na = _perpendicular(d_in[0],  d_in[1],  in_sign_a)
    in_nb = _perpendicular(d_out[0], d_out[1], in_sign_b)
    p_a = [bx + in_na[0] * w_in  / 2.0, by + in_na[1] * w_in  / 2.0]
    p_b = [bx + in_nb[0] * w_out / 2.0, by + in_nb[1] * w_out / 2.0]
    inner_miter = _line_intersect_lines(p_a, d_in, p_b, d_out)

    # Outer edge offset lines.
    out_na = _perpendicular(d_in[0],  d_in[1],  out_sign_a)
    out_nb = _perpendicular(d_out[0], d_out[1], out_sign_b)
    q_a = [bx + out_na[0] * w_in  / 2.0, by + out_na[1] * w_in  / 2.0]
    q_b = [bx + out_nb[0] * w_out / 2.0, by + out_nb[1] * w_out / 2.0]
    outer_miter = _line_intersect_lines(q_a, d_in, q_b, d_out)

    if inner_miter is None or outer_miter is None:
        return [], 0.0, 0.0

    # Project the center of each truncated path face from bend_pt.
    # Incoming cuts use negative projection; outgoing cuts use positive projection.
    inner_face_center_a = [
        inner_miter[0] - in_na[0] * w_in / 2.0,
        inner_miter[1] - in_na[1] * w_in / 2.0,
    ]
    inner_face_center_b = [
        inner_miter[0] - in_nb[0] * w_out / 2.0,
        inner_miter[1] - in_nb[1] * w_out / 2.0,
    ]
    outer_face_center_a = [
        outer_miter[0] - out_na[0] * w_in / 2.0,
        outer_miter[1] - out_na[1] * w_in / 2.0,
    ]
    outer_face_center_b = [
        outer_miter[0] - out_nb[0] * w_out / 2.0,
        outer_miter[1] - out_nb[1] * w_out / 2.0,
    ]

    proj_in_inner = (
        (inner_face_center_a[0] - bx) * d_in[0]
        + (inner_face_center_a[1] - by) * d_in[1]
    )
    proj_in_outer = (
        (outer_face_center_a[0] - bx) * d_in[0]
        + (outer_face_center_a[1] - by) * d_in[1]
    )
    proj_out_inner = (
        (inner_face_center_b[0] - bx) * d_out[0]
        + (inner_face_center_b[1] - by) * d_out[1]
    )
    proj_out_outer = (
        (outer_face_center_b[0] - bx) * d_out[0]
        + (outer_face_center_b[1] - by) * d_out[1]
    )

    cut_in = max(0.0, -proj_in_inner, -proj_in_outer)
    cut_out = max(0.0, proj_out_inner, proj_out_outer)

    if max(cut_in, cut_out) > max(w_in, w_out) * float(miter_limit) / 2.0:
        center_a = [bx, by]
        center_b = [bx, by]
        inner_face_a = [
            center_a[0] + in_na[0] * w_in / 2.0,
            center_a[1] + in_na[1] * w_in / 2.0,
        ]
        outer_face_a = [
            center_a[0] + out_na[0] * w_in / 2.0,
            center_a[1] + out_na[1] * w_in / 2.0,
        ]
        inner_face_b = [
            center_b[0] + in_nb[0] * w_out / 2.0,
            center_b[1] + in_nb[1] * w_out / 2.0,
        ]
        outer_face_b = [
            center_b[0] + out_nb[0] * w_out / 2.0,
            center_b[1] + out_nb[1] * w_out / 2.0,
        ]
        polygon = _dedupe_consecutive_points([
            inner_face_a,
            inner_face_b,
            outer_face_b,
            outer_face_a,
        ])
        if len(polygon) >= 3 and _signed_polygon_area(polygon) < 0.0:
            polygon = list(reversed(polygon))
        return polygon, 0.0, 0.0

    # Outer corners of each truncated path face.  The inner miter is already
    # on the inner edge, so reaching the outer edge takes one full width.
    face_center_a_actual = [bx - d_in[0] * cut_in, by - d_in[1] * cut_in]
    face_center_b_actual = [bx + d_out[0] * cut_out, by + d_out[1] * cut_out]

    inner_face_a = [
        face_center_a_actual[0] + in_na[0] * w_in / 2.0,
        face_center_a_actual[1] + in_na[1] * w_in / 2.0,
    ]
    outer_face_a = [
        face_center_a_actual[0] + out_na[0] * w_in / 2.0,
        face_center_a_actual[1] + out_na[1] * w_in / 2.0,
    ]
    inner_face_b = [
        face_center_b_actual[0] + in_nb[0] * w_out / 2.0,
        face_center_b_actual[1] + in_nb[1] * w_out / 2.0,
    ]
    outer_face_b = [
        face_center_b_actual[0] + out_nb[0] * w_out / 2.0,
        face_center_b_actual[1] + out_nb[1] * w_out / 2.0,
    ]

    # Quadrilateral (CCW): inner_miter → outer_face_a → outer_miter → outer_face_b
    polygon = _dedupe_consecutive_points([
        inner_face_a,
        inner_face_b,
        outer_face_b,
        outer_miter,
        outer_face_a,
    ])
    if len(polygon) >= 3 and _signed_polygon_area(polygon) < 0.0:
        polygon = list(reversed(polygon))

    return polygon, cut_in, cut_out


def _line_intersect_lines(
    p1: Sequence[float], d1: tuple[float, float],
    p2: Sequence[float], d2: tuple[float, float],
) -> Point | None:
    """Intersection of two infinite lines, or None if parallel."""
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) < 1e-15:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / det
    return [float(p1[0]) + t * d1[0], float(p1[1]) + t * d1[1]]


def route_tapered_hybrid(
    source: dict,
    target: dict,
    inner_points: Sequence[Sequence[float]] | None = None,
    *,
    launch_length_um: float | None = None,
    strategy: str | TaperStrategy = "uniform",
) -> dict:
    """Build a tapered route: inner/outer miter intersection geometry.

    At each bend, the inner offset lines intersect → truncation distance for
    each path.  Outer offset lines intersect → outer miter vertex.  The corner
    polygon is a quadrilateral connecting the two truncated path faces to the
    outer miter.  No angle formulas, no thresholds — pure geometry.
    """
    seg_route = route_tapered_segments(
        source, target, inner_points,
        launch_length_um=launch_length_um, strategy=strategy,
    )
    points = seg_route["points_um"]
    all_segs = seg_route["segments"]
    seg_widths = seg_route["segment_widths_um"]
    bend_indices = seg_route["bend_indices"]
    n_segs = len(all_segs)

    from klink.routing.backends.geometric.tapered import _segment_direction

    # ---- miter corners ----
    # _miter_corner returns (polygon, cut_in, cut_out) where:
    #   cut_in  > 0 → shorten path A (seg bi-1) end by this amount
    #   cut_out > 0 → shorten path B (seg bi)   start by this amount
    cut_end: dict[int, float] = {}
    cut_start: dict[int, float] = {}
    all_corners: dict[int, dict] = {}

    for bi in bend_indices:
        poly, cut_in, cut_out = _miter_corner(points, seg_widths, bi)
        all_corners[bi] = {"polygon_um": poly}
        if cut_in > 0:
            cut_end[bi - 1] = max(cut_end.get(bi - 1, 0.0), cut_in)
        if cut_out > 0:
            cut_start[bi] = max(cut_start.get(bi, 0.0), cut_out)

    # ---- classify: polygon fallback when cuts consume the whole segment ----
    seg_is_path = []
    for k, seg in enumerate(all_segs):
        pts = seg["points_um"]
        seg_len = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        total_cut = (cut_start.get(k, 0.0) if k > 0 else 0.0) + \
                    (cut_end.get(k, 0.0)   if k < n_segs - 1 else 0.0)
        seg_is_path.append(total_cut < seg_len)

    groups_raw = _group_segments(seg_is_path)

    # ---- build groups ----
    groups: list[dict] = []
    for g in groups_raw:
        start, end = g["seg_range"]
        if g["kind"] == "path":
            sub_segs = []
            sub_patches = []
            for k in range(start, end):
                pts = [list(all_segs[k]["points_um"][0]), list(all_segs[k]["points_um"][1])]
                w = all_segs[k]["width_um"]
                dx, dy = _segment_direction(pts[0], pts[1])
                seg_len = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
                if k > 0 and seg_is_path[k - 1]:
                    c = min(cut_start.get(k, 0.0), seg_len)
                    if c > 0:
                        pts[0] = [pts[0][0] + dx * c, pts[0][1] + dy * c]
                if k < n_segs - 1 and seg_is_path[k + 1]:
                    c = min(cut_end.get(k, 0.0), seg_len)
                    if c > 0:
                        pts[1] = [pts[1][0] - dx * c, pts[1][1] - dy * c]
                sub_segs.append({
                    "points_um": pts, "width_um": w,
                    "is_first": (k == 0), "is_last": (k == n_segs - 1),
                })
            for bi in bend_indices:
                if start <= bi - 1 < end and start <= bi < end:
                    patch_poly = all_corners[bi]["polygon_um"]
                    if patch_poly:
                        sub_patches.append({"bend_index": bi, "polygon_um": patch_poly})
            groups.append({
                "kind": "path", "segments": sub_segs, "corner_patches": sub_patches,
                "seg_range": (start, end), "first_seg_idx": start,
            })
        else:
            from klink.routing.backends.geometric.tapered import build_trapezoid_polygon
            sub_pts = [points[start]]
            for k in range(start, end):
                sub_pts.append(points[k + 1])
            sub_widths = [seg_widths[start]]
            for k in range(start, end):
                sub_widths.append(seg_widths[min(k + 1, n_segs - 1)])
            if len(sub_widths) < len(sub_pts):
                sub_widths.append(sub_widths[-1])
            poly = build_trapezoid_polygon(sub_pts, sub_widths, corner_style="miter")
            groups.append({"kind": "polygon", "polygon_um": poly, "seg_range": (start, end)})

    # ---- boundary patches (bends crossing group boundaries) ----
    boundary_patches: list[dict] = []
    polygon_patch_points: dict[int, list[Point]] = {}
    for bi in bend_indices:
        seg_a, seg_b = bi - 1, bi
        grp_a = grp_b = None
        kind_a = kind_b = None
        range_a = range_b = (0, 0)
        for i_g, grp in enumerate(groups):
            s, e = grp["seg_range"]
            if s <= seg_a < e: grp_a, kind_a, range_a = i_g, grp["kind"], (s, e)
            if s <= seg_b < e: grp_b, kind_b, range_b = i_g, grp["kind"], (s, e)
        if grp_a is None or grp_b is None or grp_a == grp_b:
            continue
        if kind_a != "polygon" and kind_b != "polygon":
            continue
        w_in_ov  = seg_widths[min(range_a[1], n_segs - 1)] if kind_a == "polygon" else None
        w_out_ov = seg_widths[range_b[0]]                   if kind_b == "polygon" else None
        poly, _, _ = _miter_corner(points, seg_widths, bi,
                                   w_in_ov=w_in_ov, w_out_ov=w_out_ov)
        if poly:
            if kind_a == "polygon" and kind_b != "polygon":
                polygon_patch_points.setdefault(grp_a, []).extend(poly)
                continue
            if kind_b == "polygon" and kind_a != "polygon":
                polygon_patch_points.setdefault(grp_b, []).extend(poly)
                continue
            boundary_patches.append({"bend_index": bi, "polygon_um": poly})

    for i_g, patch_points in polygon_patch_points.items():
        if i_g is None or i_g < 0 or i_g >= len(groups):
            continue
        grp = groups[i_g]
        if grp.get("kind") != "polygon":
            continue
        merged = _convex_hull((grp.get("polygon_um") or []) + patch_points)
        if len(merged) >= 3:
            grp["polygon_um"] = merged

    return {
        "points_um": points,
        "groups": groups,
        "boundary_patches": boundary_patches,
        "source_width_um": seg_route["source_width_um"],
        "target_width_um": seg_route["target_width_um"],
        "width_um": seg_route["width_um"],
        "num_bends": seg_route["num_bends"],
        "strategy": seg_route["strategy"],
        "backend": "tapered_hybrid",
    }


def _bend_points_for_pair(source: dict, target: dict, anchor: dict) -> list[Point]:
    center = _center(anchor)
    radius = float(anchor.get("radius_um", 0.0) or 0.0)
    if radius <= 0.0:
        radius = max(float(anchor.get("width_um", 0.0) or 0.0), float(anchor.get("height_um", 0.0) or 0.0), 2.0) / 2.0
    radius = max(radius, 2.0)
    sx, sy = [float(v) for v in (source.get("center_um") or [0.0, 0.0])]
    tx, ty = [float(v) for v in (target.get("center_um") or [0.0, 0.0])]
    if abs(tx - sx) >= abs(ty - sy):
        sign = 1.0 if tx >= sx else -1.0
        # BendAnchor means "turn inside this region", not merely "pass through
        # this center".  Enter along the dominant route axis and leave along the
        # perpendicular axis so the center itself is a real bend.
        side = 1.0 if center[1] >= (sy + ty) / 2.0 else -1.0
        return [[center[0] - sign * radius, center[1]], center, [center[0], center[1] + side * radius]]
    sign = 1.0 if ty >= sy else -1.0
    side = 1.0 if center[0] >= (sx + tx) / 2.0 else -1.0
    return [[center[0], center[1] - sign * radius], center, [center[0] + side * radius, center[1]]]


def _required_points_from_non_corridor_anchors(source: dict, target: dict, anchors: Sequence[dict]) -> list[Point]:
    points: list[Point] = []
    for anchor in anchors:
        kind = anchor.get("kind")
        if kind == "waypoint_region":
            points.append(_center(anchor))
        elif kind == "bend_region":
            points.extend(_bend_points_for_pair(source, target, anchor))
    return points


def _obstacle_aware_inner_points(
    source: dict,
    target: dict,
    required_points: Sequence[Sequence[float]],
    obstacle_bboxes: Sequence[Sequence[float]],
    *,
    angle_mode: str = "any",
    safe_distance_um: float = 0.0,
) -> list[Point]:
    if not obstacle_bboxes and angle_mode == "any":
        return [[float(p[0]), float(p[1])] for p in required_points]
    if angle_mode not in {"any", "manhattan", "fortyfive"}:
        raise ValueError("angle_mode must be one of: any, manhattan, fortyfive")

    from klink.routing.geom.constraints import port_launch_point
    from klink.routing.geom.geometric import route_points_geometric

    source_launch = port_launch_point(source)
    target_launch = port_launch_point(target)
    source_width = float(source.get("width_um", 1.0) or 1.0)
    target_width = float(target.get("width_um", 1.0) or 1.0)
    width = max(source_width, target_width)
    stops = [source_launch, *[[float(p[0]), float(p[1])] for p in required_points], target_launch]
    xs = [float(p[0]) for p in stops]
    ys = [float(p[1]) for p in stops]
    for bbox in obstacle_bboxes:
        xs.extend([float(bbox[0]), float(bbox[2])])
        ys.extend([float(bbox[1]), float(bbox[3])])
    guard_margin = max(max(xs) - min(xs), max(ys) - min(ys), width, 100.0) * 10.0
    min_x, max_x = min(xs) - guard_margin, max(xs) + guard_margin
    min_y, max_y = min(ys) - guard_margin, max(ys) + guard_margin
    routed: list[Point] = []
    previous_stop: Sequence[float] | None = None
    for start, end in zip(stops, stops[1:]):
        leg_obstacles = list(obstacle_bboxes)
        if previous_stop is not None:
            pdx = float(start[0]) - float(previous_stop[0])
            pdy = float(start[1]) - float(previous_stop[1])
            edx = float(end[0]) - float(start[0])
            edy = float(end[1]) - float(start[1])
            eps = 1e-9
            clearance = width + float(safe_distance_um) + eps
            if abs(pdy) >= abs(pdx) and abs(pdy) > eps and pdy * edy >= -eps:
                if pdy > 0:
                    leg_obstacles.append([min_x, min_y, max_x, float(start[1]) - clearance])
                else:
                    leg_obstacles.append([min_x, float(start[1]) + clearance, max_x, max_y])
            elif abs(pdx) > eps and pdx * edx >= -eps:
                if pdx > 0:
                    leg_obstacles.append([min_x, min_y, float(start[0]) - clearance, max_y])
                else:
                    leg_obstacles.append([float(start[0]) + clearance, min_y, max_x, max_y])
        leg = route_points_geometric(
            start,
            end,
            obstacle_bboxes=leg_obstacles,
            route_width_um=width,
            safe_distance_um=float(safe_distance_um),
            angle_mode="manhattan" if angle_mode == "any" else angle_mode,
        )
        if routed:
            routed.extend(leg[1:])
        else:
            routed.extend(leg)
        previous_stop = start
    if len(routed) <= 2:
        return [[float(p[0]), float(p[1])] for p in required_points]
    return routed[1:-1]


def _filter_obstacles_containing_points(
    obstacles: Sequence[Sequence[float]],
    points: Sequence[Sequence[float]],
    *,
    margin_um: float = 0.0,
) -> list[list[float]]:
    filtered = []
    for bbox in obstacles:
        x1, y1, x2, y2 = [float(v) for v in bbox]
        margin = float(margin_um)
        x1 -= margin
        y1 -= margin
        x2 += margin
        y2 += margin
        contains = False
        for point in points:
            x, y = float(point[0]), float(point[1])
            if x1 <= x <= x2 and y1 <= y <= y2:
                contains = True
                break
        if not contains:
            filtered.append([x1, y1, x2, y2])
    return filtered


def _points_bbox(points: Sequence[Sequence[float]]) -> list[float] | None:
    if not points:
        return None
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _route_geometry_bboxes(route: dict) -> list[dict]:
    from klink.routing.geom.geometry import expand_bbox

    bboxes: list[dict] = []
    for group_index, group in enumerate(route.get("groups", [])):
        if group.get("kind") == "polygon":
            bbox = _points_bbox(group.get("polygon_um") or [])
            if bbox is not None:
                bboxes.append({"kind": "polygon", "group": group_index, "bbox_um": bbox})
        for segment_index, segment in enumerate(group.get("segments", [])):
            bbox = _points_bbox(segment.get("points_um") or [])
            if bbox is None:
                continue
            half = float(segment.get("width_um", 1.0) or 1.0) / 2.0
            bboxes.append({
                "kind": "segment",
                "group": group_index,
                "segment": segment_index,
                "bbox_um": expand_bbox(bbox, half),
            })
        for patch_index, patch in enumerate(group.get("corner_patches", [])):
            bbox = _points_bbox(patch.get("polygon_um") or [])
            if bbox is not None:
                bboxes.append({
                    "kind": "corner_patch",
                    "group": group_index,
                    "patch": patch_index,
                    "bbox_um": bbox,
                })
    for patch_index, patch in enumerate(route.get("boundary_patches", [])):
        bbox = _points_bbox(patch.get("polygon_um") or [])
        if bbox is not None:
            bboxes.append({"kind": "boundary_patch", "patch": patch_index, "bbox_um": bbox})
    return bboxes


def _bbox_overlaps(a: Sequence[float], b: Sequence[float], eps: float = 1e-9) -> bool:
    return (
        max(float(a[0]), float(b[0])) < min(float(a[2]), float(b[2])) - eps
        and max(float(a[1]), float(b[1])) < min(float(a[3]), float(b[3])) - eps
    )


def _sibling_geometry_overlaps(routes: Sequence[dict]) -> list[dict]:
    overlaps = []
    route_bboxes = [_route_geometry_bboxes(route) for route in routes]
    for i in range(len(routes)):
        for j in range(i + 1, len(routes)):
            for a in route_bboxes[i]:
                for b in route_bboxes[j]:
                    if _bbox_overlaps(a["bbox_um"], b["bbox_um"]):
                        overlaps.append({
                            "route_a": routes[i].get("route_id", routes[i].get("net", "")),
                            "route_b": routes[j].get("route_id", routes[j].get("net", "")),
                            "shape_a": a,
                            "shape_b": b,
                        })
                        break
                if overlaps and overlaps[-1]["route_a"] == routes[i].get("route_id", routes[i].get("net", "")) and overlaps[-1]["route_b"] == routes[j].get("route_id", routes[j].get("net", "")):
                    break
    return overlaps


def _freeze_route_bboxes(route: dict, *, spacing_um: float = 0.0) -> list[list[float]]:
    from klink.routing.geom.geometry import expand_bbox

    return [
        expand_bbox(item["bbox_um"], float(spacing_um))
        for item in _route_geometry_bboxes(route)
    ]


def _pair_ports_by_net_tokens(ports: Sequence[dict]) -> list[dict]:
    normal_ports = [p for p in ports if not _is_candidate_sink(p)]
    candidate_sinks = [p for p in ports if _is_candidate_sink(p)]
    by_net: dict[str, list[dict]] = {}
    for port in normal_ports:
        for net in _net_tokens(port.get("net")):
            by_net.setdefault(net, []).append(port)

    pairs: list[dict] = []
    assignment_demands: list[tuple[str, dict]] = []
    for net in sorted(by_net, key=_net_sort_key):
        members = by_net[net]
        if len(members) == 1:
            assignment_demands.append((net, members[0]))
            continue
        if len(members) != 2:
            continue
        members = sorted(
            members,
            key=lambda p: (
                float((p.get("center_um") or [0.0, 0.0])[0]),
                float((p.get("center_um") or [0.0, 0.0])[1]),
                str(p.get("name") or ""),
            ),
        )
        pairs.append({
            "net": net,
            "source": members[0],
            "target": members[1],
            "route_layer": _infer_pair_route_layer(net, members[0], members[1]),
        })
    if assignment_demands and candidate_sinks:
        assigned = _assign_candidate_sinks([demand for _net, demand in assignment_demands], candidate_sinks)
        net_by_demand_name = {
            str(demand.get("name") or ""): net
            for net, demand in assignment_demands
        }
        for demand, candidate, method in assigned:
            net = net_by_demand_name[str(demand.get("name") or "")]
            pairs.append({
                "net": net,
                "source": demand,
                "target": candidate,
                "route_layer": _infer_pair_route_layer(net, demand, candidate),
                "assignment": method,
            })
    pairs.sort(key=lambda p: _net_sort_key(str(p.get("net") or "")))
    return pairs


def _unsupported_multi_port_net_errors(ports: Sequence[dict]) -> list[dict]:
    normal_ports = [p for p in ports if not _is_candidate_sink(p)]
    by_net: dict[str, list[dict]] = {}
    for port in normal_ports:
        for net in _net_tokens(port.get("net")):
            by_net.setdefault(net, []).append(port)

    errors = []
    for net in sorted(by_net, key=_net_sort_key):
        members = by_net[net]
        if len(members) <= 2:
            continue
        errors.append({
            "type": "unsupported_multi_port_net",
            "net": net,
            "port_count": len(members),
            "ports": [str(p.get("name") or p.get("id") or "") for p in members],
            "message": f"unsupported multi-port net {net}: {len(members)} ports; bus/Steiner routing is not implemented yet",
        })
    return errors


# Public aliases: net pairing and multi-port-net validation are part of the
# supported planning API for examples and external callers; the underscore
# names remain for intra-package use.
pair_ports_by_net_tokens = _pair_ports_by_net_tokens
unsupported_multi_port_net_errors = _unsupported_multi_port_net_errors


def _infer_pair_route_layer(net: str, source: dict, target: dict) -> str:
    source_layers = source.get("target_layers_by_net") or {}
    target_layers = target.get("target_layers_by_net") or {}
    if isinstance(source_layers, dict) and source_layers.get(net):
        return str(source_layers[net])
    if isinstance(target_layers, dict) and target_layers.get(net):
        return str(target_layers[net])

    source_tokens = _net_tokens(source.get("net"))
    target_tokens = _net_tokens(target.get("net"))
    source_layer = str(source.get("target_layer") or "")
    target_layer = str(target.get("target_layer") or "")
    if source_layer and source_layer == target_layer:
        return source_layer

    # Generic convention for a multi-net physical port: if a token is listed
    # first on the multi-net side, it may use the opposite single-net layer;
    # later tokens use the multi-net port's own target layer.  Explicit
    # target_layers_by_net wins whenever available.
    if len(source_tokens) > 1 and net in source_tokens:
        idx = source_tokens.index(net)
        return target_layer if idx == 0 and target_layer else source_layer
    if len(target_tokens) > 1 and net in target_tokens:
        idx = target_tokens.index(net)
        return source_layer if idx == 0 and source_layer else target_layer

    return source_layer or target_layer or "10/0"


def route_tapered_hybrid_many(
    pairs: Sequence[dict],
    *,
    anchors: Sequence[dict] | None = None,
    spacing_um: float = 4.0,
    strategy: str | TaperStrategy = "uniform",
    angle_mode: str = "any",
    safe_distance_um: float = 0.0,
    preserve_order: bool = True,
    validate_sibling_overlap: bool = True,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
) -> dict:
    """Plan and build a same-layer group of tapered hybrid routes.

    This is the multi-net planner boundary.  Callers provide route pairs and
    semantic anchors; the planner assigns corridor lanes and validates sibling
    overlaps before writeback.  Single-route geometry is still delegated to
    :func:`route_tapered_hybrid`.

    Pair schema::

        {
          "net": "sig0",
          "source": port_dict,
          "target": port_dict,
          "route_layer": "12/0"   # optional, copied to result
        }

    Anchor semantics:

    - ``corridor``: all matching nets are lane-split along the corridor path.
    - ``waypoint_region``: matching routes include the anchor center.
    - ``bend_region``: matching routes include the anchor center as a bend.
    """
    normalized_pairs: list[dict] = []
    for idx, pair in enumerate(pairs):
        source = dict(pair.get("source") or {})
        target = dict(pair.get("target") or {})
        if not source or not target:
            raise ValueError("each pair must provide source and target")
        net = _route_net(pair, source, target)
        normalized_pairs.append({
            **dict(pair),
            "source": source,
            "target": target,
            "net": net,
            "_input_index": idx,
        })
    anchors = list(anchors or [])
    obstacle_bboxes = [list(map(float, bbox)) for bbox in (obstacle_bboxes or [])]
    matching_corridors = [
        a for a in anchors
        if a.get("kind") == "corridor"
        and any(_anchor_applies(a, p["net"]) for p in normalized_pairs)
    ]
    if not matching_corridors:
        anchors.extend(_auto_corridors_for_pairs(normalized_pairs))

    routes_by_index: dict[int, dict] = {}
    lane_reports: list[dict] = []
    planning_errors: list[dict] = []

    # Corridor anchors own lane assignment for their matching nets.  If several
    # corridors match a route, use them in priority/id order as sequential
    # required path sections.
    assigned: set[int] = set()
    corridor_anchors = [a for a in anchors if a.get("kind") == "corridor"]
    corridor_anchors = sorted(corridor_anchors, key=lambda a: (int(a.get("priority") or 0), str(a.get("id") or "")))
    for corridor in corridor_anchors:
        members = [p for p in normalized_pairs if p["_input_index"] not in assigned and _anchor_applies(corridor, p["net"])]
        if not members:
            continue
        ordered = _ordered_pairs_for_lane_assignment(members) if preserve_order else members
        max_width = max(
            max(float(p["source"].get("width_um", 1.0) or 1.0), float(p["target"].get("width_um", 1.0) or 1.0))
            for p in ordered
        )
        # Corridor lanes need more than straight parallel clearance.  Hybrid
        # routes insert miter/corner patches at lane entry and exit; if pitch
        # is only width + spacing, those patches can intrude into the adjacent
        # lane even when centerline segments appear separated.
        pitch = max_width + float(spacing_um) + max_width / 2.0
        offsets = _lane_offsets(len(ordered), pitch)
        base_path = _corridor_path(corridor)
        offsets = _ordered_offsets_for_corridor(base_path, ordered, offsets)
        capacity_issue = _corridor_capacity_issue(corridor, offsets, max_width)
        if capacity_issue is not None:
            planning_errors.append(capacity_issue)
            for pair in ordered:
                assigned.add(pair["_input_index"])
            lane_reports.append({
                "corridor_id": corridor.get("id"),
                "net_count": len(ordered),
                "pitch_um": pitch,
                "offsets_um": offsets,
                "capacity_ok": False,
                "capacity_issue": capacity_issue,
            })
            continue
        nx, ny = _lane_normal(base_path)
        frozen_lane_bboxes: list[list[float]] = []
        for pair, offset in zip(ordered, offsets):
            matching_anchors = [a for a in anchors if _anchor_applies(a, pair["net"])]
            other_points = _required_points_from_non_corridor_anchors(pair["source"], pair["target"], matching_anchors)
            lane_points = _lane_points_for_corridor(
                pair["source"],
                pair["target"],
                base_path,
                nx,
                ny,
                offset,
                member_count=len(ordered),
            )
            from klink.routing.geom.constraints import port_launch_point

            source_launch = port_launch_point(pair["source"])
            target_launch = port_launch_point(pair["target"])
            route_width = max(
                float(pair["source"].get("width_um", 1.0) or 1.0),
                float(pair["target"].get("width_um", 1.0) or 1.0),
            )
            lane_points = _slide_corridor_gates(
                lane_points,
                base_path,
                nx,
                ny,
                offset,
                obstacle_bboxes,
                route_width_um=route_width,
                safe_distance_um=float(safe_distance_um),
                corridor_id=corridor.get("id"),
            )
            lane_obstacles = _filter_obstacles_containing_points(
                [*obstacle_bboxes, *frozen_lane_bboxes],
                [source_launch, target_launch, *lane_points],
                margin_um=route_width / 2.0 + float(safe_distance_um),
            )
            try:
                inner_points = _obstacle_aware_inner_points(
                    pair["source"],
                    pair["target"],
                    [*other_points, *lane_points],
                    lane_obstacles,
                    angle_mode=angle_mode,
                    safe_distance_um=safe_distance_um,
                )
            except ValueError:
                inner_points = _obstacle_aware_inner_points(
                    pair["source"],
                    pair["target"],
                    [*other_points, *lane_points],
                    obstacle_bboxes,
                    angle_mode=angle_mode,
                    safe_distance_um=safe_distance_um,
                )
            route = route_tapered_hybrid(
                pair["source"],
                pair["target"],
                inner_points,
                strategy=strategy,
            )
            route.update({
                "route_id": pair.get("route_id") or f"route_{pair['net'] or pair['_input_index']}",
                "net": pair["net"],
                "source": _route_name(pair["source"]),
                "target": _route_name(pair["target"]),
                "route_layer": pair.get("route_layer"),
                "anchors": [a.get("id") for a in matching_anchors if a.get("id")],
                "lane_offset_um": offset,
                "corridor_id": corridor.get("id"),
            })
            routes_by_index[pair["_input_index"]] = route
            assigned.add(pair["_input_index"])
            frozen_lane_bboxes.extend(_freeze_route_bboxes(route, spacing_um=spacing_um))
        lane_reports.append({
            "corridor_id": corridor.get("id"),
            "net_count": len(ordered),
            "pitch_um": pitch,
            "offsets_um": offsets,
            "capacity_ok": True,
        })

    # Routes with no matching corridor still honor waypoint/bend anchors.
    for pair in normalized_pairs:
        if pair["_input_index"] in assigned:
            continue
        matching_anchors = [a for a in anchors if _anchor_applies(a, pair["net"])]
        inner_points = _required_points_from_non_corridor_anchors(pair["source"], pair["target"], matching_anchors)
        inner_points = _obstacle_aware_inner_points(
            pair["source"],
            pair["target"],
            inner_points,
            obstacle_bboxes,
            angle_mode=angle_mode,
            safe_distance_um=safe_distance_um,
        )
        route = route_tapered_hybrid(pair["source"], pair["target"], inner_points, strategy=strategy)
        route.update({
            "route_id": pair.get("route_id") or f"route_{pair['net'] or pair['_input_index']}",
            "net": pair["net"],
            "source": _route_name(pair["source"]),
            "target": _route_name(pair["target"]),
            "route_layer": pair.get("route_layer"),
            "anchors": [a.get("id") for a in matching_anchors if a.get("id")],
            "lane_offset_um": 0.0,
            "corridor_id": None,
        })
        routes_by_index[pair["_input_index"]] = route

    routes = [routes_by_index[i] for i in sorted(routes_by_index)]
    overlaps = []
    if validate_sibling_overlap:
        overlaps = _sibling_envelope_overlaps(routes)
        geometry_overlaps = _sibling_geometry_overlaps(routes)
        if geometry_overlaps:
            overlaps.extend(geometry_overlaps)
    obstacle_hits: list[dict] = []
    if obstacle_bboxes:
        from klink.routing.geom.geometry import route_hits_bboxes

        for route in routes:
            width = max(float(route.get("source_width_um", 1.0) or 1.0), float(route.get("target_width_um", 1.0) or 1.0))
            for hit in route_hits_bboxes(route.get("points_um", []), obstacle_bboxes, width):
                obstacle_hits.append({
                    **hit,
                    "route_id": route.get("route_id"),
                    "net": route.get("net"),
                })
    error_messages = []
    if planning_errors:
        error_messages.append("corridor capacity exceeded")
    if overlaps:
        error_messages.append("same-layer sibling route overlap")
    if obstacle_hits:
        error_messages.append("route hits obstacle")
    return {
        "ok": not overlaps and not planning_errors and not obstacle_hits,
        "backend": "tapered_hybrid_many",
        "routes": routes,
        "route_count": len(routes),
        "angle_mode": angle_mode,
        "safe_distance_um": float(safe_distance_um),
        "lane_reports": lane_reports,
        "sibling_overlaps": overlaps,
        "obstacle_hits": obstacle_hits,
        "planning_errors": planning_errors,
        "errors": error_messages,
    }


def route_tapered_hybrid_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    spacing_um: float = 20.0,
    strategy: str | TaperStrategy = "uniform",
    angle_mode: str = "any",
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route all two-port net-token pairs in a cell.

    This is the public orchestration boundary for examples and agents.  The
    caller names a cell; the router owns port-token pairing, multi-net layer
    inference, anchor filtering, same-layer grouping, lane assignment, overlap
    validation, and writeback.
    """
    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": anchor_layer, "sort": "id"}).get("anchors", [])
    from klink.routing.geom.planner import collect_obstacle_bboxes

    obstacle_layers = list(obstacle_layers or [])
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)
    unsupported_net_errors = _unsupported_multi_port_net_errors(ports)
    pairs = _pair_ports_by_net_tokens(ports)

    by_layer: dict[str, list[dict]] = {}
    for pair in pairs:
        by_layer.setdefault(str(pair.get("route_layer") or "10/0"), []).append(pair)

    groups: list[dict] = []
    ok = not unsupported_net_errors
    for route_layer in sorted(by_layer):
        planned = route_tapered_hybrid_many(
            by_layer[route_layer],
            anchors=anchors,
            spacing_um=spacing_um,
            strategy=strategy,
            angle_mode=angle_mode,
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
        "cell": cell,
        "port_count": len(ports),
        "anchor_count": len(anchors),
        "pair_count": len(pairs),
        "angle_mode": angle_mode,
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "planning_errors": unsupported_net_errors,
        "errors": [e["message"] for e in unsupported_net_errors],
        "groups": groups,
    }


def _hybrid_route_items(route: dict, *, layer: int, datatype: int) -> tuple[list[dict], dict]:
    """Convert one planned hybrid route into batched shape.insert_many items."""
    groups = route.get("groups", [])
    boundary_patches = route.get("boundary_patches", [])
    stats = {"paths": 0, "patches": 0, "polygons": 0}
    items: list[dict] = []
    written_bends: set[int] = set()

    for g in groups:
        if g["kind"] == "polygon":
            poly = g.get("polygon_um") or []
            if len(poly) >= 3:
                items.append({
                    "kind": "polygon",
                    "layer": layer,
                    "datatype": datatype,
                    "points_um": poly,
                })
                stats["polygons"] += 1
        elif g["kind"] == "path":
            for seg in g.get("segments", []):
                pts = seg.get("points_um") or []
                if len(pts) < 2:
                    continue
                w = float(seg.get("width_um", 1.0))
                begin_ext = w / 2.0 if seg.get("is_first", False) else 0.0
                end_ext = w / 2.0 if seg.get("is_last", False) else 0.0
                items.append({
                    "kind": "path",
                    "layer": layer,
                    "datatype": datatype,
                    "points_um": pts,
                    "width_um": w,
                    "begin_ext_um": begin_ext,
                    "end_ext_um": end_ext,
                    "round_ends": False,
                })
                stats["paths"] += 1
            for patch in g.get("corner_patches", []):
                bi = patch.get("bend_index", -1)
                if bi in written_bends:
                    continue
                written_bends.add(bi)
                poly = patch.get("polygon_um") or []
                if len(poly) >= 3:
                    items.append({
                        "kind": "polygon",
                        "layer": layer,
                        "datatype": datatype,
                        "points_um": poly,
                    })
                    stats["patches"] += 1

    for bp in boundary_patches:
        bi = bp.get("bend_index", -1)
        if bi in written_bends:
            continue
        written_bends.add(bi)
        poly = bp.get("polygon_um") or []
        if len(poly) >= 3:
            items.append({
                "kind": "polygon",
                "layer": layer,
                "datatype": datatype,
                "points_um": poly,
            })
            stats["patches"] += 1

    return items, stats


def commit_tapered_hybrid_many(
    client,
    cell: str,
    planned: dict | Sequence[dict],
    *,
    route_layer: str | None = None,
    clear: bool = True,
) -> dict:
    """Write planned many-route hybrid results with one batched shape RPC."""
    from klink.routing.geom.geometry import parse_layer

    routes = list(planned.get("routes", []) if isinstance(planned, dict) else planned)
    layer_specs: dict[str, tuple[int, int]] = {}
    for route in routes:
        layer_name = route_layer or route.get("route_layer") or "10/0"
        if layer_name not in layer_specs:
            layer_specs[layer_name] = parse_layer(layer_name)
            layer, datatype = layer_specs[layer_name]
            client.layer_ensure(layer, datatype, name="KLINK_ROUTES")

    deleted_by_layer: dict[str, int] = {}
    if clear:
        for layer_name in layer_specs:
            deleted_by_layer[layer_name] = int(
                client.shape_delete(
                    cell, layers=[layer_name], kinds=["paths", "polygons"], limit=10000,
                ).get("deleted", 0)
            )

    totals = {"paths": 0, "patches": 0, "polygons": 0, "deleted": sum(deleted_by_layer.values())}
    all_items: list[dict] = []
    per_route = []
    reported_delete_layers: set[str] = set()

    for route in routes:
        layer_name = route_layer or route.get("route_layer") or "10/0"
        layer, datatype = layer_specs[layer_name]
        items, stats = _hybrid_route_items(route, layer=layer, datatype=datatype)
        all_items.extend(items)
        for key in ("paths", "patches", "polygons"):
            totals[key] += int(stats.get(key, 0) or 0)
        route_deleted = 0
        if layer_name not in reported_delete_layers:
            route_deleted = deleted_by_layer.get(layer_name, 0)
            reported_delete_layers.add(layer_name)
        per_route.append({
            "cell": cell,
            "route_layer": layer_name,
            "deleted": route_deleted,
            "mode": "hybrid",
            **stats,
            "net": route.get("net"),
            "route_id": route.get("route_id"),
        })

    insert_report = client.shape_insert_many(cell, all_items) if all_items else {"inserted": 0}
    return {
        "cell": cell,
        "mode": "hybrid_many",
        "writeback": "batch",
        "route_count": len(routes),
        "inserted": int(insert_report.get("inserted", 0) or 0),
        **totals,
        "routes": per_route,
    }


def commit_tapered_hybrid(
    client,
    cell: str,
    route: dict,
    *,
    route_layer: str = "10/0",
    clear: bool = True,
) -> dict:
    """Write tapered hybrid route: pulled-back paths + miter patches + polygon fallback.

    All shapes on the same GDS layer — overlap merges seamlessly.
    """
    from klink.routing.geom.geometry import parse_layer

    layer, datatype = parse_layer(route_layer)
    client.layer_ensure(layer, datatype, name="KLINK_ROUTES")

    deleted = 0
    if clear:
        deleted = int(
            client.shape_delete(
                cell, layers=[route_layer], kinds=["paths", "polygons"], limit=10000,
            ).get("deleted", 0)
        )

    groups = route.get("groups", [])
    boundary_patches = route.get("boundary_patches", [])
    stats = {"paths": 0, "patches": 0, "polygons": 0}
    written_bends: set[int] = set()

    for g in groups:
        if g["kind"] == "polygon":
            poly = g.get("polygon_um") or []
            if len(poly) >= 3:
                client.shape_insert_polygon(cell, layer=layer, datatype=datatype, points_um=poly)
                stats["polygons"] += 1
        elif g["kind"] == "path":
            for seg in g.get("segments", []):
                pts = seg.get("points_um") or []
                if len(pts) < 2:
                    continue
                w = float(seg.get("width_um", 1.0))
                is_first = seg.get("is_first", False)
                is_last = seg.get("is_last", False)
                begin_ext = w / 2.0 if is_first else 0.0
                end_ext = w / 2.0 if is_last else 0.0
                client.shape_insert_path(cell, layer=layer, datatype=datatype,
                                          points_um=pts, width_um=w,
                                          begin_ext_um=begin_ext, end_ext_um=end_ext,
                                          round_ends=False)
                stats["paths"] += 1
            for patch in g.get("corner_patches", []):
                bi = patch.get("bend_index", -1)
                if bi in written_bends:
                    continue
                written_bends.add(bi)
                poly = patch.get("polygon_um") or []
                if len(poly) >= 3:
                    client.shape_insert_polygon(cell, layer=layer, datatype=datatype, points_um=poly)
                    stats["patches"] += 1

    for bp in boundary_patches:
        bi = bp.get("bend_index", -1)
        if bi in written_bends:
            continue
        written_bends.add(bi)
        poly = bp.get("polygon_um") or []
        if len(poly) >= 3:
            client.shape_insert_polygon(cell, layer=layer, datatype=datatype, points_um=poly)
            stats["patches"] += 1

    return {"cell": cell, "route_layer": route_layer, "deleted": deleted, "mode": "hybrid", **stats}
