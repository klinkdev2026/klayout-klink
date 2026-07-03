"""Tapered router: trapezoid direct-connect and turn-narrowing multi-segment routes.

Key design principle (two-phase):
1. Pathfinding uses min(source_width, target_width) — the narrow end determines
   whether a route can fit through tight spaces.
2. After the centerline is found, the taper polygon recovers the full width
   progression, and a validation pass checks that wide portions don't overlap.

Taper strategy is pluggable.  Built-in strategies:

  ``"uniform"``     equal per-bend ratio — width steps evenly from source to target
  ``"front_load"``  narrow aggressively at early bends, gentle at the end
  ``"back_load"``   narrow gently at first, aggressively at the end

You can also pass a callable with signature::

    (bend_index, num_bends, source_w, target_w) -> float
        → the width *after* this bend

to implement any custom distribution.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

Point = list[float]
Polygon = list[Point]
TaperStrategy = Callable[[int, int, float, float], float]
"""A taper strategy computes the target width after bend *bend_index* (0-based)
given the total *num_bends*, *source_width_um*, and *target_width_um*."""


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _segment_direction(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _perpendicular_ccw(dx: float, dy: float) -> tuple[float, float]:
    """CCW perpendicular of a direction vector."""
    return (-dy, dx)


def _is_bend(
    a: Sequence[float], b: Sequence[float], c: Sequence[float], eps: float = 1e-9
) -> bool:
    """True when b is a direction change relative to a→b→c."""
    cross = (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1])) - (
        float(b[1]) - float(a[1])
    ) * (float(c[0]) - float(a[0]))
    return abs(cross) > eps


def _point_direction(points: Sequence[Sequence[float]], i: int) -> tuple[float, float]:
    """Local forward direction at centerline point i (bisector at bends)."""
    n = len(points)
    if n < 2:
        return (1.0, 0.0)
    if i == 0:
        return _segment_direction(points[0], points[1])
    if i == n - 1:
        return _segment_direction(points[-2], points[-1])
    d1x, d1y = _segment_direction(points[i - 1], points[i])
    d2x, d2y = _segment_direction(points[i], points[i + 1])
    sx, sy = d1x + d2x, d1y + d2y
    length = math.hypot(sx, sy)
    if length < 1e-12:
        return (d1x, d1y)
    return (sx / length, sy / length)


def _find_bend_indices(points: Sequence[Sequence[float]]) -> list[int]:
    """Return indices of interior points where direction changes."""
    result: list[int] = []
    for i in range(1, len(points) - 1):
        if _is_bend(points[i - 1], points[i], points[i + 1]):
            result.append(i)
    return result


# ---------------------------------------------------------------------------
# Taper ratio
# ---------------------------------------------------------------------------


def compute_taper_ratio(
    source_width_um: float,
    target_width_um: float,
    num_bends: int,
) -> float:
    """Per-bend multiplier so that after *num_bends* bends width goes
    from *source_width_um* to *target_width_um*::

        ratio = (W_target / W_source) ** (1 / num_bends)

    Returns 1.0 when num_bends == 0 or either width is non-positive.
    """
    if num_bends <= 0:
        return 1.0
    if source_width_um <= 0.0 or target_width_um <= 0.0:
        return 1.0
    return (target_width_um / source_width_um) ** (1.0 / num_bends)


# ---------------------------------------------------------------------------
# Built-in taper strategies
# ---------------------------------------------------------------------------


def strategy_uniform(
    bend_index: int, num_bends: int, source_w: float, target_w: float
) -> float:
    """Equal ratio at every bend: W_s → r·W_s → r²·W_s → ... → W_t."""
    if num_bends <= 0:
        return target_w
    ratio = compute_taper_ratio(source_w, target_w, num_bends)
    return source_w * (ratio ** (bend_index + 1))


def strategy_front_load(
    bend_index: int, num_bends: int, source_w: float, target_w: float
) -> float:
    """Aggressive narrowing early.  First bend absorbs ~50% of the total change,
    the rest is distributed uniformly.

    Keeps the wide section short — useful when obstacles crowd the near end.
    """
    if num_bends <= 0:
        return target_w
    if num_bends == 1:
        return target_w
    half = source_w + (target_w - source_w) * 0.5
    if bend_index == 0:
        return half
    remaining = num_bends - 1
    ratio = (target_w / half) ** (1.0 / remaining)
    return half * (ratio ** (bend_index))


def strategy_back_load(
    bend_index: int, num_bends: int, source_w: float, target_w: float
) -> float:
    """Gentle narrowing early, aggressive at the end.  Keeps the path wide for
    most of the route, then narrows sharply at the last bend.

    Useful when the tight space is only at the far end.
    """
    if num_bends <= 0:
        return target_w
    if num_bends == 1:
        return target_w
    # Last bend does 50% of the change
    if bend_index == num_bends - 1:
        return target_w
    remaining = num_bends - 1
    mid = source_w + (target_w - source_w) * 0.5
    ratio = (mid / source_w) ** (1.0 / remaining)
    return source_w * (ratio ** (bend_index + 1))


def _resolve_strategy(
    strategy: str | TaperStrategy,
) -> TaperStrategy:
    if callable(strategy):
        return strategy
    by_name = {
        "uniform": strategy_uniform,
        "front_load": strategy_front_load,
        "back_load": strategy_back_load,
    }
    if strategy not in by_name:
        raise ValueError(
            f"unknown taper strategy {strategy!r}; choose from {list(by_name)} or pass a callable"
        )
    return by_name[strategy]


# ---------------------------------------------------------------------------
# Width computation
# ---------------------------------------------------------------------------


def compute_tapered_widths(
    points: Sequence[Sequence[float]],
    source_width_um: float,
    target_width_um: float,
    *,
    strategy: str | TaperStrategy = "uniform",
) -> list[float]:
    """Width at each centerline point, narrowing at every bend.

    Returns a list parallel to *points*.  The first entry is *source_width_um*,
    the last is *target_width_um*.  Width only changes at bend points; between
    bends the width is constant.

    *strategy* controls how the total width change is distributed across bends.
    """
    n = len(points)
    if n < 2:
        return [float(source_width_um)] * n

    bend_indices = _find_bend_indices(points)
    num_bends = len(bend_indices)
    fn = _resolve_strategy(strategy)

    # Map point index → width
    widths = [0.0] * n
    widths[0] = float(source_width_um)

    if num_bends == 0:
        # No bends: linear interpolation from source to target
        total_len = 0.0
        seg_lens = []
        for i in range(n - 1):
            d = math.hypot(
                float(points[i + 1][0]) - float(points[i][0]),
                float(points[i + 1][1]) - float(points[i][1]),
            )
            seg_lens.append(d)
            total_len += d
        if total_len < 1e-12:
            return [float(source_width_um)] * n
        cum = 0.0
        for i in range(1, n):
            cum += seg_lens[i - 1]
            t = cum / total_len
            widths[i] = float(source_width_um) + (float(target_width_um) - float(source_width_um)) * t
        widths[-1] = float(target_width_um)
        return widths

    # Has bends: width changes AT each bend point according to strategy.
    # A bend at index i means the outgoing segment (i→i+1) has the new width.
    bend_order: dict[int, int] = {idx: k for k, idx in enumerate(bend_indices)}
    current_w = float(source_width_um)
    for i in range(1, n):
        if i in bend_order:
            k = bend_order[i]
            current_w = fn(k, num_bends, float(source_width_um), float(target_width_um))
        widths[i] = current_w
    widths[-1] = float(target_width_um)
    return widths


# ---------------------------------------------------------------------------
# Polygon generation
# ---------------------------------------------------------------------------

# Corner style configuration
#   "miter"  — edges extended until they meet (default, sharp corner)
#   "bevel"  — miter capped at miter_limit * half_w, cut flat beyond
#   "round"  — arc of *arc_points* vertices at the corner
CornerStyle = str  # "miter" | "bevel" | "round"


def _offset_perp(dx: float, dy: float, sign: float) -> tuple[float, float]:
    """Perpendicular vector for offsetting.  sign=+1 → right, sign=-1 → left."""
    return (sign * (-dy), sign * dx)


def _line_intersection(
    p1: Sequence[float], d1: tuple[float, float],
    p2: Sequence[float], d2: tuple[float, float],
) -> Point | None:
    """Intersection of two directed lines p1+t*d1 and p2+u*d2, or None if parallel."""
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-15:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / cross
    return [float(p1[0]) + t * d1[0], float(p1[1]) + t * d1[1]]


def _segment_endpoints(
    a: Sequence[float], b: Sequence[float],
    wa: float, wb: float,
    sign: float,
) -> tuple[Point, Point, tuple[float, float]]:
    """Offset segment endpoints for one polygon side.

    Returns (start_point, end_point, direction_vector).
    """
    dx, dy = _segment_direction(a, b)
    px, py = _offset_perp(dx, dy, sign)
    start = [float(a[0]) + px * wa / 2.0, float(a[1]) + py * wa / 2.0]
    end = [float(b[0]) + px * wb / 2.0, float(b[1]) + py * wb / 2.0]
    return start, end, (dx, dy)


def _build_side_polyline(
    points: Sequence[Sequence[float]],
    widths: Sequence[float],
    sign: float,
    *,
    corner_style: CornerStyle = "miter",
    miter_limit: float = 4.0,
    arc_points: int = 8,
) -> list[Point]:
    """Build one side of the tapered polygon.

    Each segment is offset perpendicular to the centerline by ±width/2.
    At bends, offset segments are either extended to their intersection (outer
    corner / miter) or bridged by a connecting segment (inner corner).

    *sign*: +1 for right side, -1 for left side.
    """
    n = len(points)
    if n < 2:
        return []

    # Build all offset segment endpoints
    seg_starts: list[Point] = []
    seg_ends: list[Point] = []
    seg_dirs: list[tuple[float, float]] = []

    for i in range(n - 1):
        s, e, d = _segment_endpoints(
            points[i], points[i + 1], widths[i], widths[i + 1], sign
        )
        seg_starts.append(s)
        seg_ends.append(e)
        seg_dirs.append(d)

    # Assemble polyline, handling corners between consecutive segments
    result: list[Point] = [seg_starts[0]]

    for k in range(len(seg_starts) - 1):
        # Corner between segment k (ending at point k+1) and segment k+1 (starting at point k+1)
        end_prev = seg_ends[k]
        start_next = seg_starts[k + 1]
        dir_prev = seg_dirs[k]
        dir_next = seg_dirs[k + 1]

        # Are the two offset endpoints essentially the same?
        if math.hypot(end_prev[0] - start_next[0], end_prev[1] - start_next[1]) < 1e-9:
            result.append(end_prev)
            continue

        # Try to intersect the offset lines
        intersection = _line_intersection(end_prev, dir_prev, start_next, dir_next)

        if intersection is None:
            # Parallel — just connect directly
            result.append(end_prev)
            result.append(start_next)
            continue

        # Is the intersection "forward" of both segments? (miter / outer corner)
        t_prev = ((intersection[0] - end_prev[0]) * dir_prev[0] +
                  (intersection[1] - end_prev[1]) * dir_prev[1])
        t_next = ((intersection[0] - start_next[0]) * dir_next[0] +
                  (intersection[1] - start_next[1]) * dir_next[1])

        bend_center = points[k + 1]
        half_w = widths[k + 1] / 2.0
        miter_dist = math.hypot(
            intersection[0] - bend_center[0],
            intersection[1] - bend_center[1],
        )

        # Outer corner: miter point extends beyond the route half-width from the
        # centerline.  Inner corner: intersection lies inside the route envelope.
        is_outer = miter_dist > half_w + 1e-9

        if is_outer:
            # Outer corner: edges extend and meet → miter
            if corner_style == "round":
                # Arc from end_prev to start_next around the outer corner
                arc = _corner_arc(
                    end_prev, intersection, start_next, bend_center, half_w,
                    num_points=arc_points, outer=True,
                )
                result.extend(arc[1:-1])  # exclude endpoints already in result
            elif corner_style == "bevel" and miter_dist > miter_limit * half_w:
                # Bevel: cut off the miter tip
                limit_dist = miter_limit * half_w
                ratio = limit_dist / miter_dist if miter_dist > 1e-12 else 1.0
                bevel_a = [
                    end_prev[0] + (intersection[0] - end_prev[0]) * ratio,
                    end_prev[1] + (intersection[1] - end_prev[1]) * ratio,
                ]
                bevel_b = [
                    start_next[0] + (intersection[0] - start_next[0]) * ratio,
                    start_next[1] + (intersection[1] - start_next[1]) * ratio,
                ]
                result.append(bevel_a)
                result.append(bevel_b)
            else:
                # Miter: use the intersection point
                result.append(intersection)
        else:
            # Inner corner: gap — connect end_prev to start_next
            if corner_style == "round":
                arc = _corner_arc(
                    end_prev, None, start_next, bend_center, half_w,
                    num_points=arc_points, outer=False,
                )
                result.extend(arc[1:-1])
            else:
                result.append(end_prev)
                result.append(start_next)

    result.append(seg_ends[-1])
    return result


def _corner_arc(
    p_start: Point,
    p_miter: Point | None,
    p_end: Point,
    center: Point,
    radius: float,
    *,
    num_points: int = 8,
    outer: bool,
) -> list[Point]:
    """Generate arc vertices for a rounded corner.

    For an *outer* corner the arc goes from *p_start* through *p_miter* to *p_end*,
    centered at *center*.  For an *inner* corner (p_miter is None), the arc
    directly connects p_start to p_end with a simple circular arc.
    """
    if num_points < 2:
        return [p_start, p_end]

    # Compute angles from center to each point
    a_start = math.atan2(p_start[1] - center[1], p_start[0] - center[0])
    a_end = math.atan2(p_end[1] - center[1], p_end[0] - center[0])

    # Determine sweep direction (shortest path)
    delta = a_end - a_start
    while delta > math.pi:
        delta -= 2.0 * math.pi
    while delta < -math.pi:
        delta += 2.0 * math.pi

    arc: list[Point] = [p_start]
    for k in range(1, num_points):
        t = k / num_points
        angle = a_start + delta * t
        arc.append([
            center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle),
        ])
    arc.append(p_end)
    return arc


def build_trapezoid_polygon(
    points: Sequence[Sequence[float]],
    widths: Sequence[float],
    *,
    corner_style: CornerStyle = "miter",
    miter_limit: float = 4.0,
    arc_points: int = 8,
) -> Polygon:
    """Build a variable-width polygon from a centerline and per-point widths.

    The polygon follows the centerline offset by ±width/2 perpendicular to the
    local direction.  Corner treatment for bends:

    ``corner_style="miter"`` (default)
        Offset edges are extended until they meet.  Sharp, standard.
    ``corner_style="bevel"``
        Like miter, but the miter tip is cut flat when the miter distance
        exceeds ``miter_limit * half_width``.
    ``corner_style="round"``
        Corner is an arc of ``arc_points`` vertices centred on the bend point.

    Returns a closed polygon (first point NOT repeated).
    """
    n = len(points)
    if n < 2 or len(widths) != n:
        return []

    left = _build_side_polyline(
        points, widths, sign=-1.0,
        corner_style=corner_style, miter_limit=miter_limit, arc_points=arc_points,
    )
    right = _build_side_polyline(
        points, widths, sign=+1.0,
        corner_style=corner_style, miter_limit=miter_limit, arc_points=arc_points,
    )

    return left + list(reversed(right))


# ---------------------------------------------------------------------------
# Route builder
# ---------------------------------------------------------------------------


def route_tapered(
    source: dict,
    target: dict,
    inner_points: Sequence[Sequence[float]] | None = None,
    *,
    launch_length_um: float | None = None,
    strategy: str | TaperStrategy = "uniform",
    corner_style: CornerStyle = "miter",
    miter_limit: float = 4.0,
    arc_points: int = 8,
) -> dict:
    """Build a tapered route skeleton between two ports.

    Returns a dict with the centerline, per-point widths, taper polygon,
    and metadata.  Callers must validate the polygon against obstacles
    after generation (see ``validate_tapered_route``).

    *strategy* controls how width is distributed across bends.  Built-in:
    ``"uniform"``, ``"front_load"``, ``"back_load"``, or a callable.

    *corner_style* controls how polygon corners at bends are shaped:
    ``"miter"`` (default), ``"bevel"``, ``"round"``.
    """
    from klink.routing.geom.constraints import break_launch_hairpins, port_launch_point, port_launch_width

    source_center = [float(v) for v in source.get("center_um", [0.0, 0.0])]
    target_center = [float(v) for v in target.get("center_um", [0.0, 0.0])]
    source_width = port_launch_width(source)
    target_width = port_launch_width(target)
    source_launch = port_launch_point(source, length_um=launch_length_um)
    target_launch = port_launch_point(target, length_um=launch_length_um)

    # Assemble centerline: source_center → source_launch → ...inner... → target_launch → target_center
    points: list[Point] = [source_center, source_launch]
    for pt in inner_points or []:
        p = [float(pt[0]), float(pt[1])]
        if p != points[-1]:
            points.append(p)
    if target_launch != points[-1]:
        points.append(target_launch)
    if target_center != points[-1]:
        points.append(target_center)
    points = break_launch_hairpins(points, source, target)

    widths = compute_tapered_widths(
        points, source_width, target_width, strategy=strategy
    )
    polygon = build_trapezoid_polygon(
        points, widths,
        corner_style=corner_style, miter_limit=miter_limit, arc_points=arc_points,
    )
    bend_indices = _find_bend_indices(points)
    num_bends = len(bend_indices)

    # Compute effective per-bend ratios for reporting
    per_bend_ratios = []
    if num_bends > 0 and source_width > 0:
        for idx in bend_indices:
            w_before = widths[idx - 1] if idx > 0 else source_width
            w_after = widths[idx]
            if w_before > 0:
                per_bend_ratios.append(round(w_after / w_before, 4))

    return {
        "points_um": points,
        "widths_um": widths,
        "polygon_um": polygon,
        "source_launch_um": source_launch,
        "target_launch_um": target_launch,
        "width_um": min(source_width, target_width),
        "source_width_um": source_width,
        "target_width_um": target_width,
        "num_bends": num_bends,
        "bend_indices": bend_indices,
        "per_bend_ratios": per_bend_ratios,
        "strategy": strategy if isinstance(strategy, str) else "custom",
        "corner_style": corner_style,
        "backend": "tapered",
    }


# ---------------------------------------------------------------------------
# Writeback
# ---------------------------------------------------------------------------


def commit_tapered_routes(
    client,
    cell: str,
    routes: list[dict],
    *,
    route_layer: str = "10/0",
    clear: bool = True,
) -> dict:
    """Write tapered routes to KLayout as polygons (not paths).

    Each route must have a ``polygon_um`` field generated by ``route_tapered``.
    Routes without a polygon (or with source_width == target_width) fall back
    to ``shape_insert_path`` with uniform width.
    """
    from klink.routing.geom.geometry import parse_layer

    layer, datatype = parse_layer(route_layer)
    client.layer_ensure(layer, datatype, name="KLINK_ROUTES")

    deleted = 0
    if clear:
        deleted = int(
            client.shape_delete(
                cell,
                layers=[route_layer],
                kinds=["paths", "polygons"],
                limit=10000,
            ).get("deleted", 0)
        )

    inserted_poly = 0
    inserted_path = 0
    for route in routes:
        polygon = route.get("polygon_um") or []
        if len(polygon) >= 3:
            client.shape_insert_polygon(
                cell,
                layer=layer,
                datatype=datatype,
                points_um=polygon,
            )
            inserted_poly += 1
        else:
            # Fallback: uniform-width path for routes without taper polygon
            points = route.get("points_um") or []
            if len(points) >= 2:
                width = float(route.get("width_um", 1.0))
                client.shape_insert_path(
                    cell,
                    layer=layer,
                    datatype=datatype,
                    points_um=points,
                    width_um=width,
                    begin_ext_um=width / 2.0,
                    end_ext_um=width / 2.0,
                    round_ends=False,
                )
                inserted_path += 1

    return {
        "cell": cell,
        "route_layer": route_layer,
        "deleted": deleted,
        "inserted_polygons": inserted_poly,
        "inserted_paths_fallback": inserted_path,
    }


# ---------------------------------------------------------------------------
# Polygon validation
# ---------------------------------------------------------------------------


def _bboxes_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    """Axis-aligned bbox overlap test."""
    return (
        float(a[0]) < float(b[2])
        and float(a[2]) > float(b[0])
        and float(a[1]) < float(b[3])
        and float(a[3]) > float(b[1])
    )


def _polygon_bbox(polygon: Sequence[Sequence[float]]) -> list[float]:
    xs = [float(p[0]) for p in polygon]
    ys = [float(p[1]) for p in polygon]
    return [min(xs), min(ys), max(xs), max(ys)]


def _segment_intersects_segment(
    a1: Sequence[float], a2: Sequence[float], b1: Sequence[float], b2: Sequence[float]
) -> bool:
    """2D segment-segment intersection test (excluding collinear overlap)."""
    ax1, ay1 = float(a1[0]), float(a1[1])
    ax2, ay2 = float(a2[0]), float(a2[1])
    bx1, by1 = float(b1[0]), float(b1[1])
    bx2, by2 = float(b2[0]), float(b2[1])

    dax = ax2 - ax1
    day = ay2 - ay1
    dbx = bx2 - bx1
    dby = by2 - by1

    cross = dax * dby - day * dbx
    if abs(cross) < 1e-15:
        return False

    t = ((bx1 - ax1) * dby - (by1 - ay1) * dbx) / cross
    u = ((bx1 - ax1) * day - (by1 - ay1) * dax) / cross
    return 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0


def polygon_hits_bboxes(
    polygon: Sequence[Sequence[float]],
    bboxes: Sequence[Sequence[float]],
) -> list[dict]:
    """Return obstacle bboxes that the tapered polygon overlaps.

    Checks: polygon bbox vs obstacle bbox, then edge intersections,
    then vertex containment.
    """
    if not polygon or not bboxes:
        return []

    poly_bbox = _polygon_bbox(polygon)
    n = len(polygon)
    hits: list[dict] = []

    for obs in bboxes:
        obs = [float(v) for v in obs]
        if not _bboxes_intersect(poly_bbox, obs):
            continue

        # Edge intersection test
        hit = False
        obs_corners = [
            [obs[0], obs[1]],
            [obs[2], obs[1]],
            [obs[2], obs[3]],
            [obs[0], obs[3]],
        ]
        for i in range(n):
            p1 = polygon[i]
            p2 = polygon[(i + 1) % n]
            for j in range(4):
                q1 = obs_corners[j]
                q2 = obs_corners[(j + 1) % 4]
                if _segment_intersects_segment(p1, p2, q1, q2):
                    hit = True
                    break
            if hit:
                break

        # Vertex containment: any polygon vertex inside obstacle
        if not hit:
            for pt in polygon:
                if (
                    obs[0] <= float(pt[0]) <= obs[2]
                    and obs[1] <= float(pt[1]) <= obs[3]
                ):
                    hit = True
                    break

        # Any obstacle corner inside polygon (point-in-polygon via ray casting)
        if not hit:
            for corner in obs_corners:
                inside = False
                j = n - 1
                for i in range(n):
                    yi = float(polygon[i][1])
                    yj = float(polygon[j][1])
                    xi = float(polygon[i][0])
                    xj = float(polygon[j][0])
                    cy = float(corner[1])
                    cx = float(corner[0])
                    if (yi > cy) != (yj > cy):
                        x_intersect = xj + (xi - xj) * (cy - yj) / (yi - yj)
                        if cx < x_intersect:
                            inside = not inside
                    j = i
                if inside:
                    hit = True
                    break

        if hit:
            hits.append({"bbox_um": obs})

    return hits


def validate_tapered_route(
    route: dict,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
    other_polygons: Sequence[Sequence[Sequence[float]]] | None = None,
) -> dict:
    """Check a tapered route's polygon against obstacles and sibling polygons."""
    polygon = route.get("polygon_um") or []
    errors: list[str] = []

    obs_hits = polygon_hits_bboxes(polygon, obstacle_bboxes or [])
    if obs_hits:
        errors.append(f"polygon hits {len(obs_hits)} obstacle(s)")

    sibling_hits = 0
    for other in other_polygons or []:
        if other and polygon_hits_bboxes(polygon, [polygon_bbox(other)]):
            # Fine-grained check
            if _polygons_overlap(polygon, other):
                sibling_hits += 1
    if sibling_hits:
        errors.append(f"polygon overlaps {sibling_hits} sibling route(s)")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "obstacle_hits": obs_hits,
        "sibling_overlaps": sibling_hits,
    }


def route_tapered_polygon_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    route_layer: str | None = None,
    spacing_um: float = 20.0,
    strategy: str | TaperStrategy = "uniform",
    corner_style: CornerStyle = "miter",
    angle_mode: str = "any",
    safe_distance_um: float = 0.0,
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route all pairable Port nets in a cell as continuous taper polygons.

    This is the cell-level orchestration boundary for the continuous polygon
    backend. It shares Port pairing and all Anchor semantics with the hybrid
    router, including corridor lane splitting. The backend difference is
    writeback geometry: one continuous taper polygon per route instead of
    hybrid paths plus corner patches.
    """

    from klink.routing.geom.planner import collect_obstacle_bboxes
    from klink.routing.backends.geometric.tapered_segments import (
        _pair_ports_by_net_tokens,
        route_tapered_hybrid_many,
        _unsupported_multi_port_net_errors,
    )

    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": anchor_layer, "sort": "id"}).get("anchors", [])
    obstacle_layers = list(obstacle_layers or [])
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)
    unsupported_net_errors = _unsupported_multi_port_net_errors(ports)
    pairs = _pair_ports_by_net_tokens(ports)

    by_layer: dict[str, list[dict]] = {}
    for pair in pairs:
        layer = str(route_layer or pair.get("route_layer") or "10/0")
        by_layer.setdefault(layer, []).append(pair)

    groups: list[dict] = []
    ok = not unsupported_net_errors
    for layer in sorted(by_layer):
        layer_pairs = by_layer[layer]
        planned = route_tapered_hybrid_many(
            layer_pairs,
            anchors=anchors,
            spacing_um=spacing_um,
            strategy=strategy,
            angle_mode=angle_mode,
            safe_distance_um=safe_distance_um,
            obstacle_bboxes=obstacle_bboxes,
        )
        routes = []
        for planned_route in planned.get("routes", []):
            points = [[float(p[0]), float(p[1])] for p in planned_route.get("points_um", [])]
            source_width = float(planned_route.get("source_width_um", planned_route.get("width_um", 1.0)) or 1.0)
            target_width = float(planned_route.get("target_width_um", planned_route.get("width_um", 1.0)) or 1.0)
            widths = compute_tapered_widths(
                points,
                source_width,
                target_width,
                strategy=strategy,
            )
            polygon = build_trapezoid_polygon(
                points,
                widths,
                corner_style=corner_style,
            )
            route = {
                **planned_route,
                "points_um": points,
                "widths_um": widths,
                "polygon_um": polygon,
                "backend": "tapered_polygon",
                "corner_style": corner_style,
                "strategy": strategy if isinstance(strategy, str) else "custom",
                "width_um": min(source_width, target_width),
            }
            routes.append(route)

        validations = []
        obstacle_hits = []
        sibling_overlaps = 0
        for idx, route in enumerate(routes):
            others = [other.get("polygon_um") for j, other in enumerate(routes) if j != idx]
            validation = validate_tapered_route(route, obstacle_bboxes=obstacle_bboxes, other_polygons=others)
            validations.append({"route_id": route.get("route_id"), "net": route.get("net"), **validation})
            obstacle_hits.extend(validation.get("obstacle_hits", []) or [])
            sibling_overlaps += int(validation.get("sibling_overlaps", 0) or 0)

        errors = list(planned.get("errors", []) or [])
        if obstacle_hits:
            errors.append("polygon route hits obstacle")
        if sibling_overlaps:
            errors.append("polygon route overlaps sibling")
        group_ok = not errors
        if not group_ok:
            ok = False
        write = None
        if group_ok:
            write = commit_tapered_routes(client, cell, routes, route_layer=layer, clear=clear)
        groups.append({
            "route_layer": layer,
            "ok": group_ok,
            "route_count": len(routes),
            "routes": routes,
            "lane_reports": planned.get("lane_reports", []),
            "validations": validations,
            "planning_errors": planned.get("planning_errors", []),
            "obstacle_hits": obstacle_hits,
            "sibling_overlaps": sibling_overlaps,
            "errors": errors,
            "write": write,
        })

    return {
        "ok": ok,
        "backend": "tapered_polygon_cell",
        "cell": cell,
        "port_count": len(ports),
        "anchor_count": len(anchors),
        "pair_count": len(pairs),
        "angle_mode": angle_mode,
        "safe_distance_um": float(safe_distance_um),
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "planning_errors": unsupported_net_errors,
        "errors": [e["message"] for e in unsupported_net_errors],
        "groups": groups,
    }


def polygon_bbox(polygon: Sequence[Sequence[float]]) -> list[float]:
    return _polygon_bbox(polygon)


def _polygons_overlap(
    a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]
) -> bool:
    """Quick polygon overlap: bbox + edge intersections + vertex containment."""
    if not _bboxes_intersect(_polygon_bbox(a), _polygon_bbox(b)):
        return False
    na, nb = len(a), len(b)
    for i in range(na):
        p1, p2 = a[i], a[(i + 1) % na]
        for j in range(nb):
            if _segment_intersects_segment(p1, p2, b[j], b[(j + 1) % nb]):
                return True
    # Check if any vertex of a is inside b
    for pt in a:
        if _point_in_polygon(pt, b):
            return True
    for pt in b:
        if _point_in_polygon(pt, a):
            return True
    return False


def _point_in_polygon(
    point: Sequence[float], polygon: Sequence[Sequence[float]]
) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    px, py = float(point[0]), float(point[1])
    for i in range(n):
        yi = float(polygon[i][1])
        yj = float(polygon[j][1])
        xi = float(polygon[i][0])
        xj = float(polygon[j][0])
        if (yi > py) != (yj > py):
            x_intersect = xj + (xi - xj) * (py - yj) / (yi - yj)
            if px < x_intersect:
                inside = not inside
        j = i
    return inside
