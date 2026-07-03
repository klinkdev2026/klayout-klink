"""Small geometry helpers for routing MVP validation and planning."""

from __future__ import annotations

from itertools import combinations
from typing import Sequence


Point = list[float]
Segment = tuple[Point, Point]


def parse_layer(layer: str) -> tuple[int, int]:
    left, right = str(layer).split("/", 1)
    return int(left), int(right)


def parse_relative_path(center: Sequence[float], path_points: str) -> list[Point]:
    points: list[Point] = []
    cx, cy = float(center[0]), float(center[1])
    for item in str(path_points or "").split(";"):
        item = item.strip()
        if not item:
            continue
        x_s, y_s = item.split(",", 1)
        points.append([cx + float(x_s), cy + float(y_s)])
    return points


def route_segments(points: Sequence[Sequence[float]]) -> list[Segment]:
    pts = [[float(p[0]), float(p[1])] for p in points]
    return list(zip(pts, pts[1:]))


def _orientation(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    return (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1])) - (
        float(b[1]) - float(a[1])
    ) * (float(c[0]) - float(a[0]))


def _same_point(a: Sequence[float], b: Sequence[float], eps: float = 1e-9) -> bool:
    return abs(float(a[0]) - float(b[0])) <= eps and abs(float(a[1]) - float(b[1])) <= eps


def _on_segment(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> bool:
    return (
        min(float(a[0]), float(c[0])) <= float(b[0]) <= max(float(a[0]), float(c[0]))
        and min(float(a[1]), float(c[1])) <= float(b[1]) <= max(float(a[1]), float(c[1]))
    )


def segments_intersect(seg_a: Segment, seg_b: Segment, *, allow_shared_endpoint: bool = True) -> bool:
    a, b = seg_a
    c, d = seg_b
    shared = _same_point(a, c) or _same_point(a, d) or _same_point(b, c) or _same_point(b, d)
    if shared and allow_shared_endpoint:
        return False
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 == 0 and _on_segment(a, c, b):
        return True
    if o2 == 0 and _on_segment(a, d, b):
        return True
    if o3 == 0 and _on_segment(c, a, d):
        return True
    if o4 == 0 and _on_segment(c, b, d):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def crossing_pairs(routes: Sequence[dict]) -> list[dict]:
    crossings: list[dict] = []
    for route_a, route_b in combinations(routes, 2):
        for seg_a in route_segments(route_a.get("points_um", [])):
            for seg_b in route_segments(route_b.get("points_um", [])):
                if segments_intersect(seg_a, seg_b, allow_shared_endpoint=False):
                    crossings.append(
                        {
                            "route_a": route_a.get("route_id", ""),
                            "route_b": route_b.get("route_id", ""),
                            "segment_a": seg_a,
                            "segment_b": seg_b,
                        }
                    )
    return crossings


def self_crossings(points: Sequence[Sequence[float]]) -> list[dict]:
    """Return non-adjacent self intersections/overlaps in one route."""
    crossings: list[dict] = []
    segments = route_segments(points)
    for i, seg_a in enumerate(segments):
        for j in range(i + 1, len(segments)):
            if j == i + 1:
                continue
            if i == 0 and j == len(segments) - 1:
                continue
            seg_b = segments[j]
            if segments_intersect(seg_a, seg_b, allow_shared_endpoint=True):
                crossings.append({"segment_i": i, "segment_j": j, "segment_a": seg_a, "segment_b": seg_b})
    return crossings


def expand_bbox(bbox: Sequence[float], margin: float) -> list[float]:
    return [
        float(bbox[0]) - float(margin),
        float(bbox[1]) - float(margin),
        float(bbox[2]) + float(margin),
        float(bbox[3]) + float(margin),
    ]


def _point_in_rect(point: Sequence[float], rect: Sequence[float]) -> bool:
    return (
        float(rect[0]) <= float(point[0]) <= float(rect[2])
        and float(rect[1]) <= float(point[1]) <= float(rect[3])
    )


def segment_intersects_rect(segment: Segment, rect: Sequence[float]) -> bool:
    a, b = segment
    xmin = min(float(a[0]), float(b[0]))
    xmax = max(float(a[0]), float(b[0]))
    ymin = min(float(a[1]), float(b[1]))
    ymax = max(float(a[1]), float(b[1]))
    if xmax < rect[0] or xmin > rect[2] or ymax < rect[1] or ymin > rect[3]:
        return False
    if _point_in_rect(a, rect) or _point_in_rect(b, rect):
        return True
    corners = [
        [float(rect[0]), float(rect[1])],
        [float(rect[2]), float(rect[1])],
        [float(rect[2]), float(rect[3])],
        [float(rect[0]), float(rect[3])],
    ]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    return any(segments_intersect(segment, edge, allow_shared_endpoint=False) for edge in edges)


def route_hits_bboxes(points: Sequence[Sequence[float]], bboxes: Sequence[Sequence[float]], width_um: float) -> list[dict]:
    hits: list[dict] = []
    margin = float(width_um) / 2.0
    for bbox in bboxes:
        expanded = expand_bbox(bbox, margin)
        for segment in route_segments(points):
            if segment_intersects_rect(segment, expanded):
                hits.append({"bbox_um": list(bbox), "expanded_bbox_um": expanded, "segment": segment})
    return hits


def subtract_port_notch(
    bbox: Sequence[float],
    port_xy: Sequence[float],
    *,
    notch_halfwidth: float,
    notch_depth: float,
    edge_eps: float = 0.5,
) -> list[list[float]] | None:
    """Split a keep-out bbox into pieces that leave an entry NOTCH at a port.

    A route may only enter a device's footprint through its port face: the
    notch is a ``2*notch_halfwidth`` gap along the bbox edge the port sits
    on, ``notch_depth`` deep, so planners/checkers using the returned pieces
    allow the port approach while still blocking every other graze or
    crossing of the box. Returns None when the port is NOT on the bbox
    boundary (within ``edge_eps``) — e.g. a tilted placement whose
    axis-aligned envelope swallowed the port; callers fall back to their
    legacy exemption then.
    """
    x0, y0, x1, y1 = (float(v) for v in bbox)
    px, py = float(port_xy[0]), float(port_xy[1])
    if not (x0 - edge_eps <= px <= x1 + edge_eps
            and y0 - edge_eps <= py <= y1 + edge_eps):
        return None  # port nowhere near this box
    edge = None
    for candidate, dist in (("S", abs(py - y0)), ("N", abs(py - y1)),
                            ("W", abs(px - x0)), ("E", abs(px - x1))):
        if dist <= edge_eps and (edge is None or dist < edge[1]):
            edge = (candidate, dist)
    if edge is None:
        return None  # port strictly inside (inflated envelope)
    side = edge[0]
    w = float(notch_halfwidth)
    d = float(notch_depth)
    pieces: list[list[float]] = []
    if side in ("S", "N"):
        lo, hi = px - w, px + w
        if lo > x0:
            pieces.append([x0, y0, lo, y1])
        if hi < x1:
            pieces.append([hi, y0, x1, y1])
        if side == "S" and y0 + d < y1:
            pieces.append([max(x0, lo), y0 + d, min(x1, hi), y1])
        if side == "N" and y1 - d > y0:
            pieces.append([max(x0, lo), y0, min(x1, hi), y1 - d])
    else:
        lo, hi = py - w, py + w
        if lo > y0:
            pieces.append([x0, y0, x1, lo])
        if hi < y1:
            pieces.append([x0, hi, x1, y1])
        if side == "W" and x0 + d < x1:
            pieces.append([x0 + d, max(y0, lo), x1, min(y1, hi)])
        if side == "E" and x1 - d > x0:
            pieces.append([x0, max(y0, lo), x1 - d, min(y1, hi)])
    return [p for p in pieces if p[0] < p[2] and p[1] < p[3]]
