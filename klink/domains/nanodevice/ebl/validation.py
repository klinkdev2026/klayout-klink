"""Validation helpers for explicit nanodevice route templates."""

from __future__ import annotations


def validate_writefield_crossings(route_paths: list[dict], wf_plan: dict) -> dict:
    """Verify explicit routes cross writefield boundaries only through windows."""

    boundaries = list(wf_plan["boundary_segments_um"])
    anchors = list(wf_plan["corridor_anchor_specs"])
    crossings = []
    violations = []
    for route in route_paths:
        points = route["points_um"]
        for a, b in zip(points, points[1:]):
            for boundary in boundaries:
                hit = _segment_boundary_crossing(a, b, boundary)
                if hit is None:
                    continue
                ok = _crossing_in_window(hit, boundary, anchors)
                rec = {"net": route["net"], "layer": route["layer"], "boundary": boundary, "point_um": hit, "ok": ok}
                crossings.append(rec)
                if not ok:
                    violations.append(rec)
    return {"crossing_count": len(crossings), "violations": violations, "crossings": crossings}


def validate_route_centerline_overlaps(route_paths: list[dict]) -> dict:
    """Find exact same-layer collinear centerline overlaps between nets."""

    segments = []
    for route in route_paths:
        pts = route["points_um"]
        for idx, (a, b) in enumerate(zip(pts, pts[1:])):
            segments.append({"net": route["net"], "layer": route["layer"], "idx": idx, "a": a, "b": b})
    overlaps = []
    for i, first in enumerate(segments):
        for second in segments[i + 1:]:
            if first["layer"] != second["layer"] or first["net"] == second["net"]:
                continue
            hit = _segment_overlap(first["a"], first["b"], second["a"], second["b"])
            if hit is not None:
                overlaps.append({"a": first, "b": second, "overlap_um": hit})
    return {"overlap_count": len(overlaps), "overlaps": overlaps}


def _segment_boundary_crossing(a: list[float], b: list[float], boundary: dict) -> list[float] | None:
    axis = boundary["axis"]
    at = float(boundary["at_um"])
    if axis == "x":
        ax, bx = float(a[0]), float(b[0])
        if (ax - at) * (bx - at) > 0 or ax == bx:
            return None
        t = (at - ax) / (bx - ax)
        if not 0.0 <= t <= 1.0:
            return None
        y = float(a[1]) + t * (float(b[1]) - float(a[1]))
        return [at, y]
    ay, by = float(a[1]), float(b[1])
    if (ay - at) * (by - at) > 0 or ay == by:
        return None
    t = (at - ay) / (by - ay)
    if not 0.0 <= t <= 1.0:
        return None
    x = float(a[0]) + t * (float(b[0]) - float(a[0]))
    return [x, at]


def _crossing_in_window(point: list[float], boundary: dict, anchors: list[dict]) -> bool:
    axis = boundary["axis"]
    at = float(boundary["at_um"])
    for anchor in anchors:
        cx, cy = [float(v) for v in anchor["center_um"]]
        width = float(anchor["width_um"])
        height = float(anchor["height_um"])
        if axis == "x" and abs(cx - at) < 1e-6:
            if abs(float(point[1]) - cy) <= height / 2.0 + 1e-6:
                return True
        if axis == "y" and abs(cy - at) < 1e-6:
            if abs(float(point[0]) - cx) <= width / 2.0 + 1e-6:
                return True
    return False


def _segment_overlap(a0: list[float], a1: list[float], b0: list[float], b1: list[float]) -> list[list[float]] | None:
    eps = 1e-9
    if abs(a0[0] - a1[0]) < eps and abs(b0[0] - b1[0]) < eps and abs(a0[0] - b0[0]) < eps:
        lo = max(min(a0[1], a1[1]), min(b0[1], b1[1]))
        hi = min(max(a0[1], a1[1]), max(b0[1], b1[1]))
        if hi - lo > eps:
            return [[a0[0], lo], [a0[0], hi]]
    if abs(a0[1] - a1[1]) < eps and abs(b0[1] - b1[1]) < eps and abs(a0[1] - b0[1]) < eps:
        lo = max(min(a0[0], a1[0]), min(b0[0], b1[0]))
        hi = min(max(a0[0], a1[0]), max(b0[0], b1[0]))
        if hi - lo > eps:
            return [[lo, a0[1]], [hi, a0[1]]]
    return None
