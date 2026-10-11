"""Plugin side of deterministic tools: tool.json validation, param resolution, dialogs, run_tool."""

from __future__ import annotations

import copy
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

pytest.importorskip("klayout.db", reason="klayout pip package not installed")

from klink_server import buttons, tools  # noqa: E402
from klink_server.errors import RpcError  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[2] / "examples_klink" / "public" / "features" / "tools" / "bool_selection"
REGION_EXAMPLE = EXAMPLE.parent / "bool_region"
MACRO_EXAMPLE = EXAMPLE.parent / "bool_region_macro"


def good_spec(tid="demo"):
    return {
        "id": tid, "title": "Demo tool", "label": "DEMO",
        "inputs": {"selection": {"min_shapes": 1}},
        "params": {
            "op": {"type": "enum", "choices": ["or", "and"], "default": "or"},
            "count": {"type": "int", "default": 2, "min": 1, "max": 10},
            "width": {"type": "number", "default": 0.5, "min": 0},
            "name": {"type": "string"},
            "keep": {"type": "bool", "default": False},
            "target": {"type": "layer", "default": "same", "allow_same": True},
        },
        "presets": {"first": {"title": "First", "params": {"op": "and", "name": "x"}},
                    "bare": {"params": {}}},
        "prompt": "missing",
    }


# ------------------------------------------------------------------ validation --
def test_example_tool_json_is_valid():
    spec = json.loads((EXAMPLE / "tool.json").read_text(encoding="utf-8"))
    assert tools.validate_tool(spec, "bool_selection") is None
    spec = json.loads((REGION_EXAMPLE / "tool.json").read_text(encoding="utf-8"))
    assert tools.validate_tool(spec, "bool_region") is None
    spec = json.loads((MACRO_EXAMPLE / "tool.json").read_text(encoding="utf-8"))
    assert tools.validate_tool(spec, "bool_region_macro") is None


def test_minimal_tool_json_is_valid():
    assert tools.validate_tool({"id": "m", "title": "M"}, "m") is None
    assert tools.validate_tool({"id": "m", "title": "M", "label": "MAC"}, "m") is None


def test_region_example_layers_come_from_the_layout(tmp_path):
    folder = tmp_path / "tools" / "bool_region"
    folder.mkdir(parents=True)
    for f in ("tool.json", "run.py"):
        (folder / f).write_bytes((REGION_EXAMPLE / f).read_bytes())
    raw = (folder / "tool.json").read_text(encoding="utf-8")
    assert "1/0" not in raw and "100/0" not in raw  # nothing hard-coded but the output proposal
    t = tools.load_tool("bool_region", tmp_path)
    assert t["min_selected"] == 1 and t["min_shapes"] == 0 and t["prompt"] == "missing"
    assert t["params"]["layer_a"]["from"] == "region" and t["params"]["layer_b"]["exclude"] == ["layer_a"]
    assert t["params"]["output"]["allow_new"] is True and t["params"]["output"]["default"] == "3/0"
    for preset in ("and", "or", "not", "xor"):  # a click asks the three layers, nothing else
        values, ask = tools.resolve_params(t, preset, interactive=True)
        assert ask == ["layer_a", "layer_b", "output"]
        assert values == {"output": "3/0", "op": preset, "keep_output": True}
        assert tools.resolve_params(t, preset) == (values, ask)  # no value yet is not an error here
    values, _ = tools.resolve_params(t, "and", {"layer_a": "2", "output": "5 0"})
    assert values["layer_a"] == "2/0" and values["output"] == "5/0"
    with pytest.raises(tools.ToolError) as ei:  # "same" is not a layer for this tool
        tools.resolve_params(t, "and", {"layer_a": "same", "layer_b": "2/0", "output": "3/0"})
    assert ei.value.message == "param 'layer_a': expected a layer as L/D (e.g. 1/0 or just 1)"


def test_good_spec_valid():
    assert tools.validate_tool(good_spec(), "demo") is None


def _mut(fn):
    s = good_spec()
    fn(s)
    return s


BAD = [
    ("not json object", [], "demo", "not a JSON object"),
    ("bad id", _mut(lambda s: s.update(id="Demo")), "Demo", "'id' must match"),
    ("dir mismatch", good_spec(), "other", "does not match the directory name 'other'"),
    ("no title", _mut(lambda s: s.pop("title")), "demo", "'title' must be a non-empty string"),
    ("bad label", _mut(lambda s: s.update(label="  ")), "demo", "'label' must be a non-empty string"),
    ("bad input kind", _mut(lambda s: s.update(inputs={"cells": {}})), "demo", "unknown input kind 'cells'"),
    ("bad min_shapes", _mut(lambda s: s["inputs"]["selection"].update(min_shapes=-1)), "demo",
     "min_shapes must be an integer >= 0"),
    ("bad min", _mut(lambda s: s["inputs"]["selection"].update(min=True)), "demo",
     "inputs.selection.min must be an integer >= 0"),
    ("same not allowed", _mut(lambda s: s["params"]["target"].pop("allow_same")), "demo",
     "params.target.default: expected a layer as L/D (e.g. 1/0 or just 1)"),
    ("allow_same not bool", _mut(lambda s: s["params"]["target"].update(allow_same="yes")), "demo",
     "params.target.allow_same must be true/false"),
    ("allow_same off layer", _mut(lambda s: s["params"]["name"].update(allow_same=True)), "demo",
     "params.name.allow_same must be true/false, on a layer param"),
    ("bad param type", _mut(lambda s: s["params"]["op"].update(type="float")), "demo",
     "params.op.type must be one of"),
    ("enum no choices", _mut(lambda s: s["params"]["op"].pop("choices")), "demo",
     "params.op: enum needs a non-empty 'choices'"),
    ("bad default", _mut(lambda s: s["params"]["count"].update(default="many")), "demo",
     "params.count.default: expected an integer"),
    ("default out of range", _mut(lambda s: s["params"]["count"].update(default=11)), "demo",
     "params.count.default: must be <= 10"),
    ("preset unknown param", _mut(lambda s: s["presets"]["first"]["params"].update(nope=1)), "demo",
     "presets.first: unknown param 'nope'"),
    ("preset value invalid", _mut(lambda s: s["presets"]["first"]["params"].update(op="xor")), "demo",
     "presets.first.params.op: expected one of or, and"),
    ("preset no params", _mut(lambda s: s["presets"]["first"].pop("params")), "demo",
     "presets.first.params must be an object"),
    ("bad preset name", _mut(lambda s: s["presets"].update({"Bad": {"params": {}}})), "demo",
     "preset name 'Bad' must match"),
    ("bad prompt", _mut(lambda s: s.update(prompt="sometimes")), "demo", "'prompt' must be one of"),
    ("bad layer default", _mut(lambda s: s["params"]["target"].update(default="1-0")), "demo",
     "expected a layer as L/D"),
    ("bad from", _mut(lambda s: s["params"]["target"].update({"from": "screen"})), "demo",
     "params.target.from must be one of region, selection, layout"),
    ("from off layer", _mut(lambda s: s["params"]["name"].update({"from": "layout"})), "demo",
     "params.name.from must be one of region, selection, layout, on a layer param"),
    ("allow_new off layer", _mut(lambda s: s["params"]["count"].update(allow_new=True)), "demo",
     "params.count.allow_new must be true/false, on a layer param"),
    ("allow_new not bool", _mut(lambda s: s["params"]["target"].update({"from": "layout", "allow_new": 1})),
     "demo", "params.target.allow_new must be true/false"),
    ("allow_new without from", _mut(lambda s: s["params"]["target"].update(allow_new=True)), "demo",
     "params.target.allow_new needs 'from'"),
    ("skip not bool", _mut(lambda s: s["params"]["target"].update({"from": "region",
                                                                  "skip_selection_layers": "no"})),
     "demo", "params.target.skip_selection_layers must be true/false"),
    ("exclude without from", _mut(lambda s: s["params"]["target"].update(exclude=[])), "demo",
     "params.target.exclude needs 'from'"),
    ("exclude not list", _mut(lambda s: s["params"]["target"].update({"from": "region", "exclude": "a"})),
     "demo", "params.target.exclude must be a list of layer param names"),
    ("exclude unknown", _mut(lambda s: s["params"]["target"].update({"from": "region", "exclude": ["zz"]})),
     "demo", "params.target.exclude: 'zz' is not another layer param of this tool"),
    ("exclude non-layer", _mut(lambda s: s["params"]["target"].update({"from": "region", "exclude": ["op"]})),
     "demo", "params.target.exclude: 'op' is not another layer param"),
    ("exclude itself", _mut(lambda s: s["params"]["target"].update({"from": "region",
                                                                   "exclude": ["target"]})),
     "demo", "params.target.exclude: 'target' is not another layer param"),
]


@pytest.mark.parametrize("label,spec,dirname,needle", BAD, ids=[b[0] for b in BAD])
def test_validation_rejects(label, spec, dirname, needle):
    problem = tools.validate_tool(spec, dirname)
    assert problem is not None and needle in problem, problem
    assert "\n" not in problem


def _install(root: Path, spec: dict, run_src: str | None = "def run(ctx, params):\n    return {}\n"):
    folder = root / "tools" / spec["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tool.json").write_text(json.dumps(spec), encoding="utf-8")
    if run_src is not None:
        (folder / "run.py").write_text(run_src, encoding="utf-8")
    return folder


def test_missing_run_py_unusable(tmp_path):
    _install(tmp_path, good_spec(), run_src=None)
    with pytest.raises(tools.ToolError) as ei:
        tools.load_tool("demo", tmp_path)
    assert ei.value.code == "ERR_TOOL_NOT_FOUND" and "missing run.py" in ei.value.message
    assert tools.list_tools(tmp_path) == [
        {"id": "demo", "title": None, "label": None, "presets": [], "problem": ei.value.message}]


def test_not_installed_lists_installed(tmp_path):
    _install(tmp_path, good_spec())
    with pytest.raises(tools.ToolError) as ei:
        tools.load_tool("nope", tmp_path)
    assert ei.value.code == "ERR_TOOL_NOT_FOUND" and "demo" in ei.value.hint
    with pytest.raises(tools.ToolError):
        tools.load_tool("../demo", tmp_path)


def test_load_normalizes(tmp_path):
    spec = good_spec()
    spec.pop("label")
    _install(tmp_path, spec)
    t = tools.load_tool("demo", tmp_path)
    assert t["label"] == "DEMO" and t["min_shapes"] == 1 and t["prompt"] == "missing"
    assert t["presets"]["bare"]["title"] == "bare" and t["presets"]["first"]["title"] == "First"
    assert Path(t["run_path"]).name == "run.py"
    assert tools.list_tools(tmp_path)[0]["presets"] == ["first", "bare"]


# ------------------------------------------------------------- resolve_params --
def _tool(tmp_path, **over):
    spec = good_spec()
    spec.update(over)
    _install(tmp_path, spec)
    return tools.load_tool("demo", tmp_path)


def test_precedence_defaults_preset_given(tmp_path):
    t = _tool(tmp_path)
    values, ask = tools.resolve_params(t, "first", {"count": "7", "keep": "yes", "target": " 2 / 0 "})
    assert values == {"op": "and", "count": 7, "width": 0.5, "name": "x", "keep": True, "target": "2/0"}
    # "missing": everything not pinned by the preset (or given explicitly) is asked
    assert ask == ["width"]


def test_missing_rule_asks_defaults_not_in_preset(tmp_path):
    t = _tool(tmp_path)
    values, ask = tools.resolve_params(t, "bare", None, interactive=True)
    assert ask == ["op", "count", "width", "name", "keep", "target"]
    assert "name" not in values  # required, will be asked


def test_preset_pinning_everything_asks_nothing(tmp_path):
    spec = good_spec()
    spec["presets"]["all"] = {"params": {"op": "or", "count": 1, "width": 1, "name": "n",
                                         "keep": True, "target": "same"}}
    _install(tmp_path, spec)
    t = tools.load_tool("demo", tmp_path)
    assert tools.resolve_params(t, "all")[1] == []


def test_prompt_always_and_never(tmp_path):
    t = _tool(tmp_path, prompt="always")
    assert tools.resolve_params(t, "first")[1] == list(t["params"])
    t = _tool(tmp_path, prompt="never")
    values, ask = tools.resolve_params(t, "first")
    assert ask == [] and values["name"] == "x"
    with pytest.raises(tools.ToolError) as ei:
        tools.resolve_params(t, "bare", interactive=True)  # never asks, so interactive does not help
    assert ei.value.code == "ERR_TOOL_PARAMS" and "name" in ei.value.message
    assert "interactive" not in ei.value.hint


def test_required_without_value_noninteractive(tmp_path):
    t = _tool(tmp_path)
    with pytest.raises(tools.ToolError) as ei:
        tools.resolve_params(t, None, None, interactive=False)
    assert ei.value.code == "ERR_TOOL_PARAMS" and "name" in ei.value.message
    assert "--param" in ei.value.hint


@pytest.mark.parametrize("given,needle", [
    ({"count": "0"}, "must be >= 1"), ({"count": 11}, "must be <= 10"), ({"count": 1.5}, "integer"),
    ({"count": True}, "integer"), ({"width": "-1"}, ">= 0"), ({"width": "abc"}, "a number"),
    ({"keep": "maybe"}, "yes/no"), ({"op": "xor"}, "one of or, and"), ({"target": "1/x"}, "L/D"),
    ({"name": 3}, "a string"), ({"nope": 1}, "no param 'nope'"),
])
def test_bad_given_values(tmp_path, given, needle):
    t = _tool(tmp_path)
    with pytest.raises(tools.ToolError) as ei:
        tools.resolve_params(t, "first", given)
    assert ei.value.code == "ERR_TOOL_PARAMS" and needle in ei.value.message


def test_unknown_preset(tmp_path):
    t = _tool(tmp_path)
    with pytest.raises(tools.ToolError) as ei:
        tools.resolve_params(t, "zzz")
    assert ei.value.code == "ERR_TOOL_PARAMS" and "first, bare" in ei.value.hint


def test_layer_forms():
    p = {"type": "layer", "allow_same": True}
    assert tools.coerce(p, "SAME") == "same"
    assert tools.coerce(p, "10/03") == "10/3"
    for bad in ("a/b", "1/2/3", "1-0", "", "-1", -1, True, [1], [1, True], (1, 0), 1.0, None):
        with pytest.raises(ValueError, match="or 'same'"):
            tools.coerce(p, bad)
    for strict in ({"type": "layer"}, {"type": "layer", "allow_same": False}):
        assert tools.coerce(strict, " 7 / 0 ") == "7/0"
        with pytest.raises(ValueError) as ei:
            tools.coerce(strict, "same")
        assert str(ei.value) == "expected a layer as L/D (e.g. 1/0 or just 1)"


@pytest.mark.parametrize("value,want", [
    ("1/0", "1/0"), ("1", "1/0"), (" 1 ", "1/0"), ("1,0", "1/0"), ("1 0", "1/0"), ("1:0", "1/0"),
    (" 100 , 2 ", "100/2"), ("7 :3", "7/3"), ("100", "100/0"), (1, "1/0"), (100, "100/0"),
    ([1, 0], "1/0"), ([100, 5], "100/5"),
])
def test_layer_lenient_forms(value, want):
    assert tools.coerce({"type": "layer"}, value) == want
    assert tools.coerce({"type": "layer", "allow_same": True}, value) == want


# ----------------------------------------------------------------- ask_params --
class FakeDialog:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def _next(self, kind, *args):
        self.calls.append((kind, *args))
        return self.answers.pop(0)

    def ask_item(self, title, label, items, index):
        return self._next("item", label, list(items), index)

    def ask_int_ex(self, title, label, value, lo, hi, step):
        return self._next("int", label, value, lo, hi)

    def ask_double_ex(self, title, label, value, lo, hi, digits):
        return self._next("double", label, value, lo, hi)

    def ask_string(self, title, label, value):
        return self._next("string", label, value)


class DialogPya:
    def __init__(self, answers):
        self.InputDialog = FakeDialog(answers)


def test_ask_params_in_order(tmp_path):
    t = _tool(tmp_path)
    fake = DialogPya(["and", 3, 1.25, "hello", "yes", "5/0"])
    values, ask = tools.resolve_params(t, "bare", interactive=True)
    out = tools.ask_params(fake, t, ask, values)
    assert out == {"op": "and", "count": 3, "width": 1.25, "name": "hello", "keep": True, "target": "5/0"}
    kinds = [c[0] for c in fake.InputDialog.calls]
    assert kinds == ["item", "int", "double", "string", "item", "string"]
    assert fake.InputDialog.calls[0][2:] == (["or", "and"], 0)        # default proposed
    assert fake.InputDialog.calls[1][2:] == (2, 1, 10)                 # value, min, max
    assert fake.InputDialog.calls[2][2:] == (0.5, 0.0, 1e15)           # number: min given, no max
    assert fake.InputDialog.calls[4][2:] == (["yes", "no"], 1)         # keep default False -> "no"


def test_ask_params_cancel(tmp_path):
    t = _tool(tmp_path)
    fake = DialogPya(["or", None])
    assert tools.ask_params(fake, t, ["op", "count"], {"op": "or", "count": 2}) is None


def test_ask_params_reasks_invalid(tmp_path):
    t = _tool(tmp_path)
    fake = DialogPya(["1-0", "1/0"])
    assert tools.ask_params(fake, t, ["target"], {"target": "same"}) == {"target": "1/0"}
    assert fake.InputDialog.calls[1][1].startswith("expected a layer as L/D")


# -------------------------------------------------------------------- run_tool --
class FakeShapeObj:
    def __init__(self, inst=False):
        self._inst = inst

    def is_cell_inst(self):
        return self._inst


class FakeLayout:
    dbu = 0.001


class FakeCell:
    def __init__(self):
        self.inserted = []

    def cell_index(self):
        return 0


class FakeCV:
    def __init__(self):
        self.cell = FakeCell()
        self._layout = FakeLayout()

    def is_valid(self):
        return True

    def layout(self):
        return self._layout


class FakeView:
    def __init__(self, selection):
        self.selection = selection
        self.cv = FakeCV()

    def active_cellview(self):
        return self.cv

    def each_object_selected(self):
        return iter(self.selection)


class FakeMessageBox:
    Ok = 1
    shown = []

    @classmethod
    def info(cls, title, text, buttons):
        cls.shown.append(("info", title, text))

    @classmethod
    def critical(cls, title, text, buttons):
        cls.shown.append(("critical", title, text))


class RunPya:
    def __init__(self, view, answers=()):
        self._view = view
        self.InputDialog = FakeDialog(answers)
        self.MessageBox = FakeMessageBox
        FakeMessageBox.shown = []
        outer = self

        class _MW:
            def current_view(self):
                return outer._view

        class _App:
            def main_window(self):
                return _MW()

        class Application:
            @staticmethod
            def instance():
                return _App()

        self.Application = Application


class FakeTxn:
    """Stands in for auto_txn: records enter/exit/exception and wraps like the real one."""

    def __init__(self):
        self.events = []

    def __call__(self, view, title):
        @contextmanager
        def cm():
            self.events.append(("enter", title))
            try:
                yield
            except BaseException as exc:
                self.events.append(("exception", exc))
                raise RpcError("ERR_INTERNAL", f"{type(exc).__name__}: {exc}",
                               data={"transaction": {"title": title, "partial_edit": "undone"}}) from exc
            self.events.append(("exit",))
        return cm()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(tmp_path))
    monkeypatch.setattr(buttons, "_session_id", lambda srv=None: "sess1")
    txn = FakeTxn()
    monkeypatch.setattr(tools, "_txn", txn)
    state = {"txn": txn, "root": tmp_path}

    def use(selection, answers=()):
        fake = RunPya(FakeView(selection), answers)
        monkeypatch.setattr(tools, "_pya", lambda: fake)
        state["pya"] = fake
        return fake
    state["use"] = use
    return state


def _journal(root):
    path = root / "journals" / "sess1.tools.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


RUN_OK = """
def run(ctx, params):
    ctx.cell.inserted.append(params["op"])
    return {"op": params["op"], "n": len(ctx.selection), "dbu": ctx.dbu,
            "pya_is_ctx": pya is ctx.pya, "dir": ctx.tool_dir.name}
"""


def _spec_with_defaults():
    spec = good_spec()
    spec["params"]["name"]["default"] = "d"
    return spec


def test_run_ok_journals_and_returns(env):
    _install(env["root"], _spec_with_defaults(), RUN_OK)
    fake = env["use"]([FakeShapeObj(), FakeShapeObj(inst=True), FakeShapeObj()])
    fake._view.add_missing_layers = lambda: env["txn"].events.append(("layers",))
    out = tools.run_tool("demo", preset="first")
    assert out["status"] == "ok" and out["selection_count"] == 2 and out["seq"] == 1
    assert out["result"] == {"op": "and", "n": 2, "dbu": 0.001, "pya_is_ctx": True, "dir": "demo"}
    assert out["params"]["op"] == "and" and out["tool"] == "demo" and out["preset"] == "first"
    # layer views are added INSIDE the transaction (after commit they wipe KLayout's undo stack)
    assert [e[0] for e in env["txn"].events] == ["enter", "layers", "exit"]
    assert env["txn"].events[0][1] == "klink tool: Demo tool"
    rec = _journal(env["root"])
    assert len(rec) == 1 and rec[0]["status"] == "ok" and rec[0]["seq"] == 1
    out2 = tools.run_tool("demo", preset="first")
    assert out2["seq"] == 2


def test_run_raising_rolls_back_and_journals_error(env):
    src = "def run(ctx, params):\n    ctx.cell.inserted.append(1)\n    raise ValueError('pick two boxes')\n"
    _install(env["root"], _spec_with_defaults(), src)
    env["use"]([FakeShapeObj()])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    err = ei.value
    assert err.code == "ERR_TOOL_RUN" and err.message == "pick two boxes"
    assert "nothing was changed" in err.hint and "run.py" in err.hint
    assert err.data["transaction"]["partial_edit"] == "undone"
    assert 'run.py", line 3' in err.data["traceback"]
    kinds = [e[0] for e in env["txn"].events]
    assert kinds == ["enter", "exception"] and isinstance(env["txn"].events[1][1], ValueError)
    rec = _journal(env["root"])[-1]
    assert rec["status"] == "error" and rec["error"]["code"] == "ERR_TOOL_RUN"
    assert rec["error"]["type"] == "ValueError" and rec["error"]["message"] == "pick two boxes"
    assert "nothing was changed" in rec["error"]["hint"]   # the hint is journaled for `tool log`


def test_run_non_dict_result_rolls_back(env):
    _install(env["root"], _spec_with_defaults(), "def run(ctx, params):\n    return [1]\n")
    env["use"]([FakeShapeObj()])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    assert "must return a dict" in ei.value.message
    assert [e[0] for e in env["txn"].events] == ["enter", "exception"]


def test_min_shapes_unmet_before_txn(env):
    spec = _spec_with_defaults()
    spec["inputs"]["selection"]["min_shapes"] = 2
    _install(env["root"], spec, RUN_OK)
    fake = env["use"]([FakeShapeObj(), FakeShapeObj(inst=True)])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    assert ei.value.code == "ERR_TOOL_INPUT" and "at least 2 selected shape(s); 1 selected" in ei.value.message
    assert env["txn"].events == [] and fake.MessageBox.shown == []
    assert _journal(env["root"])[-1]["status"] == "error"
    with pytest.raises(tools.ToolError):
        tools.run_tool("demo", preset="first", interactive=True)
    assert [s[0] for s in fake.MessageBox.shown] == ["info"]


def test_missing_required_noninteractive_before_txn(env):
    _install(env["root"], good_spec(), RUN_OK)  # "name" has no default
    env["use"]([FakeShapeObj()])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="bare")
    assert ei.value.code == "ERR_TOOL_PARAMS" and env["txn"].events == []


def test_interactive_asks_then_runs(env):
    _install(env["root"], good_spec(), RUN_OK)
    fake = env["use"]([FakeShapeObj()], answers=["7", 1.0, "typed", "no", "same"])
    out = tools.run_tool("demo", preset="first", interactive=True)
    assert out["status"] == "ok" and out["params"]["count"] == 7 and out["params"]["name"] == "x"
    assert out["params"]["width"] == 1.0 and out["params"]["keep"] is False
    assert len(fake.InputDialog.calls) == 5  # op + name pinned by preset "first"


def test_interactive_cancel(env):
    _install(env["root"], good_spec(), RUN_OK)
    env["use"]([FakeShapeObj()], answers=[None])
    out = tools.run_tool("demo", preset="first", interactive=True)
    assert out["status"] == "cancelled" and env["txn"].events == []
    assert _journal(env["root"])[-1]["status"] == "cancelled"


def test_no_view_is_input_error(env, monkeypatch):
    _install(env["root"], _spec_with_defaults(), RUN_OK)
    fake = env["use"]([])
    fake._view = None
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    assert ei.value.code == "ERR_TOOL_INPUT" and "no layout" in ei.value.message


def test_viewer_mode_passes_code_through(env, monkeypatch):
    _install(env["root"], _spec_with_defaults(), RUN_OK)
    env["use"]([FakeShapeObj()])

    def refuse(view, title):
        raise RpcError("ERR_VIEWER_MODE", "viewer mode", hint="restart with -e")
    monkeypatch.setattr(tools, "_txn", refuse)
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    assert ei.value.code == "ERR_VIEWER_MODE" and ei.value.hint == "restart with -e"


def test_broken_run_py_is_run_error(env):
    _install(env["root"], _spec_with_defaults(), "def run(ctx, params)\n")
    env["use"]([FakeShapeObj()])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    assert ei.value.code == "ERR_TOOL_RUN" and "failed to load" in ei.value.message
    assert env["txn"].events == []


# ------------------------------------------------------- form (b): plain macro --
MACRO = """import pya
print("loaded")   # module level: must only run inside the transaction

def main():
    view = pya.Application.instance().main_window().current_view()
    cell = view.active_cellview().cell
    cell.inserted.append("macro")
    sel = list(view.each_object_selected())
    if not sel:
        raise ValueError("未选中任何对象！请先选中要进行运算的区域。")
    print(f"完成！{len(sel)} 个对象")
    print("")

if __name__ == "__main__":
    main()
"""


@pytest.fixture
def macro_env(env, monkeypatch):
    """env + `import pya` inside a macro resolves to the fake pya of the run."""
    def use(selection, answers=()):
        fake = env["use"](selection, answers)
        monkeypatch.setitem(sys.modules, "pya", fake)
        fake.status = []
        fake.Application.instance().main_window().__class__.message = \
            lambda self, text, ms: fake.status.append((text, ms))
        return fake
    return {**env, "use": use}


def _minimal(tid="demo", **extra):
    return {"id": tid, "title": "Macro tool", **extra}


def test_macro_runs_inside_txn_with_main_guard(macro_env, capsys):
    _install(macro_env["root"], _minimal(), MACRO)
    fake = macro_env["use"]([FakeShapeObj(), FakeShapeObj(inst=True)])
    cell = fake._view.cv.cell
    events = macro_env["txn"].events
    real_enter = macro_env["txn"].__call__

    def spy(view, title):  # the macro body must not have run when the txn opens
        events.append(("inserted_at_enter", list(cell.inserted)))
        return real_enter(view, title)
    import klink_server.tools as T
    T._txn = spy
    try:
        out = tools.run_tool("demo")
    finally:
        T._txn = macro_env["txn"]
    assert out["status"] == "ok" and cell.inserted == ["macro"]
    assert ("inserted_at_enter", []) in events
    assert [e[0] for e in events if e[0] in ("enter", "exit")] == ["enter", "exit"]
    assert out["result"] == {"stdout": "loaded\n完成！2 个对象"}
    assert _journal(macro_env["root"])[-1]["result"]["stdout"] == "loaded\n完成！2 个对象"
    assert "完成！2 个对象" in capsys.readouterr().out  # still forwarded to the console
    assert fake.status == []  # not interactive: no status bar message


def test_macro_without_main_guard_and_ctx_params_globals(macro_env):
    src = "cell = ctx.cell\ncell.inserted.append(params.get('n', 'none'))\n" \
          "assert __name__ == '__main__' and pya is ctx.pya\n"
    _install(macro_env["root"], _minimal(params={"n": {"type": "int", "default": 4}}), src)
    fake = macro_env["use"]([FakeShapeObj()])
    out = tools.run_tool("demo")
    assert out["status"] == "ok" and out["result"] == {} and fake._view.cv.cell.inserted == [4]


def test_macro_load_does_not_execute_body(macro_env):
    _install(macro_env["root"], _minimal(), "ctx.cell.inserted.append('ran')\n")
    fake = macro_env["use"]([FakeShapeObj()])
    t = tools.load_tool("demo", macro_env["root"])
    run = tools._load_run(t, fake)
    assert fake._view.cv.cell.inserted == [] and macro_env["txn"].events == []
    run(type("C", (), {"cell": fake._view.cv.cell})(), {})
    assert fake._view.cv.cell.inserted == ["ran"]


def test_macro_value_error_rolls_back_with_message(macro_env):
    _install(macro_env["root"], _minimal(), MACRO)
    macro_env["use"]([])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo")
    err = ei.value
    assert err.code == "ERR_TOOL_RUN" and err.message == "未选中任何对象！请先选中要进行运算的区域。"
    assert "nothing was changed" in err.hint and err.data["transaction"]["partial_edit"] == "undone"
    assert err.data["stdout"] == "loaded"
    assert [e[0] for e in macro_env["txn"].events] == ["enter", "exception"]
    rec = _journal(macro_env["root"])[-1]
    assert rec["status"] == "error" and rec["error"]["type"] == "ValueError" and rec["stdout"] == "loaded"


def test_macro_min_selection_is_friendly_input_error(macro_env):
    _install(macro_env["root"], _minimal(inputs={"selection": {"min": 1}}), MACRO)
    fake = macro_env["use"]([])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", interactive=True)
    assert ei.value.code == "ERR_TOOL_INPUT" and macro_env["txn"].events == []
    assert [s[0] for s in fake.MessageBox.shown] == ["info"]


def test_macro_syntax_error_names_path(macro_env):
    folder = _install(macro_env["root"], _minimal(), "def main(:\n    pass\n")
    macro_env["use"]([FakeShapeObj()])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo")
    assert ei.value.code == "ERR_TOOL_RUN" and "SyntaxError" in ei.value.message
    assert str(folder / "run.py") in ei.value.message and macro_env["txn"].events == []


def test_macro_sys_exit(macro_env):
    _install(macro_env["root"], _minimal(), "import sys\nprint('a')\nsys.exit(0)\nprint('b')\n")
    macro_env["use"]([FakeShapeObj()])
    assert tools.run_tool("demo")["result"] == {"stdout": "a"}
    _install(macro_env["root"], _minimal(), "import sys\nsys.exit(2)\n")
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo")
    assert "sys.exit(2)" in ei.value.message and ei.value.code == "ERR_TOOL_RUN"


def test_macro_with_utf8_bom(macro_env):
    folder = _install(macro_env["root"], _minimal(), None)
    (folder / "run.py").write_bytes("\ufeffprint('bom ok')\n".encode("utf-8"))
    macro_env["use"]([FakeShapeObj()])
    assert tools.run_tool("demo")["result"] == {"stdout": "bom ok"}


def test_form_a_stdout_merged_without_overwriting(macro_env):
    src = "def run(ctx, params):\n    print('hi')\n    return {'n': 1}\n"
    _install(macro_env["root"], _minimal(), src)
    macro_env["use"]([FakeShapeObj()])
    assert tools.run_tool("demo")["result"] == {"n": 1, "stdout": "hi"}
    src = "def run(ctx, params):\n    print('hi')\n    return {'stdout': 'mine'}\n"
    _install(macro_env["root"], _minimal(), src)
    assert tools.run_tool("demo")["result"] == {"stdout": "mine"}
    _install(macro_env["root"], _minimal(), "def run(ctx, params):\n    return None\n")
    assert tools.run_tool("demo")["result"] == {}  # nothing printed: no stdout key


def test_status_bar_on_interactive_ok(macro_env):
    _install(macro_env["root"], _minimal(), MACRO)
    fake = macro_env["use"]([FakeShapeObj()])
    tools.run_tool("demo", interactive=True)
    assert fake.status == [("完成！1 个对象", 4000)]
    _install(macro_env["root"], _minimal(), "x = 1\n")
    tools.run_tool("demo", interactive=True)
    assert fake.status[-1] == ("Macro tool: done", 4000)


def test_status_bar_failure_never_breaks_run(macro_env):
    _install(macro_env["root"], _minimal(), MACRO)
    fake = macro_env["use"]([FakeShapeObj()])

    def boom(self, text, ms):
        raise RuntimeError("no status bar")
    fake.Application.instance().main_window().__class__.message = boom
    out = tools.run_tool("demo", interactive=True)
    assert out["status"] == "ok" and _journal(macro_env["root"])[-1]["status"] == "ok"


# ---------------------------------------------------------------- buttons hook --
def test_button_tool_definition_dispatches(env, monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: calls.append((a, k)) or {"status": "ok"})
    manifest = {"id": "x", "title": "X", "definition": "tool:x",
                "items": [{"id": "x_item", "title": "Item", "definition": "tool:x"}]}
    buttons._on_trigger(manifest, "x_item", "item")
    assert calls == [(("x",), {"preset": "x_item", "interactive": True})]


def test_button_other_definition_does_not_dispatch(env, monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: calls.append((a, k)))
    buttons._on_trigger({"id": "y", "title": "Y", "definition": "notes/notes/y.md"}, None, None)
    buttons._on_trigger({"id": "z", "title": "Z", "definition": None}, None, None)
    assert calls == []


def test_button_tool_error_shows_critical(env, monkeypatch):
    shown = []

    def boom(*a, **k):
        raise tools.ToolError("ERR_TOOL_RUN", "it broke", hint="fix run.py")
    monkeypatch.setattr(tools, "run_tool", boom)

    class Box:
        Ok = 1

        @staticmethod
        def critical(title, text, b):
            shown.append((title, text))
    fake_pya = type(sys)("pya")
    fake_pya.MessageBox = Box
    monkeypatch.setitem(sys.modules, "pya", fake_pya)
    buttons._on_trigger({"id": "x", "title": "X", "definition": "tool:x"}, None, None)
    assert shown == [("X", "it broke\n\nfix run.py")]
    shown.clear()

    def input_err(*a, **k):
        raise tools.ToolError("ERR_TOOL_INPUT", "select more")
    monkeypatch.setattr(tools, "run_tool", input_err)
    buttons._on_trigger({"id": "x", "title": "X", "definition": "tool:x"}, None, None)
    assert shown == []  # run_tool already showed its info box


def test_spec_not_mutated_by_load(tmp_path):
    spec = good_spec()
    before = copy.deepcopy(spec)
    _install(tmp_path, spec)
    tools.load_tool("demo", tmp_path)
    assert spec == before


# ------------------------------------------- example tool body against klayout.db --
class _Obj:
    def __init__(self, shape, layer, cell_index, trans=None):
        self.shape, self.layer, self._ci, self._t = shape, layer, cell_index, trans

    def cell_index(self):
        return self._ci

    def trans(self):
        import pya
        return self._t or pya.ICplxTrans()


class _View:
    def __init__(self):
        self.cleared = 0

    def clear_object_selection(self):
        self.cleared += 1


def _example_ctx(boxes, nested_last=False):
    import pya
    ly = pya.Layout()
    top = ly.create_cell("TOP")
    li = ly.layer(1, 0)
    objs = []
    for i, b in enumerate(boxes):
        shape = top.shapes(li).insert(pya.Box(*b))
        objs.append(_Obj(shape, li, top.cell_index() + (1 if nested_last and i == len(boxes) - 1 else 0)))
    ns = {"pya": pya}
    exec(compile((EXAMPLE / "run.py").read_text(encoding="utf-8"), str(EXAMPLE / "run.py"), "exec"), ns)
    ctx = type("Ctx", (), {})()
    ctx.pya, ctx.layout, ctx.cell, ctx.selection, ctx.view = pya, ly, top, objs, _View()
    return ns["run"], ctx, li


@pytest.mark.parametrize("op,area,polys", [("or", 175, 1), ("and", 25, 1), ("not", 75, 1), ("xor", 150, 1)])
def test_example_bool_ops(op, area, polys):
    run, ctx, li = _example_ctx([(0, 0, 10, 10), (5, 5, 15, 15)])
    out = run(ctx, {"op": op, "target": "same", "keep": False})
    assert out == {"op": op, "inputs": 2, "result_polygons": polys, "target_layer": "1/0", "removed": 2}
    import pya
    region = pya.Region(ctx.cell.shapes(li))
    assert region.area() == area and ctx.cell.shapes(li).size() == polys
    assert ctx.view.cleared == 1


def test_example_keep_and_target_layer():
    run, ctx, li = _example_ctx([(0, 0, 10, 10), (5, 5, 15, 15)])
    out = run(ctx, {"op": "and", "target": "2/0", "keep": True})
    assert out["target_layer"] == "2/0" and out["removed"] == 0
    assert ctx.cell.shapes(li).size() == 2
    assert ctx.cell.shapes(ctx.layout.layer(2, 0)).size() == 1


def test_example_refuses_before_mutating():
    run, ctx, li = _example_ctx([(0, 0, 10, 10), (5, 5, 15, 15)], nested_last=True)
    with pytest.raises(ValueError, match="inside instances; run with keep=true"):
        run(ctx, {"op": "or", "target": "same", "keep": False})
    assert ctx.cell.shapes(li).size() == 2
    run, ctx, li = _example_ctx([(0, 0, 1, 1), (5, 5, 6, 6)])
    with pytest.raises(ValueError, match="result is empty"):
        run(ctx, {"op": "and", "target": "same", "keep": False})
    assert ctx.cell.shapes(li).size() == 2


# ------------------------------------------------ inputs.selection.min + ctx.selected --
RUN_SELECTED = """
def run(ctx, params):
    return {"shapes": len(ctx.selection), "all": len(ctx.selected),
            "insts": sum(1 for o in ctx.selected if o.is_cell_inst())}
"""


def test_min_counts_instances_and_ctx_selected_has_them(env):
    spec = _spec_with_defaults()
    spec["inputs"]["selection"] = {"min": 2}
    _install(env["root"], spec, RUN_SELECTED)
    env["use"]([FakeShapeObj(inst=True), FakeShapeObj()])
    out = tools.run_tool("demo", preset="first")
    assert out["result"] == {"shapes": 1, "all": 2, "insts": 1}
    assert out["selection_count"] == 1 and out["selected_count"] == 2
    env["use"]([FakeShapeObj(inst=True), FakeShapeObj(inst=True)])  # instances only also count
    assert tools.run_tool("demo", preset="first")["result"]["all"] == 2


def test_min_unmet_before_txn(env):
    spec = _spec_with_defaults()
    spec["inputs"]["selection"] = {"min": 2, "min_shapes": 0}
    _install(env["root"], spec, RUN_SELECTED)
    env["use"]([FakeShapeObj(inst=True)])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    assert ei.value.code == "ERR_TOOL_INPUT"
    assert "at least 2 selected object(s); 1 selected" in ei.value.message
    assert "shapes or instances" in ei.value.hint and env["txn"].events == []


def test_min_and_min_shapes_both_enforced(env):
    spec = _spec_with_defaults()
    spec["inputs"]["selection"] = {"min": 1, "min_shapes": 1}
    _install(env["root"], spec, RUN_SELECTED)
    env["use"]([FakeShapeObj(inst=True)])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("demo", preset="first")
    assert "selected shape(s)" in ei.value.message


# --------------------------------------- bool_region example body against klayout.db/lay --
def _region_scene(inst_disp=(0, 0), array=None):
    """1/0 box [0,0,10,10] um in TOP; CHILD with 100/0 box [5,5,15,15] placed at inst_disp;
    a 50/0 marker box [0,0,8,8] in TOP. dbu 0.001."""
    lay = pytest.importorskip("klayout.lay")
    import pya
    ly = pya.Layout()
    ly.dbu = 0.001
    top, child = ly.create_cell("TOP"), ly.create_cell("CHILD")
    l1, l100, l50 = ly.layer(1, 0), ly.layer(100, 0), ly.layer(50, 0)
    top.shapes(l1).insert(pya.Box(0, 0, 10000, 10000))
    child.shapes(l100).insert(pya.Box(5000, 5000, 15000, 15000))
    t = pya.Trans(pya.Vector(*inst_disp))
    cia = pya.CellInstArray(child.cell_index(), t) if array is None else \
        pya.CellInstArray(child.cell_index(), t, pya.Vector(*array[0]), pya.Vector(0, 0), array[1], 1)
    inst = top.insert(cia)
    marker = top.shapes(l50).insert(pya.Box(0, 0, 8000, 8000))

    def shape_obj(shape, li):
        o = lay.ObjectInstPath()
        o.top, o.layer, o.shape = top.cell_index(), li, shape
        return o

    def inst_obj():
        o = lay.ObjectInstPath()
        o.top = top.cell_index()
        o.append_path(pya.InstElement.new(inst))
        return o
    ns = {"pya": pya}
    path = REGION_EXAMPLE / "run.py"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    ctx = type("Ctx", (), {})()
    ctx.pya, ctx.layout, ctx.cell = pya, ly, top
    return ns["run"], ctx, shape_obj(marker, l50), inst_obj, pya


def _bbox_um(pya, ctx, ln, dt):
    li = ctx.layout.find_layer(ln, dt)
    b = pya.Region(ctx.cell.shapes(li)).bbox()
    return (b.left / 1000, b.bottom / 1000, b.right / 1000, b.top / 1000)


P = {"layer_a": "1/0", "layer_b": "100/0", "output": "3/0", "keep_output": True}


@pytest.mark.parametrize("op,area,bbox", [
    ("and", 9.0, (5, 5, 8, 8)), ("or", 55.0, (0, 0, 8, 8)),
    ("not", 55.0, (0, 0, 8, 8)), ("xor", 55.0, (0, 0, 8, 8))])
def test_region_example_shape_marker(op, area, bbox):
    run, ctx, marker, _inst, pya = _region_scene()
    ctx.selected = [marker]
    out = run(ctx, {**P, "op": op})
    # inside [0,8]^2: A = 64, B = [5,8]^2 = 9; or = 64, not = 55, xor = 55
    exp_area = {"and": 9.0, "or": 64.0, "not": 55.0, "xor": 55.0}[op]
    assert out == {"op": op, "clip_objects": 1, "result_polygons": 1,
                   "result_area_um2": exp_area, "output": "3/0"}
    assert _bbox_um(pya, ctx, 3, 0) == bbox


def test_region_example_instance_marker_uses_its_layers():
    run, ctx, _marker, inst_obj, pya = _region_scene(inst_disp=(2000, 0))
    ctx.selected = [inst_obj()]
    out = run(ctx, {**P, "op": "and"})
    # clip = the child's 100/0 box placed at +2 um: [7,5,17,15]; A & clip & B = [7,5,10,10]
    assert out["result_polygons"] == 1 and out["result_area_um2"] == 15.0
    assert _bbox_um(pya, ctx, 3, 0) == (7, 5, 10, 10)


def test_region_example_array_instance_places_every_member():
    run, ctx, _marker, inst_obj, pya = _region_scene(inst_disp=(-20000, 0), array=((20000, 0), 2))
    ctx.selected = [inst_obj()]
    out = run(ctx, {**P, "op": "and"})  # member 0 at -20 um (misses A), member 1 at 0 um
    assert out["result_area_um2"] == 25.0 and _bbox_um(pya, ctx, 3, 0) == (5, 5, 10, 10)


def test_region_example_keep_output_false_clears_inside_region():
    run, ctx, marker, _inst, pya = _region_scene()
    out_li = ctx.layout.layer(3, 0)
    ctx.cell.shapes(out_li).insert(pya.Box(-4000, 0, 4000, 2000))  # half inside the region
    ctx.cell.shapes(out_li).insert(pya.Box(20000, 0, 21000, 1000))  # far outside: untouched
    ctx.cell.shapes(out_li).insert(pya.Text("keep", pya.Trans(1000, 1000)))
    ctx.selected = [marker]
    run(ctx, {**P, "op": "and", "keep_output": False})
    reg = pya.Region(ctx.cell.shapes(out_li))
    assert reg.area() == 9_000_000 + 4000 * 2000 + 1_000_000
    texts = [s for s in ctx.cell.shapes(out_li).each() if s.is_text()]
    assert len(texts) == 1
    run2, ctx2, marker2, _i, _p = _region_scene()
    ctx2.cell.shapes(ctx2.layout.layer(3, 0)).insert(pya.Box(-4000, 0, 4000, 2000))
    ctx2.selected = [marker2]
    run2(ctx2, {**P, "op": "and", "keep_output": True})
    # kept as is: 16 um2 of the old box + 9 um2 of the result (no overlap)
    assert pya.Region(ctx2.cell.shapes(ctx2.layout.layer(3, 0))).area() == 16_000_000 + 9_000_000


def test_region_example_refuses_before_mutating():
    run, ctx, marker, _inst, pya = _region_scene()
    ctx.selected = [marker]
    with pytest.raises(ValueError, match=r"layer_b 101/0 does not exist"):
        run(ctx, {**P, "layer_b": "101/0", "op": "and"})
    assert ctx.layout.find_layer(3, 0) is None  # output layer not even created
    text = ctx.cell.shapes(ctx.layout.layer(50, 0)).insert(pya.Text("t", pya.Trans(0, 0)))
    tobj = type(marker)()
    tobj.top, tobj.layer, tobj.shape = ctx.cell.cell_index(), ctx.layout.layer(50, 0), text
    ctx.selected = [tobj]
    with pytest.raises(ValueError, match="defines no region"):
        run(ctx, {**P, "op": "and"})
    assert ctx.layout.find_layer(3, 0) is None


# ------------------------------------------- layer recognition (from / exclude / allow_new) --
class SimpleScene:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _layer_scene():
    """A real LayoutView: TOP has 1/0 [0,10]^2 (panel name "metal1"), a 7/0 box far away,
    a 6/0 text inside, the 50/0 marker [0,8]^2 and CHILD (100/0 [5,15]^2) placed at 0."""
    lay = pytest.importorskip("klayout.lay")
    import pya
    view = lay.LayoutView()
    cv = view.cellview(view.create_layout(True))
    ly = cv.layout()
    ly.dbu = 0.001
    top, child = ly.create_cell("TOP"), ly.create_cell("CHILD")
    cv.cell = top
    li = {ld: ly.layer(*ld) for ld in ((1, 0), (6, 0), (7, 0), (50, 0), (100, 0))}
    top.shapes(li[(1, 0)]).insert(pya.Box(0, 0, 10000, 10000))
    top.shapes(li[(7, 0)]).insert(pya.Box(100000, 100000, 110000, 110000))
    top.shapes(li[(6, 0)]).insert(pya.Text("lbl", pya.Trans(2000, 2000)))
    child.shapes(li[(100, 0)]).insert(pya.Box(5000, 5000, 15000, 15000))
    inst = top.insert(pya.CellInstArray(child.cell_index(), pya.Trans()))
    marker = top.shapes(li[(50, 0)]).insert(pya.Box(0, 0, 8000, 8000))
    view.add_missing_layers()
    it = view.begin_layers()
    while not it.at_end():
        if it.current().layer_index() == li[(1, 0)]:
            node = it.current().dup()
            node.name = "metal1"
            view.set_layer_properties(it, node)
        it.next()

    def shape_obj(shape, layer):
        o = lay.ObjectInstPath()
        o.top, o.layer, o.shape = top.cell_index(), layer, shape
        return o

    def inst_obj():
        o = lay.ObjectInstPath()
        o.top = top.cell_index()
        o.append_path(pya.InstElement.new(inst))
        return o
    return SimpleScene(view=view, layout=ly, cell=top, li=li, marker=shape_obj(marker, li[(50, 0)]),
                       inst=inst_obj, shape_obj=shape_obj, pya=pya)


def _ctx(scene, selected):
    return SimpleScene(pya=scene.pya, view=scene.view, layout=scene.layout, cell=scene.cell,
                       selected=selected)


def _brief(cands):
    return [(c["layer"], c["name"], c["shapes"]) for c in cands]


def test_layer_candidates_region_marker_layer_comes_last():
    sc = _layer_scene()
    ctx = _ctx(sc, [sc.marker])
    # 6/0 is a text (no area), 7/0 is outside; the marker's own 50/0 is offered, but LAST
    cands = tools.layer_candidates(ctx, "region")
    assert _brief(cands) == [("1/0", "metal1", 1), ("100/0", None, 1), ("50/0", None, 1)]
    assert [c["selected"] for c in cands] == [0, 0, 1]
    assert tools.layer_candidates(ctx, "region", skip_selection_layers=False) == cands
    assert tools.layer_candidates(ctx, "region", skip_selection_layers=None) == cands
    c = cands[0]
    assert c["index"] == sc.li[(1, 0)] and c["capped"] is False
    assert tools.candidate_label(cands[-1]) == "50/0  (1 shape) [selected]"
    # legacy opt-in: skip_selection_layers=True hides a layer that is only the selection, L/D order
    legacy = tools.layer_candidates(ctx, "region", skip_selection_layers=True)
    assert _brief(legacy) == [("1/0", "metal1", 1), ("100/0", None, 1)]
    # a second 50/0 shape inside the region: 50/0 counts both (still last); legacy counts 1
    shapes50 = sc.cell.shapes(sc.li[(50, 0)])
    shapes50.insert(sc.pya.Box(1000, 1000, 2000, 2000))
    marker = sc.shape_obj(next(s for s in shapes50.each() if s.box.right == 8000), sc.li[(50, 0)])
    assert _brief(tools.layer_candidates(_ctx(sc, [marker]), "region"))[-1] == ("50/0", None, 2)
    assert ("50/0", None, 1) in _brief(tools.layer_candidates(_ctx(sc, [marker]), "region", True))


def _operand_scene():
    """_layer_scene plus two operand boxes: 101/0 [2,6]^2 and 102/0 [4,8]^2 (over 1/0)."""
    sc = _layer_scene()
    a = sc.cell.shapes(sc.layout.layer(101, 0)).insert(sc.pya.Box(2000, 2000, 6000, 6000))
    b = sc.cell.shapes(sc.layout.layer(102, 0)).insert(sc.pya.Box(4000, 4000, 8000, 8000))
    sc.op_a = sc.shape_obj(a, sc.layout.layer(101, 0))
    sc.op_b = sc.shape_obj(b, sc.layout.layer(102, 0))
    return sc


def test_layer_candidates_region_operands_first_in_selection_order():
    sc = _operand_scene()
    # the user's case: both operands selected, no marker -> their layers first, nothing hidden
    cands = tools.layer_candidates(_ctx(sc, [sc.op_a, sc.op_b]), "region")
    assert [c["layer"] for c in cands] == ["101/0", "102/0", "1/0", "50/0", "100/0"]
    assert [c["selected"] for c in cands] == [1, 1, 0, 0, 0]
    # selection order decides (B clicked first -> 102/0 first)
    assert [c["layer"] for c in tools.layer_candidates(_ctx(sc, [sc.op_b, sc.op_a]), "region")][:2] \
        == ["102/0", "101/0"]
    # legacy opt-in hides both operand layers (only the selection lies there)
    assert [c["layer"] for c in tools.layer_candidates(_ctx(sc, [sc.op_a, sc.op_b]), "region", True)] \
        == ["1/0", "50/0", "100/0"]
    # selection / layout scopes stay in L/D order (with the selected counts)
    sel = tools.layer_candidates(_ctx(sc, [sc.op_b, sc.op_a]), "selection")
    assert [(c["layer"], c["selected"]) for c in sel] == [("101/0", 1), ("102/0", 1)]
    lay = tools.layer_candidates(_ctx(sc, [sc.op_b, sc.op_a]), "layout")
    assert [c["layer"] for c in lay] == ["1/0", "6/0", "7/0", "50/0", "100/0", "101/0", "102/0"]


def test_layer_candidates_region_only_the_selection_layer():
    sc = _layer_scene()
    lone = sc.shape_obj(sc.cell.shapes(sc.li[(50, 0)]).insert(sc.pya.Box(50000, 0, 52000, 2000)),
                        sc.li[(50, 0)])  # nothing else under it: its own layer is still offered
    cands = tools.layer_candidates(_ctx(sc, [lone]), "region")
    assert [(c["layer"], c["shapes"], c["selected"]) for c in cands] == [("50/0", 1, 1)]
    assert tools.layer_candidates(_ctx(sc, [lone]), "region", True) == []


def test_layer_candidates_region_of_an_instance_is_its_bbox():
    sc = _layer_scene()
    ctx = _ctx(sc, [sc.inst()])  # outline = CHILD bbox [5,15]^2: 1/0 and the marker overlap it
    assert _brief(tools.layer_candidates(ctx, "region")) == [
        ("1/0", "metal1", 1), ("50/0", None, 1), ("100/0", None, 1)]
    abut = sc.shape_obj(sc.cell.shapes(sc.li[(50, 0)]).insert(sc.pya.Box(10000, 0, 12000, 2000)),
                        sc.li[(50, 0)])
    # touching 1/0 is not overlapping: only the selected shape's own layer is under it
    assert _brief(tools.layer_candidates(_ctx(sc, [abut]), "region")) == [("50/0", None, 1)]
    text = sc.shape_obj(next(sc.cell.shapes(sc.li[(6, 0)]).each()), sc.li[(6, 0)])
    assert tools.layer_candidates(_ctx(sc, [text]), "region") == []  # a text marks no region


def test_layer_candidates_selection_and_layout():
    sc = _layer_scene()
    assert _brief(tools.layer_candidates(_ctx(sc, [sc.marker, sc.inst()]), "selection")) == [
        ("50/0", None, 1), ("100/0", None, 1)]
    assert _brief(tools.layer_candidates(_ctx(sc, []), "layout")) == [
        ("1/0", "metal1", 1), ("6/0", None, 1), ("7/0", None, 1), ("50/0", None, 1), ("100/0", None, 1)]
    with pytest.raises(ValueError, match="scope must be one of"):
        tools.layer_candidates(_ctx(sc, []), "screen")


def test_layer_candidates_count_is_capped(monkeypatch):
    assert tools.COUNT_CAP == 200 and tools._count.__defaults__ == (200,)  # a label, not a census
    sc = _layer_scene()
    for i in range(5):
        sc.cell.shapes(sc.li[(7, 0)]).insert(sc.pya.Box(i * 100, 0, i * 100 + 50, 50))
    monkeypatch.setattr(tools, "COUNT_CAP", 3)
    monkeypatch.setattr(tools._count, "__defaults__", (3,))
    c = next(c for c in tools.layer_candidates(_ctx(sc, []), "layout") if c["layer"] == "7/0")
    assert c["shapes"] == 3 and c["capped"] is True
    assert tools.candidate_label(c) == "7/0  (3+ shapes)"
    # region: the walk stops after COUNT_CAP shape VISITS of any type (texts too)
    for i in range(5):
        sc.cell.shapes(sc.li[(6, 0)]).insert(sc.pya.Text("t", sc.pya.Trans(1000 + i * 100, 1000)))
    reg = {c["layer"]: c for c in tools.layer_candidates(_ctx(sc, [sc.marker]), "region")}
    assert (reg["7/0"]["shapes"], reg["7/0"]["capped"]) == (3, True)  # 5 boxes under the marker
    assert (reg["6/0"]["shapes"], reg["6/0"]["capped"]) == (0, True)  # 6 texts: stopped, not hidden
    assert tools.candidate_label(reg["6/0"]) == "6/0  (0+ shapes)"
    assert tools.candidate_label({"layer": "1/0", "name": "metal1", "shapes": 1}) == "1/0  metal1  (1 shape)"


def test_selection_summary():
    sc = _layer_scene()
    extra = sc.shape_obj(next(sc.cell.shapes(sc.li[(1, 0)]).each()), sc.li[(1, 0)])
    assert tools.selection_summary(sc.layout, [sc.marker, extra, sc.inst()]) == \
        "Selected: 2 shapes (1/0 x1, 50/0 x1), 1 instance"
    assert tools.selection_summary(sc.layout, []) == "Selected: nothing"


class SceneView:
    """The real LayoutView of a scene with a chosen selection (each_object_selected)."""

    def __init__(self, scene, selected):
        self.scene, self.selected = scene, selected
        self.cv = scene.view.active_cellview()

    def active_cellview(self):
        return self.cv

    def active_cellview_index(self):
        return self.scene.view.active_cellview_index()

    def each_object_selected(self):
        return iter(self.selected)

    def begin_layers(self):
        return self.scene.view.begin_layers()

    def add_missing_layers(self):
        self.scene.view.add_missing_layers()

    def clear_object_selection(self):
        self.selected = []


@pytest.fixture
def scene_env(env, monkeypatch):
    def use(scene, selected, answers=()):
        fake = RunPya(SceneView(scene, selected), answers)
        fake.Region, fake.Polygon = scene.pya.Region, scene.pya.Polygon
        monkeypatch.setattr(tools, "_pya", lambda: fake)
        return fake
    return {**env, "use_scene": use}


def _install_example(root, src: Path, **spec_over):
    spec = json.loads((src / "tool.json").read_text(encoding="utf-8"))
    spec.update(spec_over)
    return _install(root, spec, (src / "run.py").read_text(encoding="utf-8"))


def test_bool_region_click_one_list_for_a_b_auto_list_for_output(scene_env):
    _install_example(scene_env["root"], REGION_EXAMPLE)
    sc = _layer_scene()
    mark = "50/0  (1 shape) [selected]"
    fake = scene_env["use_scene"](sc, [sc.marker], answers=[
        "1/0  metal1  (1 shape)", "100/0  (1 shape)", tools.NEW_LAYER, "3/0"])
    out = tools.run_tool("bool_region", preset="and", interactive=True)
    calls = fake.InputDialog.calls
    assert [c[0] for c in calls] == ["item", "item", "item", "string"]
    assert calls[0][1] == "Selected: 1 shape (50/0 x1)\n\nlayer A"
    # the marker's own layer is offered too, last
    assert calls[0][2:] == (["1/0  metal1  (1 shape)", "100/0  (1 shape)", mark], 0)
    assert calls[1][1] == "layer B" and calls[1][2] == ["100/0  (1 shape)", mark]
    layout_items = ["1/0  metal1  (1 shape)", "6/0  (1 shape)", "7/0  (1 shape)", mark,
                    "100/0  (1 shape)", tools.NEW_LAYER]
    assert calls[2][1] == "output layer" and calls[2][2:] == (layout_items, 5)  # 3/0 absent: "new layer…"
    assert calls[3][2] == "3/0"  # the default proposed in the text field
    assert out["status"] == "ok" and "auto" not in out
    assert out["params"] == {"layer_a": "1/0", "layer_b": "100/0", "output": "3/0", "op": "and",
                             "keep_output": True}
    assert out["result"]["result_area_um2"] == 9.0
    # the result on 3/0 is under the region now: it joins the list (L/D order, marker last),
    # and 3/0 exists: preselected in the output list
    fake = scene_env["use_scene"](sc, [sc.marker], answers=[
        "100/0  (1 shape)", "1/0  metal1  (1 shape)", "3/0  (1 shape)"])
    out = tools.run_tool("bool_region", preset="not", interactive=True)
    calls = fake.InputDialog.calls
    assert calls[0][2] == ["1/0  metal1  (1 shape)", "3/0  (1 shape)", "100/0  (1 shape)", mark]
    assert calls[1][1] == "layer B" and calls[1][2] == ["1/0  metal1  (1 shape)", "3/0  (1 shape)", mark]
    items, index = calls[2][2:]
    assert items[index] == "3/0  (1 shape)" and "auto" not in out
    assert out["params"]["layer_a"] == "100/0" and out["params"]["layer_b"] == "1/0"


def test_bool_region_noninteractive_lists_candidates_then_auto_fills(scene_env):
    _install_example(scene_env["root"], REGION_EXAMPLE)
    sc = _layer_scene()
    scene_env["use_scene"](sc, [sc.marker])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("bool_region", preset="and", params={"output": "3/0"})
    err = ei.value
    assert err.code == "ERR_TOOL_PARAMS" and err.message == "tool 'bool_region': pick a layer for layer_a, layer_b"
    assert err.hint.startswith("layer_a: one of 1/0, 100/0, 50/0; layer_b: one of 1/0, 100/0, 50/0. "
                               "Pass it as a param")
    assert err.data == {"candidates": {"layer_a": ["1/0", "100/0", "50/0"],
                                       "layer_b": ["1/0", "100/0", "50/0"]}}
    assert scene_env["txn"].events == []
    with pytest.raises(tools.ToolError) as ei:  # A given: B still has a choice (100/0 or the marker's)
        tools.run_tool("bool_region", preset="and", params={"output": "3/0", "layer_a": "1/0"})
    assert ei.value.data == {"candidates": {"layer_b": ["100/0", "50/0"]}}
    out = tools.run_tool("bool_region", preset="and", params={"output": "3/0", "layer_a": "1/0",
                                                               "layer_b": "100/0"})
    assert "auto" not in out and out["result"]["result_area_um2"] == 9.0


def test_bool_region_noninteractive_operands_selected(scene_env):
    _install_example(scene_env["root"], REGION_EXAMPLE)
    sc = _operand_scene()
    scene_env["use_scene"](sc, [sc.op_a, sc.op_b])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("bool_region", preset="and", params={"output": "3/0"})
    cands = ei.value.data["candidates"]
    assert cands["layer_a"][:2] == cands["layer_b"][:2] == ["101/0", "102/0"]
    out = tools.run_tool("bool_region", preset="and",
                         params={"output": "3/0", "layer_a": "101/0", "layer_b": "102/0"})
    assert out["status"] == "ok" and out["result"]["result_area_um2"] == 4.0  # [4,6]^2


def _install_region_legacy(root):
    """bool_region with the legacy ``skip_selection_layers: true`` on A and B."""
    spec = json.loads((REGION_EXAMPLE / "tool.json").read_text(encoding="utf-8"))
    for n in ("layer_a", "layer_b"):
        spec["params"][n]["skip_selection_layers"] = True
    return _install(root, spec, (REGION_EXAMPLE / "run.py").read_text(encoding="utf-8"))


def test_bool_region_one_layer_under_region_falls_back_to_layout(scene_env):
    # legacy skip_selection_layers=true: the marker's layer is hidden, so B falls back
    _install_region_legacy(scene_env["root"])
    sc = _layer_scene()
    small = sc.shape_obj(sc.cell.shapes(sc.li[(50, 0)]).insert(sc.pya.Box(8500, 1000, 9500, 3000)),
                         sc.li[(50, 0)])  # only 1/0 under it
    scene_env["use_scene"](sc, [small])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("bool_region", preset="and", params={"output": "3/0"})
    assert ei.value.data == {"candidates": {"layer_b": ["6/0", "7/0", "50/0", "100/0"]}}
    assert "layer_b: one of 6/0, 7/0, 50/0, 100/0 (nothing is under the selected region; these are " \
           "the layout's layers)" in ei.value.hint
    assert _journal(scene_env["root"])[-1]["auto"] == {"layer_a": "1/0"}
    fake = scene_env["use_scene"](sc, [small], answers=["100/0  (1 shape)", "50/0  (2 shapes) [selected]"])
    out = tools.run_tool("bool_region", preset="or", interactive=True)
    calls = fake.InputDialog.calls
    assert calls[0][1] == ("Selected: 1 shape (50/0 x1)\n\nlayer B\n"
                           "(nothing is under the selected region; all layers of the layout)")
    assert "1/0  metal1  (1 shape)" not in calls[0][2]  # excluded: A took it
    assert out["auto"] == {"layer_a": "1/0"} and out["params"]["output"] == "50/0"


def _layer_spec(**layer_param):
    return {"id": "lay", "title": "Layers", "params": {
        "a": {"type": "layer", "from": "region"},
        "b": {"type": "layer", "from": "region", "exclude": ["a"], **layer_param}}}


def test_no_candidates_anywhere_is_input_error_or_new_layer_text(scene_env):
    lay = pytest.importorskip("klayout.lay")
    import pya
    view = lay.LayoutView()
    cv = view.cellview(view.create_layout(True))
    top = cv.layout().create_cell("TOP")
    cv.cell = top
    li = cv.layout().layer(1, 0)
    mk = top.shapes(li).insert(pya.Box(0, 0, 10, 10))
    top.shapes(li).insert(pya.Box(2, 2, 4, 4))
    o = lay.ObjectInstPath()
    o.top, o.layer, o.shape = top.cell_index(), li, mk
    sc = SimpleScene(view=view, pya=pya)
    _install(scene_env["root"], _layer_spec(), "def run(ctx, params):\n    return dict(params)\n")
    fake = scene_env["use_scene"](sc, [o])
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("lay", interactive=True)
    assert ei.value.code == "ERR_TOOL_INPUT"
    assert ei.value.message == ("no layer to offer for 'b': nothing is under the selected region "
                                "and the layout has no other layer")
    assert [s[0] for s in fake.MessageBox.shown] == ["info"] and fake.InputDialog.calls == []
    _install(scene_env["root"], _layer_spec(allow_new=True, default="9/0"),
             "def run(ctx, params):\n    return dict(params)\n")
    fake = scene_env["use_scene"](sc, [o], answers=["9 1"])
    out = tools.run_tool("lay", interactive=True)  # a auto (1/0), b: straight to the text field
    assert [c[0] for c in fake.InputDialog.calls] == ["string"]
    assert fake.InputDialog.calls[0][1].startswith("Selected: 1 shape (1/0 x1)\n\nb\n")
    assert fake.InputDialog.calls[0][2] == "9/0"
    assert out["result"] == {"a": "1/0", "b": "9/1"} and out["auto"] == {"a": "1/0"}
    # non-interactive: b has its default 9/0, so nothing to pick
    assert tools.run_tool("lay")["result"] == {"a": "1/0", "b": "9/0"}


def test_new_layer_text_reasks_invalid_and_cancel(scene_env):
    sc = _layer_scene()
    spec = {"id": "lay", "title": "Layers", "params": {
        "out": {"type": "layer", "from": "layout", "allow_new": True}}}
    _install(scene_env["root"], spec, "def run(ctx, params):\n    return dict(params)\n")
    fake = scene_env["use_scene"](sc, [sc.marker], answers=[tools.NEW_LAYER, "x/y", "12"])
    assert tools.run_tool("lay", interactive=True)["result"] == {"out": "12/0"}
    calls = fake.InputDialog.calls
    assert calls[0][3] == 0  # no default: first entry preselected
    assert calls[1][2] == "" and calls[2][1].startswith("expected a layer as L/D") and calls[2][2] == "x/y"
    scene_env["use_scene"](sc, [sc.marker], answers=[None])
    assert tools.run_tool("lay", interactive=True)["status"] == "cancelled"
    with pytest.raises(tools.ToolError) as ei:  # non-interactive: several layers + "any new"
        tools.run_tool("lay")
    assert ei.value.code == "ERR_TOOL_PARAMS" and "or any new L/D" in ei.value.hint


def test_bool_selection_target_list_same_first(scene_env):
    _install_example(scene_env["root"], EXAMPLE)
    sc = _layer_scene()
    b = sc.shape_obj(sc.cell.shapes(sc.li[(1, 0)]).insert(sc.pya.Box(5000, 5000, 15000, 15000)),
                     sc.li[(1, 0)])
    a = sc.shape_obj(next(s for s in sc.cell.shapes(sc.li[(1, 0)]).each() if s.box.left == 0), sc.li[(1, 0)])
    fake = scene_env["use_scene"](sc, [a, b], answers=[tools.SAME_LABEL, "yes"])
    out = tools.run_tool("bool_selection", preset="union", interactive=True)
    first = fake.InputDialog.calls[0]
    assert first[1] == "Selected: 2 shapes (1/0 x2)\n\ntarget layer"
    assert first[2][0] == tools.SAME_LABEL and first[2][-1] == tools.NEW_LAYER and first[3] == 0
    assert "1/0  metal1  (2 shapes) [selected]" in first[2]
    assert fake.InputDialog.calls[1][1] == "keep source shapes"  # the summary only on the first dialog
    assert out["params"]["target"] == "same" and out["result"]["target_layer"] == "1/0"
    assert "auto" not in out


def test_run_py_sees_ctx_layers(scene_env):
    src = ("def run(ctx, params):\n"
           "    out = {s: [c['layer'] for c in ctx.layers(s)] for s in ('region', 'selection')}\n"
           "    out['default'] = [c['layer'] for c in ctx.layers()]\n"
           "    out['raw'] = [c['layer'] for c in ctx.layers('region', skip_selection_layers=False)]\n"
           "    out['legacy'] = [c['layer'] for c in ctx.layers('region', skip_selection_layers=True)]\n"
           "    return out\n")
    _install(scene_env["root"], {"id": "lay", "title": "Layers"}, src)
    sc = _layer_scene()
    scene_env["use_scene"](sc, [sc.marker])
    assert tools.run_tool("lay")["result"] == {
        "region": ["1/0", "100/0", "50/0"], "selection": ["50/0"], "default": ["1/0", "100/0", "50/0"],
        "raw": ["1/0", "100/0", "50/0"], "legacy": ["1/0", "100/0"]}


# ------------------------------------------------------------------ one form --
def _region_tool(root, src=REGION_EXAMPLE, preset="and", given=None):
    _install_example(root, src)
    t = tools.load_tool(src.name, root)
    return t, tools.resolve_params(t, preset, given, interactive=True)[0]


def _labels(f):
    return [lb for _, lb in f["options"]]


def test_form_fields_bool_region_all_params_a_b_differ_output_new(tmp_path):
    t, values = _region_tool(tmp_path)
    sc = _layer_scene()
    fields = tools.form_fields(t, values, tools.LayerLists(_ctx(sc, [sc.marker])))
    assert [f["name"] for f in fields] == ["layer_a", "layer_b", "output", "op", "keep_output"]
    assert [f["kind"] for f in fields] == ["layer_list", "layer_list", "layer_list", "enum", "bool"]
    a, b, out, op, keep = fields
    assert a["label"] == "layer A" and b["label"] == "layer B"
    # both list every layer under the region (exclude is checked on OK), the marker's own layer
    # last; preselects differ: A = 1/0, B = 100/0
    assert _labels(a) == _labels(b) == ["1/0  metal1  (1 shape)", "100/0  (1 shape)",
                                        "50/0  (1 shape) [selected]"]
    assert (a["index"], b["index"]) == (0, 1) and a["new_index"] is None and a["note"] == ""
    assert _labels(out) == ["1/0  metal1  (1 shape)", "6/0  (1 shape)", "7/0  (1 shape)",
                            "50/0  (1 shape) [selected]", "100/0  (1 shape)", tools.NEW_LAYER]
    assert out["index"] == out["new_index"] == 5 and out["new_text"] == "3/0"  # 3/0 not in the layout
    assert op["options"][op["index"]] == ("and", "and") and keep["value"] is True
    values2, err = tools.form_collect(t, fields, {})  # untouched form = the preselection
    assert err is None and values2 == {"layer_a": "1/0", "layer_b": "100/0", "output": "3/0",
                                       "op": "and", "keep_output": True}
    # a given value is preselected; B then takes the first other layer
    fields = tools.form_fields(t, {**values, "layer_a": "100/0", "output": "7/0"},
                               tools.LayerLists(_ctx(sc, [sc.marker])))
    assert (fields[0]["index"], fields[1]["index"]) == (1, 0)
    assert fields[2]["options"][fields[2]["index"]][0] == "7/0" and fields[2]["new_text"] == ""


def test_form_fields_bool_region_operands_preselected(tmp_path):
    t, values = _region_tool(tmp_path)
    sc = _operand_scene()
    a, b = tools.form_fields(t, values, tools.LayerLists(_ctx(sc, [sc.op_a, sc.op_b])))[:2]
    assert [v for v, _ in a["options"]] == [v for v, _ in b["options"]] == [
        "101/0", "102/0", "1/0", "50/0", "100/0"]
    assert _labels(a)[:2] == ["101/0  (1 shape) [selected]", "102/0  (1 shape) [selected]"]
    assert a["options"][a["index"]][0] == "101/0" and b["options"][b["index"]][0] == "102/0"
    # legacy skip_selection_layers=true: operands hidden, A/B open on the layers under them
    _install_region_legacy(tmp_path)
    lt = tools.load_tool("bool_region", tmp_path)
    a, b = tools.form_fields(lt, values, tools.LayerLists(_ctx(sc, [sc.op_a, sc.op_b])))[:2]
    assert [v for v, _ in a["options"]] == ["1/0", "50/0", "100/0"]
    assert (a["options"][a["index"]][0], b["options"][b["index"]][0]) == ("1/0", "50/0")


def test_form_fields_same_first_and_kinds(tmp_path):
    t, values = _region_tool(tmp_path, EXAMPLE, preset="union")
    sc = _layer_scene()
    f = next(f for f in tools.form_fields(t, values, tools.LayerLists(_ctx(sc, [sc.marker])))
             if f["name"] == "target")
    assert f["options"][0] == ("same", tools.SAME_LABEL) and f["index"] == 0
    assert f["options"][-1] == (None, tools.NEW_LAYER) and f["new_index"] == len(f["options"]) - 1
    t = _tool(tmp_path)
    fields = tools.form_fields(t, tools.resolve_params(t, "first", interactive=True)[0], None)
    assert [(f["name"], f["kind"]) for f in fields] == [
        ("op", "enum"), ("count", "int"), ("width", "number"), ("name", "string"), ("keep", "bool"),
        ("target", "layer")]
    count, width = fields[1], fields[2]
    assert (count["value"], count["min"], count["max"]) == (2, 1, 10)
    assert (width["value"], width["min"], width["max"]) == (0.5, 0.0, 1e15)
    assert fields[0]["index"] == 1 and fields[3]["value"] == "x" and fields[4]["value"] is False
    assert fields[5]["value"] == "same"


def test_form_fields_fallback_note_and_no_layer_error(tmp_path):
    t, values = _region_tool(tmp_path)
    sc = _layer_scene()
    small = sc.shape_obj(sc.cell.shapes(sc.li[(50, 0)]).insert(sc.pya.Box(8500, 1000, 9500, 3000)),
                         sc.li[(50, 0)])  # only 1/0 under it (and the marker itself)
    a, b = tools.form_fields(t, values, tools.LayerLists(_ctx(sc, [small])))[:2]
    assert _labels(a) == ["1/0  metal1  (1 shape)", "50/0  (1 shape) [selected]"] and b["note"] == ""
    assert b["options"][b["index"]][0] == "50/0"  # A took 1/0: B opens on the marker's layer
    # legacy skip_selection_layers=true: the marker's layer is hidden, B falls back to the layout
    _install_region_legacy(tmp_path)
    t = tools.load_tool("bool_region", tmp_path)
    sc = _layer_scene()
    small = sc.shape_obj(sc.cell.shapes(sc.li[(50, 0)]).insert(sc.pya.Box(8500, 1000, 9500, 3000)),
                         sc.li[(50, 0)])  # only 1/0 under it
    a, b = tools.form_fields(t, values, tools.LayerLists(_ctx(sc, [small])))[:2]
    assert _labels(a) == ["1/0  metal1  (1 shape)"] and a["note"] == ""
    assert b["note"] == "nothing is under the selected region; all layers of the layout"
    assert b["options"][0][0] == "1/0" and b["options"][b["index"]][0] == "6/0"  # A took 1/0
    # nothing to offer anywhere (and no allow_new): the error comes before any form
    lay = pytest.importorskip("klayout.lay")
    import pya
    view = lay.LayoutView()
    cv = view.cellview(view.create_layout(True))
    top = cv.layout().create_cell("TOP")
    cv.cell = top
    li = cv.layout().layer(1, 0)
    mk = top.shapes(li).insert(pya.Box(0, 0, 10, 10))
    top.shapes(li).insert(pya.Box(2, 2, 4, 4))
    o = lay.ObjectInstPath()
    o.top, o.layer, o.shape = top.cell_index(), li, mk
    ctx = SimpleScene(pya=pya, view=view, layout=cv.layout(), cell=top, selected=[o])
    _install(tmp_path, _layer_spec())
    lt = tools.load_tool("lay", tmp_path)
    with pytest.raises(tools.ToolError) as ei:
        tools.form_fields(lt, {}, tools.LayerLists(ctx))
    assert ei.value.code == "ERR_TOOL_INPUT" and "no layer to offer for 'b'" in ei.value.message
    _install(tmp_path, _layer_spec(allow_new=True, default="9/0"))
    lt = tools.load_tool("lay", tmp_path)
    a, b = tools.form_fields(lt, {"b": "9/0"}, tools.LayerLists(ctx))
    assert b["options"][b["index"]] == (None, tools.NEW_LAYER) and b["new_text"] == "9/0"


def test_form_collect_values_and_errors(tmp_path):
    t, values = _region_tool(tmp_path)
    sc = _layer_scene()
    fields = tools.form_fields(t, values, tools.LayerLists(_ctx(sc, [sc.marker])))
    picks = {"layer_a": ("option", 1), "layer_b": ("option", 0), "output": ("new", " 4 1 "),
             "op": ("option", 2), "keep_output": ("value", False)}
    assert tools.form_collect(t, fields, picks) == (
        {"layer_a": "100/0", "layer_b": "1/0", "output": "4/1", "op": "not", "keep_output": False}, None)
    _, err = tools.form_collect(t, fields, {**picks, "layer_b": ("option", 1)})
    assert err == "layer B must differ from layer A"
    _, err = tools.form_collect(t, fields, {**picks, "output": ("new", "x/y")})
    assert err == "output layer: new layer… expected a layer as L/D (e.g. 1/0 or just 1)"
    _, err = tools.form_collect(t, fields, {**picks, "output": ("option", fields[2]["new_index"])})
    assert err.startswith("output layer: new layer… expected a layer")  # combo on "new layer…", no text
    _, err = tools.form_collect(t, fields, {**picks, "output": ("option", 4)})
    assert err is None  # an existing layer (100/0) as output is fine
    d = _tool(tmp_path)
    dfields = tools.form_fields(d, tools.resolve_params(d, "first", interactive=True)[0], None)
    vals, err = tools.form_collect(d, dfields, {"count": ("value", 11), "name": ("value", 3),
                                                "target": ("value", "1-0"), "width": ("value", "0.25")})
    assert err.splitlines() == ["count: must be <= 10", "name: expected a string",
                                "target: expected a layer as L/D (e.g. 1/0 or just 1) or 'same'"]
    assert vals == {"op": "and", "width": 0.25, "keep": False}


class FormPya(RunPya):
    QDialog = object  # marks a Qt build: run_tool uses ONE form (ask_form, monkeypatched)


def test_run_without_qdialog_keeps_sequential_dialogs(scene_env):
    _install_example(scene_env["root"], REGION_EXAMPLE)
    sc = _layer_scene()
    fake = scene_env["use_scene"](sc, [sc.marker], answers=[
        "1/0  metal1  (1 shape)", "100/0  (1 shape)", tools.NEW_LAYER, "3/0"])
    assert not hasattr(fake, "QDialog")
    out = tools.run_tool("bool_region", preset="and", interactive=True)
    assert [c[0] for c in fake.InputDialog.calls] == ["item", "item", "item", "string"]
    assert out["status"] == "ok" and "auto" not in out
    assert (out["params"]["layer_a"], out["params"]["layer_b"]) == ("1/0", "100/0")


def test_run_with_qdialog_uses_one_form(scene_env, monkeypatch):
    _install_example(scene_env["root"], REGION_EXAMPLE)
    sc = _layer_scene()
    seen = []

    def use(picks):
        fake = FormPya(SceneView(sc, [sc.marker]))
        fake.Region, fake.Polygon = sc.pya.Region, sc.pya.Polygon
        monkeypatch.setattr(tools, "_pya", lambda: fake)

        def ask_form(pya, tool, fields, summary=""):
            seen.append((pya, [f["name"] for f in fields], summary))
            return picks
        monkeypatch.setattr(tools, "ask_form", ask_form)
        return fake

    fake = use({"layer_a": ("option", 0), "layer_b": ("option", 1), "output": ("new", "3/0"),
                "op": ("option", 0), "keep_output": ("value", True)})
    out = tools.run_tool("bool_region", preset="and", interactive=True)
    assert seen[-1] == (fake, ["layer_a", "layer_b", "output", "op", "keep_output"],
                        "Selected: 1 shape (50/0 x1)")
    assert fake.InputDialog.calls == []
    assert out["status"] == "ok" and "auto" not in out and "auto" not in _journal(scene_env["root"])[-1]
    assert out["params"] == {"layer_a": "1/0", "layer_b": "100/0", "output": "3/0", "op": "and",
                             "keep_output": True}
    assert out["result"]["result_area_um2"] == 9.0
    # the form may change a preset-pinned param too (every param is in the form)
    use({"op": ("option", 1)})
    out = tools.run_tool("bool_region", preset="and", interactive=True)
    # (the first run's result on 3/0 is under the region now: B preselects the next layer, 3/0)
    assert out["params"]["op"] == "or" and out["params"]["layer_b"] == "3/0"
    use(None)
    assert tools.run_tool("bool_region", preset="and", interactive=True)["status"] == "cancelled"
    use({"layer_b": ("option", 0)})  # A == B slipped past the dialog: refused before any edit
    n = len(scene_env["txn"].events)
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("bool_region", preset="and", interactive=True)
    assert ei.value.code == "ERR_TOOL_PARAMS" and "layer B must differ from layer A" in ei.value.message
    assert len(scene_env["txn"].events) == n
    # non-interactive never opens the form
    use({})
    with pytest.raises(tools.ToolError) as ei:
        tools.run_tool("bool_region", preset="and", params={"output": "3/0"})
    assert ei.value.code == "ERR_TOOL_PARAMS" and len(seen) == 4
