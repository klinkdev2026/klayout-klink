"""PUBLIC test: imaging raster exits — SEM top view, section PNGs,
film strip/GIF, and the render=True wiring of run_xsection.

Needs numpy/scipy/pillow (skips without); section/film paths also need
klayout_pyxs.  Determinism via seeded noise + run-twice SHA256.
"""

import hashlib
import json
import pathlib
import sys

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


EXAMPLE = (pathlib.Path(__file__).resolve().parents[2]
           / "examples_klink" / "public" / "imaging")


@pytest.fixture(scope="module")
def sem_style():
    """The example owns every SEM appearance number; klink has
    none, so a test cannot render without loading it either."""
    sys.path.insert(0, str(EXAMPLE))
    try:
        from sem_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))
    return STYLE


@pytest.fixture(scope="module")
def section_style():
    """The example owns every page number; klink has none, so a
    test cannot render a section without loading it either."""
    sys.path.insert(0, str(EXAMPLE))
    try:
        from section_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))
    return STYLE


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


def test_sem_top_deterministic_and_subset(device, sem_style):
    from PIL import Image

    from klink.domains.imaging.raster import render_sem_png

    gds, _recipe, tmp = device
    stack = VisualStack.from_dict(STACK)
    g1 = str(tmp / "a_sem.png"); c1 = str(tmp / "a_col.png")
    r = render_sem_png(gds, stack, g1, sem_style, out_color=c1,
                       width_px=400)
    assert r["layers_rendered"] == ["1/0", "2/0"]
    assert Image.open(g1).mode == "L"
    assert Image.open(c1).mode == "RGB"
    g2 = str(tmp / "b_sem.png")
    render_sem_png(gds, stack, g2, sem_style, width_px=400)
    assert sha(g1) == sha(g2)                      # seeded noise
    g3 = str(tmp / "c_sem.png")
    r3 = render_sem_png(gds, stack, g3, sem_style, width_px=400,
                        layers=["2/0"])
    assert r3["layers_rendered"] == ["2/0"]
    assert sha(g3) != sha(g1)


def test_xsection_render_film(device, section_style):
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    stack = VisualStack.from_dict(STACK)
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "film"), basename="f",
                     steps=True, render=True, stack=stack,
                     style=section_style)
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


def test_section_png_auto_color_reported(device, section_style):
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
    rep = render_section_png(sec, mats, out, section_style)
    assert set(rep["auto_colored"]) == {"pbulk", "well", "metal"}
    out2 = str(tmp / "sec2.png")
    render_section_png(sec, mats, out2, section_style)
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

    # no style at all: refused, and the refusal names the file
    res = tool.handler(None, {
        "gds": gds, "stack": str(stack_path),
        "output_dir": str(tmp / "nostyle"), "basename": "t",
        "width_px": 300})
    assert res.get("isError")
    assert "sem_style.py" in res["content"][0]["text"]

    sys.path.insert(0, str(EXAMPLE))
    try:
        from sem_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))
    style_path = tmp / "sem_style.json"
    STYLE.save(str(style_path))

    res = tool.handler(None, {
        "gds": gds, "stack": str(stack_path),
        "style": str(style_path),
        "output_dir": str(tmp / "o"), "basename": "t",
        "width_px": 300})
    assert not res.get("isError"), res
    body = json.loads(res["content"][0]["text"])
    kinds = {f["kind"] for f in body["outputs"]["files"]}
    assert kinds == {"sem_png", "sem_color_png"}
    # overwrite refusal is instructive
    res = tool.handler(None, {
        "gds": gds, "stack": str(stack_path),
        "style": str(style_path),
        "output_dir": str(tmp / "o"), "basename": "t",
        "width_px": 300})
    assert res.get("isError")
    assert "overwrite=true" in res["content"][0]["text"]


def test_axis_ruler_and_z_window(device, section_style):
    """axis=True must draw a real ruler (ticks + numbers in the gutter),
    and z_window_um must actually re-frame the geometry."""
    pytest.importorskip("klayout_pyxs")
    import numpy as np
    from PIL import Image

    from klink.domains.imaging.raster import render_section_png
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "ax"), basename="a")
    sec = r["outputs"]["files"][0]["path"]
    mats = {m["layer"]: m["name"]
            for m in r["outputs"]["stages"][0]["materials"]}

    plain = str(tmp / "plain.png")
    ruled = str(tmp / "ruled.png")
    render_section_png(sec, mats, plain, section_style,
                       width_px=600,
                       z_window_um=(-1.0, 0.6))
    render_section_png(sec, mats, ruled, section_style,
                       width_px=600,
                       z_window_um=(-1.0, 0.6), axis=True)
    a, b = Image.open(plain), Image.open(ruled)
    assert b.width == a.width + 74 and b.height == a.height
    # the gutter is not an empty margin: ticks and labels are drawn
    gut = np.asarray(b.convert("RGB"))[:, :73]
    ink = (gut < 160).all(axis=-1)
    assert ink.sum() > 40, "axis gutter has no tick/label ink"

    # a taller window must shrink the geometry (same width, more height)
    tall = str(tmp / "tall.png")
    render_section_png(sec, mats, tall, section_style,
                       width_px=600,
                       z_window_um=(-2.0, 0.6))
    assert Image.open(tall).height > a.height


def test_z_window_must_be_ordered(device, section_style):
    from klink.domains.imaging.raster import RasterError, render_section_png
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "zw"), basename="z")
    sec = r["outputs"]["files"][0]["path"]
    with pytest.raises(RasterError, match="z_bottom, z_top"):
        render_section_png(sec, {}, str(tmp / "bad.png"),
                           section_style,
                           z_window_um=(0.6, -1.0))


# ------------------------------------------------------------------ #
# CJK labels: a '# klink-step: 生长栅氧' marker must not silently bake
# tofu boxes into a sha256'd PNG
# ------------------------------------------------------------------ #

CJK_RECIPE = """\
l1 = layer("1/0")
l2 = layer("2/0")
# klink-step: 衬底
pbulk = bulk()
# klink-step: 生长栅氧
well = mask(l1).grow(0.4, -0.05, mode='round', into=pbulk)
"""


def test_notdef_detector_actually_detects(device):
    """The Latin faces have no Han glyph — the detector must SAY so
    rather than trust the font's name."""
    from PIL import ImageFont

    from klink.domains.imaging import raster

    latin = None
    for name in raster._LATIN_FONTS:
        latin = raster._try_font(ImageFont, name, 22)
        if latin is not None:
            break
    if latin is None:
        pytest.skip("no Latin TrueType face installed")
    assert raster._missing_glyphs(latin, "gate ox") == []
    assert raster._missing_glyphs(latin, "生长栅氧") == list("生长栅氧")


def test_cjk_label_uses_a_cjk_face_when_installed(device, section_style):
    """With a CJK face available the label renders for real (no
    missing glyphs) and the header band carries ink."""
    pytest.importorskip("klayout_pyxs")
    import numpy as np
    from PIL import Image, ImageFont

    from klink.domains.imaging import raster
    from klink.domains.imaging.xsection_driver import run_xsection

    if all(raster._try_font(ImageFont, n, 22) is None
           for n in raster._CJK_FONTS):
        pytest.skip("no CJK font on this machine")

    gds, recipe, tmp = device
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "cjk"), basename="c")
    sec = r["outputs"]["files"][0]["path"]
    mats = {m["layer"]: m["name"]
            for m in r["outputs"]["stages"][0]["materials"]}
    out = str(tmp / "cjk.png")
    rep = raster.render_section_png(sec, mats, out, section_style,
                                    label="生长栅氧")
    assert rep["missing_glyphs"] == []
    # the header band (top 32 px) really holds glyphs, not an empty bar
    band = np.asarray(Image.open(out).convert("RGB"))[:32]
    assert (band > 200).all(axis=-1).sum() > 60, "label band has no ink"


def test_unrenderable_label_is_reported_not_silently_drawn(monkeypatch,
                                                           device, section_style):
    """Force the no-CJK-font machine: the run still succeeds, but the
    sidecar names the characters that came out as boxes."""
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging import raster
    from klink.domains.imaging.xsection_driver import run_xsection

    monkeypatch.setattr(raster, "_CJK_FONTS", ())
    monkeypatch.setattr(raster, "_FONT_CACHE", {})

    gds, _recipe, tmp = device
    recipe = tmp / "cjk.pyxs"
    recipe.write_text(CJK_RECIPE, encoding="utf-8")
    r = run_xsection(gds, str(recipe), [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "warn"), basename="w",
                     steps=True, render=True, style=section_style)
    warn = r["outputs"]["render"]["font_warnings"]
    assert [w["label"] for w in warn["labels"]] == ["衬底", "生长栅氧"]
    assert set(warn["labels"][1]["characters"]) == set("生长栅氧")
    assert "fonts-noto-cjk" in warn["reason"]


def test_ascii_label_never_warns(device, section_style):
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging.raster import render_section_png
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "ascii"), basename="a",
                     steps=True, render=True, style=section_style)
    assert "font_warnings" not in r["outputs"]["render"]
    sec = r["outputs"]["files"][0]["path"]
    rep = render_section_png(sec, {}, str(tmp / "p.png"),
                             section_style,
                             label="gate ox 0.2 um")
    assert rep["missing_glyphs"] == []
