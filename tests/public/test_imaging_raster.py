"""PUBLIC test: imaging raster exits — SEM top view, section PNGs,
film strip/GIF, and the render=True wiring of run_xsection.

Needs numpy/scipy/pillow (skips without); section/film paths also need
klayout_pyxs.  Determinism via seeded noise + run-twice SHA256.
"""

import hashlib
import json

import pytest

from klink.domains.imaging.visual_stack import VisualStack

STACK = {
    "format": "klink_visual_stack_v1",
    "name": "demo",
    "recipe_styles": {
        "pbulk": {"name": "substrate", "color": "#c8b89a"},
    },
    "layers": [
        {"layer": "1/0", "z0_um": -0.4, "z1_um": 0.0, "name": "well",
         "color": "#e0d2ae", "sem_grey": 0.3, "edge_glow": 0.4,
         "recipe_symbol": "well"},
        {"layer": "2/0", "z0_um": 0.0, "z1_um": 0.2, "name": "metal",
         "color": "#aab8c4", "sem_grey": 0.75, "edge_glow": 0.9,
         "recipe_symbol": "metal"},
    ],
}

RECIPE = """\
l1 = layer("1/0")
l2 = layer("2/0")
# klink-step: bulk
pbulk = bulk()
# klink-step: well
well = mask(l1).grow(0.4, -0.05, mode='round', into=pbulk)
# klink-step: metal
metal = mask(l2).grow(0.2)
"""


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


@pytest.fixture()
def device(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    pytest.importorskip("PIL")
    kdb = pytest.importorskip("klayout.db")
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEV")
    top.shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 6000, 4000))
    top.shapes(ly.layer(2, 0)).insert(kdb.Box(2000, -1000, 4000, 5000))
    gds = tmp_path / "dev.gds"
    ly.write(str(gds))
    recipe = tmp_path / "proc.pyxs"
    recipe.write_text(RECIPE, encoding="utf-8")
    return str(gds), str(recipe), tmp_path


def test_sem_top_deterministic_and_subset(device):
    from PIL import Image

    from klink.domains.imaging.raster import render_sem_png

    gds, _recipe, tmp = device
    stack = VisualStack.from_dict(STACK)
    g1 = str(tmp / "a_sem.png"); c1 = str(tmp / "a_col.png")
    r = render_sem_png(gds, stack, g1, out_color=c1, width_px=400)
    assert r["layers_rendered"] == ["1/0", "2/0"]
    assert Image.open(g1).mode == "L"
    assert Image.open(c1).mode == "RGB"
    g2 = str(tmp / "b_sem.png")
    render_sem_png(gds, stack, g2, width_px=400)
    assert sha(g1) == sha(g2)                      # seeded noise
    g3 = str(tmp / "c_sem.png")
    r3 = render_sem_png(gds, stack, g3, width_px=400, layers=["2/0"])
    assert r3["layers_rendered"] == ["2/0"]
    assert sha(g3) != sha(g1)


def test_xsection_render_film(device):
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    stack = VisualStack.from_dict(STACK)
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "film"), basename="f",
                     steps=True, render=True, stack=stack)
    kinds = {}
    for f in r["outputs"]["files"]:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    assert kinds == {"section_gds": 3, "section_png": 3,
                     "film_strip": 1, "film_gif": 1}
    # ox (blanket deposit) is absent from this recipe; the two mask
    # materials + styled pbulk leave nothing auto-colored
    assert r["outputs"]["render"]["auto_colored"] == []
    # frames form a GIF with one frame per step
    from PIL import Image
    gif = [f["path"] for f in r["outputs"]["files"]
           if f["kind"] == "film_gif"][0]
    im = Image.open(gif)
    assert getattr(im, "n_frames", 1) == 3


def test_section_png_auto_color_reported(device):
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging.raster import render_section_png
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "s"), basename="s")
    sec = r["outputs"]["files"][0]["path"]
    mats = {m["layer"]: m["name"]
            for m in r["outputs"]["stages"][0]["materials"]}
    out = str(tmp / "sec.png")
    # no stack: every material auto-colors, deterministically, reported
    rep = render_section_png(sec, mats, out)
    assert set(rep["auto_colored"]) == {"pbulk", "well", "metal"}
    out2 = str(tmp / "sec2.png")
    render_section_png(sec, mats, out2)
    assert sha(out) == sha(out2)
    # the picture actually CONTAINS the section (a blank render once
    # slipped past file-level checks): >15% non-background pixels
    import numpy as np
    from PIL import Image
    arr = np.asarray(Image.open(out).convert("RGB"))
    bg = (arr == np.array([250, 249, 246])).all(axis=-1)
    assert (~bg).mean() > 0.15, "section render is (nearly) blank"


def test_film_strip_needs_frames(device):
    from klink.domains.imaging.raster import RasterError, film_strip

    _gds, _recipe, tmp = device
    with pytest.raises(RasterError, match="at least one frame"):
        film_strip([], str(tmp / "x.png"), str(tmp / "x.gif"))


def test_sem_top_tool_handler_offline(device):
    from klink.mcp.local_tools import _LOCAL_TOOLS

    gds, _recipe, tmp = device
    stack_path = tmp / "stack.json"
    stack_path.write_text(json.dumps(STACK), encoding="utf-8")
    tool = _LOCAL_TOOLS["imaging.sem_top"]
    res = tool.handler(None, {
        "gds": gds, "stack": str(stack_path),
        "output_dir": str(tmp / "o"), "basename": "t",
        "width_px": 300})
    assert not res.get("isError"), res
    body = json.loads(res["content"][0]["text"])
    kinds = {f["kind"] for f in body["outputs"]["files"]}
    assert kinds == {"sem_png", "sem_color_png"}
    # overwrite refusal is instructive
    res = tool.handler(None, {
        "gds": gds, "stack": str(stack_path),
        "output_dir": str(tmp / "o"), "basename": "t",
        "width_px": 300})
    assert res.get("isError")
    assert "overwrite=true" in res["content"][0]["text"]
