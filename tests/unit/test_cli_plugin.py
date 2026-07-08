"""Tests for `klink plugin install/status` and `klink --version` /
`klink update --dry-run` (the wheel-bundled KLayout plugin path)."""
from __future__ import annotations

import re

import pytest

from klink import cli
from klink._meta import __version__


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_payload_dir_exists_and_looks_like_a_salt_package():
    payload = cli._plugin_payload_dir()
    assert payload.is_dir(), "plugin payload missing; run tools/sync_plugin_payload.py"
    assert (payload / "grain.xml").is_file()
    assert (payload / "pymacros").is_dir()
    assert cli._grain_is_klink(payload)
    assert cli._grain_version(payload) == __version__


def test_plugin_install_and_status_roundtrip(tmp_path, capsys):
    salt = tmp_path / "salt"

    assert cli.plugin_install(salt) == 0
    out = capsys.readouterr().out
    assert "Installed klink plugin" in out
    assert "Restart KLayout" in out
    target = salt / "klink_plugin"
    assert (target / "grain.xml").is_file()
    assert (target / "pymacros").is_dir()
    assert not list(target.rglob("__pycache__"))

    assert cli.plugin_status(salt) == 0
    out = capsys.readouterr().out
    assert "up to date." in out


def test_plugin_install_replaces_our_own_plugin(tmp_path, capsys):
    salt = tmp_path / "salt"
    assert cli.plugin_install(salt) == 0
    # Simulate an older install: rewrite the grain version.
    grain = salt / "klink_plugin" / "grain.xml"
    grain.write_text(
        re.sub(r"<version>[^<]+</version>", "<version>0.0.1</version>",
               grain.read_text(encoding="utf-8")),
        encoding="utf-8")
    capsys.readouterr()

    assert cli.plugin_status(salt) == 0
    assert "MISMATCH" in capsys.readouterr().out

    assert cli.plugin_install(salt) == 0
    out = capsys.readouterr().out
    assert "replaced existing plugin (0.0.1)" in out
    assert cli._grain_version(salt / "klink_plugin") == __version__


def test_plugin_install_refuses_foreign_dir_without_force(tmp_path, capsys):
    salt = tmp_path / "salt"
    foreign = salt / "klink_plugin"
    foreign.mkdir(parents=True)
    (foreign / "grain.xml").write_text(
        "<salt-grain><name>other</name></salt-grain>", encoding="utf-8")

    assert cli.plugin_install(salt) == 1
    assert "--force" in capsys.readouterr().err

    assert cli.plugin_install(salt, force=True) == 0
    assert cli._grain_is_klink(salt / "klink_plugin")


def test_default_salt_dir_honours_klayout_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KLAYOUT_HOME", str(tmp_path / "kh"))
    assert cli._default_salt_dir() == tmp_path / "kh" / "salt"


def test_update_dry_run_writes_nothing(tmp_path, capsys):
    proj = tmp_path / "proj"
    assert cli.init(str(proj)) == 0
    # Delete one starter and modify another; dry-run must only REPORT.
    examples = proj / "example_template"
    victims = sorted(p for p in examples.rglob("*.py"))[:2]
    removed_file, modified_file = victims[0], victims[1]
    removed_file.unlink()
    modified_file.write_text("# locally edited\n", encoding="utf-8")
    capsys.readouterr()

    assert cli.update(str(proj), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Would refresh" in out
    assert not removed_file.exists()                      # still gone
    assert modified_file.read_text(encoding="utf-8") == "# locally edited\n"

    assert cli.update(str(proj)) == 0
    out = capsys.readouterr().out
    assert "Refreshed" in out
    assert "overwritten with the packaged version" in out  # the warning
    assert removed_file.exists()
    assert modified_file.read_text(encoding="utf-8") != "# locally edited\n"
