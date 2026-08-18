"""PUBLIC test: the imaging domain — VisualStack contract + the klink
xsection driver (headless, deterministic, step protocol).

Engine tests skip when klayout_pyxs is absent (optional dep; CI floor
stays pytest+klayout).  Determinism is asserted by running twice and
comparing SHA256 (GDS timestamps disabled by contract); geometry is
golden-tested structurally (materials + per-layer shape counts), which
is stable across library byte-layout details.
"""

import json

import pytest

from klink.domains.imaging.visual_stack import (
    FORMAT_V1, VisualLayer, VisualStack, VisualStackError)
from klink.domains.imaging.xsection_driver import (
    XSectionError, parse_steps, run_xsection)

# NOTE: only the ENGINE tests need klayout_pyxs — they gate on it via
# the `device` fixture so the VisualStack/motif contract tests still
# run on the CI floor (pytest + klayout, no optional deps).


# --------------------------------------------------------------------- #
# fixtures: tiny synthetic device + tiny recipe (IP-free, test-owned)
# --------------------------------------------------------------------- #

RECIPE = """\
l1 = layer("1/0")
l2 = layer("2/0")
# klink-step: substrate
pbulk = bulk()
# klink-step: well
well = mask(l1).grow(0.4, -0.05, mode='round', into=pbulk)
# klink-step: conformal oxide
ox = deposit(0.1, 0.1)
# klink-step: metal
metal = mask(l2).grow(0.2)
"""


@pytest.fixture()
def device(tmp_path):
    pytest.importorskip("klayout_pyxs", reason="optional engine dep")
    kdb = pytest.importorskip("klayout.db")
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEV")
    li1 = ly.layer(1, 0)
    li2 = ly.layer(2, 0)
    top.shapes(li1).insert(kdb.Box(0, 0, 6000, 4000))
    top.shapes(li2).insert(kdb.Box(2000, -1000, 4000, 5000))
    gds = tmp_path / "dev.gds"
    ly.write(str(gds))
    recipe = tmp_path / "proc.pyxs"
    recipe.write_text(RECIPE, encoding="utf-8")
    return str(gds), str(recipe), tmp_path


CUT = [[-1.0, 2.0], [7.0, 2.0]]


def test_single_run_deterministic_and_structural(device):
    gds, recipe, tmp = device
    r1 = run_xsection(gds, recipe, CUT, output_dir=str(tmp / "o1"),
                      basename="sec")
    r2 = run_xsection(gds, recipe, CUT, output_dir=str(tmp / "o2"),
                      basename="sec")
    assert r1["format"] == "klink_imaging_result_v1"
    # determinism: identical inputs -> byte-identical section GDS
    assert r1["outputs"]["files"][0]["sha256"] == \
        r2["outputs"]["files"][0]["sha256"]
    # no explicit output() in the recipe -> auto-output of all materials
    mats = {m["name"] for m in r1["outputs"]["stages"][0]["materials"]}
    assert {"pbulk", "well", "ox", "metal"} <= mats
    # every material has geometry on the cut
    assert all(m["shapes"] > 0
               for m in r1["outputs"]["stages"][0]["materials"])
    # sidecar file exists, stable-sorted, and round-trips
    with open(r1["sidecar_path"], encoding="utf-8") as fh:
        disk = json.load(fh)
    assert disk["inputs"]["cut_um"] == CUT
    import klayout_pyxs
    assert disk["versions"]["klayout_pyxs"] == klayout_pyxs.__version__


def test_steps_protocol_and_film(device):
    gds, recipe, tmp = device
    assert [s for s, _ in parse_steps(RECIPE)] == [
        "substrate", "well", "conformal oxide", "metal"]
    r = run_xsection(gds, recipe, CUT, output_dir=str(tmp / "steps"),
                     basename="film", steps=True)
    stages = r["outputs"]["stages"]
    assert [s["step"] for s in stages] == [
        "substrate", "well", "conformal oxide", "metal"]
    # materials accumulate monotonically across steps
    ns = [len(s["materials"]) for s in stages]
    assert ns == sorted(ns) and ns[0] >= 1 and ns[-1] >= 4
    # name->layer map is stable: same material, same layer in every step
    seen = {}
    for s in stages:
        for m in s["materials"]:
            assert seen.setdefault(m["name"], m["layer"]) == m["layer"]
    assert len(r["outputs"]["files"]) == 4


def test_overwrite_refusal_and_missing_markers(device):
    gds, recipe, tmp = device
    out = str(tmp / "ow")
    run_xsection(gds, recipe, CUT, output_dir=out, basename="x")
    with pytest.raises(XSectionError, match="overwrite=True"):
        run_xsection(gds, recipe, CUT, output_dir=out, basename="x")
    run_xsection(gds, recipe, CUT, output_dir=out, basename="x",
                 overwrite=True)
    plain = tmp / "plain.pyxs"
    plain.write_text("pbulk = bulk()\n", encoding="utf-8")
    with pytest.raises(XSectionError, match="klink-step"):
        run_xsection(gds, str(plain), CUT, output_dir=out,
                     basename="y", steps=True)


def test_bad_inputs_are_instructive(device):
    gds, recipe, tmp = device
    with pytest.raises(XSectionError, match=r"cut_um must be"):
        run_xsection(gds, recipe, [[0, 0]], output_dir=str(tmp),
                     basename="z")
    with pytest.raises(XSectionError, match="not in"):
        run_xsection(gds, recipe, CUT, output_dir=str(tmp),
                     basename="z", cell="NOPE")


# --------------------------------------------------------------------- #
# VisualStack contract (no engine needed, but kept in one module)
# --------------------------------------------------------------------- #

def _vl(**kw):
    # colour is REQUIRED on a non-lattice layer: klink ships no
    # default one, so a test cannot omit it either
    base = dict(layer="1/0", z0_um=0.0, z1_um=0.2, name="metal",
                color="#aab8c4")
    base.update(kw)
    return VisualLayer(**base)


def test_visual_stack_roundtrip_and_lookup(tmp_path):
    vs = VisualStack(layers=(
        _vl(), _vl(layer="2/0", z0_um=0.2, z1_um=0.4, name="MoS2",
                   kind="lattice", motif="mos2", recipe_symbol="chan")),
        name="demo")
    p = tmp_path / "stack.json"
    vs.save(str(p))
    back = VisualStack.load(str(p))
    assert back == vs
    assert back.to_dict()["format"] == FORMAT_V1
    assert back.by_layer("2/0").motif == "mos2"
    assert back.by_recipe_symbol("chan").name == "MoS2"


def test_visual_stack_validation():
    with pytest.raises(VisualStackError, match="z1_um"):
        _vl(z1_um=-1.0)
    with pytest.raises(VisualStackError, match="motif"):
        _vl(kind="lattice")
    with pytest.raises(VisualStackError, match="kind"):
        _vl(kind="blob")
    with pytest.raises(VisualStackError, match="no layers"):
        VisualStack(layers=())


def test_motifs_generate_valid_lattices():
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    pytest.importorskip("shapely")
    from klink.domains.imaging.motifs import MOTIFS, REAL_A_NM

    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    g = MOTIFS["graphene"](square, a_um=0.1)
    assert len(g["species"]) == 1 and g["species"][0]["name"] == "C"
    assert len(g["species"][0]["positions"]) > 50
    assert g["bonds"], "graphene must have bonds"
    m = MOTIFS["mos2"](square, a_um=0.1)
    names = [s["name"] for s in m["species"]]
    assert names == ["Mo", "S"]
    n_mo = len(m["species"][0]["positions"])
    n_s = len(m["species"][1]["positions"])
    assert n_s == pytest.approx(2 * n_mo, rel=0.3)   # two S sheets
    # every bond pairs Mo with an S sheet (z differs)
    assert all(abs(p1[2] - p2[2]) > 0 for p1, p2 in m["bonds"])
    assert REAL_A_NM["graphene"] == 0.246


def test_from_stackspec_refuses_to_guess():
    from klink.process_stack import StackSpec
    spec = StackSpec.from_dict({"conductors": [
        {"layer": "10/0", "role": "gate"},
        {"layer": "20/0", "role": "metal"}]})
    z = {"10/0": (0.0, 0.1), "20/0": (0.3, 0.5)}
    mats = {"10/0": {"name": "poly", "color": "#c94f4f"},
            "20/0": {"name": "alu", "color": "#aab8c4", "metallic": 0.9}}
    vs = VisualStack.from_stackspec(spec, z, mats, name="lab")
    assert [vl.name for vl in vs.layers] == ["poly", "alu"]
    assert vs.by_layer("10/0").role == "gate"
    with pytest.raises(VisualStackError, match="z_um"):
        VisualStack.from_stackspec(spec, {"10/0": (0, 0.1)}, mats)
    with pytest.raises(VisualStackError, match="materials"):
        VisualStack.from_stackspec(spec, z, {"10/0": {"name": "p"}})
    # ... and a material named but never COLOURED is refused too:
    # klink has no house colour to fall back on
    with pytest.raises(VisualStackError, match="color is required"):
        VisualStack.from_stackspec(
            spec, z, {"10/0": {"name": "poly", "color": "#c94f4f"},
                      "20/0": {"name": "alu"}})


def test_output_guard_ignores_comments_and_strings():
    """The multi-line-output() guard is tokenized: a recipe that merely
    MENTIONS output() in a comment or docstring must still run (a naive
    substring check refused a perfectly good recipe)."""
    from klink.domains.imaging.xsection_driver import (XSectionError,
                                                       _strip_outputs)
    ok = ('# no output() lines are needed here\n'
          '"""output( in a string too"""\n'
          'output("300/0", foo)\n'
          'bar = bulk()\n')
    stripped = _strip_outputs(ok)
    assert "bar = bulk()" in stripped
    assert 'output("300/0", foo)' not in stripped

    with pytest.raises(XSectionError, match="ONE line"):
        _strip_outputs('output(\n    "300/0", foo)\n')


def test_underscore_materials_stay_intermediates(device):
    """A recipe variable named with a leading _ is an intermediate: it
    must not be auto-output as a material of its own."""
    gds, _recipe, tmp = device
    recipe = tmp / "under.pyxs"
    recipe.write_text(
        "l1 = layer('1/0')\n"
        "# klink-step: bulk\n"
        "sub = bulk()\n"
        "# klink-step: grow\n"
        "_half = mask(l1).grow(0.2, 0.1, mode='round')\n"
        "film = _half.or_(_half)\n", encoding="utf-8")
    r = run_xsection(gds, str(recipe), [[-1.0, 2.0], [7.0, 2.0]],
                     output_dir=str(tmp / "u"), basename="u", steps=True)
    names = {m["name"] for s in r["outputs"]["stages"]
             for m in s["materials"]}
    assert "film" in names and "sub" in names
    assert not any(n.startswith("_") for n in names)
