"""PUBLIC test: the fitted-device PCell EDGE MATH in the KLayout plugin.

The plugin ships generic, N-ary fitted-PCell machinery. This tests its pure edge
math off-KLayout (pya is stubbed): the N-ary model base + sum(coef*param), the
legacy v1 -> v2 normalisation (byte-identical arithmetic), exemplar lookup, and
the integer-dbu honesty rule. No lab data; a synthetic table is used.
"""

import sys
import types
from pathlib import Path

import pytest

# The plugin module imports pya. Prefer the REAL pya (the klayout pip
# package provides one) — a fake left in sys.modules would poison every
# later test that imports klink_server modules in the same process. Only
# stub when pya is genuinely unimportable (bare env without the klayout
# pip package; the pya-dependent tests elsewhere skip there anyway).
try:
    import pya  # noqa: F401
except ImportError:
    pya = types.ModuleType("pya")
    for _n in ("LayerInfo", "Text", "Trans", "Box", "Library"):
        setattr(pya, _n, type(_n, (), {"__init__": lambda self, *a, **k: None}))
    pya.PCellDeclarationHelper = object
    sys.modules["pya"] = pya

_PLUGIN = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))

import klink_server.structdevice_pcell as sd  # noqa: E402


def _v2_table():
    # 2 params (w, l), one parametric edge + one exemplar (non_parametric) edge
    return {
        "format": "klink_fitted_device_pcell_v2",
        "param_order": ["w_um", "l_um"],
        "sample_order": [{"w_um": 7, "l_um": 5}, {"w_um": 14, "l_um": 5}],
        "styles": {"s": {"roles": {"r": {"layer": "1/0", "edges": {
            "x1": {"kind": "parametric", "base": 0, "coef": {"w_um": -500, "l_um": 0}},
            "y1": {"kind": "non_parametric", "values": [-8500, -12500]},
            "x2": {"kind": "parametric", "base": 0, "coef": {"w_um": 500, "l_um": 0}},
            "y2": {"kind": "parametric", "base": 2000, "coef": {"w_um": 0, "l_um": 500}},
        }}}}},
    }


def test_nary_parametric_sum():
    t = _v2_table()
    po, so = t["param_order"], t["sample_order"]
    e = t["styles"]["s"]["roles"]["r"]["edges"]
    # base + sum(coef*param): x1 = -500*w ; y2 = 2000 + 500*l
    assert sd._edge_value(e["x1"], {"w_um": 7, "l_um": 5}, po, so) == -3500
    assert sd._edge_value(e["y2"], {"w_um": 7, "l_um": 5}, po, so) == 4500
    assert sd._edge_value(e["x2"], {"w_um": 14, "l_um": 5}, po, so) == 7000


def test_non_parametric_exemplar_lookup_and_miss():
    t = _v2_table()
    po, so = t["param_order"], t["sample_order"]
    y1 = t["styles"]["s"]["roles"]["r"]["edges"]["y1"]
    assert sd._edge_value(y1, {"w_um": 7, "l_um": 5}, po, so) == -8500
    assert sd._edge_value(y1, {"w_um": 14, "l_um": 5}, po, so) == -12500
    with pytest.raises(ValueError, match="no exemplar"):
        sd._edge_value(y1, {"w_um": 99, "l_um": 5}, po, so)   # not a sample point


def test_integer_dbu_honesty_rule():
    po = ["w_um", "l_um"]
    edge = {"kind": "parametric", "base": 0, "coef": {"w_um": 333, "l_um": 0}}
    with pytest.raises(ValueError, match="non-integer dbu"):
        sd._edge_value(edge, {"w_um": 0.5, "l_um": 0}, po, [])   # 333*0.5 = 166.5


def test_v1_normalises_to_v2_byte_identical():
    # legacy v1 (a + b*W + c*L) must normalise to the SAME integers as the v2 sum
    v1 = {
        "format": "klink_transistor_pcell_fit_v1",
        "sample_order": [{"W": 7, "L": 5}, {"W": 14, "L": 5}],
        "styles": {"s": {"roles": {"r": {"layer": "1/0", "edges": {
            "x1": {"kind": "parametric", "a": -380, "b": -536, "c": -258},
            "y1": {"kind": "non_parametric", "values": [-8500, -12500]},
        }}}}},
    }
    norm = sd._normalise_v1(v1)
    assert norm["param_order"] == ["w_um", "l_um"]
    po, so = norm["param_order"], norm["sample_order"]
    e = norm["styles"]["s"]["roles"]["r"]["edges"]
    for W, L in [(7, 5), (14, 5), (10, 3)]:
        old = -380 + -536 * W + -258 * L          # v1 arithmetic, same order
        assert sd._edge_value(e["x1"], {"w_um": W, "l_um": L}, po, so) == int(round(old))


def _v3_table():
    # one static role + one repeat group exercising every v3 feature:
    # param-driven count (floor_linear den>1), param-driven pitch,
    # fixed x origin, centered y origin
    z = {"kind": "parametric", "base": 0, "coef": {}}
    return {
        "format": "klink_fitted_device_pcell_v3",
        "param_order": ["W", "P"],
        "sample_order": [],
        "styles": {"default": {
            "roles": {"plate": {"layer": "41/0", "edges": {
                "x1": {"kind": "parametric", "base": -600, "coef": {}},
                "y1": {"kind": "parametric", "base": -600, "coef": {}},
                "x2": {"kind": "parametric", "base": 100, "coef": {"W": 1}},
                "y2": {"kind": "parametric", "base": 800, "coef": {}},
            }}},
            "repeat_groups": {"contacts": {
                "unit_roles": {"u0": {"layer": "45/0", "edges": {
                    "x1": z, "y1": z,
                    "x2": {"kind": "parametric", "base": 220, "coef": {}},
                    "y2": {"kind": "parametric", "base": 220, "coef": {}},
                }}},
                "count": {
                    "x": {"kind": "floor_linear", "num_base": 0,
                          "num_coef": {"W": 0, "P": 0}, "den": 1, "plus": 2},
                    "y": {"kind": "floor_linear", "num_base": -300,
                          "num_coef": {"W": 1, "P": 0}, "den": 470,
                          "plus": 1},
                },
                "pitch_dbu": {
                    "x": {"kind": "parametric", "base": 0, "coef": {"P": 1}},
                    "y": {"kind": "parametric", "base": 470, "coef": {}},
                },
                "origin": {
                    "x": {"kind": "fixed",
                          "expr": {"kind": "parametric", "base": -110,
                                   "coef": {}}},
                    "y": {"kind": "centered",
                          "expr": {"kind": "parametric", "base": -220,
                                   "coef": {}}},
                },
            }},
        }},
    }


def test_v3_repeat_group_parity_plugin_vs_klink():
    # The plugin's _count_value/_group_boxes and klink's eval_count/
    # eval_repeat_group/render_table must never drift: same boxes, same
    # errors, byte for byte, or a fitted device silently changes geometry.
    from klink.domains.structdevice import pcell_repeat as R

    t = _v3_table()
    po = t["param_order"]
    group = t["styles"]["default"]["repeat_groups"]["contacts"]
    for W, P in [(770, 800), (1239, 900), (1240, 1000), (2100, 850)]:
        params = {"W": W, "P": P}
        plugin = sorted(_plugin_group_boxes(group, params, po))
        klink_side = sorted(
            (layer, tuple(box)) for layer, box in
            R.eval_repeat_group(group, params, po, []))
        assert plugin == klink_side, (W, P)
        # render_table agrees with a manual plugin-side expansion
        rendered = R.render_table(t, params)
        assert sorted(rendered["45/0"]) == sorted(
            [list(b) for _, b in plugin])


def _plugin_group_boxes(group, params, po):
    return [(layer, tuple(box))
            for layer, box in sd._group_boxes(group, params, po, [])]


def test_v3_count_law_errors_match_between_ends():
    from klink.domains.structdevice.pcell_repeat import eval_count

    bad = {"kind": "floor_linear", "num_base": 0, "num_coef": {"W": 333},
           "den": 1, "plus": 0}
    for fn in (sd._count_value, eval_count):
        with pytest.raises(ValueError, match="non-integer numerator"):
            fn(bad, {"W": 0.5}, ["W"])


def test_v3_table_validates_and_v2_ignores_sampled_block():
    import json
    import tempfile
    import os

    # v3 loads through the plugin loader; a v2 table with the tier-1
    # 'sampled' metadata block still loads (unknown top-level keys are
    # ignored by design)
    v2 = _v2_table()
    v2["sampled"] = {"params": {"w_um": {"min": 7, "max": 14}}, "points": []}
    for table in (v2, _v3_table()):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(table, fh)
            loaded = sd._load_table(path)
            assert loaded["format"] == table["format"]
        finally:
            os.unlink(path)
    # a v3 group missing an axis is refused with an instructive error
    broken = _v3_table()
    del broken["styles"]["default"]["repeat_groups"]["contacts"]["count"]["y"]
    with pytest.raises(ValueError, match="has no 'y' axis"):
        sd._validate_v3(broken)


def test_klink_eval_edge_matches_plugin_edge_value():
    # The klink package ships its own edge evaluator (klink.domains.structdevice
    # .pcell_fitter.eval_edge) so device EXAMPLES stay decoupled from the KLayout
    # plugin. This guard asserts the two implementations never drift -- a drift
    # would silently break a fitted device's geometry (and its LVS).
    from klink.domains.structdevice.pcell_fitter import eval_edge

    t = _v2_table()
    po = t["param_order"]
    so = t["sample_order"]
    edges = t["styles"]["s"]["roles"]["r"]["edges"]
    for W, L in [(7, 5), (14, 5), (10, 3), (50, 8)]:
        params = {"w_um": W, "l_um": L}
        for name, edge in edges.items():
            try:
                plugin_val = sd._edge_value(edge, params, po, so)
                plugin_err = None
            except Exception as e:  # non_parametric miss etc.
                plugin_val, plugin_err = None, type(e).__name__
            try:
                klink_val = eval_edge(edge, params, po, so)
                klink_err = None
            except Exception as e:
                klink_val, klink_err = None, type(e).__name__
            assert (plugin_val, plugin_err) == (klink_val, klink_err), (
                name, (W, L), plugin_val, plugin_err, klink_val, klink_err)
