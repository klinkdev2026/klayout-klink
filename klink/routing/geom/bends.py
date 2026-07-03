"""Euler-bend corner rounding for Manhattan route polylines.

Optical waveguides cannot take sharp corners: every 90-degree turn of a
klink-planned detour must become a smooth bend before it is drawn. This
module is pure geometry (no client, no process data): it turns an
axis-aligned polyline into a densely sampled centerline whose corners are
EULER bends — a mirrored clothoid pair, curvature ramping linearly
0 -> 1/R -> 0, the standard low-loss photonic bend shape. Electrical routes
keep their sharp corners; the CALLER decides which nets are optical.

Geometry of one 90-degree euler bend with minimum radius R (= 1/k_max):

* each clothoid half turns 45 deg, so ``k_max * (L/2) / 2 = pi/4`` gives a
  half-length ``L/2 = (pi/2) * R`` (total length ``pi * R`` — longer than
  the quarter circle's ``(pi/2) R``);
* in the canonical frame (enter at the origin heading +x, leave heading
  +y) the bend ends at ``(d, d)`` by symmetry: ``d`` is the SETBACK — the
  distance before the corner vertex where the bend departs the incoming
  segment, and equally the distance after the vertex where it meets the
  outgoing one. Numerically ``d ~= 1.4067 * R`` (vs exactly ``R`` for a
  circular fillet), so an euler bend needs ~40% more room per corner.

The radius is the CALLER's process/design fact (style or the user's PDK
cross-section); this module only clamps it per corner to the room the
neighbouring segments actually have.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Sequence

Point = Sequence[float]

#: sampling density of one bend (points per clothoid pair)
DEFAULT_SAMPLES_PER_BEND = 48

#: numeric slack so a clamped bend never overshoots its segment
_FIT_MARGIN = 0.98


@lru_cache(maxsize=8)
def _canonical_euler_quarter(samples: int) -> tuple[tuple[tuple[float, float], ...], float]:
    """Sampled 90-deg euler bend for R=1: enter (0,0) heading +x, leave heading +y.

    Returns ``(points, setback)`` where ``setback`` is the (equal) distance
    from the corner vertex to the bend's entry and exit points. Scale every
    coordinate by R for an arbitrary radius.
    """
    half_length = math.pi / 2.0  # L/2 for R = 1
    total = 2.0 * half_length
    steps = max(int(samples) * 8, 64)  # fine integration grid
    ds = total / steps
    ramp = 1.0 / half_length       # dk/ds on the first half (k = ramp * s)

    xs = [0.0]
    ys = [0.0]
    theta = 0.0
    s = 0.0
    for _ in range(steps):
        # curvature triangle: up on the first half, mirrored down after
        mid = s + ds / 2.0
        if mid <= half_length:
            k = ramp * mid
        else:
            k = ramp * (total - mid)
        theta_mid = theta + k * ds / 2.0  # midpoint rule on the heading
        xs.append(xs[-1] + math.cos(theta_mid) * ds)
        ys.append(ys[-1] + math.sin(theta_mid) * ds)
        theta += k * ds
        s += ds

    # By symmetry the exact end point is (d, d); symmetrize away the
    # residual integration error and pin the exit tangent to +y.
    d = (xs[-1] + ys[-1]) / 2.0
    xs[-1] = d
    ys[-1] = d

    stride = max(1, steps // max(int(samples), 2))
    picked = list(range(0, steps + 1, stride))
    if picked[-1] != steps:
        picked.append(steps)
    points = tuple((xs[i], ys[i]) for i in picked)
    return points, d


def euler_setback_ratio(samples: int = DEFAULT_SAMPLES_PER_BEND) -> float:
    """Setback / radius for a 90-deg euler bend (~1.4067)."""
    return _canonical_euler_quarter(samples)[1]


def round_manhattan_corners(
    points_um: Sequence[Point],
    radius_um: float,
    *,
    samples_per_bend: int = DEFAULT_SAMPLES_PER_BEND,
) -> list[list[float]]:
    """Replace every 90-deg corner of an axis-aligned polyline with an euler bend.

    ``radius_um`` is the desired minimum bend radius (the caller's process/
    design fact). Each corner independently clamps it to the room available
    on its two segments (half of each, shared corners split naturally, with
    a small numeric margin); collinear vertices are dropped. Non-axis-
    aligned input segments raise — the visibility planner only emits
    Manhattan paths, anything else is a caller bug.
    """
    pts = [[float(p[0]), float(p[1])] for p in points_um]
    cleaned: list[list[float]] = []
    for p in pts:
        if cleaned and abs(p[0] - cleaned[-1][0]) < 1e-9 and abs(p[1] - cleaned[-1][1]) < 1e-9:
            continue
        cleaned.append(p)
    if len(cleaned) < 3 or radius_um <= 0:
        return cleaned

    def _direction(a: Point, b: Point) -> tuple[float, float, float]:
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return 0.0, 0.0, 0.0
        if abs(dx) > 1e-6 and abs(dy) > 1e-6:
            raise ValueError(
                "round_manhattan_corners needs axis-aligned segments; got "
                f"({a[0]:.3f},{a[1]:.3f})->({b[0]:.3f},{b[1]:.3f})")
        return dx / length, dy / length, length

    canonical, setback_unit = _canonical_euler_quarter(samples_per_bend)

    out: list[list[float]] = [cleaned[0]]
    cursor = cleaned[0]
    for i in range(1, len(cleaned) - 1):
        vertex = cleaned[i]
        ux, uy, _ = _direction(cursor, vertex)
        wx, wy, len_out = _direction(vertex, cleaned[i + 1])
        cross = ux * wy - uy * wx
        if abs(cross) < 1e-9:
            continue  # collinear (or reversal): no corner here
        len_in = math.hypot(vertex[0] - cursor[0], vertex[1] - cursor[1])
        # room on each side: half of each segment (two corners may share it)
        avail = min(len_in / 2.0, len_out / 2.0) * _FIT_MARGIN
        radius = min(float(radius_um), avail / setback_unit)
        if radius <= 1e-6:
            out.append(list(vertex))  # no room at all: keep the sharp corner
            cursor = vertex
            continue
        setback = setback_unit * radius
        entry = [vertex[0] - ux * setback, vertex[1] - uy * setback]
        if math.hypot(entry[0] - out[-1][0], entry[1] - out[-1][1]) > 1e-9:
            out.append(entry)
        # canonical frame: +x = incoming direction, +y = left normal;
        # right turns (cross < 0) mirror the canonical left-turn bend.
        sign = 1.0 if cross > 0 else -1.0
        nx, ny = -uy * sign, ux * sign
        for cx, cy in canonical[1:]:
            out.append([entry[0] + ux * (cx * radius) + nx * (cy * radius),
                        entry[1] + uy * (cx * radius) + ny * (cy * radius)])
        cursor = out[-1]
    if math.hypot(cleaned[-1][0] - out[-1][0], cleaned[-1][1] - out[-1][1]) > 1e-9:
        out.append(cleaned[-1])
    return [[round(x, 4), round(y, 4)] for x, y in out]


def max_turn_deg(points_um: Sequence[Point]) -> float:
    """Largest per-vertex heading change of a polyline, in degrees.

    Smoothness gauge for tests: a properly sampled euler path stays well
    under ~10 deg per vertex, a sharp Manhattan corner shows 90.
    """
    worst = 0.0
    for i in range(1, len(points_um) - 1):
        ax, ay = points_um[i][0] - points_um[i - 1][0], points_um[i][1] - points_um[i - 1][1]
        bx, by = points_um[i + 1][0] - points_um[i][0], points_um[i + 1][1] - points_um[i][1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la < 1e-9 or lb < 1e-9:
            continue
        dot = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
        worst = max(worst, math.degrees(math.acos(dot)))
    return worst
