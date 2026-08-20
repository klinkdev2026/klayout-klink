"""Unit tests: native-PCell registration payload builder (offline).

The exec.python payload is executed here against a minimal fake ``pya``
so the whole registration path — generator compile, reserved-name
check, duplicate refusal, library reuse, produce() box insertion — is
proven without KLayout.
"""

import io
import json
import sys
import types
import contextlib

import pytest

from klink.domains.structdevice.pcell_native import (
    NativePCellError, build_registration_source, register_native_pcell)

GEN = """\
def dev(params):
    w = int(params["w"])
    n = int(params["n"])
    out = []
    for i in range(n):
        x = i * (w + 100)
        out.append([x, 0, x + w, 200])
    return {"1/0": out}
"""

SPEC = [{"name": "w", "type": "int", "default": 500},
        {"name": "n", "type": "int", "default": 2}]


# --------------------------------------------------------------------------- #
# minimal fake pya
# --------------------------------------------------------------------------- #
class _FakeLayout:
    def __init__(self):
        self.pcells = {}
        self.layers = {}

    def register_pcell(self, name, decl):
        self.pcells[name] = decl

    def layer(self, info):
        return self.layers.setdefault((info.l, info.d), len(self.layers))


class _FakeShapes:
    def __init__(self, store):
        self.store = store

    def insert(self, box):
        self.store.append(box)


class _FakeCell:
    def __init__(self):
        self.by_layer = {}

    def shapes(self, li):
        return _FakeShapes(self.by_layer.setdefault(li, []))


class _FakeLibrary:
    def __init__(self):
        self._layout = _FakeLayout()
        self.description = ""
        self.registered_as = None

    def register(self, name):
        self.registered_as = name

    def layout(self):
        return self._layout

    def refresh(self):
        pass


class _FakeHelper:
    TypeInt = "int"
    TypeDouble = "double"
    # a name agents plausibly pick that MUST be refused
    layout = None
    cell = None

    def __init__(self):
        self._params = []

    def param(self, name, ptype, description, default=None):
        self._params.append(name)
        setattr(self, name, default)


def _fake_pya():
    pya = types.ModuleType("pya")
    pya.PCellDeclarationHelper = _FakeHelper
    pya.Library = _FakeLibrary
    pya.LayerInfo = type("LayerInfo", (), {
        "__init__": lambda self, l, d: (setattr(self, "l", l),
                                        setattr(self, "d", d))[0]})
    pya.Box = lambda x1, y1, x2, y2: (x1, y1, x2, y2)
    return pya


def _run_payload(code, pya):
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "pya", pya)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<payload>", "exec"), {})
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# builder validation
# --------------------------------------------------------------------------- #
def test_builder_output_compiles():
    code = build_registration_source(GEN, "dev", "MYDEV", SPEC)
    compile(code, "<payload>", "exec")


@pytest.mark.parametrize("name", ["", "1bad", "a b", "a-b"])
def test_bad_pcell_name_refused(name):
    with pytest.raises(NativePCellError, match="identifier"):
        build_registration_source(GEN, "dev", name, SPEC)


def test_bad_spec_refused():
    with pytest.raises(NativePCellError, match="at least one"):
        build_registration_source(GEN, "dev", "D", [])
    with pytest.raises(NativePCellError, match="duplicate"):
        build_registration_source(GEN, "dev", "D", [
            {"name": "w", "type": "int", "default": 1},
            {"name": "w", "type": "int", "default": 2}])
    with pytest.raises(NativePCellError, match="type"):
        build_registration_source(GEN, "dev", "D", [
            {"name": "w", "type": "str", "default": 1}])
    with pytest.raises(NativePCellError, match="numeric default"):
        build_registration_source(GEN, "dev", "D", [
            {"name": "w", "type": "int", "default": True}])
    with pytest.raises(NativePCellError, match="identifier"):
        build_registration_source(GEN, "dev", "D", [
            {"name": "_w", "type": "int", "default": 1}])


def test_generator_syntax_error_refused():
    with pytest.raises(NativePCellError, match="does not parse"):
        build_registration_source("def dev(:\n", "dev", "D", SPEC)


# --------------------------------------------------------------------------- #
# payload behavior against fake pya
# --------------------------------------------------------------------------- #
def test_payload_registers_and_produces():
    pya = _fake_pya()
    out = _run_payload(
        build_registration_source(GEN, "dev", "MYDEV", SPEC), pya)
    line = [l for l in out.splitlines()
            if l.startswith("KLINK_NATIVE_PCELL_RESULT ")][0]
    report = json.loads(line.split(" ", 1)[1])
    assert report == {"library": "klink_custom", "pcell": "MYDEV",
                      "params": ["w", "n"]}
    lib = pya._klink_native_libs["klink_custom"]
    assert lib.registered_as == "klink_custom"
    decl = lib.layout().pcells["MYDEV"]
    # drive produce_impl exactly like KLayout would
    decl.layout = _FakeLayout()
    decl.cell = _FakeCell()
    decl.w, decl.n = 300, 3
    decl.produce_impl()
    li = decl.layout.layers[(1, 0)]
    assert decl.cell.by_layer[li] == [
        (0, 0, 300, 200), (400, 0, 700, 200), (800, 0, 1100, 200)]
    assert "MYDEV(w=300, n=3)" == decl.display_text_impl()


def test_payload_refuses_duplicate_and_reserved():
    pya = _fake_pya()
    code = build_registration_source(GEN, "dev", "DUP", SPEC)
    _run_payload(code, pya)
    with pytest.raises(ValueError, match="already registered"):
        _run_payload(code, pya)
    # second library into the SAME session reuses nothing but registers new
    code2 = build_registration_source(GEN, "dev", "OTHER", SPEC,
                                      library="other_lib")
    _run_payload(code2, pya)
    assert set(pya._klink_native_libs) == {"klink_custom", "other_lib"}
    # reserved helper attribute ('layout' exists on the helper class)
    bad = [{"name": "layout", "type": "int", "default": 1}]
    with pytest.raises(ValueError, match="collides"):
        _run_payload(
            build_registration_source(GEN, "dev", "RSV", bad), _fake_pya())


def test_layer_map_translates_names_and_refuses_unmapped():
    pya = _fake_pya()
    gen = ('def dev(params):\n'
           '    return {"Poly": [[0, 0, 100, 100]]}\n')
    with pytest.raises(NativePCellError, match="L/D"):
        build_registration_source(gen, "dev", "LM0", SPEC,
                                  layer_map={"Poly": "bad"})
    code = build_registration_source(gen, "dev", "LM1", SPEC,
                                     layer_map={"Poly": "17/0"})
    _run_payload(code, pya)
    decl = pya._klink_native_libs["klink_custom"].layout().pcells["LM1"]
    decl.layout = _FakeLayout()
    decl.cell = _FakeCell()
    decl.produce_impl()
    assert (17, 0) in decl.layout.layers
    # unmapped NAME at produce time is an instruction, not a crash
    code2 = build_registration_source(gen, "dev", "LM2", SPEC)
    _run_payload(code2, pya)
    decl2 = pya._klink_native_libs["klink_custom"].layout().pcells["LM2"]
    decl2.layout = _FakeLayout()
    decl2.cell = _FakeCell()
    with pytest.raises(ValueError, match="layer_map"):
        decl2.produce_impl()


def test_payload_refuses_missing_entry():
    pya = _fake_pya()
    with pytest.raises(ValueError, match="not a function"):
        _run_payload(
            build_registration_source("x = 1\n", "dev", "NOENT", SPEC), pya)


# --------------------------------------------------------------------------- #
# client wrapper
# --------------------------------------------------------------------------- #
class _StubClient:
    def __init__(self, res):
        self.res = res
        self.code = None

    def exec_python(self, code, **kw):
        self.code = code
        return self.res


def test_register_native_pcell_parses_result():
    rep = {"library": "klink_custom", "pcell": "X", "params": ["w", "n"]}
    c = _StubClient({"exception": None,
                     "stdout": "noise\nKLINK_NATIVE_PCELL_RESULT "
                               + json.dumps(rep) + "\n"})
    assert register_native_pcell(c, GEN, "dev", "X", SPEC) == rep
    assert "register_pcell" in c.code


def test_register_native_pcell_surfaces_remote_refusal():
    c = _StubClient({"exception": {"type": "ValueError",
                                   "message": "already registered"},
                     "stdout": ""})
    with pytest.raises(NativePCellError, match="already registered"):
        register_native_pcell(c, GEN, "dev", "X", SPEC)
