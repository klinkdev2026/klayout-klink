"""Offline guards for the PCell -> T-Cell scaffold.

The scaffold's job is to get the BOILERPLATE right, because a compile error
in written-back code pops a modal dialog in L-Edit that freezes the bridge
until a human closes it. Two things are worth locking down without L-Edit:
the need_layer() helper really comes from the verified template (not a
second, drifting copy), and the parameter-type mapping only ever names
getters that UPI actually has.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[2] / "examples_klink" /
          "public" / "ledit_bridge" / "tcell_workflows.py")
TEMPLATE = SCRIPT.parent / "tcell_template.cpp"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("tcell_workflows", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_need_layer_is_copied_from_the_verified_template(mod):
    src = mod._need_layer_src()
    assert src.startswith("static LLayer need_layer(")
    assert src.rstrip().endswith("}")
    assert src.count("{") == src.count("}")        # balanced, i.e. complete
    # it is the template's text, not a paraphrase living in the script
    assert src in TEMPLATE.read_text(encoding="utf-8")
    # the two rules that make it safe are still in there
    assert "GDSNumber" in src and "lp.GDSNumber < 0" in src


def test_only_real_upi_getters_are_emitted(mod):
    # ldata.h offers exactly these five; anything else would be invented and
    # would fail the in-place compile.
    real = {"LCell_GetParameterAsLayer", "LCell_GetParameterAsCoord",
            "LCell_GetParameterAsCoordOnGrid", "LCell_GetParameterAsDouble",
            "LCell_GetParameterAsBoolean", "LCell_GetParameterAsInt"}
    emitted = {getter for getter, _ctype, _ttype in mod._GETTER.values()}
    assert emitted <= real, "scaffold would emit a getter UPI does not have"
    assert "double" in mod._GETTER and "int" in mod._GETTER


def test_identifier_sanitising_survives_hostile_parameter_names(mod):
    assert mod._cident("w") == "w"
    assert mod._cident("gate-length") == "gate_length"
    assert mod._cident("2fingers").startswith("p_")   # no leading digit
    assert mod._cident("L/D") == "L_D"


def test_params_arg_takes_a_single_set_as_well_as_a_list(mod):
    # a PCell's parameters are ONE object, while --paramsets is a list; the
    # shared loader has to accept both or from_pcell cannot be called.
    assert mod._load_json_arg('{"w": 1}', "params") == {"w": 1}
    assert mod._load_json_arg('[{"w": 1}]', "params") == [{"w": 1}]
    with pytest.raises(SystemExit):
        mod._load_json_arg("not json", "params")
