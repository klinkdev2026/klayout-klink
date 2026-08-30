"""PUBLIC test: tapered extrusion (sidewall_deg) + model-then-cut.

The user contract, verbatim from the ruling that retired the 3D
process sweep: the whole model is DRAWN (extruded/lofted — a circle
stays a smooth prism, walls tilt as one clean face), and a cutaway is
the finished model boolean-cut afterwards, so a section shows only on
the cut faces. sidewall_deg is a per-layer STACK declaration (a
process fact the example owns); klink's default is 0 = vertical.

Geometry layer needs shapely+trimesh; cutaway additionally needs
manifold3d (instructive error without it, tested).
"""

import math

import pytest

from klink.domains.imaging.visual_stack import (
    VisualLayer, VisualStack, VisualStackError)

kdb = pytest.importorskip("klayout.db")


# --------------------------------------------------------------------- #
# declaration contract (no heavy deps)
# --------------------------------------------------------------------- #

def test_sidewall_deg_roundtrips_and_defaults_vertical():
    vl = VisualLayer(layer="1/0", z0_um=0.0, z1_um=0.5, name="m",
                     color="#808080")
    assert vl.sidewall_deg == 0.0
    stack = VisualStack(layers=(VisualLayer(
        layer="1/0", z0_um=0.0, z1_um=0.5, name="m", color="#808080",
        sidewall_deg=8.0),))
    again = VisualStack.from_dict(stack.to_dict())
    assert again.layers[0].sidewall_deg == 8.0


def test_sidewall_deg_range_is_validated():
    with pytest.raises(VisualStackError, match="sidewall_deg"):
        VisualLayer(layer="1/0", z0_um=0.0, z1_um=0.5, name="m",
                    color="#808080", sidewall_deg=90.0)
    with pytest.raises(VisualStackError, match="sidewall_deg"):
        VisualLayer(layer="1/0", z0_um=0.0, z1_um=0.5, name="m",
                    color="#808080", sidewall_deg=-1.0)


# --------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------- #

class _Style:
    roughness = 0.5


def _gds_with(tmp_path, name, draw):
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("T")
    draw(ly, top)
    path = tmp_path / f"{name}.gds"
    ly.write(str(path))
    return str(path)


def _stack(deg, z1=1.0):
    return VisualStack.from_dict({
        "format": "klink_visual_stack_v1",
        "layers": [{"name": "m", "layer": "1/0", "z0_um": 0.0,
                    "z1_um": z1, "color": "#808080",
                    "sidewall_deg": deg}],
    })


def _volume(glb_path):
    import trimesh
    scene = trimesh.load(glb_path)
    return {name: g for name, g in scene.geometry.items()}


def test_tapered_square_is_an_exact_frustum(tmp_path):
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    from klink.domains.imaging.mesh3d import build_glb_fast
    gds = _gds_with(tmp_path, "sq", lambda ly, top: top.shapes(
        ly.layer(1, 0)).insert(kdb.Box(0, 0, 4000, 4000)))
    glb = str(tmp_path / "sq.glb")
    r = build_glb_fast(gds, _stack(45.0), glb, _Style())
    assert r["warnings"] == []
    m = _volume(glb)["m"]
    # 4x4 base, t=1, 45 degrees: V = integral (4-2z)^2 dz = 56/6
    assert m.is_watertight
    assert abs(abs(m.volume) - 56.0 / 6.0) < 1e-6


def test_tapered_donut_keeps_a_smooth_growing_hole(tmp_path):
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    import numpy as np
    from klink.domains.imaging.mesh3d import build_glb_fast

    def draw(ly, top):
        hole = kdb.Region(kdb.Polygon(
            [kdb.Point(int(1000 * math.cos(2 * math.pi * i / 64)),
                       int(1000 * math.sin(2 * math.pi * i / 64)))
             for i in range(64)]))
        for p in (kdb.Region(kdb.Box(-3000, -3000, 3000, 3000))
                  - hole).each():
            top.shapes(ly.layer(1, 0)).insert(p)

    gds = _gds_with(tmp_path, "donut", draw)
    glb = str(tmp_path / "donut.glb")
    r = build_glb_fast(gds, _stack(10.0, z1=0.5), glb, _Style())
    assert r["warnings"] == []
    m = _volume(glb)["m"]
    assert m.is_watertight
    # glTF is y-up: layout z landed on axis 1. The hole rim at the TOP
    # must sit farther from the axis than at the BOTTOM (the hole
    # grows), and stay smooth: radii within each cap vary by less than
    # a facet's sagitta, far less than the 0.088 um taper offset.
    v = m.vertices
    rad = np.hypot(v[:, 0], v[:, 2])
    inner = rad < 1.5
    r_bot = rad[inner & (v[:, 1] < 0.01)]
    r_top = rad[inner & (v[:, 1] > 0.49)]
    d = 0.5 * math.tan(math.radians(10.0))
    assert abs(r_top.mean() - r_bot.mean() - d) < 0.01
    assert r_bot.std() < 0.01 and r_top.std() < 0.01


def test_collapse_falls_back_vertical_and_warns(tmp_path):
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    from klink.domains.imaging.mesh3d import build_glb_fast

    def draw(ly, top):
        li = ly.layer(1, 0)
        top.shapes(li).insert(kdb.Box(0, 0, 4000, 4000))     # survives
        top.shapes(li).insert(kdb.Box(6000, 0, 6800, 4000))  # 0.8 < 2d
    gds = _gds_with(tmp_path, "mix", draw)
    glb = str(tmp_path / "mix.glb")
    r = build_glb_fast(gds, _stack(45.0), glb, _Style())     # d = 1 um
    assert [w["kind"] for w in r["warnings"]] == ["taper_collapse"]
    note = r["warnings"][0]["note"]
    assert "VERTICAL" in note and "sidewall_deg" in note
    m = _volume(glb)["m"]
    # frustum + the narrow bar drawn vertical (0.8 * 4 * 1)
    assert abs(abs(m.volume) - (56.0 / 6.0 + 3.2)) < 1e-6


def test_cutaway_cuts_the_finished_model(tmp_path):
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    pytest.importorskip("manifold3d")
    from klink.domains.imaging.mesh3d import build_glb_fast
    gds = _gds_with(tmp_path, "cut", lambda ly, top: top.shapes(
        ly.layer(1, 0)).insert(kdb.Box(0, 0, 4000, 4000)))
    glb = str(tmp_path / "cut.glb")
    r = build_glb_fast(gds, _stack(0.0), glb, _Style(),
                       cutaway_um=[0.0, 0.0, 4.0, 2.0])
    assert r["cutaway_um"] == [0.0, 0.0, 4.0, 2.0]
    m = _volume(glb)["m"]
    assert abs(abs(m.volume) - 8.0) < 1e-6      # half of 4*4*1
    # a keep-region that misses everything is refused with the cell
    # bounds in hand, not written as an empty file
    from klink.domains.imaging.mesh3d import Mesh3DError
    with pytest.raises(Mesh3DError, match="keep-region"):
        build_glb_fast(gds, _stack(0.0), str(tmp_path / "cut2.glb"),
                       _Style(), cutaway_um=[10.0, 10.0, 12.0, 12.0])


def test_cutaway_without_manifold_is_instructive(tmp_path, monkeypatch):
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    import sys
    from klink.domains.imaging.mesh3d import Mesh3DError, build_glb_fast
    gds = _gds_with(tmp_path, "nm", lambda ly, top: top.shapes(
        ly.layer(1, 0)).insert(kdb.Box(0, 0, 4000, 4000)))
    monkeypatch.setitem(sys.modules, "manifold3d", None)
    with pytest.raises(Mesh3DError, match="pip install manifold3d"):
        build_glb_fast(gds, _stack(0.0), str(tmp_path / "nm.glb"),
                       _Style(), cutaway_um=[0.0, 0.0, 2.0, 2.0])


def test_bad_cutaway_box_is_instructive(tmp_path):
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    pytest.importorskip("manifold3d")
    from klink.domains.imaging.mesh3d import Mesh3DError, build_glb_fast
    gds = _gds_with(tmp_path, "bb", lambda ly, top: top.shapes(
        ly.layer(1, 0)).insert(kdb.Box(0, 0, 4000, 4000)))
    with pytest.raises(Mesh3DError, match="x1>x0"):
        build_glb_fast(gds, _stack(0.0), str(tmp_path / "bb.glb"),
                       _Style(), cutaway_um=[4.0, 0.0, 0.0, 2.0])


def test_tapered_build_is_deterministic(tmp_path):
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    import hashlib
    from klink.domains.imaging.mesh3d import build_glb_fast

    def sha(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    gds = _gds_with(tmp_path, "det", lambda ly, top: top.shapes(
        ly.layer(1, 0)).insert(kdb.Box(0, 0, 4000, 4000)))
    a = str(tmp_path / "a.glb")
    b = str(tmp_path / "b.glb")
    build_glb_fast(gds, _stack(10.0), a, _Style())
    build_glb_fast(gds, _stack(10.0), b, _Style())
    assert sha(a) == sha(b)
