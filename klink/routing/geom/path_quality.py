"""Conservative post-processing for planned route polylines."""

from __future__ import annotations

import math
from typing import Sequence


Point = list[float]

_EPS = 1e-9


def _original_points(points: object) -> list:
    try:
        return list(points)  # type: ignore[arg-type]
    except Exception:
        return []


def _coerce_points(points: object) -> list[Point] | None:
    try:
        raw_points = list(points)  # type: ignore[arg-type]
    except Exception:
        return None
    coerced: list[Point] = []
    for point in raw_points:
        try:
            if len(point) != 2:  # type: ignore[arg-type]
                return None
            x = float(point[0])  # type: ignore[index]
            y = float(point[1])  # type: ignore[index]
        except Exception:
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        coerced.append([x, y])
    return coerced


def _same_point(a: Sequence[float], b: Sequence[float]) -> bool:
    return abs(float(a[0]) - float(b[0])) <= _EPS and abs(float(a[1]) - float(b[1])) <= _EPS


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _is_collinear(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> bool:
    abx = float(b[0]) - float(a[0])
    aby = float(b[1]) - float(a[1])
    bcx = float(c[0]) - float(b[0])
    bcy = float(c[1]) - float(b[1])
    scale = max(abs(abx), abs(aby), abs(bcx), abs(bcy), 1.0)
    return abs(_cross(abx, aby, bcx, bcy)) <= _EPS * scale * scale


def _c_between_a_b(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> bool:
    abx = float(b[0]) - float(a[0])
    aby = float(b[1]) - float(a[1])
    acx = float(c[0]) - float(a[0])
    acy = float(c[1]) - float(a[1])
    ab2 = abx * abx + aby * aby
    if ab2 <= _EPS:
        return False
    dot = acx * abx + acy * aby
    return -_EPS <= dot <= ab2 + _EPS


def _remove_zero_length_segments(points: Sequence[Sequence[float]]) -> list[Point]:
    if len(points) <= 2:
        return [[float(point[0]), float(point[1])] for point in points]

    out: list[Point] = [[float(points[0][0]), float(points[0][1])]]
    for point in points[1:-1]:
        candidate = [float(point[0]), float(point[1])]
        if not _same_point(out[-1], candidate):
            out.append(candidate)

    last = [float(points[-1][0]), float(points[-1][1])]
    while len(out) > 1 and _same_point(out[-1], last):
        out.pop()
    out.append(last)
    return out


def _prune_once(points: Sequence[Sequence[float]]) -> tuple[list[Point], bool, bool]:
    for i in range(len(points) - 2):
        a = points[i]
        b = points[i + 1]
        c = points[i + 2]
        if _same_point(a, b) or _same_point(b, c):
            continue
        abx = float(b[0]) - float(a[0])
        aby = float(b[1]) - float(a[1])
        bcx = float(c[0]) - float(b[0])
        bcy = float(c[1]) - float(b[1])
        dot = abx * bcx + aby * bcy
        if _is_collinear(a, b, c) and dot < -_EPS:
            if not _c_between_a_b(a, b, c):
                return [[float(p[0]), float(p[1])] for p in points], False, True
            pruned = [
                [float(p[0]), float(p[1])]
                for index, p in enumerate(points)
                if index != i + 1
            ]
            return pruned, True, False
    return [[float(p[0]), float(p[1])] for p in points], False, False


def _merge_collinear(points: Sequence[Sequence[float]]) -> list[Point]:
    if len(points) <= 2:
        return [[float(point[0]), float(point[1])] for point in points]

    out: list[Point] = [[float(points[0][0]), float(points[0][1])]]
    for point in points[1:-1]:
        candidate = [float(point[0]), float(point[1])]
        while len(out) >= 2 and _is_collinear(out[-2], out[-1], candidate):
            vx0 = out[-1][0] - out[-2][0]
            vy0 = out[-1][1] - out[-2][1]
            vx1 = candidate[0] - out[-1][0]
            vy1 = candidate[1] - out[-1][1]
            if vx0 * vx1 + vy0 * vy1 <= _EPS:
                break
            out.pop()
        out.append(candidate)

    last = [float(points[-1][0]), float(points[-1][1])]
    while len(out) >= 2 and _is_collinear(out[-2], out[-1], last):
        vx0 = out[-1][0] - out[-2][0]
        vy0 = out[-1][1] - out[-2][1]
        vx1 = last[0] - out[-1][0]
        vy1 = last[1] - out[-1][1]
        if vx0 * vx1 + vy0 * vy1 <= _EPS:
            break
        out.pop()
    out.append(last)
    return out


def simplify_route_points(points_um: object, *, width_um: float) -> list:
    """Prune unambiguous route spurs and redundant collinear vertices.

    Malformed or ambiguous inputs are returned unchanged as a list.
    """

    original = _original_points(points_um)
    try:
        width = float(width_um)
    except Exception:
        return original
    if not math.isfinite(width) or width <= 0.0:
        return original

    points = _coerce_points(points_um)
    if points is None or len(points) <= 1:
        return original

    simplified = _remove_zero_length_segments(points)
    while True:
        simplified = _merge_collinear(simplified)
        simplified = _remove_zero_length_segments(simplified)
        pruned, changed, ambiguous = _prune_once(simplified)
        if ambiguous:
            return original
        simplified = _remove_zero_length_segments(pruned)
        if not changed:
            break
    simplified = _merge_collinear(simplified)
    simplified = _remove_zero_length_segments(simplified)

    if not _same_point(points[0], simplified[0]) or not _same_point(points[-1], simplified[-1]):
        return original
    return simplified
