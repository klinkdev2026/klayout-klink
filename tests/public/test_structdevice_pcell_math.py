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
