"""Pure geometry for Region claim: boolean compose + safe ellipse discretization.

MECHANISM ONLY, and deliberately importable OFF-KLayout: everything here uses
the `pya` geometry classes that ship with `pip install klayout` (klayout.db
compat), no view/application access. CI unit tests import this module directly
(same pattern as `methods/port_m.py` pure validators).

Contracts implemented here (docs/REGION_INTENT_DESIGN.md):

* sect.4.1 three roles: result = union(include) [ intersect  union(clip)] [ minus  union(exclude)].
* sect.4.1 single connected component: >1 merged polygon => RegionGeomError
  listing each island's bbox, instructing the caller to claim separately.
* sect.4.2 / red line 9: discretization error always SHRINKS the writable area.
  include/clip ellipses are inscribed (polygon <= ellipse), exclude ellipses
  are circumscribed (polygon >= ellipse). Integer rounding follows the same
  direction: inscribed vertices round toward the ellipse center,
  circumscribed vertices round away from it.
* Multi-point rulers are EXACT polygons (polygon_from_points): auto-closed,
  integer-DBU vertices as drawn, no discretization for any role; fewer than 3
  distinct points, collinear, and self-touching outlines are refused.
* Canonical contour string encoding (integer DBU) with explicit limits;
  over-limit input is an instructive error, never a silent truncation.
"""

from __future__ import annotations

import math

import pya


# Explicit limits (third-review fix 4): exceeding any of them is an instructive
# error. Sized so the canonical string stays well inside what a GDS PCell
# context comfortably carries.
MAX_TOTAL_VERTICES = 4000
MAX_HOLES = 32
MAX_CONTOUR_CHARS = 120_000

ROLE_INCLUDE = "include"
ROLE_CLIP = "clip"
ROLE_EXCLUDE = "exclude"
ROLES = (ROLE_INCLUDE, ROLE_CLIP, ROLE_EXCLUDE)

DEFAULT_NPOINTS = 64
MIN_NPOINTS = 8
MAX_NPOINTS = 1024


class RegionGeomError(ValueError):
    """Geometry contract violation. `hint` is agent-actionable."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _require_npoints(npoints: int) -> int:
    n = int(npoints)
    if n < MIN_NPOINTS or n > MAX_NPOINTS:
        raise RegionGeomError(
            "npoints must be in [%d, %d], got %d" % (MIN_NPOINTS, MAX_NPOINTS, n),
            hint="pass npoints=%d (default) or another value in range" % DEFAULT_NPOINTS,
        )
    return n


def box_polygon(p1_dbu: tuple[int, int], p2_dbu: tuple[int, int]) -> pya.Polygon:
    """Exact box from two corner points (any corner order)."""
    x0, x1 = sorted((int(p1_dbu[0]), int(p2_dbu[0])))
    y0, y1 = sorted((int(p1_dbu[1]), int(p2_dbu[1])))
    if x0 == x1 or y0 == y1:
        raise RegionGeomError(
            "box ruler is degenerate (zero width or height)",
            hint="drag a ruler with nonzero extent, then claim again",
        )
    return pya.Polygon(pya.Box(x0, y0, x1, y1))


def ellipse_polygon(
    p1_dbu: tuple[int, int],
    p2_dbu: tuple[int, int],
    npoints: int,
    *,
    circumscribed: bool,
) -> pya.Polygon:
    """Discretize the ellipse inscribed in the p1/p2 bbox as an N-gon.

    circumscribed=False -> inscribed N-gon (polygon <= ellipse): vertices ON
    the ellipse, rounded TOWARD the center so integer rounding can never
    poke outside.
    circumscribed=True -> circumscribed N-gon (polygon >= ellipse): the
    circle's tangent polygon mapped through the axis-aligned affine map
    (tangency is affine-invariant), vertices rounded AWAY from the center.
    """
    n = _require_npoints(npoints)
    x0, x1 = sorted((int(p1_dbu[0]), int(p2_dbu[0])))
    y0, y1 = sorted((int(p1_dbu[1]), int(p2_dbu[1])))
    if x0 == x1 or y0 == y1:
        raise RegionGeomError(
            "ellipse ruler is degenerate (zero width or height)",
            hint="drag a ruler with nonzero extent, then claim again",
        )
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    a = (x1 - x0) / 2.0
    b = (y1 - y0) / 2.0

    if circumscribed:
        # Circumscribed polygon of the unit circle: vertices at angles
        # theta_k + pi/n with radius 1/cos(pi/n); edges tangent at theta_k.
        # Tangency is affine-invariant, so scaling the semi-axes gives the
        # circumscribed polygon of the ellipse. Add a 2-DBU radial pad: the
        # per-vertex outward rounding below moves each COORDINATE outward,
        # but two rounded endpoints can still rotate an edge inward by a
        # sub-DBU amount; the pad strictly dominates that perturbation so
        # polygon >= ellipse holds exactly, not just to within a DBU.
        scale = 1.0 / math.cos(math.pi / n)
        pad = 2.0
        phase = math.pi / n
    else:
        scale = 1.0
        pad = 0.0
        phase = 0.0

    pts = []
    for k in range(n):
        theta = 2.0 * math.pi * k / n + phase
        fx = cx + (a * scale + pad) * math.cos(theta)
        fy = cy + (b * scale + pad) * math.sin(theta)
        if circumscribed:
            # round away from center: containment of the true ellipse survives
            ix = int(math.floor(fx)) if fx < cx else int(math.ceil(fx))
            iy = int(math.floor(fy)) if fy < cy else int(math.ceil(fy))
        else:
            # round toward center: polygon stays inside the true ellipse
            ix = int(math.ceil(fx)) if fx < cx else int(math.floor(fx))
            iy = int(math.ceil(fy)) if fy < cy else int(math.floor(fy))
        pts.append(pya.Point(ix, iy))

    poly = pya.Polygon(pts)
    if poly.num_points() < 3:
        raise RegionGeomError(
            "ellipse ruler is too small to discretize at this dbu",
            hint="draw a larger ellipse or reduce npoints",
        )
    return poly


def _dedupe_consecutive(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in points:
        q = (int(p[0]), int(p[1]))
        if not out or out[-1] != q:
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()  # an explicitly closed ring: the closing edge is implicit
    return out


def _orient(a, b, c) -> int:
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (v > 0) - (v < 0)


def _on_segment(a, b, p) -> bool:
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _segments_touch(a, b, c, d) -> bool:
    """Closed-segment intersection test (proper crossing OR touching)."""
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(a, b, c):
        return True
    if o2 == 0 and _on_segment(a, b, d):
        return True
    if o3 == 0 and _on_segment(c, d, a):
        return True
    if o4 == 0 and _on_segment(c, d, b):
        return True
    return False


def polygon_from_points(points_dbu, dbu: float = 1.0) -> pya.Polygon:
    """Exact polygon from a multi-point ruler: auto-closed first..last..first.

    Vertices are integer DBU as drawn -- no discretization, no
    inscribed/circumscribed asymmetry (that is ellipse-only), so the
    contribution is exact for include AND exclude. Refuses, instructively:
    fewer than 3 distinct points, zero area (collinear), and any
    self-touching outline (a proper crossing or a vertex landing on a
    non-adjacent edge) naming the offending segment pair. `dbu` is only
    used to print coordinates in microns in those messages.
    """
    pts = _dedupe_consecutive([(p[0], p[1]) for p in points_dbu])
    n = len(pts)
    if n < 3:
        raise RegionGeomError(
            "ruler has %d distinct point(s); a polygon outline needs at "
            "least 3" % n,
            hint="click a third point (multi-point ruler), or use the Box / "
                 "Ellipse ruler template for a 2-point region",
        )
    if all(_orient(pts[0], pts[1], q) == 0 for q in pts[2:]):
        raise RegionGeomError(
            "polygon ruler is degenerate (zero area: all points collinear)",
            hint="move a point off the line so the outline encloses an area, "
                 "then claim again",
        )

    def um(p):
        return "(%g, %g)" % (p[0] * dbu, p[1] * dbu)

    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent segments share a vertex by design
            c, d = pts[j], pts[(j + 1) % n]
            if _segments_touch(a, b, c, d):
                raise RegionGeomError(
                    "polygon ruler is self-intersecting: segment %d %s->%s "
                    "meets segment %d %s->%s" % (i, um(a), um(b), j, um(c), um(d)),
                    hint="redraw the outline without crossings (a simple "
                         "ring; use several rulers with roles for complex "
                         "shapes) -- klink refuses to guess an even-odd fill",
                )
    # Guard only: a simple non-collinear ring always has nonzero area (a
    # bowtie also has zero signed area, but it was refused just above).
    area2 = 0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        area2 += x0 * y1 - x1 * y0
    if area2 == 0:
        raise RegionGeomError(
            "polygon ruler is degenerate (zero area: all points collinear)",
            hint="move a point off the line so the outline encloses an area, "
                 "then claim again",
        )

    poly = pya.Polygon([pya.Point(x, y) for x, y in pts])
    if poly.num_points() < 3:
        raise RegionGeomError(
            "polygon ruler collapsed to fewer than 3 points",
            hint="draw a larger outline",
        )
    return poly


def compose(
    include: list[pya.Polygon],
    clip: list[pya.Polygon],
    exclude: list[pya.Polygon],
) -> pya.Polygon:
    """Boolean compose per sect.4.1 and enforce the single-component contract.

    Returns ONE merged polygon (holes allowed) in the same integer-DBU space
    as the inputs.
    """
    if not include:
        raise RegionGeomError(
            "at least one include ruler is required",
            hint="assign role 'include' to the ruler(s) that outline the region",
        )
    region = pya.Region()
    for poly in include:
        region.insert(poly)
    region.merge()

    if clip:
        clip_region = pya.Region()
        for poly in clip:
            clip_region.insert(poly)
        clip_region.merge()
        region = region & clip_region

    if exclude:
        excl_region = pya.Region()
        for poly in exclude:
            excl_region.insert(poly)
        excl_region.merge()
        region = region - excl_region

    region.merge()
    components = [p.dup() for p in region.each_merged()]
    if not components:
        raise RegionGeomError(
            "boolean result is empty (clip/exclude removed everything)",
            hint="check the ruler roles: include minus exclude intersected "
                 "with clip left no area",
        )
    if len(components) > 1:
        boxes = ", ".join(
            "[%d,%d,%d,%d]" % (p.bbox().left, p.bbox().bottom, p.bbox().right, p.bbox().top)
            for p in components
        )
        raise RegionGeomError(
            "boolean result has %d disconnected islands; a Region must be "
            "exactly one connected component (bboxes dbu: %s)" % (len(components), boxes),
            hint="claim each island separately (one region.claim per island)",
        )
    result = components[0]
    _check_limits(result)
    return result


def _check_limits(poly: pya.Polygon) -> None:
    holes = poly.holes()
    if holes > MAX_HOLES:
        raise RegionGeomError(
            "region has %d holes; limit is %d" % (holes, MAX_HOLES),
            hint="simplify the exclude rulers or split into several regions",
        )
    total = poly.num_points()
    if total > MAX_TOTAL_VERTICES:
        raise RegionGeomError(
            "region has %d vertices; limit is %d" % (total, MAX_TOTAL_VERTICES),
            hint="reduce npoints or simplify the ruler set",
        )


def anchor_of(poly: pya.Polygon) -> tuple[int, int]:
    """Anchor = bbox lower-left (sect.3.1 coordinate contract)."""
    bbox = poly.bbox()
    return int(bbox.left), int(bbox.bottom)


def to_local(poly: pya.Polygon) -> tuple[pya.Polygon, tuple[int, int]]:
    """Shift the polygon so its anchor sits at the local origin.

    Returns (local_polygon, anchor_target_cell_dbu).
    """
    ax, ay = anchor_of(poly)
    local = poly.dup()
    local.move(-ax, -ay)
    return local, (ax, ay)


# ---------------------------------------------------------------------------
# Canonical contour string encoding (integer DBU)
#
#   hull:  "x,y;x,y;..."       hole separator: "|"
#   e.g.   "0,0;100,0;100,80;0,80|10,10;20,10;20,20;10,20"
# ---------------------------------------------------------------------------


def encode_contours(poly: pya.Polygon) -> str:
    _check_limits(poly)
    parts = [_encode_ring(poly.each_point_hull())]
    for h in range(poly.holes()):
        parts.append(_encode_ring(poly.each_point_hole(h)))
    text = "|".join(parts)
    if len(text) > MAX_CONTOUR_CHARS:
        raise RegionGeomError(
            "encoded contours are %d chars; limit is %d" % (len(text), MAX_CONTOUR_CHARS),
            hint="reduce npoints or simplify the ruler set",
        )
    return text


def _encode_ring(points) -> str:
    return ";".join("%d,%d" % (int(pt.x), int(pt.y)) for pt in points)


def decode_contours(text: str) -> pya.Polygon:
    if not text or not str(text).strip():
        raise RegionGeomError(
            "contours string is empty",
            hint="the Region PCell parameter is corrupt; re-claim the region",
        )
    text = str(text)
    if len(text) > MAX_CONTOUR_CHARS:
        raise RegionGeomError(
            "contours string exceeds %d chars" % MAX_CONTOUR_CHARS,
            hint="the Region PCell parameter is corrupt; re-claim the region",
        )
    rings = []
    for ring_text in text.split("|"):
        pts = []
        for pair in ring_text.split(";"):
            xy = pair.split(",")
            if len(xy) != 2:
                raise RegionGeomError(
                    "malformed contours string near %r" % pair[:40],
                    hint="expected 'x,y;x,y;...' integer DBU pairs",
                )
            try:
                pts.append(pya.Point(int(xy[0]), int(xy[1])))
            except ValueError:
                raise RegionGeomError(
                    "non-integer coordinate in contours near %r" % pair[:40],
                    hint="contours must be integer DBU",
                )
        if len(pts) < 3:
            raise RegionGeomError(
                "contour ring has fewer than 3 points",
                hint="the Region PCell parameter is corrupt; re-claim the region",
            )
        rings.append(pts)
    poly = pya.Polygon(rings[0])
    for hole in rings[1:]:
        poly.insert_hole(hole)
    _check_limits(poly)
    return poly
