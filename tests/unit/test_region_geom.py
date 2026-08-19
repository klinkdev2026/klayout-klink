"""Offline unit tests for the pure Region-claim geometry in
`klink_plugin/python/klink_server/region_geom.py`.

`region_geom` never touches a pya.Layout/Cell/View -- it only builds
`pya.Polygon` objects from integer-DBU rulers, composes them with boolean
ops, and (de)serializes the result -- so it can be exercised directly,
off-KLayout, following the same "import the plugin's Python package
directly" pattern used by `test_port_mark_many_validation.py`.

Each test below guards ONE invariant from the module's own contract list
(docs/REGION_INTENT_DESIGN.md, restated in the module docstring):

* box_polygon is corner-order-independent and rejects degenerate rulers.
* ellipse_polygon's inscribed/circumscribed split (red line 9: discretization
  error must always SHRINK the writable area, never grow it) -- inscribed
  vertices must stay ON-OR-INSIDE the true ellipse, circumscribed vertices
  must stay ON-OR-OUTSIDE it, checked at multiple npoints resolutions.
* compose enforces the include/clip/exclude role algebra, the
  single-connected-component contract (multi-island results are rejected,
  not silently merged or truncated), and the "empty result is an error, not
  a silent no-op" rule.
* the canonical contour string encoding round-trips byte-identically and
  enforces its own vertex/char limits instructively rather than truncating.
* anchor_of / to_local implement the bbox-lower-left local-coordinate
  contract used when handing a claimed region to a PCell.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

# klink_server imports pya at package level; the offline compat ships with
# `pip install klayout`. Gate on klayout.db (not "pya"): another test module
# may plant a minimal fake pya in sys.modules, which must not un-skip us.
pytest.importorskip("klayout.db", reason="klayout pip package not installed")

import pya  # noqa: E402

from klink_server.region_geom import (  # noqa: E402
    MAX_TOTAL_VERTICES,
    RegionGeomError,
    anchor_of,
    box_polygon,
    compose,
    decode_contours,
    ellipse_polygon,
    encode_contours,
    to_local,
)


def _region_of(poly: pya.Polygon) -> pya.Region:
    r = pya.Region()
    r.insert(poly)
    return r


def _regions_equal(a: pya.Polygon, b: pya.Polygon) -> bool:
    return (_region_of(a) ^ _region_of(b)).is_empty()


# ----------------------------------------------------------------------
# box_polygon
# ----------------------------------------------------------------------


def test_box_polygon_exact():
    poly = box_polygon((10, 20), (110, 80))
    bbox = poly.bbox()
    assert (bbox.left, bbox.bottom, bbox.right, bbox.top) == (10, 20, 110, 80)

    # corner order is irrelevant -- same box either way.
    flipped = box_polygon((110, 80), (10, 20))
    flipped_bbox = flipped.bbox()
    assert (flipped_bbox.left, flipped_bbox.bottom, flipped_bbox.right, flipped_bbox.top) == (
        10, 20, 110, 80,
    )
    assert _regions_equal(poly, flipped)

    with pytest.raises(RegionGeomError) as excinfo:
        box_polygon((10, 20), (10, 80))
    assert excinfo.value.hint


# ----------------------------------------------------------------------
# ellipse_polygon -- red line 9 safety golden: discretization error must
# always SHRINK the writable area (inscribed) or only ever GROW it in the
# exclude direction (circumscribed), never the reverse.
# ----------------------------------------------------------------------


def test_ellipse_inscribed_containment():
    p1, p2 = (0, 0), (100000, 60000)
    cx, cy, a, b = 50000.0, 30000.0, 50000.0, 30000.0
    for npoints in (8, 64, 257):
        poly = ellipse_polygon(p1, p2, npoints, circumscribed=False)
        for pt in poly.each_point_hull():
            val = ((pt.x - cx) / a) ** 2 + ((pt.y - cy) / b) ** 2
            assert val <= 1.0 + 1e-12, (npoints, pt.x, pt.y, val)
        bbox = poly.bbox()
        assert bbox.left >= 0 and bbox.bottom >= 0
        assert bbox.right <= 100000 and bbox.top <= 60000


def test_ellipse_circumscribed_containment():
    p1, p2 = (0, 0), (100000, 60000)
    cx, cy, a, b = 50000.0, 30000.0, 50000.0, 30000.0
    n_samples = 720
    for npoints in (8, 64, 257):
        poly = ellipse_polygon(p1, p2, npoints, circumscribed=True)
        poly_region = _region_of(poly)

        sample_region = pya.Region()
        for i in range(n_samples):
            theta = 2.0 * math.pi * i / n_samples
            px = cx + a * math.cos(theta)
            py = cy + b * math.sin(theta)
            ix, iy = int(round(px)), int(round(py))
            sample_region.insert(pya.Box(ix, iy, ix + 1, iy + 1))

        uncovered = sample_region - poly_region
        assert uncovered.is_empty(), (
            "npoints=%d: circumscribed polygon does not cover every sampled "
            "true-ellipse point" % npoints
        )


def test_ellipse_npoints_bounds():
    p1, p2 = (0, 0), (100000, 60000)
    with pytest.raises(RegionGeomError):
        ellipse_polygon(p1, p2, 7, circumscribed=False)
    with pytest.raises(RegionGeomError):
        ellipse_polygon(p1, p2, 1025, circumscribed=False)


# ----------------------------------------------------------------------
# compose -- role algebra, single-connected-component contract, empty
# result rejection.
# ----------------------------------------------------------------------


def test_compose_union_two_boxes_L_shape():
    b1 = box_polygon((0, 0), (100, 50))
    b2 = box_polygon((0, 0), (50, 100))
    result = compose([b1, b2], [], [])

    bbox = result.bbox()
    assert (bbox.left, bbox.bottom, bbox.right, bbox.top) == (0, 0, 100, 100)

    area = _region_of(result).area()
    assert area == 100 * 50 + 50 * 100 - 50 * 50


def test_compose_half_disc():
    ellipse = ellipse_polygon((0, 0), (1000, 1000), 64, circumscribed=False)
    clip = box_polygon((0, 0), (1000, 500))
    result = compose([ellipse], [clip], [])

    bbox = result.bbox()
    assert bbox.top <= 500

    full_area = _region_of(ellipse).area()
    half_area = _region_of(result).area()
    assert half_area > 0
    assert half_area < (full_area / 2.0) + 0.02 * full_area


def test_compose_annulus_single_component_with_hole():
    outer = ellipse_polygon((0, 0), (1000, 1000), 64, circumscribed=False)
    inner = ellipse_polygon((300, 300), (700, 700), 64, circumscribed=True)
    # compose() itself raises if the result isn't exactly one component, so
    # simply not raising already proves the single-component contract.
    result = compose([outer], [], [inner])
    assert result.holes() == 1


def test_compose_disconnected_islands_rejected():
    b1 = box_polygon((0, 0), (100, 100))
    b2 = box_polygon((200, 0), (300, 100))
    with pytest.raises(RegionGeomError) as excinfo:
        compose([b1, b2], [], [])
    message = str(excinfo.value)
    assert "2" in message
    assert "[0,0,100,100]" in message
    assert "[200,0,300,100]" in message
    assert "separately" in excinfo.value.hint


def test_compose_empty_result_rejected():
    include = box_polygon((0, 0), (100, 100))
    exclude = box_polygon((-10, -10), (200, 200))
    with pytest.raises(RegionGeomError) as excinfo:
        compose([include], [], [exclude])
    assert excinfo.value.hint


def test_compose_requires_include():
    with pytest.raises(RegionGeomError) as excinfo:
        compose([], [], [])
    assert excinfo.value.hint


# ----------------------------------------------------------------------
# canonical contour string encoding: round-trip + limits.
# ----------------------------------------------------------------------


def test_encode_decode_roundtrip():
    outer = ellipse_polygon((0, 0), (1000, 1000), 64, circumscribed=False)
    inner = ellipse_polygon((300, 300), (700, 700), 64, circumscribed=True)
    poly = compose([outer], [], [inner])

    text = encode_contours(poly)
    decoded = decode_contours(text)

    assert _regions_equal(poly, decoded)
    assert encode_contours(decoded) == text


def test_encode_limits():
    # MAX_TOTAL_VERTICES=4000; build a polygon comfortably over that limit
    # directly (not via ellipse_polygon, which is capped at 1024 points).
    # pya.Polygon can compress a handful of near-collinear integer points
    # on construction, so ask for a healthy margin above the limit.
    n = MAX_TOTAL_VERTICES + 300
    radius = 1_000_000
    pts = []
    for k in range(n):
        theta = 2.0 * math.pi * k / n
        pts.append(pya.Point(int(round(radius * math.cos(theta))), int(round(radius * math.sin(theta)))))
    poly = pya.Polygon(pts)
    assert poly.num_points() > MAX_TOTAL_VERTICES

    with pytest.raises(RegionGeomError) as excinfo:
        encode_contours(poly)
    assert str(MAX_TOTAL_VERTICES) in str(excinfo.value)


def test_decode_malformed():
    for bad in ("1,2;3", "", "a,b;c,d;e,f"):
        with pytest.raises(RegionGeomError) as excinfo:
            decode_contours(bad)
        assert excinfo.value.hint, bad


# ----------------------------------------------------------------------
# anchor_of / to_local
# ----------------------------------------------------------------------


def test_to_local_anchor():
    poly = box_polygon((1000, 2000), (3000, 4000))
    local, anchor = to_local(poly)

    assert anchor == (1000, 2000)
    assert anchor_of(poly) == anchor

    bbox = local.bbox()
    assert (bbox.left, bbox.bottom, bbox.right, bbox.top) == (0, 0, 2000, 2000)
