"""Pure Python inference for hand-drawn routing anchor markers."""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def _unique_points(points: Iterable[Sequence[float]]) -> list[list[float]]:
    unique: list[list[float]] = []
    for point in points:
        pair = [float(point[0]), float(point[1])]
        if pair not in unique:
            unique.append(pair)
    return unique


def _bbox_center(bbox: Sequence[float]) -> list[float]:
    return [
        (float(bbox[0]) + float(bbox[2])) / 2.0,
        (float(bbox[1]) + float(bbox[3])) / 2.0,
    ]


def _bbox_size_um(bbox: Sequence[float], dbu: float) -> tuple[float, float]:
    return (
        abs(float(bbox[2]) - float(bbox[0])) * dbu,
        abs(float(bbox[3]) - float(bbox[1])) * dbu,
    )


def is_triangle_marker(shape: dict) -> bool:
    return shape.get("type") == "polygon" and len(_unique_points(shape.get("points_dbu") or [])) == 3


def is_box_marker(shape: dict) -> bool:
    return shape.get("type") == "box"


def is_corridor_marker(shape: dict) -> bool:
    return shape.get("type") == "path" and len(shape.get("points_dbu") or []) >= 2


def triangle_incircle(points: Iterable[Sequence[float]], dbu: float) -> dict | None:
    """Return incenter/inradius for a three-point triangle marker.

    Coordinates are returned in DBU for the center and um for the radius. The
    BendAnchor visual marker is intentionally just a triangle; the incircle is
    the router's search region implied by that triangle.
    """
    pts = _unique_points(points)
    if len(pts) != 3:
        return None

    ax, ay = pts[0]
    bx, by = pts[1]
    cx, cy = pts[2]
    a = math.hypot(bx - cx, by - cy)
    b = math.hypot(cx - ax, cy - ay)
    c = math.hypot(ax - bx, ay - by)
    perimeter = a + b + c
    if perimeter <= 0:
        return None

    area2 = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))
    if area2 <= 0:
        return None

    center_x = (a * ax + b * bx + c * cx) / perimeter
    center_y = (a * ay + b * by + c * cy) / perimeter
    semiperimeter = perimeter / 2.0
    area = area2 / 2.0
    inradius_dbu = area / semiperimeter
    if inradius_dbu <= 0:
        return None

    return {
        "center_dbu": [center_x, center_y],
        "radius_um": inradius_dbu * dbu,
    }


def path_points_relative_to_center(points: Iterable[Sequence[float]], center_dbu: Sequence[float], dbu: float) -> str:
    parts = []
    cx, cy = float(center_dbu[0]), float(center_dbu[1])
    for point in points:
        x_um = (float(point[0]) - cx) * dbu
        y_um = (float(point[1]) - cy) * dbu
        parts.append("%.6g,%.6g" % (x_um, y_um))
    return ";".join(parts)


def infer_anchor_marker(
    shape: dict,
    *,
    dbu: float,
    default_net: str = "",
    default_mode: str = "flexible",
    default_required: bool = True,
) -> dict | None:
    """Infer an anchor descriptor from one raw marker shape.

    Accepted raw marker grammar:
      - triangle polygon -> bend_region
      - box              -> waypoint_region
      - path             -> corridor
    """
    bbox = shape.get("bbox_dbu") or [0, 0, 0, 0]

    if is_triangle_marker(shape):
        points = shape.get("points_dbu") or []
        incircle = triangle_incircle(points, dbu)
        if incircle is None:
            return None
        center = incircle["center_dbu"]
        radius_um = max(float(incircle["radius_um"]), dbu)
        return {
            "kind": "bend_region",
            "mode": default_mode,
            "net": default_net,
            "required": bool(default_required),
            "center_dbu": [int(round(center[0])), int(round(center[1]))],
            "radius_um": radius_um,
            "width_um": radius_um * 2.0,
            "height_um": radius_um * 2.0,
            "orientation": 0.0,
            "path_points": "",
            "source_shape": shape,
        }

    if is_box_marker(shape):
        center = _bbox_center(bbox)
        width_um, height_um = _bbox_size_um(bbox, dbu)
        return {
            "kind": "waypoint_region",
            "mode": default_mode,
            "net": default_net,
            "required": bool(default_required),
            "center_dbu": [int(round(center[0])), int(round(center[1]))],
            "radius_um": max(width_um, height_um) / 2.0,
            "width_um": width_um,
            "height_um": height_um,
            "orientation": 0.0,
            "path_points": "",
            "source_shape": shape,
        }

    if is_corridor_marker(shape):
        center = _bbox_center(bbox)
        points = shape.get("points_dbu") or []
        width_um = max(float(shape.get("width_dbu", 0)) * dbu, dbu)
        return {
            "kind": "corridor",
            "mode": default_mode,
            "net": default_net,
            "required": bool(default_required),
            "center_dbu": [int(round(center[0])), int(round(center[1]))],
            "radius_um": width_um / 2.0,
            "width_um": width_um,
            "height_um": width_um,
            "orientation": 0.0,
            "path_points": path_points_relative_to_center(points, center, dbu),
            "source_shape": shape,
        }

    return None
