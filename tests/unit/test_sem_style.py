"""The SEM exit owns no taste, and it always offers a scale bar.

Two things are locked here.

1. klink ships NO default look. Background level, rim gains, beam blur,
   grain, scanlines, vignette, false-colour mix: every one is somebody's
   idea of what their microscope produces, so a missing field is an
   error naming the file to copy — never a silent default.

2. The scale bar. `render_sem_png` used to have no scale-bar option at
   all, in klink or in the tool, while advertising itself as an SEM-style
   view. A micrograph you cannot measure is a picture, and no reviewer
   accepts one. The bar length rounds to 1/2/5 x 10^n so it never reads
   "1.87 um".

Per-layer response (sem_grey, edge_glow, color) stays the STACK's job and
is deliberately absent from this style.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from klink.domains.imaging.sem_style import SemStyle, SemStyleError

EXAMPLE = (pathlib.Path(__file__).resolve().parents[2]
           / "examples_klink" / "public" / "imaging")


@pytest.fixture(scope="module")
def style() -> SemStyle:
    sys.path.insert(0, str(EXAMPLE))
    try:
        from sem_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))
    return STYLE


# ---------------------------------------------------------------- #
# no style, no render
# ---------------------------------------------------------------- #

@pytest.mark.parametrize("block", ["background", "edges", "beam", "noise",
                                   "vignette", "false_color"])
def test_a_missing_block_names_the_file_to_copy(style, block):
    d = style.to_dict()
    d.pop(block)
    with pytest.raises(SemStyleError) as excinfo:
        SemStyle.from_dict(d)
    msg = str(excinfo.value)
    assert block in msg
    assert "example_template/imaging/sem_style.py" in msg


@pytest.mark.parametrize("block, key", [
    ("background", "grey"),
    ("edges", "inner_gain"),
    ("beam", "blur_px"),
    ("noise", "grain"),
    ("noise", "seed"),
    ("vignette", "amount"),
    ("false_color", "gain"),
])
def test_a_missing_field_is_refused_not_defaulted(style, block, key):
    d = style.to_dict()
    d[block].pop(key)
    with pytest.raises(SemStyleError) as excinfo:
        SemStyle.from_dict(d)
    assert key in str(excinfo.value)
    assert "klink ships no default" in str(excinfo.value)


def test_bad_colour_and_bad_bar_fraction_are_caught(style):
    d = style.to_dict()
    d["background"]["color"] = "not-a-colour"
    with pytest.raises(SemStyleError, match="#RRGGBB"):
        SemStyle.from_dict(d)

    d = style.to_dict()
    d["scale_bar"]["target_fraction"] = 4.0
    with pytest.raises(SemStyleError, match="between 0 and 1"):
        SemStyle.from_dict(d)


def test_style_round_trips_through_json(style, tmp_path):
    p = tmp_path / "s.json"
    style.save(str(p))
    assert SemStyle.load(str(p)).to_dict() == style.to_dict()


def test_scale_bar_may_be_switched_off(style):
    d = style.to_dict()
    d["scale_bar"] = None
    assert SemStyle.from_dict(d).scale_bar is None


# ---------------------------------------------------------------- #
# the bar is a round number
# ---------------------------------------------------------------- #

@pytest.mark.parametrize("want, expect", [
    (1.87, 1.0), (2.0, 2.0), (4.9, 2.0), (5.5, 5.0), (9.9, 5.0),
    (12.0, 10.0), (0.37, 0.2), (0.099, 0.05), (0.0, 1.0),
])
def test_bar_length_rounds_to_1_2_5(want, expect):
    from klink.domains.imaging.raster import _nice_bar
    assert _nice_bar(want) == pytest.approx(expect)


# ---------------------------------------------------------------- #
# the style actually drives the image
# ---------------------------------------------------------------- #

@pytest.fixture()
def device(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    pytest.importorskip("PIL")
    kdb = pytest.importorskip("klayout.db")
    from klink.domains.imaging.visual_stack import VisualLayer, VisualStack

    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEV")
    # TWO separated boxes on purpose: a single shape fills its own
    # bounding box, so the background would never be visible and a
    # background test would pass on an unchanged image
    top.shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 4000, 2000))
    top.shapes(ly.layer(1, 0)).insert(kdb.Box(8000, 4000, 12000, 6000))
    gds = tmp_path / "dev.gds"
    ly.write(str(gds))
    stack = VisualStack(name="t", layers=(
        VisualLayer(layer="1/0", z0_um=0.0, z1_um=0.1, name="m",
                    color="#aabbcc", sem_grey=0.8, edge_glow=0.6),))
    return str(gds), stack, tmp_path


def _grey(path):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.open(path).convert("L"), dtype=float) / 255.0


def test_the_declaration_changes_the_picture(device, style):
    """Same geometry, two backgrounds -> two different images. If this
    passes with identical images, the style is not being read."""
    from klink.domains.imaging.raster import render_sem_png

    gds, stack, tmp = device
    a = str(tmp / "a.png")
    render_sem_png(gds, stack, a, style, width_px=300)

    d = style.to_dict()
    d["background"]["grey"] = 0.75
    bright = SemStyle.from_dict(d)
    b = str(tmp / "b.png")
    render_sem_png(gds, stack, b, bright, width_px=300)

    assert _grey(b).mean() > _grey(a).mean() + 0.1


def test_same_seed_same_image_new_seed_new_grain(device, style):
    from klink.domains.imaging.raster import render_sem_png

    gds, stack, tmp = device
    a, b, c = (str(tmp / n) for n in ("s1.png", "s2.png", "s3.png"))
    render_sem_png(gds, stack, a, style, width_px=300)
    render_sem_png(gds, stack, b, style, width_px=300)
    assert (_grey(a) == _grey(b)).all()

    d = style.to_dict()
    d["noise"]["seed"] = style.noise["seed"] + 1
    render_sem_png(gds, stack, c, SemStyle.from_dict(d), width_px=300)
    assert not (_grey(a) == _grey(c)).all()


def test_scale_bar_is_drawn_and_reported(device, style):
    from klink.domains.imaging.raster import render_sem_png

    gds, stack, tmp = device
    out = str(tmp / "bar.png")
    r = render_sem_png(gds, stack, out, style, width_px=600)
    bar = r["scale_bar"]
    assert bar["drawn"] is True
    assert bar["missing_glyphs"] == []
    # a round 1/2/5 number, and a real fraction of the frame
    assert bar["bar_um"] in (0.5, 1.0, 2.0, 5.0)
    assert "um" in bar["text"] or "nm" in bar["text"]
    # it is really painted: the bottom-right corner gained bright pixels
    img = _grey(out)
    corner = img[-60:, -260:]
    assert (corner > 0.9).sum() > 200


def test_switching_the_bar_off_leaves_no_report_and_no_ink(device, style):
    from klink.domains.imaging.raster import render_sem_png

    gds, stack, tmp = device
    d = style.to_dict()
    d["scale_bar"] = None
    off = SemStyle.from_dict(d)
    with_bar = str(tmp / "on.png")
    without = str(tmp / "off.png")
    r_on = render_sem_png(gds, stack, with_bar, style, width_px=600)
    r_off = render_sem_png(gds, stack, without, off, width_px=600)
    assert "scale_bar" not in r_off
    assert r_on["scale_bar"]["drawn"] is True
    assert (_grey(with_bar) != _grey(without)).any()


def test_width_px_sharpens_but_window_um_magnifies(device, style):
    """The distinction the SEM exit had no way to express before: more
    pixels over the same field is not magnification, and only real
    magnification may change what the scale bar claims."""
    from klink.domains.imaging.raster import render_sem_png

    gds, stack, tmp = device
    base = render_sem_png(gds, stack, str(tmp / "b.png"), style,
                          width_px=400)
    sharp = render_sem_png(gds, stack, str(tmp / "s.png"), style,
                           width_px=1200)
    # 3x the pixels, 1/3 the microns per pixel, SAME field -> same bar
    assert sharp["um_per_px"] == pytest.approx(base["um_per_px"] / 3,
                                               rel=0.02)
    assert sharp["scale_bar"]["bar_um"] == base["scale_bar"]["bar_um"]

    zoom = render_sem_png(gds, stack, str(tmp / "z.png"), style,
                          width_px=400, window_um=(0.0, 0.0, 1.0, 1.0))
    assert zoom["um_per_px"] < base["um_per_px"] / 3
    assert zoom["scale_bar"]["bar_um"] < base["scale_bar"]["bar_um"]


@pytest.mark.parametrize("window, expect", [
    ((1.0, 1.0, 0.0, 0.0), "x1>x0"),
    ((50.0, 50.0, 60.0, 60.0), "does not overlap"),
])
def test_a_bad_window_is_refused_with_the_real_bounds(device, style,
                                                      window, expect):
    from klink.domains.imaging.raster import RasterError, render_sem_png

    gds, stack, tmp = device
    with pytest.raises(RasterError, match=expect):
        render_sem_png(gds, stack, str(tmp / "bad.png"), style,
                       width_px=200, window_um=window)


def test_a_bar_that_cannot_fit_says_so_instead_of_drawing_junk(device,
                                                               style):
    from klink.domains.imaging.raster import render_sem_png

    gds, stack, tmp = device
    d = style.to_dict()
    d["scale_bar"]["target_fraction"] = 0.99
    d["scale_bar"]["margin_px"] = 4000        # wider than the frame
    r = render_sem_png(gds, stack, str(tmp / "nofit.png"),
                       SemStyle.from_dict(d), width_px=300)
    assert r["scale_bar"]["drawn"] is False
    assert "does not fit" in r["scale_bar"]["reason"]


# ---------------------------------------------------------------- #
# the contrast plate: a bar you cannot read is not a scale bar
# ---------------------------------------------------------------- #

def test_a_bar_on_bright_geometry_is_still_readable(device, style):
    """Blind-test evidence: the bar landed white-on-white on a metal
    rail while the user's ask was literally "I want to measure off the
    image". The plate is what makes it legible; blended, not pasted, so
    the layout stays visible underneath."""
    import numpy as np
    from PIL import Image

    from klink.domains.imaging.raster import render_sem_png

    gds, stack, tmp = device
    d = style.to_dict()
    d["scale_bar"]["plate"] = None
    bare = str(tmp / "bare.png")
    render_sem_png(gds, stack, bare, SemStyle.from_dict(d), width_px=600)
    plated = str(tmp / "plated.png")
    render_sem_png(gds, stack, plated, style, width_px=600)

    def corner(p):
        a = np.asarray(Image.open(p).convert("L"), dtype=float)
        return a[-46:, -270:]          # roughly the plate's footprint

    # The plate darkens the region under the bar, which is what buys the
    # contrast. The shift is modest because this fixture's bottom-right
    # corner is already mostly empty background — on a real layout with
    # a bright rail there (the blind-test case) it is the difference
    # between legible and invisible.
    assert corner(plated).mean() < corner(bare).mean() - 4
    # and it BLENDS: the geometry underneath is still varying, not a
    # flat block of paint
    assert corner(plated).std() > 5


def test_plate_opacity_is_validated(style):
    d = style.to_dict()
    d["scale_bar"]["plate"] = {"color": "#000000", "opacity": 3.0,
                               "pad_px": 4}
    with pytest.raises(SemStyleError, match="0..1"):
        SemStyle.from_dict(d)
    d["scale_bar"]["plate"] = {"color": "nope", "opacity": 0.5,
                               "pad_px": 4}
    with pytest.raises(SemStyleError, match="#RRGGBB"):
        SemStyle.from_dict(d)
    d["scale_bar"]["plate"] = {"color": "#000000", "opacity": 0.5}
    with pytest.raises(SemStyleError, match="pad_px"):
        SemStyle.from_dict(d)
