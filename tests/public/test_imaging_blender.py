"""PUBLIC test: the Blender exit.

bpy is a ~300MB optional dep and renders take seconds, so the
FUNCTIONAL tests are opt-in: set KLINK_BPY_TESTS=1 (full venv runs them
on demand; pre-commit/CI stay fast).  The contract tests (instructive
missing-dep error, runner usage) always run.
"""

import json
import os
import subprocess
import sys

import pytest

HEAVY = pytest.mark.skipif(
    not os.environ.get("KLINK_BPY_TESTS"),
    reason="heavy bpy renders; set KLINK_BPY_TESTS=1 to run")


def test_missing_bpy_error_is_instructive(monkeypatch):
    from klink.domains.imaging import blender_scene as bs

    monkeypatch.setitem(sys.modules, "bpy", None)
    with pytest.raises(bs.BlenderSceneError, match="pip install bpy"):
        bs._bpy()


def test_runner_usage_error():
    from klink.domains.imaging.blender_scene import main

    assert main(["prog"]) == 2


STACK = {
    "format": "klink_visual_stack_v1",
    "name": "fig",
    "layers": [
        {"layer": "10/0", "z0_um": 0.03, "z1_um": 0.09, "name": "MoS2",
         "kind": "lattice", "motif": "mos2"},
        {"layer": "20/0", "z0_um": 0.02, "z1_um": 0.16, "name": "Au",
         "color": "#d4af37", "metallic": 1.0},
    ],
}

SLABS = [{"name": "SiO2", "z0_um": -0.28, "z1_um": 0.0,
          "color": "#a1b8cc", "alpha": 0.85},
         {"name": "Si", "z0_um": -0.7, "z1_um": -0.28,
          "color": "#1a1c20"}]


@pytest.fixture()
def device(tmp_path):
    kdb = pytest.importorskip("klayout.db")
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    pytest.importorskip("shapely")
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEV2D")
    um = 1000
    flake = [(0.5, 0.4), (1.6, 0.3), (2.2, 0.8), (1.9, 1.6),
             (0.8, 1.7), (0.4, 1.1)]
    top.shapes(ly.layer(10, 0)).insert(kdb.Polygon(
        [kdb.Point(int(x * um), int(y * um)) for x, y in flake]))
    for x0, x1 in [(0.0, 0.7), (1.9, 2.6)]:
        top.shapes(ly.layer(20, 0)).insert(
            kdb.Box(int(x0 * um), 500, int(x1 * um), 1500))
    gds = tmp_path / "dev2d.gds"
    ly.write(str(gds))
    return str(gds), tmp_path


@HEAVY
def test_device_figure_lattice_and_solids(device):
    pytest.importorskip("bpy")
    from PIL import Image

    from klink.domains.imaging.blender_scene import render_device_figure
    from klink.domains.imaging.visual_stack import VisualStack

    gds, tmp = device
    png = str(tmp / "fig.png"); blend = str(tmp / "fig.blend")
    report = render_device_figure(
        gds, VisualStack.from_dict(STACK), png, blend, slabs=SLABS,
        lattice_a_um=0.12, samples=6, resolution=(320, 200))
    assert report["atoms"] > 50 and report["bonds"] > 50
    assert report["solids"] == 2 + 2          # 2 slabs + 2 electrodes
    im = Image.open(png)
    assert im.mode == "RGBA" and im.size == (320, 200)
    assert os.path.getsize(blend) > 10000


@HEAVY
def test_die_mode_via_subprocess_runner(device):
    pytest.importorskip("bpy")
    pytest.importorskip("trimesh")
    from klink.domains.imaging.mesh3d import build_glb_fast
    from klink.domains.imaging.visual_stack import VisualStack

    gds, tmp = device
    stack = VisualStack.from_dict({
        "format": "klink_visual_stack_v1",
        "layers": [{"layer": "20/0", "z0_um": 0.0, "z1_um": 0.15,
                    "name": "Au", "color": "#d4af37",
                    "metallic": 1.0}]})
    glb = str(tmp / "die.glb")
    import sys as _sys
    import pathlib as _pl
    _ex = (_pl.Path(__file__).resolve().parents[2]
           / "examples_klink" / "public" / "imaging")
    _sys.path.insert(0, str(_ex))
    try:
        from viewer_style import STYLE as _VS
    finally:
        _sys.path.remove(str(_ex))
    build_glb_fast(gds, stack, glb, _VS)
    payload = {"mode": "die", "glb": glb,
               "out_png": str(tmp / "die.png"),
               "out_blend": str(tmp / "die.blend"),
               "samples": 6, "resolution": [320, 200],
               "out_report": str(tmp / "rep.json")}
    ppath = tmp / "payload.json"
    ppath.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m",
         "klink.domains.imaging.blender_scene", str(ppath)],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads((tmp / "rep.json").read_text())
    assert report["kind"] == "die" and report["meshes"] >= 1
    assert os.path.exists(payload["out_png"])
    assert os.path.exists(payload["out_blend"])
