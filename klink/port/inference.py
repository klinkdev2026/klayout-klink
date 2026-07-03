"""Pure geometry helpers for PortResource inference.

The KLayout plugin is responsible for extracting plain points/edges from pya
objects and for writing PCell instances. The geometric decisions live here so
they can be tested without KLayout and reused by future frontends.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

NEAR_EDGE_DISTANCE_RATIO = 0.75
NEAR_EDGE_MIN_DBU = 5.0
NEAR_EDGE_ANGLE_TOLERANCE_DEG = 35.0
NEAR_EDGE_CLUSTER_TOLERANCE_DEG = 12.0


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two directed angles."""
    diff = abs((a % 360.0) - (b % 360.0))
    return min(diff, 360.0 - diff)


def undirected_angle_diff(a: float, b: float) -> float:
    """Smallest difference between two line angles where 0 == 180."""
    diff = abs((a % 180.0) - (b % 180.0))
    return min(diff, 180.0 - diff)


def snap_to_angle_grid(angle: float, step: float = 45.0,
                       tolerance: Optional[float] = None) -> float:
    """Snap angle to a regular angular grid.

    When tolerance is None, always snap to the nearest grid angle.
    """
    a = angle % 360.0
    target = (round(a / step) * step) % 360.0
    if tolerance is None:
        return target
    if angle_diff(a, target) <= tolerance:
        return target
    return a


def _unique_points(points: Iterable[Sequence[float]]) -> list[list[float]]:
    unique: list[list[float]] = []
    for p in points:
        q = [float(p[0]), float(p[1])]
        if q not in unique:
            unique.append(q)
    return unique


def _triangle_vertex_angles(pts: list[list[float]]) -> Optional[list[float]]:
    angles = []
    for i, p in enumerate(pts):
        p0 = pts[(i - 1) % 3]
        p1 = pts[(i + 1) % 3]
        v0 = (p0[0] - p[0], p0[1] - p[1])
        v1 = (p1[0] - p[0], p1[1] - p[1])
        n0 = math.hypot(v0[0], v0[1])
        n1 = math.hypot(v1[0], v1[1])
        if n0 < 1e-9 or n1 < 1e-9:
            return None
        dot = (v0[0] * v1[0] + v0[1] * v1[1]) / (n0 * n1)
        dot = max(-1.0, min(1.0, dot))
        angles.append(math.degrees(math.acos(dot)))
    return angles


def _triangle_tip_index(pts: list[list[float]]) -> Optional[int]:
    angles = _triangle_vertex_angles(pts)
    if angles is None:
        return None
    max_angle = max(angles)
    if max_angle >= 100.0:
        return angles.index(max_angle)
    return angles.index(min(angles))


def triangle_base_geometry(points: Iterable[Sequence[float]]) -> Optional[dict]:
    """Return base midpoint/angle for a triangular hand-drawn port marker."""
    pts = _unique_points(points)
    if len(pts) != 3:
        return None

    tip_idx = _triangle_tip_index(pts)
    if tip_idx is None:
        return None
    base = [p for i, p in enumerate(pts) if i != tip_idx]
    bx = (base[0][0] + base[1][0]) / 2.0
    by = (base[0][1] + base[1][1]) / 2.0
    base_angle = math.degrees(
        math.atan2(base[1][1] - base[0][1], base[1][0] - base[0][0])
    ) % 180.0
    width_dbu = math.hypot(base[0][0] - base[1][0],
                           base[0][1] - base[1][1])
    return {
        "mid": (bx, by),
        "angle": base_angle,
        "width_dbu": width_dbu,
        "tip": (pts[tip_idx][0], pts[tip_idx][1]),
    }


def _infer_triangle(points: Iterable[Sequence[float]], dbu: float,
                    fallback: float) -> tuple[float, float]:
    pts = _unique_points(points)
    tri = triangle_base_geometry(pts)
    if tri is None:
        return fallback, 0.0

    bx, by = tri["mid"]
    tx, ty = tri["tip"]
    dx = tx - bx
    dy = ty - by
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return fallback, 0.0

    orientation = math.degrees(math.atan2(dy, dx)) % 360.0
    orientation = snap_to_angle_grid(orientation, step=45.0)
    return orientation, float(tri["width_dbu"]) * dbu


def infer_polygon_points(points: Iterable[Sequence[float]], dbu: float,
                         direction_guess: str = "long_edge",
                         fallback_orientation: float = 0.0) -> tuple[float, float]:
    """Infer (orientation_deg, width_um) from polygon hull points."""
    unique = _unique_points(points)
    if len(unique) < 3:
        return fallback_orientation, 0.0
    if len(unique) == 3:
        return _infer_triangle(unique, dbu, fallback_orientation)
    return _infer_pca(unique, dbu, direction_guess, fallback_orientation)


def _infer_pca(pts: list[list[float]], dbu: float, direction_guess: str,
               fallback: float) -> tuple[float, float]:
    if len(pts) < 2:
        return fallback, 0.0

    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cxx = sum((p[0] - cx) ** 2 for p in pts) / n
    cyy = sum((p[1] - cy) ** 2 for p in pts) / n
    cxy = sum((p[0] - cx) * (p[1] - cy) for p in pts) / n

    trace = cxx + cyy
    det = cxx * cyy - cxy * cxy
    disc = math.sqrt(max(trace * trace - 4.0 * det, 0.0))
    eig1 = (trace + disc) / 2.0
    eig2 = (trace - disc) / 2.0
    if eig1 < 1e-12:
        return fallback, 0.0

    if abs(cxy) > 1e-12:
        vx = eig1 - cyy
        vy = cxy
    elif abs(cxx - eig1) < 1e-12:
        vx, vy = 1.0, 0.0
    else:
        vx, vy = 0.0, 1.0

    orientation = math.degrees(math.atan2(vy, vx)) % 360.0
    if direction_guess == "short_edge":
        orientation = (orientation + 90.0) % 360.0
    orientation = _snap_to_cardinal(orientation)

    width_um = 2.0 * math.sqrt(max(eig2, 0.0)) * dbu
    if width_um < 0.1:
        width_um = 5.0
    return orientation, width_um


def _snap_to_cardinal(angle: float, tolerance: float = 15.0) -> float:
    for target in (0.0, 90.0, 180.0, 270.0, 360.0):
        if angle_diff(angle, target) <= tolerance:
            return target % 360.0
    return angle % 360.0


def infer_box_direction(width_dbu: float, height_dbu: float, dbu: float,
                        direction_guess: str = "long_edge",
                        fallback_orientation: float = 0.0) -> tuple[float, float]:
    """Infer orientation/width from a box's dbu dimensions."""
    if width_dbu < 1 and height_dbu < 1:
        return fallback_orientation, 5.0

    w_um = width_dbu * dbu
    h_um = height_dbu * dbu
    aspect = width_dbu / max(height_dbu, 1)
    if aspect < 1.0:
        aspect = 1.0 / aspect
    if aspect < 1.2:
        return fallback_orientation, (w_um + h_um) / 2.0

    if direction_guess == "short_edge":
        orientation = 90.0 if width_dbu < height_dbu else 0.0
    else:
        orientation = 0.0 if width_dbu > height_dbu else 90.0
    return orientation, min(w_um, h_um)


def infer_path_direction(points: Iterable[Sequence[float]], width_dbu: float,
                         dbu: float,
                         fallback_orientation: float = 0.0) -> tuple[float, float]:
    pts = _unique_points(points)
    if len(pts) >= 2:
        dx = pts[-1][0] - pts[0][0]
        dy = pts[-1][1] - pts[0][1]
        orientation = math.degrees(math.atan2(dy, dx)) % 360.0
        orientation = _snap_to_cardinal(orientation)
    else:
        orientation = fallback_orientation
    return orientation, width_dbu * dbu


def distance_point_to_segment(px: float, py: float,
                              ax: float, ay: float,
                              bx: float, by: float) -> tuple[float, tuple[float, float]]:
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    seg_len2 = vx * vx + vy * vy
    if seg_len2 < 1e-9:
        return math.hypot(px - ax, py - ay), (ax, ay)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / seg_len2))
    qx = ax + t * vx
    qy = ay + t * vy
    return math.hypot(px - qx, py - qy), (qx, qy)


def orientation_from_base_angle(base_angle: float,
                                preferred_orientation: float) -> float:
    candidates = [((base_angle + 90.0) % 360.0),
                  ((base_angle + 270.0) % 360.0)]
    return min(candidates, key=lambda a: angle_diff(a, preferred_orientation))


def align_triangle_to_nearby_edge(
    marker_points: Iterable[Sequence[float]],
    edges: Iterable[Sequence[float]],
    inferred_orientation: float,
    distance_ratio: float = NEAR_EDGE_DISTANCE_RATIO,
    min_distance_dbu: float = NEAR_EDGE_MIN_DBU,
    angle_tolerance_deg: float = NEAR_EDGE_ANGLE_TOLERANCE_DEG,
    cluster_tolerance_deg: float = NEAR_EDGE_CLUSTER_TOLERANCE_DEG,
) -> dict:
    """Align a triangular marker to one nearby real geometry edge.

    Returns a dict with:
      orientation: final port direction
      center: projection point on the matched edge when attached, else None
      attached: whether a unique reliable edge was found
    """
    tri = triangle_base_geometry(marker_points)
    if tri is None:
        return {"orientation": inferred_orientation, "center": None, "attached": False}

    mx, my = tri["mid"]
    marker_base_angle = float(tri["angle"])
    width_dbu = max(float(tri["width_dbu"]), 1.0)
    search_dbu = max(width_dbu * distance_ratio, min_distance_dbu)

    candidates: list[dict] = []
    for edge in edges:
        if len(edge) < 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in edge[:4]]
        dist, projected = distance_point_to_segment(mx, my, x0, y0, x1, y1)
        if dist > search_dbu:
            continue
        edge_angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
        if undirected_angle_diff(edge_angle, marker_base_angle) <= angle_tolerance_deg:
            candidates.append({
                "edge_angle": edge_angle,
                "distance": dist,
                "projected": projected,
                "edge": [x0, y0, x1, y1],
            })

    if not candidates:
        return {"orientation": inferred_orientation, "center": None, "attached": False}

    clusters: list[float] = []
    for candidate in candidates:
        angle = candidate["edge_angle"]
        if not any(undirected_angle_diff(angle, existing) <= cluster_tolerance_deg
                   for existing in clusters):
            clusters.append(angle)
    if len(clusters) != 1:
        return {"orientation": inferred_orientation, "center": None, "attached": False}

    best = min(candidates, key=lambda c: c["distance"])
    return {
        "orientation": orientation_from_base_angle(best["edge_angle"], inferred_orientation),
        "center": best["projected"],
        "attached": True,
        "edge_angle": best["edge_angle"],
        "edge": best["edge"],
        "distance": best["distance"],
    }
