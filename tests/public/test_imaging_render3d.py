"""PUBLIC test: imaging 3D exit — GLB builders + self-contained viewer.

fast mode needs trimesh+shapely (skips without); process mode
additionally needs klayout_pyxs.  Determinism asserted by
run-twice-SHA256; GLB content asserted structurally by parsing the
glTF JSON chunk (material names), never by pixel/byte goldens.
"""

import hashlib
import json
import struct

import pytest

from klink.domains.imaging.visual_stack import VisualStack

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

STACK = {
    "format": "klink_visual_stack_v1",
    "name": "demo",
    "recipe_styles": {
        "pbulk": {"name": "substrate", "color": "#c8b89a"},
    },
    "layers": [
        {"layer": "1/0", "z0_um": -0.4, "z1_um": 0.0, "name": "well",
         "color": "#e0d2ae", "recipe_symbol": "well"},
        {"layer": "2/0", "z0_um": 0.0, "z1_um": 0.2, "name": "metal",
         "color": "#aab8c4", "metallic": 0.9,
         "recipe_symbol": "metal"},
    ],
}


def glb_doc(path):
    with open(path, "rb") as fh:
        data = fh.read()
    magic, _ver, _len = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67                    # 'glTF'
    chunk_len, chunk_type = struct.unpack_from("<II", data, 12)
    assert chunk_type == 0x4E4F534A               # 'JSON'
    return json.loads(data[20:20 + chunk_len].decode("utf-8"))


def glb_material_names(path):
    return [m.get("name")
            for m in glb_doc(path).get("materials", [])]


def glb_extent(path):
    """Max |coordinate| over all accessor min/max (model scale probe)."""
    doc = glb_doc(path)
    vals = []
    for acc in doc.get("accessors", []):
        for k in ("min", "max"):
            v = acc.get(k)
            if isinstance(v, list) and len(v) == 3:
                vals.extend(abs(float(x)) for x in v)
    return max(vals) if vals else 0.0


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


@pytest.fixture()
def device(tmp_path):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")
    kdb = pytest.importorskip("klayout.db")
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEV")
    top.shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 6000, 4000))
    top.shapes(ly.layer(2, 0)).insert(kdb.Box(2000, -1000, 4000, 5000))
    gds = tmp_path / "dev.gds"
    ly.write(str(gds))
    stack_path = tmp_path / "stack.json"
    stack_path.write_text(json.dumps(STACK), encoding="utf-8")
    recipe = tmp_path / "proc.pyxs"
    recipe.write_text(RECIPE, encoding="utf-8")
    return str(gds), str(stack_path), str(recipe), tmp_path


def test_fast_mode_materials_and_determinism(device):
    from klink.domains.imaging.mesh3d import build_glb_fast

    gds, stack_path, _recipe, tmp = device
    stack = VisualStack.load(stack_path)
    g1 = str(tmp / "a.glb"); g2 = str(tmp / "b.glb")
    r1 = build_glb_fast(gds, stack, g1)
    build_glb_fast(gds, stack, g2)
    assert sha(g1) == sha(g2)
    assert set(glb_material_names(g1)) == {"well", "metal"}
    assert r1["triangles"] > 0
    assert {m["name"]: m["solids"] for m in r1["materials"]} == \
        {"well": 1, "metal": 1}


def test_process_mode_styles_by_recipe_symbol(device):
    pytest.importorskip("klayout_pyxs")
    from klink.domains.imaging.mesh3d import build_glb_process

    gds, stack_path, recipe, tmp = device
    stack = VisualStack.load(stack_path)
    g = str(tmp / "p.glb")
    r = build_glb_process(gds, stack, recipe, g, slices=5,
                          fraction=0.8)
    names = set(glb_material_names(g))
    # styled via recipe_symbol (mask layers) AND recipe_styles
    # (engine-only materials); nothing else in this recipe -> no grey
    assert {"well", "metal", "substrate"} <= names
    assert r["unstyled"] == []
    assert r["fraction"] == 0.8 and r["slices"] == 5
    g2 = str(tmp / "p2.glb")
    build_glb_process(gds, stack, recipe, g2, slices=5, fraction=0.8)
    assert sha(g) == sha(g2)
    # geometry SCALE is layout-scale microns: a lost dbu factor (a real
    # shipped bug — sections came back 1000x too small and no test
    # noticed) would put the extent near 0.008
    assert 3.0 < glb_extent(g) < 50.0


def test_viewer_html_is_self_contained(device):
    from klink.domains.imaging.mesh3d import build_glb_fast
    from klink.domains.imaging.viewer import ViewerError, \
        build_viewer_html

    gds, stack_path, _recipe, tmp = device
    stack = VisualStack.load(stack_path)
    glb = str(tmp / "v.glb")
    build_glb_fast(gds, stack, glb)
    html_path = str(tmp / "v.html")
    build_viewer_html(glb, html_path)
    text = open(html_path, encoding="utf-8").read()
    assert text.count("<script") == 2 and text.count("</script>") == 2
    assert "data:model/gltf-binary;base64," in text
    assert "<model-viewer" in text
    assert "@license" in text          # vendored notices preserved
    with pytest.raises(ViewerError, match="overwrite=True"):
        build_viewer_html(glb, html_path)


def test_missing_triangulation_engine_error_is_instructive(monkeypatch):
    import sys

    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")
    from klink.domains.imaging.mesh3d import Mesh3DError, _deps

    monkeypatch.setitem(sys.modules, "mapbox_earcut", None)
    monkeypatch.setitem(sys.modules, "manifold3d", None)
    with pytest.raises(Mesh3DError, match="mapbox-earcut"):
        _deps()


def test_registry_binds_each_tool_to_its_own_handler():
    # a stacked-decorator slip once bound imaging.xsection_run to the
    # BLENDER handler and no test noticed — pin name->handler forever
    from klink.mcp.local_tools import _LOCAL_TOOLS

    expected = {
        "imaging.xsection_run": "_tool_imaging_xsection_run",
        "imaging.render3d": "_tool_imaging_render3d",
        "imaging.sem_top": "_tool_imaging_sem_top",
        "imaging.blender": "_tool_imaging_blender",
    }
    for name, fn in expected.items():
        assert _LOCAL_TOOLS[name].handler.__name__ == fn, name


def test_xsection_tool_dispatches_through_registry(device):
    pytest.importorskip("klayout_pyxs")
    from klink.mcp.local_tools import _LOCAL_TOOLS

    gds, _stack, recipe, tmp = device
    res = _LOCAL_TOOLS["imaging.xsection_run"].handler(None, {
        "gds": gds, "recipe": recipe,
        "cut_um": [[-1.0, 2.0], [7.0, 2.0]],
        "output_dir": str(tmp / "reg"), "basename": "reg"})
    assert not res.get("isError"), res
    body = json.loads(res["content"][0]["text"])
    assert body["tool"] == "imaging.xsection_run"
    # basename containment (security review finding): traversal refused
    res = _LOCAL_TOOLS["imaging.xsection_run"].handler(None, {
        "gds": gds, "recipe": recipe,
        "cut_um": [[-1.0, 2.0], [7.0, 2.0]],
        "output_dir": str(tmp / "reg2"), "basename": "../evil"})
    assert res.get("isError")
    assert "filename stem" in res["content"][0]["text"]


def test_blender_tool_instructive_errors_offline():
    from klink.mcp.local_tools import _LOCAL_TOOLS

    res = _LOCAL_TOOLS["imaging.blender"].handler(None, {
        "mode": "die", "output_dir": "test_outputs/never"})
    assert res.get("isError")
    assert "imaging.render3d" in res["content"][0]["text"]


def test_render3d_tool_handler_offline(device):
    pytest.importorskip("klayout_pyxs")
    from klink.mcp.local_tools import _LOCAL_TOOLS

    gds, stack_path, recipe, tmp = device
    tool = _LOCAL_TOOLS["imaging.render3d"]
    res = tool.handler(None, {
        "gds": gds, "stack": stack_path, "mode": "process",
        "recipe": recipe, "slices": 4, "fraction": 0.6,
        "output_dir": str(tmp / "out"), "basename": "r3d"})
    assert not res.get("isError"), res
    body = json.loads(res["content"][0]["text"])
    kinds = {f["kind"] for f in body["outputs"]["files"]}
    assert kinds == {"glb", "viewer_html"}
    # process without recipe is an instructive error
    res = tool.handler(None, {
        "gds": gds, "stack": stack_path, "mode": "process",
        "output_dir": str(tmp / "out2")})
    assert res.get("isError")
    assert "mode='fast'" in res["content"][0]["text"]
