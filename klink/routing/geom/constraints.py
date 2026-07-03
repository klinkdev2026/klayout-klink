"""Router constraints derived from klink Port semantics."""

from __future__ import annotations

import math
from typing import Sequence

Point = list[float]


def orientation_vector(orientation: float) -> tuple[float, float]:
    """Return the unit vector for a KLayout/klink orientation in degrees."""
    rad = math.radians(float(orientation))
    return (math.cos(rad), math.sin(rad))


def port_launch_width(port: dict) -> float:
    """The initial route width is the port width."""
    return float(port.get("width_um", 0.0))


def port_launch_point(port: dict, *, length_um: float | None = None) -> list[float]:
    """Return the first legal point after leaving a port.

    ``port.center_um`` is the contact/base midpoint.  The first segment must
    leave from that point in ``port.orientation`` direction, perpendicular to
    the port base/access edge.  The returned point is a short stub endpoint
    that a global pathfinder can use as its actual graph endpoint.
    """
    center = port.get("center_um") or [0.0, 0.0]
    width = port_launch_width(port)
    length = float(length_um) if length_um is not None else max(width * 2.0, width, 1.0)
    vx, vy = orientation_vector(float(port.get("orientation", 0.0)))
    return [
        float(center[0]) + vx * length,
        float(center[1]) + vy * length,
    ]


def _launch_length(port: dict, length_um: float | None = None) -> float:
    width = port_launch_width(port)
    return float(length_um) if length_um is not None else max(width * 2.0, width, 1.0)


def direct_head_on_route(
    source: dict,
    target: dict,
    *,
    launch_length_um: float | None = None,
    eps: float = 1e-9,
) -> list[Point] | None:
    """Return a center-to-center route for aligned ports facing each other."""

    source_center = [float(v) for v in source.get("center_um", [0.0, 0.0])]
    target_center = [float(v) for v in target.get("center_um", [0.0, 0.0])]
    if abs(port_launch_width(source) - port_launch_width(target)) > eps:
        return None
    sx, sy = _orientation_axis(source)
    tx, ty = _orientation_axis(target)
    if (sx, sy) != (-tx, -ty):
        return None
    dx = target_center[0] - source_center[0]
    dy = target_center[1] - source_center[1]
    distance = abs(dx) + abs(dy)
    launch_sum = _launch_length(source, launch_length_um) + _launch_length(target, launch_length_um)
    if distance > launch_sum + eps:
        return None
    if sx != 0:
        if abs(dy) > eps or dx * sx <= eps:
            return None
    elif abs(dx) > eps or dy * sy <= eps:
        return None
    return [source_center, target_center]


def route_with_port_launch_stubs(
    source: dict,
    target: dict,
    inner_points: Sequence[Sequence[float]] | None = None,
    *,
    launch_length_um: float | None = None,
) -> dict:
    """Build an ordered route skeleton that respects both port launches.

    The skeleton is not a complete router result.  It is the contract the
    router must preserve around endpoints:

    source.center -> source_launch -> ...inner path... -> target_launch -> target.center

    At the target, the segment from target.center to target_launch follows the
    target orientation; therefore the ordered route approaches the target from
    the reverse direction, as physical connectivity requires.
    """
    source_center = [float(v) for v in source.get("center_um", [0.0, 0.0])]
    target_center = [float(v) for v in target.get("center_um", [0.0, 0.0])]
    source_launch = port_launch_point(source, length_um=launch_length_um)
    target_launch = port_launch_point(target, length_um=launch_length_um)
    direct = direct_head_on_route(source, target, launch_length_um=launch_length_um)
    if direct is not None and not inner_points:
        return {
            "points_um": direct,
            "source_launch_um": source_launch,
            "target_launch_um": target_launch,
            "width_um": min(port_launch_width(source), port_launch_width(target)),
            "source_width_um": port_launch_width(source),
            "target_width_um": port_launch_width(target),
        }
    points = [source_center, source_launch]
    for point in inner_points or []:
        p = [float(point[0]), float(point[1])]
        if p != points[-1]:
            points.append(p)
    if target_launch != points[-1]:
        points.append(target_launch)
    if target_center != points[-1]:
        points.append(target_center)
    points = break_launch_hairpins(points, source, target)
    return {
        "points_um": points,
        "source_launch_um": source_launch,
        "target_launch_um": target_launch,
        "width_um": min(port_launch_width(source), port_launch_width(target)),
        "source_width_um": port_launch_width(source),
        "target_width_um": port_launch_width(target),
    }


def _orientation_axis(port: dict) -> tuple[int, int]:
    vx, vy = orientation_vector(float(port.get("orientation", 0.0)))
    if abs(vx) >= abs(vy):
        return (1 if vx >= 0.0 else -1, 0)
    return (0, 1 if vy >= 0.0 else -1)


def _perpendicular_launch_point(
    launch: Sequence[float],
    reference: Sequence[float],
    axis: tuple[int, int],
    distance_um: float,
) -> Point:
    px, py = -axis[1], axis[0]
    a = [float(launch[0]) + px * distance_um, float(launch[1]) + py * distance_um]
    b = [float(launch[0]) - px * distance_um, float(launch[1]) - py * distance_um]
    da = math.hypot(a[0] - float(reference[0]), a[1] - float(reference[1]))
    db = math.hypot(b[0] - float(reference[0]), b[1] - float(reference[1]))
    return a if da <= db else b


def break_launch_hairpins(points: Sequence[Sequence[float]], source: dict, target: dict) -> list[Point]:
    """Preserve Port orientation while avoiding terminal backtracking.

    If the path enters a target launch stub from the same direction as the
    target orientation, the final target stub must immediately reverse
    direction to reach the Port center.  The same issue can happen just after a
    source launch.  Insert a small perpendicular dogleg outside the launch stub
    instead of rewriting the user-defined Port orientation.
    """

    if len(points) < 4:
        return [[float(p[0]), float(p[1])] for p in points]
    result = [[float(p[0]), float(p[1])] for p in points]

    source_axis = _orientation_axis(source)
    source_out_x = float(result[2][0]) - float(result[1][0])
    source_out_y = float(result[2][1]) - float(result[1][1])
    if source_out_x * source_axis[0] + source_out_y * source_axis[1] < -1e-9:
        distance = max(float(source.get("width_um", 1.0) or 1.0) * 2.0, 1.0)
        result.insert(2, _perpendicular_launch_point(result[1], result[2], source_axis, distance))

    target_axis = _orientation_axis(target)
    target_launch_index = len(result) - 2
    target_in_x = float(result[target_launch_index][0]) - float(result[target_launch_index - 1][0])
    target_in_y = float(result[target_launch_index][1]) - float(result[target_launch_index - 1][1])
    if target_in_x * target_axis[0] + target_in_y * target_axis[1] > 1e-9:
        distance = max(float(target.get("width_um", 1.0) or 1.0) * 2.0, 1.0)
        result.insert(
            target_launch_index,
            _perpendicular_launch_point(result[target_launch_index], result[target_launch_index - 1], target_axis, distance),
        )

    deduped: list[Point] = []
    for point in result:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
    return deduped
