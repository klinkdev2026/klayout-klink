"""klink doctor — offline-checkable behaviour (no live plugin)."""

import socket
import sys
import types
from pathlib import Path

from klink.doctor import format_issue_report, main, run_doctor
from klink._meta import __version__


def _by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_doctor_reports_interpreter_and_package_even_offline():
    # Port 0 is never a live klink plugin; the connection check must fail
    # without hanging, but the local checks still report.
    report = run_doctor(port=0)
    checks = _by_name(report)
    assert checks["interpreter"]["ok"] is True
    assert checks["klink"]["ok"] is True
    assert "protocol" in checks["klink"]["detail"]


def test_doctor_flags_missing_plugin_with_an_instructive_fix():
    report = run_doctor(port=0)
    checks = _by_name(report)
    assert report["ok"] is False
    conn = checks["plugin_connection"]
    assert conn["ok"] is False
    assert "KLayout" in conn["fix"]
    # want_scan defaults to False, so the fix should point the user at --scan.
    assert "--scan" in conn["fix"]
    # No handshake/protocol check when the connection itself failed.
    assert "protocol" not in checks


def test_doctor_missing_plugin_fix_omits_scan_hint_when_scan_already_used():
    report = run_doctor(port=0, want_scan=True)
    checks = _by_name(report)
    conn = checks["plugin_connection"]
    assert conn["ok"] is False
    assert "--scan" not in conn["fix"]


def test_doctor_reports_kernels_check_present():
    report = run_doctor(port=0)
    checks = _by_name(report)
    assert "kernels" in checks
    # Informational: ok regardless of whether the kernels are installed.
    assert checks["kernels"]["ok"] is True
    assert checks["kernels"]["detail"]


def test_doctor_treats_namespace_only_klayout_as_not_installed(monkeypatch):
    # A stray `klayout/` directory on sys.path imports as an empty namespace
    # package: `import klayout` succeeds but `import klayout.db` fails. The
    # check must NOT report that as an install.
    fake_pkg = types.ModuleType("klayout")   # no `db`, no __path__
    monkeypatch.setitem(sys.modules, "klayout", fake_pkg)
    monkeypatch.delitem(sys.modules, "klayout.db", raising=False)

    report = run_doctor(port=0)
    kp = _by_name(report)["klayout_pip"]
    assert kp["ok"] is True
    assert "not installed" in kp["detail"]


def test_doctor_flags_old_klayout_pip_version(monkeypatch):
    import importlib.metadata as importlib_metadata

    fake_pkg = types.ModuleType("klayout")
    fake_db = types.ModuleType("klayout.db")
    fake_pkg.db = fake_db
    monkeypatch.setitem(sys.modules, "klayout", fake_pkg)
    monkeypatch.setitem(sys.modules, "klayout.db", fake_db)

    real_version = importlib_metadata.version

    def fake_version(name):
        if name == "klayout":
            return "0.27.0"
        return real_version(name)

    monkeypatch.setattr(importlib_metadata, "version", fake_version)

    report = run_doctor(port=0)
    checks = _by_name(report)
    kp = checks["klayout_pip"]
    assert kp["ok"] is False
    assert "0.27.0" in kp["detail"]
    assert "0.28" in kp["fix"]


def test_doctor_accepts_klayout_pip_version_at_floor(monkeypatch):
    import importlib.metadata as importlib_metadata

    fake_pkg = types.ModuleType("klayout")
    fake_db = types.ModuleType("klayout.db")
    fake_pkg.db = fake_db
    monkeypatch.setitem(sys.modules, "klayout", fake_pkg)
    monkeypatch.setitem(sys.modules, "klayout.db", fake_db)

    real_version = importlib_metadata.version

    def fake_version(name):
        if name == "klayout":
            return "0.29.1"
        return real_version(name)

    monkeypatch.setattr(importlib_metadata, "version", fake_version)

    report = run_doctor(port=0)
    checks = _by_name(report)
    kp = checks["klayout_pip"]
    assert kp["ok"] is True
    assert "0.29.1" in kp["detail"]
    assert "fix" not in kp


class _RefusingSocket:
    """Stand-in for socket.socket() that always reports "connection refused"."""

    def __init__(self, *args, **kwargs):
        pass

    def settimeout(self, timeout):
        pass

    def connect_ex(self, address):
        return 111  # ECONNREFUSED

    def connect(self, address):
        # socket.create_connection() (used by klink's own transport) calls
        # connect(), not connect_ex(); keep both paths "refused" so this fake
        # is safe to install globally for the duration of the test.
        raise ConnectionRefusedError(111, "Connection refused")

    def close(self):
        pass


def test_doctor_scan_reports_no_listeners_when_none_open(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: _RefusingSocket())

    report = run_doctor(port=0, want_scan=True)
    checks = _by_name(report)
    scan = checks["port_scan"]
    assert scan["ok"] is True
    assert "no listeners" in scan["detail"]
    assert "fix" in scan


def test_doctor_scan_not_run_without_flag():
    report = run_doctor(port=0)
    checks = _by_name(report)
    assert "port_scan" not in checks


def test_doctor_default_args_do_not_raise():
    # Exercises the exact CLI defaults (host 127.0.0.1, port 8765) end to end;
    # must not hang or raise even with no KLayout running.
    report = run_doctor()
    assert "ok" in report
    assert "checks" in report
    names = {c["name"] for c in report["checks"]}
    assert {"interpreter", "klink", "kernels", "klayout_pip", "plugin_connection"}.issubset(
        names
    )


def test_doctor_report_renders_fenced_markdown_and_contains_version():
    # port=0 -> no live KLayout, so this also proves --report works offline.
    report = run_doctor(port=0, want_gdsfactory=True, want_scan=True)
    text = format_issue_report(report)
    lines = text.splitlines()
    assert lines[0] == "```"
    assert lines[-1] == "```"
    assert __version__ in text


def test_doctor_report_works_offline_and_states_not_reachable():
    report = run_doctor(port=0)
    text = format_issue_report(report)
    assert "plugin connection: not reachable" in text


def test_doctor_report_redacts_home_directory(monkeypatch, tmp_path):
    fake_home = tmp_path / "Users" / "secretperson"
    fake_home.mkdir(parents=True)
    fake_exe = fake_home / "venv" / "Scripts" / "python.exe"

    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    report = run_doctor(port=0)
    text = format_issue_report(report)

    assert "secretperson" not in text
    assert str(fake_home) not in text
    assert "~" in text


def test_doctor_report_omits_gdsfactory_line_when_not_importable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gdsfactory":
            raise ImportError("no gdsfactory")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    report = run_doctor(port=0, want_gdsfactory=True)
    text = format_issue_report(report)
    assert "gdsfactory:" not in text


def test_doctor_main_report_flag_prints_fenced_block(capsys):
    code = main(["--port", "0", "--report"])
    captured = capsys.readouterr()
    assert "```" in captured.out
    assert __version__ in captured.out
    # port 0 is never reachable, so the report describes a real failure.
    assert code == 1
