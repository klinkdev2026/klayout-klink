"""Optional toolbar icons: tool.json / button manifest ``icon`` key, scaffold, check, install, CLI."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

from klink import buttons, tools
from klink.cli import main as cli_main


def write_png(path: Path, size: int = 24) -> Path:
    """A minimal valid RGB PNG (stdlib only)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    row = b"\x00" + b"\x20\x80\xe0" * size
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(row * size))
                     + chunk(b"IEND", b""))
    return path


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    root = tmp_path / "reg"
    monkeypatch.setenv("KLINK_REGISTRY_ROOT", str(root))
    return root


def _tool(folder: Path, **extra) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tool.json").write_text(json.dumps({"id": folder.name, "title": "T", **extra}), encoding="utf-8")
    (folder / "run.py").write_text("def run(ctx, params):\n    return {}\n", encoding="utf-8")
    return folder


# ------------------------------------------------------------------ tool.json --

@pytest.mark.parametrize("icon,ok", [
    ("icon.png", True), ("icon.svg", True), ("img/ICON.PNG", True), ("img\\i.svg", True),
    ("icon.gif", False), ("icon", False), ("../icon.png", False), ("a/../../i.png", False),
    (str(Path(__file__).resolve().with_name("icon.png")), False), ("/x/icon.png", False), ("\\\\srv\\i.png", False), ("C:i.png", False),
    ("", False), ("  ", False), (3, False), (["icon.png"], False),
])
def test_tool_icon_name_rules(icon, ok):
    problem = tools.validate_tool({"id": "t", "title": "T", "icon": icon}, "t")
    assert (problem is None) is ok, problem
    if not ok:
        assert problem == tools.ICON_REASON


def test_no_icon_key_is_unchanged():
    assert tools.validate_tool({"id": "t", "title": "T"}, "t") is None
    assert tools.validate_tool({"id": "t", "title": "T", "icon": None}, "t") is None


def test_check_reports_icon_path_and_missing_file(tmp_path, capsys):
    folder = _tool(tmp_path / "t", icon="icon.png")
    rep = tools.check(folder)
    assert not rep["ok"] and rep["icon"] == str(folder / "icon.png")
    assert any("icon file not found" in e and str(folder / "icon.png") in e for e in rep["errors"])
    assert cli_main(["tool", "check", str(folder)]) == 1
    out = capsys.readouterr().out
    assert f"icon: {folder / 'icon.png'}" in out.splitlines()[0] and "icon file not found" in out
    write_png(folder / "icon.png")
    rep = tools.check(folder)
    assert rep["ok"] and rep["errors"] == []
    assert cli_main(["tool", "check", str(folder)]) == 0
    assert "icon: - (text button)" not in capsys.readouterr().out
    assert tools.check(_tool(tmp_path / "plain"))["icon"] is None
    assert cli_main(["tool", "check", str(tmp_path / "plain")]) == 0
    assert "icon: - (text button)" in capsys.readouterr().out.splitlines()[0]


def test_check_bad_icon_name_is_an_error(tmp_path):
    rep = tools.check(_tool(tmp_path / "t", icon="icon.jpg"))
    assert not rep["ok"] and any(tools.ICON_REASON in e and "icon.jpg" in e for e in rep["errors"])
    assert rep["icon"] is None


def test_install_refuses_missing_icon_naming_path(tmp_path, registry):
    folder = _tool(tmp_path / "t", icon="icon.png")
    with pytest.raises(ValueError, match="icon file not found") as exc:
        tools.install(folder, reload=False)
    assert str(folder / "icon.png") in str(exc.value)
    assert not (registry / "buttons" / "t.json").exists()


def test_install_writes_absolute_icon_into_button_manifest(tmp_path, registry):
    folder = _tool(tmp_path / "t", icon="img/icon.png", presets={"a": {"params": {}}})
    write_png(folder / "img" / "icon.png")
    rep = tools.install(folder, reload=False)
    manifest = json.loads(Path(rep["button"]).read_text(encoding="utf-8"))
    icon = Path(manifest["icon"])
    assert icon.is_absolute() and icon.is_file()
    assert icon == (registry / "tools" / "t" / "img" / "icon.png").resolve()
    assert buttons.validate(manifest, "t") is None
    assert [e["icon"] for e in tools.list_installed()] == ["img/icon.png"]
    # without icon: the manifest has no icon key at all (text button, as before)
    plain = tools.install(_tool(tmp_path / "plain"), reload=False)
    assert "icon" not in json.loads(Path(plain["button"]).read_text(encoding="utf-8"))


def test_installed_zip_with_icon(tmp_path, registry):
    folder = _tool(tmp_path / "t", icon="icon.svg")
    (folder / "icon.svg").write_text('<svg width="24" height="24"/>',
                                     encoding="utf-8")
    tools.install(folder, reload=False)
    z = tools.export("t", tmp_path / "t.zip")
    tools.uninstall("t", reload=False)
    rep = tools.install(z, reload=False)
    assert Path(json.loads(Path(rep["button"]).read_text(encoding="utf-8"))["icon"]).is_file()


# --------------------------------------------------------------------- scaffold --

def test_tool_new_icon_copies_and_sets_key(tmp_path, capsys):
    src = write_png(tmp_path / "src" / "My Icon.PNG")
    assert cli_main(["tool", "new", "ico", "--dir", str(tmp_path / "tools"), "--icon", str(src)]) == 0
    out = capsys.readouterr().out
    folder = tmp_path / "tools" / "ico"
    assert (folder / "icon.png").read_bytes() == src.read_bytes()
    assert json.loads((folder / "tool.json").read_text(encoding="utf-8"))["icon"] == "icon.png"
    assert "icon.png" in out and "ERROR" not in out
    assert tools.check(folder)["ok"]
    assert cli_main(["tool", "check", str(folder)]) == 0
    assert f"icon: {folder / 'icon.png'}" in capsys.readouterr().out
    assert Path(json.loads(Path(tools.install(folder, reload=False)["button"])
                           .read_text(encoding="utf-8"))["icon"]).is_file()


def test_tool_new_icon_errors(tmp_path, capsys):
    assert cli_main(["tool", "new", "a", "--dir", str(tmp_path), "--icon", str(tmp_path / "no.png")]) == 2
    assert "icon file not found" in capsys.readouterr().out
    bad = tmp_path / "i.bmp"
    bad.write_bytes(b"x")
    assert cli_main(["tool", "new", "b", "--dir", str(tmp_path), "--icon", str(bad)]) == 2
    assert "must be a .png or .svg" in capsys.readouterr().out
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


def test_tool_new_without_icon_unchanged(tmp_path):
    rep = tools.scaffold("plain", dest=tmp_path)
    assert rep["files"] == ["tool.json", "run.py", "README.md"]
    assert "icon" not in json.loads((tmp_path / "plain" / "tool.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- buttons --

def test_define_icon_absolute_and_must_exist(tmp_path, registry):
    with pytest.raises(ValueError, match="icon file not found") as exc:
        buttons.define("b", "B", icon=tmp_path / "gone.png")
    assert str((tmp_path / "gone.png").resolve()) in str(exc.value)
    bmp = tmp_path / "i.bmp"
    bmp.write_bytes(b"x")
    with pytest.raises(ValueError, match="absolute path to a .png or .svg"):
        buttons.define("b", "B", icon=bmp)
    png = write_png(tmp_path / "i.png")
    path = buttons.define("b", "B", icon=png)
    assert json.loads(path.read_text(encoding="utf-8"))["icon"] == str(png.resolve())
    assert "icon" not in json.loads(buttons.define("c", "C").read_text(encoding="utf-8"))


def test_cli_button_add_icon_and_list(tmp_path, registry, capsys, monkeypatch):
    png = write_png(tmp_path / "i.png")
    monkeypatch.chdir(tmp_path)
    assert cli_main(["button", "add", "b", "--label", "B", "--icon", "i.png", "--no-reload"]) == 0
    assert json.loads((registry / "buttons" / "b.json").read_text(encoding="utf-8"))["icon"] == str(png.resolve())
    capsys.readouterr()
    assert cli_main(["button", "list"]) == 0
    out = capsys.readouterr().out
    assert f"icon: {png.resolve()}" in out and "file missing" not in out
    png.unlink()
    assert cli_main(["button", "list"]) == 0
    assert "(file missing: text button)" in capsys.readouterr().out
    assert cli_main(["button", "add", "c", "--label", "C", "--icon", "nope.png", "--no-reload"]) == 2
    assert "icon file not found" in capsys.readouterr().out
