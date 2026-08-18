"""The 3D exit owns no taste — not the model's finish, not the page.

The viewer is a whole small UI written into a self-contained HTML file,
so its palette is as much somebody's taste as a sun's energy is. Locked
here:

1. klink ships no default. Every field missing is an error naming the
   file to copy.
2. The colours really do reach the page — a style change must change
   the HTML, or the declaration is decorative.
3. Colours are validated as plain #RRGGBB BEFORE they are pasted into
   the page's CSS. The page is written by string substitution, so this
   validator is also the thing standing between a style file and
   injected markup.
4. `undeclared_color` marks materials the STACK forgot, and those are
   reported as `unstyled` — it is a signal, not a palette choice.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from klink.domains.imaging.viewer_style import (ViewerStyle,
                                                ViewerStyleError)

EXAMPLE = (pathlib.Path(__file__).resolve().parents[2]
           / "examples_klink" / "public" / "imaging")


@pytest.fixture(scope="module")
def style() -> ViewerStyle:
    sys.path.insert(0, str(EXAMPLE))
    try:
        from viewer_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))
    return STYLE


@pytest.mark.parametrize("block", ["material", "viewer"])
def test_a_missing_block_names_the_file_to_copy(style, block):
    d = style.to_dict()
    d.pop(block)
    with pytest.raises(ViewerStyleError) as excinfo:
        ViewerStyle.from_dict(d)
    assert block in str(excinfo.value)
    assert "example_template/imaging/viewer_style.py" in str(excinfo.value)


@pytest.mark.parametrize("block, key", [
    ("material", "roughness"),
    ("material", "undeclared_color"),
    ("viewer", "page"),
    ("viewer", "panel"),
    ("viewer", "button_hover"),
    ("viewer", "exposure"),
])
def test_a_missing_field_is_refused_not_defaulted(style, block, key):
    d = style.to_dict()
    d[block].pop(key)
    with pytest.raises(ViewerStyleError) as excinfo:
        ViewerStyle.from_dict(d)
    assert key in str(excinfo.value)
    assert "klink ships no default" in str(excinfo.value)


@pytest.mark.parametrize("bad", [
    "red", "#12345", "#GGGGGG", "rgb(1,2,3)",
    "#000; } body { background: url(http://evil/x)",
])
def test_only_a_plain_hex_colour_can_reach_the_page_css(style, bad):
    """The page is built by string substitution, so this validator is
    what stands between a style file and injected CSS."""
    d = style.to_dict()
    d["viewer"]["page"] = bad
    with pytest.raises(ViewerStyleError, match="#RRGGBB"):
        ViewerStyle.from_dict(d)


def test_roughness_stays_a_pbr_value(style):
    d = style.to_dict()
    d["material"]["roughness"] = 7.0
    with pytest.raises(ViewerStyleError, match="0..1"):
        ViewerStyle.from_dict(d)


def test_style_round_trips_through_json(style, tmp_path):
    p = tmp_path / "s.json"
    style.save(str(p))
    assert ViewerStyle.load(str(p)).to_dict() == style.to_dict()


# ---------------------------------------------------------------- #
# it actually reaches the artefacts
# ---------------------------------------------------------------- #

@pytest.fixture()
def glb(tmp_path):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")
    kdb = pytest.importorskip("klayout.db")
    from klink.domains.imaging.mesh3d import build_glb_fast
    from klink.domains.imaging.visual_stack import VisualLayer, VisualStack

    sys.path.insert(0, str(EXAMPLE))
    try:
        from viewer_style import STYLE
    finally:
        sys.path.remove(str(EXAMPLE))

    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEV")
    top.shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 4000, 2000))
    gds = tmp_path / "d.gds"
    ly.write(str(gds))
    stack = VisualStack(name="t", layers=(
        VisualLayer(layer="1/0", z0_um=0.0, z1_um=0.2, name="m",
                    color="#445566"),))
    out = tmp_path / "m.glb"
    build_glb_fast(str(gds), stack, str(out), STYLE)
    return str(out), tmp_path


def test_the_palette_reaches_the_page(style, glb):
    from klink.domains.imaging.viewer import build_viewer_html

    path, tmp = glb
    a = tmp / "a.html"
    build_viewer_html(path, str(a), style)
    text = a.read_text(encoding="utf-8")
    assert style.css("page") in text
    assert style.css("button_hover") in text
    assert "__PAGE__" not in text and "__EXPOSURE__" not in text

    d = style.to_dict()
    d["viewer"]["page"] = "#ff00ff"
    b = tmp / "b.html"
    build_viewer_html(path, str(b), ViewerStyle.from_dict(d))
    other = b.read_text(encoding="utf-8")
    assert "#ff00ff" in other
    assert other != text


def test_the_page_is_still_self_contained(style, glb):
    from klink.domains.imaging.viewer import build_viewer_html

    path, tmp = glb
    out = tmp / "c.html"
    build_viewer_html(path, str(out), style)
    text = out.read_text(encoding="utf-8")
    assert "data:model/gltf-binary;base64," in text
    assert text.count("<script") == 2


def test_roughness_reaches_the_glb(style, glb):
    """The one PBR value the stack does not carry."""
    import json
    import struct

    path, tmp = glb
    with open(path, "rb") as fh:
        data = fh.read()
    chunk_len, _kind = struct.unpack_from("<II", data, 12)
    doc = json.loads(data[20:20 + chunk_len].decode("utf-8"))
    factors = [m["pbrMetallicRoughness"].get("roughnessFactor")
               for m in doc.get("materials", [])]
    assert factors and all(f == pytest.approx(style.roughness)
                           for f in factors)
