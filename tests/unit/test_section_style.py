"""The cross-section exit owns no taste, and only pictures need one.

Three things are locked here.

1. klink ships NO page look. Background, gradient, outline darkening,
   ruler colours, label bar, scale bar and the fallback colour for
   undeclared materials are all somebody's taste, so a missing field is
   an error naming the file to copy.

2. A section GDS has no look at all, so `render=True` is what makes the
   style required — not the tool. Asking for geometry alone must keep
   working with nothing declared.

3. Material colours are NOT part of this style. They come from the
   stack, and a material the stack never declared gets a stable
   name-derived colour AND is reported as `auto_colored`, so an odd
   colour is a prompt to declare it rather than a thing to tune here.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from klink.domains.imaging.section_style import (SectionStyle,
                                                 SectionStyleError)

EXAMPLE = (pathlib.Path(__file__).resolve().parents[2]
           / "examples_klink" / "public" / "imaging")


@pytest.fixture(scope="module")
def style() -> SectionStyle:
    sys.path.insert(0, str(EXAMPLE))
    try:
        from section_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))
    return STYLE


@pytest.mark.parametrize("block", ["page", "axis", "auto_color"])
def test_a_missing_block_names_the_file_to_copy(style, block):
    d = style.to_dict()
    d.pop(block)
    with pytest.raises(SectionStyleError) as excinfo:
        SectionStyle.from_dict(d)
    msg = str(excinfo.value)
    assert block in msg
    assert "example_template/imaging/section_style.py" in msg


@pytest.mark.parametrize("block, key", [
    ("page", "background"),
    ("page", "geometry_shade"),
    ("page", "edge_darken"),
    ("page", "supersample"),
    ("axis", "gutter_px"),
    ("axis", "tick_color"),
    ("auto_color", "saturation"),
])
def test_a_missing_field_is_refused_not_defaulted(style, block, key):
    d = style.to_dict()
    d[block].pop(key)
    with pytest.raises(SectionStyleError) as excinfo:
        SectionStyle.from_dict(d)
    assert key in str(excinfo.value)
    assert "klink ships no default" in str(excinfo.value)


def test_out_of_range_edge_darken_is_caught(style):
    d = style.to_dict()
    d["page"]["edge_darken"] = 4.0
    with pytest.raises(SectionStyleError, match="0..1"):
        SectionStyle.from_dict(d)


def test_bars_may_each_be_switched_off(style):
    d = style.to_dict()
    d["scale_bar"] = None
    d["label_bar"] = None
    plain = SectionStyle.from_dict(d)
    assert plain.scale_bar is None and plain.label_bar is None


def test_style_round_trips_through_json(style, tmp_path):
    p = tmp_path / "s.json"
    style.save(str(p))
    assert SectionStyle.load(str(p)).to_dict() == style.to_dict()


# ---------------------------------------------------------------- #
# the auto colour is derived, and its look is declared
# ---------------------------------------------------------------- #

def test_auto_colour_is_stable_per_name_and_tuned_by_the_style(style):
    from klink.domains.imaging.raster import _auto_color

    spec = style.auto_color
    assert _auto_color("fox", spec) == _auto_color("fox", spec)
    assert _auto_color("fox", spec) != _auto_color("ild", spec)
    # the hue is the name's, the saturation and value are the style's
    grey = _auto_color("fox", {"saturation": 0.0, "value": 0.5})
    assert grey[0] == grey[1] == grey[2]


def test_a_named_material_with_no_colour_falls_back_not_to_house_grey(
        style):
    """A recipe_style may name a material without colouring it. klink
    has no house colour to offer, so it uses the same name-derived
    fallback and reports the material as undeclared."""
    from klink.domains.imaging.raster import _auto_color, _style_for
    from klink.domains.imaging.visual_stack import VisualLayer, VisualStack

    stack = VisualStack(
        name="t",
        layers=(VisualLayer(layer="1/0", z0_um=0.0, z1_um=0.1,
                            name="m", color="#112233",
                            recipe_symbol="metal"),),
        recipe_styles={"oxide": {"name": "an oxide"}},
    )
    name, rgb, declared = _style_for(stack, "oxide", style.auto_color)
    assert name == "an oxide"
    assert declared is False
    assert rgb == _auto_color("oxide", style.auto_color)

    name, rgb, declared = _style_for(stack, "metal", style.auto_color)
    assert (name, rgb, declared) == ("m", (0x11, 0x22, 0x33), True)


# ---------------------------------------------------------------- #
# geometry without pictures needs no style
# ---------------------------------------------------------------- #

RECIPE = """\
l1 = layer("1/0")
pbulk = bulk()
well = mask(l1).grow(0.4, -0.05, mode='round', into=pbulk)
"""


@pytest.fixture()
def device(tmp_path):
    kdb = pytest.importorskip("klayout.db")
    pytest.importorskip("klayout_pyxs")
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEV")
    top.shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 6000, 4000))
    gds = tmp_path / "dev.gds"
    ly.write(str(gds))
    recipe = tmp_path / "p.pyxs"
    recipe.write_text(RECIPE, encoding="utf-8")
    return str(gds), str(recipe), tmp_path


def test_gds_only_needs_no_style_at_all(device):
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "g"), basename="g")
    assert [f["kind"] for f in r["outputs"]["files"]] == ["section_gds"]


def test_render_without_a_style_says_what_to_do(device):
    from klink.domains.imaging.xsection_driver import (XSectionError,
                                                       run_xsection)

    gds, recipe, tmp = device
    with pytest.raises(XSectionError) as excinfo:
        run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "r"), basename="r",
                     render=True)
    msg = str(excinfo.value)
    assert "section_style" in msg
    assert "drop render=True" in msg


def test_auto_layer_base_is_movable(device):
    """The 300/0 base is a klink convention, not process data — but a
    recipe that already writes 300/0 needs it out of the way."""
    from klink.domains.imaging.xsection_driver import run_xsection

    gds, recipe, tmp = device
    a = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "a"), basename="a")
    b = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "b"), basename="b",
                     auto_layer_base=800)
    layers_a = {m["layer"] for m in a["outputs"]["stages"][0]["materials"]}
    layers_b = {m["layer"] for m in b["outputs"]["stages"][0]["materials"]}
    assert all(int(x.split("/")[0]) >= 300 for x in layers_a)
    assert all(int(x.split("/")[0]) >= 800 for x in layers_b)
    assert not (layers_a & layers_b)


def test_the_declaration_changes_the_page(device, style, tmp_path):
    """Same section, two page colours -> two different images."""
    import numpy as np
    from PIL import Image

    from klink.domains.imaging.raster import render_section_png
    from klink.domains.imaging.xsection_driver import run_xsection

    pytest.importorskip("numpy")
    gds, recipe, tmp = device
    r = run_xsection(gds, recipe, [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "s"), basename="s")
    sec = r["outputs"]["files"][0]["path"]
    mats = {m["layer"]: m["name"]
            for m in r["outputs"]["stages"][0]["materials"]}

    # a z window with HEADROOM above the stack: without empty space in
    # the frame the page colour is never visible and this test would
    # pass on two identical images
    window = (-1.0, 1.5)
    light = str(tmp / "light.png")
    render_section_png(sec, mats, light, style, width_px=300,
                       z_window_um=window)
    d = style.to_dict()
    d["page"]["background"] = "#000000"
    dark = str(tmp / "dark.png")
    render_section_png(sec, mats, dark, SectionStyle.from_dict(d),
                       width_px=300, z_window_um=window)

    mean = lambda p: np.asarray(Image.open(p).convert("L")).mean()
    assert mean(light) > mean(dark) + 10


def test_step_names_that_sanitise_to_nothing_leave_clean_filenames(
        device, style, tmp_path):
    """A recipe written in Chinese used to produce `film_step03__`,
    `film_step09__`, `film_step10__1_` — eleven files a Chinese-speaking
    user cannot tell apart, decorated with dangling underscores. The
    real name is in the sidecar and burnt into the frame; the filename
    just has to be clean and unique."""
    from klink.domains.imaging.xsection_driver import (_filename_slug,
                                                       run_xsection)

    assert _filename_slug("生长栅氧化层") == ""       # nothing left, and
    assert _filename_slug("刻蚀接触孔") == ""          # that is honest
    assert _filename_slug("淀积金属层1(大马士革工艺)") == "1"
    assert _filename_slug("gate ox") == "gate_ox"
    assert _filename_slug("--LOCOS--") == "LOCOS"

    gds, _recipe, tmp = device
    recipe = tmp_path / "cn.pyxs"
    recipe.write_text(
        'l1 = layer("1/0")\n'
        '# klink-step: 生长P型衬底\n'
        'pbulk = bulk()\n'
        '# klink-step: 注入N阱\n'
        'well = mask(l1).grow(0.4, -0.05, mode="round", into=pbulk)\n',
        encoding="utf-8")
    r = run_xsection(gds, str(recipe), [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp_path / "cn"), basename="film",
                     steps=True)
    names = [f["path"].rsplit("/", 1)[-1]
             for f in r["outputs"]["files"]]
    # an ASCII fragment inside a Chinese name is KEPT (that is the part
    # a reader can still use); a name with none leaves just the index.
    # Either way: no dangling underscores, and the files stay unique.
    assert names == ["film_step00_P.gds", "film_step01_N.gds"]
    assert not any("__" in n for n in names)
    assert not any(n.rsplit(".", 1)[0].endswith("_") for n in names)
    # the real names survive where they belong
    assert [s["step"] for s in r["outputs"]["stages"]] == [
        "生长P型衬底", "注入N阱"]
