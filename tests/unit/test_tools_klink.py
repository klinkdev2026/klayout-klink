"""klink package side of deterministic tools: validation parity, install/uninstall/export, CLI."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from klink import buttons, tools
from klink.cli import main as cli_main

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
EXAMPLE = Path(__file__).resolve().parents[2] / "examples_klink" / "public" / "features" / "tools" / "bool_selection"


@pytest.fixture
def plugin_tools():
    pytest.importorskip("klayout.db", reason="klayout pip package not installed")
    if str(PLUGIN_PYTHON) not in sys.path:
        sys.path.insert(0, str(PLUGIN_PYTHON))
    from klink_server import tools as plugin

    return plugin


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    root = tmp_path / "reg"
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(root))
    return root


def _spec():
    return json.loads((EXAMPLE / "tool.json").read_text(encoding="utf-8"))


def _region_spec():
    return json.loads((EXAMPLE.parent / "bool_region" / "tool.json").read_text(encoding="utf-8"))


def _macro_spec():
    return json.loads((EXAMPLE.parent / "bool_region_macro" / "tool.json").read_text(encoding="utf-8"))


def _variants():
    def mut(fn):
        s = _spec()
        fn(s)
        return s
    return [
        (_spec(), "bool_selection"), (_spec(), "other"), ([], "x"), ({}, "x"),
        (mut(lambda s: s.update(id="Bad")), "Bad"),
        (mut(lambda s: s.update(title="")), "bool_selection"),
        (mut(lambda s: s.update(label=3)), "bool_selection"),
        (mut(lambda s: s.update(description=1)), "bool_selection"),
        (mut(lambda s: s.update(inputs=[])), "bool_selection"),
        (mut(lambda s: s.update(inputs={"cells": {}})), "bool_selection"),
        (mut(lambda s: s.update(inputs={"selection": {"min_shapes": True}})), "bool_selection"),
        (mut(lambda s: s.update(inputs={"selection": {"min": -1}})), "bool_selection"),
        (mut(lambda s: s.update(inputs={"selection": {"min": 1, "min_shapes": 1}})), "bool_selection"),
        (mut(lambda s: s["params"]["target"].pop("allow_same")), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(allow_same=1)), "bool_selection"),
        (mut(lambda s: s["params"]["keep"].update(allow_same=True)), "bool_selection"),
        (_region_spec(), "bool_region"),
        (_macro_spec(), "bool_region_macro"), ({"id": "m", "title": "M"}, "m"),
        (mut(lambda s: s["params"]["target"].update(default="5")), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(default=[5, 1])), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(default=[5, True])), "bool_selection"),
        (mut(lambda s: s.update(params=[])), "bool_selection"),
        (mut(lambda s: s["params"].update(Bad={"type": "int"})), "bool_selection"),
        (mut(lambda s: s["params"].update(n=1)), "bool_selection"),
        (mut(lambda s: s["params"]["op"].update(type="x")), "bool_selection"),
        (mut(lambda s: s["params"]["op"].update(choices=["a", "a"])), "bool_selection"),
        (mut(lambda s: s["params"].update(n={"type": "int", "min": "0"})), "bool_selection"),
        (mut(lambda s: s["params"].update(n={"type": "int", "unit": 1})), "bool_selection"),
        (mut(lambda s: s["params"].update(n={"type": "number", "default": "x"})), "bool_selection"),
        (mut(lambda s: s["params"]["keep"].update(default="maybe")), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(default="1/a")), "bool_selection"),
        (mut(lambda s: s.update(presets=[])), "bool_selection"),
        (mut(lambda s: s["presets"].update(Bad={"params": {}})), "bool_selection"),
        (mut(lambda s: s["presets"].update(p=[])), "bool_selection"),
        (mut(lambda s: s["presets"].update(p={"title": 1, "params": {}})), "bool_selection"),
        (mut(lambda s: s["presets"].update(p={"params": {"zz": 1}})), "bool_selection"),
        (mut(lambda s: s["presets"].update(p={"params": {"op": "nand"}})), "bool_selection"),
        (mut(lambda s: s.update(prompt="x")), "bool_selection"),
        # layer recognition keys (from / exclude / allow_new / skip_selection_layers)
        (mut(lambda s: s["params"]["target"].update({"from": "screen"})), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update({"from": "region"})), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update({"from": "selection", "allow_new": False})), "bool_selection"),
        (mut(lambda s: s["params"]["keep"].update({"from": "layout"})), "bool_selection"),
        (mut(lambda s: s["params"]["keep"].update(allow_new=True)), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(allow_new="yes")), "bool_selection"),
        (mut(lambda s: s["params"]["target"].pop("from")), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(skip_selection_layers=1)), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(skip_selection_layers=False)), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(exclude=["keep"])), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(exclude=["target"])), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(exclude=["zz"])), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(exclude="op")), "bool_selection"),
        (mut(lambda s: s["params"]["target"].update(exclude=[1])), "bool_selection"),
        (mut(lambda s: s["params"].update(o2={"type": "layer", "from": "region", "exclude": ["target"]})),
         "bool_selection"),
        (mut(lambda s: s["params"].update(o2={"type": "layer", "exclude": ["target"]})), "bool_selection"),
        (_region_spec(), "bool_region"),
        # icon: a .png/.svg name inside the tool folder (existence: check_dir / check)
        (mut(lambda s: s.update(icon="icon.png")), "bool_selection"),
        (mut(lambda s: s.update(icon="img/Icon.SVG")), "bool_selection"),
        (mut(lambda s: s.update(icon="icon.gif")), "bool_selection"),
        (mut(lambda s: s.update(icon="../icon.png")), "bool_selection"),
        (mut(lambda s: s.update(icon=str(Path(__file__).resolve().with_name("icon.png")))), "bool_selection"),
        (mut(lambda s: s.update(icon="/x/icon.png")), "bool_selection"),
        (mut(lambda s: s.update(icon="")), "bool_selection"),
        (mut(lambda s: s.update(icon=7)), "bool_selection"),
    ]


@pytest.mark.parametrize("change,needle", [
    (lambda p: p["target"].update({"from": "screen"}), "params.target.from must be one of region, selection, layout"),
    (lambda p: p["keep"].update({"from": "layout"}), "params.keep.from must be one of"),
    (lambda p: p["keep"].update(allow_new=True), "params.keep.allow_new must be true/false, on a layer param"),
    (lambda p: p["target"].pop("from"), "params.target.allow_new needs 'from'"),
    (lambda p: p["target"].update(exclude=["keep"]), "params.target.exclude: 'keep' is not another layer param"),
    (lambda p: p["target"].update(exclude=["nope"]), "params.target.exclude: 'nope' is not another layer param"),
    (lambda p: p["target"].update(exclude="op"), "params.target.exclude must be a list of layer param names"),
])
def test_validator_rejects_bad_layer_recognition_keys(change, needle):
    spec = _spec()
    change(spec["params"])
    problem = tools.validate_tool(spec, "bool_selection")
    assert problem is not None and needle in problem, problem


def test_validation_parity(plugin_tools):
    for spec, dirname in _variants():
        assert tools.validate_tool(spec, dirname) == plugin_tools.validate_tool(spec, dirname), spec
    assert tools.validate_tool(_spec(), "bool_selection") is None
    assert tools.validate_tool(_region_spec(), "bool_region") is None
    assert tools.validate_tool(_macro_spec(), "bool_region_macro") is None
    assert tools.LAYER_RE.pattern == plugin_tools.LAYER_RE.pattern
    assert tools.ID_RE.pattern == plugin_tools.ID_RE.pattern
    assert tools.ICON_EXTS == plugin_tools.ICON_EXTS and tools.ICON_REASON == plugin_tools.ICON_REASON
    pdefs = [{"type": kind, "choices": ["a"]} for kind in tools.TYPES]
    pdefs += [{"type": "layer", "allow_same": True}, {"type": "layer", "allow_same": False}]
    for pdef in pdefs:
        kind = pdef["type"]
        for value in ("1", 1, 1.5, True, "yes", "a", "same", " SAME ", "1/0", None, " 7 ", "1,0",
                      "1 0", "1:0", " 100 / 2 ", "1/2/3", "1-0", "", "-1", -1, [1, 0], [1, True],
                      [1], (1, 0)):
            def outcome(mod):
                try:
                    return ("ok", mod.coerce(pdef, value))
                except ValueError as exc:
                    return ("err", str(exc))
            assert outcome(tools) == outcome(plugin_tools), (kind, value)


def test_install_from_dir_writes_button(registry):
    rep = tools.install(EXAMPLE, reload=False)
    dest = registry / "tools" / "bool_selection"
    assert rep["id"] == "bool_selection" and Path(rep["dir"]) == dest
    assert sorted(p.name for p in dest.iterdir()) == ["README.md", "run.py", "tool.json"]
    assert rep["items"] == ["union", "intersect", "subtract", "xor"]
    manifest = json.loads((registry / "buttons" / "bool_selection.json").read_text(encoding="utf-8"))
    assert manifest == {
        "id": "bool_selection", "label": "BOOL", "title": "Boolean on selection",
        "definition": "tool:bool_selection",
        "items": [{"id": "union", "title": "Union"}, {"id": "intersect", "title": "Intersection"},
                  {"id": "subtract", "title": "Subtract (first minus rest)"},
                  {"id": "xor", "title": "XOR"}]}
    assert buttons.validate(manifest, "bool_selection") is None
    assert "no-reload" in rep["reload"]
    listed = tools.list_installed()
    assert [(e["id"], e["problem"], e["presets"][0]) for e in listed] == [("bool_selection", None, "union")]


def test_install_region_example(registry):
    rep = tools.install(EXAMPLE.parent / "bool_region", reload=False)
    assert rep["items"] == ["and", "or", "not", "xor"]
    manifest = json.loads((registry / "buttons" / "bool_region.json").read_text(encoding="utf-8"))
    assert manifest["label"] == "BOOL2" and manifest["definition"] == "tool:bool_region"
    assert buttons.validate(manifest, "bool_region") is None
    assert [e["problem"] for e in tools.list_installed()] == [None]


@pytest.mark.parametrize("value,want", [
    ("1/0", "1/0"), ("1", "1/0"), ("1,0", "1/0"), ("1 0", "1/0"), ("1:0", "1/0"), (" 3 / 4 ", "3/4"),
    (1, "1/0"), ([1, 0], "1/0"), ([100, 2], "100/2")])
def test_layer_lenient_forms(value, want):
    assert tools.coerce({"type": "layer"}, value) == want


def test_layer_bad_message():
    with pytest.raises(ValueError) as ei:
        tools.coerce({"type": "layer"}, "1-0")
    assert str(ei.value) == "expected a layer as L/D (e.g. 1/0 or just 1)"


def test_install_macro_example(registry):
    rep = tools.install(EXAMPLE.parent / "bool_region_macro", reload=False)
    assert rep["id"] == "bool_region_macro" and rep["items"] == []
    manifest = json.loads((registry / "buttons" / "bool_region_macro.json").read_text(encoding="utf-8"))
    assert manifest["label"] == "MACRO" and manifest["definition"] == "tool:bool_region_macro"
    assert buttons.validate(manifest, "bool_region_macro") is None


def _copy_example(tmp_path, name="bool_selection"):
    dst = tmp_path / "src" / name
    shutil.copytree(EXAMPLE, dst)
    return dst


def test_install_replaces_existing_atomically(registry, tmp_path):
    tools.install(EXAMPLE, reload=False)
    stale = registry / "tools" / "bool_selection" / "stale.txt"
    stale.write_text("old", encoding="utf-8")
    src = _copy_example(tmp_path)
    spec = json.loads((src / "tool.json").read_text(encoding="utf-8"))
    spec.pop("presets")
    spec.pop("label")
    (src / "tool.json").write_text(json.dumps(spec), encoding="utf-8")
    rep = tools.install(src, reload=False)
    assert rep["items"] == [] and not stale.exists()
    assert sorted(p.name for p in (registry / "tools").iterdir()) == ["bool_selection"]  # no temp left
    manifest = json.loads((registry / "buttons" / "bool_selection.json").read_text(encoding="utf-8"))
    assert "items" not in manifest and manifest["label"] == "BOOL_S"


def test_install_invalid_leaves_existing(registry, tmp_path):
    tools.install(EXAMPLE, reload=False)
    src = _copy_example(tmp_path)
    (src / "run.py").unlink()
    with pytest.raises(ValueError, match="missing run.py"):
        tools.install(src, reload=False)
    assert (registry / "tools" / "bool_selection" / "run.py").is_file()


def test_install_dir_name_must_match_id(tmp_path):
    src = _copy_example(tmp_path, name="renamed")
    with pytest.raises(ValueError, match="does not match the directory name 'renamed'"):
        tools.install(src, reload=False)


def test_install_from_zip_both_layouts(registry, tmp_path):
    flat = tmp_path / "flat.zip"
    with zipfile.ZipFile(flat, "w") as zf:
        for f in EXAMPLE.iterdir():
            zf.write(f, f.name)
    assert tools.install(flat, reload=False)["id"] == "bool_selection"
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested, "w") as zf:
        for f in EXAMPLE.iterdir():
            zf.write(f, f"bool_selection/{f.name}")
    assert tools.install(nested, reload=False)["id"] == "bool_selection"
    wrong = tmp_path / "wrong.zip"
    with zipfile.ZipFile(wrong, "w") as zf:
        for f in EXAMPLE.iterdir():
            zf.write(f, f"other_name/{f.name}")
    with pytest.raises(ValueError, match="directory name 'other_name'"):
        tools.install(wrong, reload=False)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "x")
        zf.writestr("tool.json", "{}")
    with pytest.raises(ValueError, match="escapes"):
        tools.install(evil, reload=False)


def test_uninstall_removes_both(registry):
    tools.install(EXAMPLE, reload=False)
    assert tools.uninstall("bool_selection", reload=False) is True
    assert not (registry / "tools" / "bool_selection").exists()
    assert not (registry / "buttons" / "bool_selection.json").exists()
    assert tools.uninstall("bool_selection", reload=False) is False


def test_export_round_trips(registry, tmp_path):
    tools.install(EXAMPLE, reload=False)
    (registry / "tools" / "bool_selection" / "__pycache__").mkdir()
    (registry / "tools" / "bool_selection" / "__pycache__" / "run.cpython.pyc").write_bytes(b"x")
    out = tools.export("bool_selection", tmp_path / "out" / "b.zip")
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["bool_selection/README.md", "bool_selection/run.py",
                                         "bool_selection/tool.json"]
    tools.uninstall("bool_selection", reload=False)
    assert tools.install(out, reload=False)["items"] == ["union", "intersect", "subtract", "xor"]
    assert (registry / "tools" / "bool_selection" / "run.py").read_bytes() == (EXAMPLE / "run.py").read_bytes()


class FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, method, params, timeout=None):
        self.calls.append((method, params, timeout))
        return {"status": "ok"}


def test_run_builds_request():
    c = FakeClient()
    tools.run(c, "bool_selection", preset="union", params={"keep": True})
    tools.run(c, "bool_selection", interactive=True)
    assert c.calls[0] == ("tool.run", {"id": "bool_selection", "interactive": False, "preset": "union",
                                       "params": {"keep": True}}, None)
    assert c.calls[1][1] == {"id": "bool_selection", "interactive": True} and c.calls[1][2] == 3600.0


def test_cli_install_list_export_uninstall(registry, tmp_path, capsys):
    assert cli_main(["tool", "install", str(EXAMPLE), "--no-reload"]) == 0
    out = capsys.readouterr().out
    assert "installed bool_selection" in out and "entries: union, intersect, subtract, xor" in out
    assert cli_main(["tool", "list"]) == 0
    out = capsys.readouterr().out
    assert "1 tool(s)" in out and "bool_selection" in out and "[ok]" in out
    zp = tmp_path / "e.zip"
    assert cli_main(["tool", "export", "bool_selection", str(zp)]) == 0 and zp.is_file()
    assert cli_main(["tool", "export", "nope", str(zp)]) == 1
    assert cli_main(["tool", "uninstall", "bool_selection", "--no-reload"]) == 0
    assert cli_main(["tool", "uninstall", "bool_selection", "--no-reload"]) == 1
    assert cli_main(["tool", "install", str(tmp_path / "missing"), "--no-reload"]) == 2
    capsys.readouterr()
    assert cli_main(["tool", "run", "bool_selection", "--param", "novalue"]) == 2
    assert "expected NAME=VALUE" in capsys.readouterr().out
    assert cli_main(["tool"]) == 2


def test_cli_run_reports_server_error(monkeypatch, capsys):
    from klink.errors import KLinkServerError
    import klink.client

    class Client:
        def __init__(self, **kw):
            pass

        def connect(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def call(self, method, params, timeout=None):
            assert params == {"id": "t", "interactive": False, "preset": "p", "params": {"op": "and"}}
            raise KLinkServerError("ERR_TOOL_INPUT", "needs 2 shapes", hint="select 2 shapes")
    monkeypatch.setattr(klink.client, "KLinkClient", Client)
    assert cli_main(["tool", "run", "t", "--preset", "p", "--param", "op=and"]) == 1
    out = capsys.readouterr().out
    assert "[ERR_TOOL_INPUT] needs 2 shapes" in out and "next: select 2 shapes" in out
