"""PUBLIC test: 3D exits normalize layout rings to AREA semantics.

GDS cannot store holes: a drawn "rectangle with a hole" arrives as one
ring with a keyhole cut-line, and some flows leave that cut-line 1 dbu
WIDE. The 2.5d view normalizes internally so the cut-line never shows;
the 3D exits must do the same — before this contract existed, the
xsection engine's float crossing math grew a phantom 1-dbu mask gap on
~half the cuts of a plain zero-width GDS donut (live-proven 10/24),
and a process taper widened it by 2*thickness*tan(angle) into a canyon
that was never drawn (1 nm slit -> 2.52 um groove at taper=40, t=1.5).

The contract (user ruling): a ZERO-WIDTH cut-line is pure storage
artifact — always dissolved silently. A slit with real width (>= 1
dbu) may be DRAWN intent (nanogaps are real devices) — it is KEPT and
a warning is reported; welding it is the user's explicit choice
(weld_slits_dbu > 0).

Layer 1: weld_region/layer_region contract (klayout only).
Layer 2: fast-mode extraction (adds shapely).
Layer 3: engine sweep regression (adds klayout_pyxs).
"""

import math

import pytest

kdb = pytest.importorskip("klayout.db")

from klink.domains.imaging._util import (          # noqa: E402
    DEFAULT_WELD_DBU, layer_region, weld_region)
from klink.domains.imaging.mesh3d import Mesh3DError  # noqa: E402

# realistic sidewall angle (shipped demo.pyxs uses 5; keep examples
# and tests <= 10 degrees so the layout shape stays recognizable —
# the amplification law needs no exaggeration: even at 10 degrees a
# kept 1 nm slit under 0.5 um of metal opens to ~0.18 um)
TAPER_RECIPE = """\
delta(5 * dbu)
lmetal = layer("3/0")
sub = bulk()
metal = mask(lmetal).grow(0.5, taper=10)
"""


def donut_gds(tmp_path, slit_w_dbu, name):
    """Rectangle with a circular hole; the hole's connection to the
    outside is a VERTICAL slit of the given width (0 = let the GDS
    writer emit its zero-width keyhole cut-line)."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("TOP")
    li = ly.layer(3, 0)
    n = 64
    hole = kdb.Region(kdb.Polygon(
        [kdb.Point(int(1000 * math.cos(2 * math.pi * i / n)),
                   int(1000 * math.sin(2 * math.pi * i / n)))
         for i in range(n)]))
    donut = kdb.Region(kdb.Box(-3000, -2500, 3000, 2500)) - hole
    if slit_w_dbu:
        donut -= kdb.Region(kdb.Box(0, -2500, slit_w_dbu, 0))
    for p in donut.each():
        top.shapes(li).insert(p)
    path = tmp_path / f"{name}.gds"
    ly.write(str(path))
    return str(path)


def read_layer(path):
    ly = kdb.Layout()
    ly.read(path)
    return ly, ly.top_cell()


# --------------------------------------------------------------------- #
# Layer 1: the normalization contract itself
# --------------------------------------------------------------------- #

def test_zero_width_keyhole_restores_hole_silently(tmp_path):
    ly, top = read_layer(donut_gds(tmp_path, 0, "kh0"))
    warnings = []
    region = layer_region(ly, top, "3/0", Mesh3DError,
                          warnings=warnings)
    polys = list(region.each())
    assert len(polys) == 1
    assert polys[0].holes() == 1
    assert warnings == []          # pure artifact, nothing to report


def test_real_slit_is_kept_and_warned_unless_welded(tmp_path):
    path = donut_gds(tmp_path, 1, "kh1")
    ly, top = read_layer(path)
    # default: the 1-dbu slit may be drawn intent — kept, but warned
    warnings = []
    region = layer_region(ly, top, "3/0", Mesh3DError,
                          warnings=warnings)
    assert all(p.holes() == 0 for p in region.each())
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "hairline_slit"
    assert warnings[0]["layer"] == "3/0"
    assert "weld_slits_dbu" in warnings[0]["note"]
    assert "taper" in warnings[0]["note"]
    # explicit weld: the user decided it is an artifact — welded,
    # nothing left to warn about
    warnings = []
    welded = layer_region(ly, top, "3/0", Mesh3DError, weld_dbu=2,
                          warnings=warnings)
    polys = list(welded.each())
    assert len(polys) == 1 and polys[0].holes() == 1
    assert warnings == []


def test_weld_never_distorts_clean_geometry():
    rect = kdb.Region(kdb.Box(0, 0, 1000, 1000))
    tri = kdb.Region(kdb.Polygon(
        [kdb.Point(0, 0), kdb.Point(1000, 1000), kdb.Point(2000, 0)]))
    for region in (rect, tri):
        before = [str(p) for p in region.dup().merged().each()]
        after = [str(p)
                 for p in weld_region(region, DEFAULT_WELD_DBU).each()]
        assert before == after


def test_missing_layer_is_none(tmp_path):
    ly, top = read_layer(donut_gds(tmp_path, 0, "kh_none"))
    assert layer_region(ly, top, "99/0", Mesh3DError) is None


# --------------------------------------------------------------------- #
# Layer 2: fast-mode extraction sees one polygon with one interior
# --------------------------------------------------------------------- #

def test_fast_extraction_keeps_slit_and_warns(tmp_path):
    pytest.importorskip("shapely")
    from klink.domains.imaging.mesh3d import _layer_shapely
    from shapely.geometry import Polygon as ShPoly
    ly, top = read_layer(donut_gds(tmp_path, 1, "kh_fast"))
    warnings = []
    polys = _layer_shapely(ly, top, "3/0", ShPoly, warnings=warnings)
    assert len(polys) == 1 and len(polys[0].interiors) == 0
    assert [w["layer"] for w in warnings] == ["3/0"]
    # explicit weld -> the hole is restored
    polys = _layer_shapely(ly, top, "3/0", ShPoly, weld_dbu=2)
    assert len(polys) == 1
    assert len(polys[0].interiors) == 1
    expected = 6.0 * 5.0 - math.pi * 1.0 ** 2
    assert abs(polys[0].area - expected) < 0.01


# --------------------------------------------------------------------- #
# Layer 3: the engine sweep — no phantom canyons, either slit kind
# --------------------------------------------------------------------- #

def _section_metal_count(gds, tmp_path, tag, weld_dbu, y):
    """One 2D cross-section at height ``y`` -> (metal solids in the
    section, sidecar warnings). The cut at y=-1.5 crosses the donut
    where it is drawn SOLID (below the hole, through the slit zone);
    y=-0.5 crosses the hole itself, where two solids are the truth."""
    from klink.domains.imaging.xsection_driver import run_xsection
    recipe = tmp_path / f"{tag}.pyxs"
    recipe.write_text(TAPER_RECIPE, encoding="utf-8")
    side = run_xsection(
        gds, str(recipe), [[-3.5, y], [3.5, y]],
        output_dir=str(tmp_path / tag), basename=tag,
        weld_slits_dbu=weld_dbu)
    by_name = {m["name"]: m["shapes"]
               for m in side["outputs"]["stages"][0]["materials"]}
    return by_name["metal"], side["outputs"]["warnings"]


def test_engine_dissolves_zero_width_keyhole(tmp_path):
    pytest.importorskip("klayout_pyxs")
    gds = donut_gds(tmp_path, 0, "sweep0")
    # solid through the slit zone (pre-fix: the engine's float
    # crossing math split even zero-width cut-lines on ~40% of cuts)
    n, warns = _section_metal_count(gds, tmp_path, "kh0a", 0, -1.5)
    assert (n, warns) == (1, [])
    # through the hole two solids are the honest truth
    n, _ = _section_metal_count(gds, tmp_path, "kh0b", 0, -0.5)
    assert n == 2


def test_engine_keeps_real_slit_and_warns(tmp_path):
    pytest.importorskip("klayout_pyxs")
    gds = donut_gds(tmp_path, 1, "sweep1")
    # default: the 1-dbu slit is kept — the section splits, and the
    # sidecar carries exactly one warning for the layer
    n, warns = _section_metal_count(gds, tmp_path, "kh1a", 0, -1.5)
    assert n == 2
    assert [w["layer"] for w in warns] == ["3/0"]
    assert warns[0]["kind"] == "hairline_slit"
    # explicit weld: user declared it an artifact — solid again
    n, warns = _section_metal_count(gds, tmp_path, "kh1b", 2, -1.5)
    assert (n, warns) == (1, [])


def test_sidecar_records_weld_and_warnings(tmp_path):
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging.xsection_driver import run_xsection
    recipe = tmp_path / "taper.pyxs"
    recipe.write_text(TAPER_RECIPE, encoding="utf-8")
    gds = donut_gds(tmp_path, 1, "sidecar")
    side = run_xsection(
        gds, str(recipe), [[-3.5, -1.5], [3.5, -1.5]],
        output_dir=str(tmp_path / "out"), basename="weld")
    assert side["inputs"]["params"]["weld_slits_dbu"] == \
        DEFAULT_WELD_DBU == 0
    warns = side["outputs"]["warnings"]
    assert [w["layer"] for w in warns] == ["3/0"]
    assert "weld_slits_dbu" in warns[0]["note"]
