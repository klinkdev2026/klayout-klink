"""`klink tool new / check / log`: the command-driven pipeline for writing a toolbar tool."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from klink import tools
from klink.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[2]
BOOL_REGION = ROOT / "klink" / "templates" / "project" / "example_template" / "tools" / "bool_region"


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    root = tmp_path / "reg"
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(root))
    return root


def _write(folder: Path, spec, run_src: str | None = "def run(ctx, params):\n    return {}\n") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tool.json").write_text(spec if isinstance(spec, str) else json.dumps(spec), encoding="utf-8")
    if run_src is not None:
        (folder / "run.py").write_text(run_src, encoding="utf-8")
    return folder


# ------------------------------------------------------------------------- new --

@pytest.mark.parametrize("macro,form", [(False, "run"), (True, "macro")])
def test_scaffold_then_check_clean(tmp_path, capsys, macro, form):
    argv = ["tool", "new", "my_tool", "--dir", str(tmp_path)] + (["--macro"] if macro else [])
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    folder = tmp_path / "my_tool"
    assert {p.name for p in folder.iterdir()} == {"tool.json", "run.py", "README.md"}
    assert "created " in out and "(tool.json, run.py, README.md)" in out
    for step in ("tool check", "tool install", "tool run my_tool", "tool log my_tool"):
        assert f"python -m klink.cli {step}" in out
    assert "ERROR" not in out and "warning" not in out
    rep = tools.check(folder)
    assert rep["ok"] and rep["errors"] == [] and rep["warnings"] == [] and rep["form"] == form
    assert cli_main(["tool", "check", str(folder)]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out
    spec = json.loads((folder / "tool.json").read_text(encoding="utf-8"))
    assert spec["id"] == "my_tool" and spec["label"] == "MY_TOO" and spec["title"] == "My tool"
    # the scaffold is installable as is
    assert tools.install(folder, reload=False)["id"] == "my_tool"


def test_scaffold_options_and_refuse_overwrite(tmp_path, capsys):
    rep = tools.scaffold("cnt", dest=tmp_path, title='Count "the" {shapes}', label="CNT", min_selected=2)
    spec = json.loads((tmp_path / "cnt" / "tool.json").read_text(encoding="utf-8"))
    assert spec["title"] == 'Count "the" {shapes}' and spec["label"] == "CNT"
    assert spec["inputs"]["selection"]["min"] == 2
    assert rep["check"]["ok"]
    compile((tmp_path / "cnt" / "run.py").read_text(encoding="utf-8"), "run.py", "exec")
    with pytest.raises(FileExistsError, match="already exists"):
        tools.scaffold("cnt", dest=tmp_path)
    assert cli_main(["tool", "new", "cnt", "--dir", str(tmp_path)]) == 2
    assert "already exists and is not empty" in capsys.readouterr().out
    assert cli_main(["tool", "new", "Bad-Id", "--dir", str(tmp_path)]) == 2
    assert "must match" in capsys.readouterr().out
    (tmp_path / "empty").mkdir()  # an EMPTY folder is fine to fill
    tools.scaffold("empty", dest=tmp_path)
    assert (tmp_path / "empty" / "run.py").is_file()


def test_scaffold_from_copies_and_rewrites_id(tmp_path, capsys):
    assert cli_main(["tool", "new", "my_bool", "--dir", str(tmp_path), "--from", str(BOOL_REGION),
                     "--title", "My boolean"]) == 0
    out = capsys.readouterr().out
    assert "from " in out
    folder = tmp_path / "my_bool"
    spec = json.loads((folder / "tool.json").read_text(encoding="utf-8"))
    src = json.loads((BOOL_REGION / "tool.json").read_text(encoding="utf-8"))
    assert spec["id"] == "my_bool" and spec["title"] == "My boolean"
    assert spec["params"] == src["params"] and spec["presets"] == src["presets"]
    assert (folder / "run.py").read_text(encoding="utf-8") == (BOOL_REGION / "run.py").read_text(encoding="utf-8")
    assert tools.check(folder)["ok"]
    with pytest.raises(ValueError, match="--macro"):
        tools.scaffold("x", dest=tmp_path, from_dir=BOOL_REGION, macro=True)
    with pytest.raises(FileNotFoundError, match="no tool.json"):
        tools.scaffold("y", dest=tmp_path, from_dir=tmp_path / "nowhere")


class _Info:
    def __init__(self, layer, datatype):
        self.layer, self.datatype = layer, datatype


def test_scaffold_run_returns_dict_with_fake_ctx(tmp_path, capsys):
    tools.scaffold("probe", dest=tmp_path)
    ns = {"__name__": "klink_tool_probe"}
    src = (tmp_path / "probe" / "run.py").read_text(encoding="utf-8")
    exec(compile(src, "run.py", "exec"), ns)
    layout = SimpleNamespace(get_info=lambda li: _Info(*{0: (1, 0), 1: (2, 5)}[li]))
    ctx = SimpleNamespace(selection=[], layout=layout, cell=object(), dbu=0.001)
    out = ns["run"](ctx, {})
    assert out == {"shapes": 0, "per_layer": {}}
    assert capsys.readouterr().out.strip() == "Probe: nothing selected"
    ctx.selection = [SimpleNamespace(layer=0), SimpleNamespace(layer=0), SimpleNamespace(layer=1)]
    out = ns["run"](ctx, {})
    assert out == {"shapes": 3, "per_layer": {"1/0": 2, "2/5": 1}}
    json.dumps(out)
    assert capsys.readouterr().out.strip() == "Probe: 3 selected shape(s) (1/0: 2, 2/5: 1)"


def test_macro_skeleton_compiles_without_run(tmp_path):
    tools.scaffold("mac", dest=tmp_path, macro=True)
    src = (tmp_path / "mac" / "run.py").read_text(encoding="utf-8")
    compile(src, "run.py", "exec")
    assert "\ndef run(" not in src and 'if __name__ == "__main__":' in src


def test_tool_templates_ship_in_wheel():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"templates/tool/*"' in text
    for name in ("run_skeleton.py", "macro_skeleton.py", "tool.json"):
        assert (tools.TEMPLATE_DIR / name).is_file()


def test_cli_new_subprocess_end_to_end(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "KLINK_REGISTRY_ROOT": str(tmp_path / "reg")}
    r = subprocess.run([sys.executable, "-m", "klink.cli", "tool", "new", "sub_tool", "--dir", "tools_here"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "created tools_here/sub_tool" in r.stdout and "next:" in r.stdout
    r = subprocess.run([sys.executable, "-m", "klink.cli", "tool", "check", "tools_here/sub_tool", "--json"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads(r.stdout)
    assert rep["ok"] is True and rep["form"] == "run" and rep["errors"] == []


# ----------------------------------------------------------------------- check --

def test_check_reports_many_errors_at_once(tmp_path, capsys):
    spec = {
        "id": "Wrong",
        "title": "",
        "params": {"a": {"type": "int", "default": 1},
                   "lay": {"type": "layer", "from": "layout", "exclude": ["ghost"]}},
        "presets": {"p1": {"params": {"nope": 1}}, "p2": {"params": {"a": "xx"}}},
        "prompt": "sometimes",
    }
    folder = _write(tmp_path / "broken", spec, run_src=None)
    rep = tools.check(folder)
    errs = "\n".join(rep["errors"])
    assert not rep["ok"]
    for needle in ("'id' must match", "'title' must be", "exclude: 'ghost'",
                   "presets.p1: unknown param 'nope'", "presets.p2.params.a: expected an integer",
                   "'prompt' must be one of", "missing run.py"):
        assert needle in errs, needle
    assert len(rep["errors"]) >= 7
    assert cli_main(["tool", "check", str(folder)]) == 1
    out = capsys.readouterr().out
    assert out.count("ERROR:") == len(rep["errors"]) and "FAILED" in out
    assert cli_main(["tool", "check", str(folder), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert set(data) >= {"ok", "errors", "warnings", "form", "params", "presets"}
    assert data["ok"] is False and data["errors"] == rep["errors"]


def test_check_id_vs_folder_and_syntax_error_line(tmp_path):
    folder = _write(tmp_path / "folder_name", {"id": "other_name", "title": "T"},
                    run_src="x = 1\n\ndef run(ctx, params)\n    return {}\n")
    rep = tools.check(folder)
    errs = "\n".join(rep["errors"])
    assert "does not match the directory name 'folder_name'" in errs
    assert "run.py line 3: SyntaxError" in errs
    assert rep["form"] is None


def test_check_bad_json_has_line(tmp_path):
    folder = _write(tmp_path / "t", '{\n  "id": "t",\n  "title": "T",,\n}')
    rep = tools.check(folder)
    assert any(e.startswith("tool.json line 3") for e in rep["errors"])


def test_check_warnings(tmp_path, capsys):
    spec = {"id": "warny", "title": "W", "label": "WAYTOOLONG", "prompt": "never",
            "params": {"width": {"type": "number"}, "src": {"type": "layer", "from": "region"}}}
    macro = ("import pya, sys\n"
             "view = pya.Application.instance().main_window().current_view()\n"
             "view.transaction('x')\n"
             "v = pya.InputDialog.ask_string('a', 'b', 'c')\n"
             "if v is None:\n"
             "    sys.exit(2)\n"
             "view.commit()\n")
    folder = _write(tmp_path / "warny", spec, run_src=macro)
    rep = tools.check(folder)
    assert rep["ok"] and rep["form"] == "macro", rep["errors"]
    warns = "\n".join(rep["warnings"])
    assert "label 'WAYTOOLONG'" in warns
    assert "param 'width' has no default and \"prompt\": \"never\"" in warns
    assert "'src'" not in warns  # a from-layer param is filled from the live list
    assert "transaction" in warns and "InputDialog" in warns and "sys.exit(2)" in warns
    assert cli_main(["tool", "check", str(folder)]) == 0  # warnings only -> exit 0
    assert "warning:" in capsys.readouterr().out
    params = {p["name"]: p for p in rep["params"]}
    assert params["width"]["required"] and params["width"]["asked"] == "never"
    assert params["src"]["from"] == "region"


def test_check_run_form_signature_and_dead_main(tmp_path):
    src = "def run(ctx):\n    return {}\n\nif __name__ == '__main__':\n    run(None)\n"
    rep = tools.check(_write(tmp_path / "sig", {"id": "sig", "title": "S"}, run_src=src))
    warns = "\n".join(rep["warnings"])
    assert rep["form"] == "run" and "two positional" in warns and "never runs" in warns
    ok = "def run(ctx, params, extra=None):\n    return {}\n"
    assert tools.check(_write(tmp_path / "ok2", {"id": "ok2", "title": "S"}, run_src=ok))["warnings"] == []
    macro_run = "run = 3\nprint(run)\n"
    rep = tools.check(_write(tmp_path / "mr", {"id": "mr", "title": "S"}, run_src=macro_run))
    assert rep["form"] == "macro" and any("top-level name `run`" in w for w in rep["warnings"])


def test_check_installed_id_and_unknown_target(tmp_path, capsys):
    tools.scaffold("inst", dest=tmp_path)
    tools.install(tmp_path / "inst", reload=False)
    rep = tools.check("inst")
    assert rep["ok"] and Path(rep["dir"]).parent == tools.tools_dir()
    rep = tools.check("not_there")
    assert not rep["ok"] and "not a tool directory" in rep["errors"][0]
    assert cli_main(["tool", "check", "not_there"]) == 1


def test_check_parity_with_validator_on_example():
    rep = tools.check(BOOL_REGION)
    assert rep["ok"] and rep["form"] == "run" and rep["warnings"] == []


# ------------------------------------------------------------------------- log --

def _journal(registry: Path, session: str, records: list[dict]) -> None:
    folder = registry / "journals"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{session}.tools.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps({"session_id": session, **r}) + "\n")
        fh.write("not json\n")


def test_log_empty_says_no_runs(capsys):
    assert tools.read_log() == []
    assert cli_main(["tool", "log"]) == 0
    out = capsys.readouterr().out
    assert "no runs recorded" in out and "tool run <id>" in out
    assert cli_main(["tool", "log", "my_tool"]) == 0
    assert "tool run my_tool" in capsys.readouterr().out


def test_log_merges_sessions_sorts_and_filters(registry, capsys):
    _journal(registry, "klayout-8765", [
        {"seq": 1, "tool": "a", "status": "ok", "ts": 100.0, "params": {"x": 1},
         "result": {"n": 1, "stdout": "a: one"}},
        {"seq": 2, "tool": "b", "status": "error", "ts": 300.0, "params": {},
         "error": {"code": "ERR_TOOL_RUN", "type": "ValueError", "message": "bad input",
                   "hint": "select two shapes"}, "stdout": "half way"},
    ])
    _journal(registry, "klayout-8766", [
        {"seq": 1, "tool": "a", "status": "cancelled", "ts": 200.0, "preset": "union",
         "auto": {"src": "1/0"}},
        {"seq": 2, "tool": "a", "status": "ok", "ts": 400.0, "result": {"n": 4}},
    ])
    recs = tools.read_log(n=0)
    assert [r["ts"] for r in recs] == [100.0, 200.0, 300.0, 400.0]
    assert [r["session"] for r in recs] == ["klayout-8765", "klayout-8766", "klayout-8765", "klayout-8766"]
    assert [r["ts"] for r in tools.read_log("a", n=0)] == [100.0, 200.0, 400.0]
    assert [r["ts"] for r in tools.read_log("a", n=2)] == [200.0, 400.0]
    assert [r["ts"] for r in tools.read_log(n=5)] == [100.0, 200.0, 300.0, 400.0]

    assert cli_main(["tool", "log", "-n", "10"]) == 0
    out = capsys.readouterr().out
    assert out.index("tool=a  preset=-") < out.index("CANCELLED") < out.index("ERROR") < out.rindex("OK")
    assert "[ERR_TOOL_RUN] bad input" in out and "hint:   select two shapes" in out
    assert "| half way" in out and "| a: one" in out
    assert 'auto:   {"src": "1/0"}' in out and "session=klayout-8766" in out
    assert '"stdout"' not in out  # stdout shown once, not inside the result dict

    assert cli_main(["tool", "log", "b", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1 and data[0]["tool"] == "b" and data[0]["error"]["code"] == "ERR_TOOL_RUN"
    assert cli_main(["tool", "log", "zzz"]) == 0
    assert "no runs recorded for tool 'zzz'" in capsys.readouterr().out


# ------------------------------------------------------------------ run output --

def test_cli_run_failure_prints_stdout_and_hint(monkeypatch, capsys):
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
            raise KLinkServerError("ERR_TOOL_RUN", "layer 9/0 is empty", hint="nothing was changed; pick a layer",
                                   data={"stdout": "checked 3 shapes", "traceback": "Traceback\n  File x\nValueError: e"})
    monkeypatch.setattr(klink.client, "KLinkClient", Client)
    assert cli_main(["tool", "run", "t"]) == 1
    out = capsys.readouterr().out
    assert "[ERR_TOOL_RUN] layer 9/0 is empty" in out and "next: nothing was changed; pick a layer" in out
    assert "checked 3 shapes" in out and "ValueError: e" in out and "tool log t" in out


def test_cli_run_success_prints_stdout_separately(monkeypatch, capsys):
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
            return {"status": "ok", "tool": "t", "result": {"n": 2, "stdout": "T: 2 shapes"}}
    monkeypatch.setattr(klink.client, "KLinkClient", Client)
    assert cli_main(["tool", "run", "t"]) == 0
    out = capsys.readouterr().out
    assert "    T: 2 shapes" in out and '{"n": 2}' in out
