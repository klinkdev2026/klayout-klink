"""Offline contract: every klink marker PCell draws ZERO-AREA outlines.

Marker doctrine (Port / Anchor / Region):
a marker is a pure mark -- a closed width-0 path that renders as a
constant 1-px line, never occludes user geometry, is invisible to
booleans/metrics, and keeps the bbox of the filled shape it replaced.
The corridor anchor is the deliberate exception: its path width IS the
corridor the router must honor.

Runs against the pip ``klayout`` module (pya compat): the PCell modules
only use db classes, so the libraries register and produce offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PYTHON = ROOT / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

# Gate on klayout.db, NOT on "pya": another test module plants a minimal
# fake pya in sys.modules, which must not un-skip us on a klayout-less CI
# job (it did once: Library without .layout()).
pytest.importorskip("klayout.db", reason="klayout pip package not installed")

import pya  # noqa: E402

from klink_server import anchor_pcell, port_pcell, region_pcell  # noqa: E402
from klink_server.region_geom import encode_contours  # noqa: E402


@pytest.fixture(scope="module")
def libs():
    port_pcell.register_port_library()
    anchor_pcell.register_anchor_library()
    region_pcell.register_region_library()
    yield


def _variant_shapes(ly: pya.Layout, cell: pya.Cell):
    out = []
    for li in ly.layer_indexes():
        for s in cell.shapes(li).each():
            out.append(s)
    return out


def _assert_zero_area_outlines(ly, cell, *, expect_bbox=None,
                               allow_wide=False):
    shapes = _variant_shapes(ly, cell)
    geom = [s for s in shapes if not s.is_text()]
    assert geom, "marker drew no geometry"
    total = pya.Region()
    for s in geom:
        assert s.is_path(), "marker shape is not a path: %s" % s
        if not allow_wide:
            assert s.path.width == 0, "marker path has width %d" % s.path.width
            pts = list(s.path.each_point())
            assert pts[0] == pts[-1], "outline is not closed"
        total.insert(s.polygon)
    if not allow_wide:
        assert total.area() == 0
    if expect_bbox is not None:
        bb = cell.bbox()
        assert (bb.left, bb.bottom, bb.right, bb.top) == expect_bbox


def test_port_triangle_is_closed_zero_width_path(libs):
    ly = pya.Layout()
    ly.dbu = 0.001
    cell = ly.create_cell("Port", "klink_port", {
        "layer": pya.LayerInfo(999, 99), "port_name": "P1",
        "orientation": 0.0, "width_um": 5.0, "show_label": False,
    })
    # east-pointing 120-degree triangle: base on x=0 spanning +-2500,
    # tip at 2500/sqrt(3) = 1443 -> same bbox the filled polygon had
    _assert_zero_area_outlines(ly, cell, expect_bbox=(0, -2500, 1443, 2500))


def test_port_label_still_drawn_and_orientation_survives(libs):
    ly = pya.Layout()
    ly.dbu = 0.001
    cell = ly.create_cell("Port", "klink_port", {
        "layer": pya.LayerInfo(999, 99), "port_name": "P2", "net": "vdd",
        "orientation": 90.0, "width_um": 5.0, "show_label": True,
    })
    shapes = _variant_shapes(ly, cell)
    texts = [s for s in shapes if s.is_text()]
    assert len(texts) == 1 and texts[0].text.string == "P2:vdd"
    path = [s for s in shapes if s.is_path()][0]
    pts = list(path.each_point())
    # tip rotated to +y: the vertex with max y is the tip
    assert max(p.y for p in pts) == 1443 and path.path.width == 0


def test_bend_and_waypoint_anchors_are_zero_width_outlines(libs):
    ly = pya.Layout()
    ly.dbu = 0.001
    bend = ly.create_cell("BendAnchor", "klink_anchor", {
        "layer": pya.LayerInfo(999, 1), "anchor_id": "A1", "radius_um": 5.0,
    })
    _assert_zero_area_outlines(ly, bend)
    paths = [s for s in _variant_shapes(ly, bend) if s.is_path()]
    assert len(paths) == 2  # triangle + circle, both width 0

    way = ly.create_cell("WaypointAnchor", "klink_anchor", {
        "layer": pya.LayerInfo(999, 1), "anchor_id": "A2",
        "width_um": 10.0, "height_um": 6.0,
    })
    _assert_zero_area_outlines(ly, way, expect_bbox=(-5000, -3000, 5000, 3000))


def test_corridor_anchor_keeps_its_real_width(libs):
    ly = pya.Layout()
    ly.dbu = 0.001
    cor = ly.create_cell("CorridorAnchor", "klink_anchor", {
        "layer": pya.LayerInfo(999, 1), "anchor_id": "A3",
        "width_um": 3.0, "path_points": "0,0;10,0;10,8",
    })
    paths = [s for s in _variant_shapes(ly, cor) if s.is_path()]
    assert len(paths) == 1 and paths[0].path.width == 3000


def test_region_outline_zero_width_with_hole_and_text_inset(libs):
    ly = pya.Layout()
    ly.dbu = 0.001
    outer = pya.Polygon(pya.Box(0, 0, 200000, 120000))
    outer.insert_hole([pya.Point(50000, 50000), pya.Point(80000, 50000),
                       pya.Point(80000, 80000), pya.Point(50000, 80000)])
    cell = ly.create_cell("Region", "klink_region", {
        "layer": pya.LayerInfo(999, 10), "region_name": "R001",
        "contours": encode_contours(outer), "npoints": 64,
    })
    _assert_zero_area_outlines(ly, cell, expect_bbox=(0, 0, 200000, 120000))
    shapes = _variant_shapes(ly, cell)
    assert len([s for s in shapes if s.is_path()]) == 2  # hull + hole
    text = [s for s in shapes if s.is_text()][0]
    # text size = min(w, h) // 8 = 15000; inset = size // 4, independent of
    # any outline width (the old width*2 offset collapsed at width 0)
    assert text.text.size == 15000
    assert (text.text.x, text.text.y) == (3750, 3750)


def test_region_invalid_marker_is_an_outline_too(libs):
    ly = pya.Layout()
    cell = ly.create_cell("Region", "klink_region", {
        "layer": pya.LayerInfo(999, 10), "region_name": "RBAD",
        "contours": "garbage", "npoints": 64,
    })
    _assert_zero_area_outlines(ly, cell, expect_bbox=(0, 0, 1000, 1000))
    assert any(s.is_text() and "INVALID" in s.text.string
               for s in _variant_shapes(ly, cell))
