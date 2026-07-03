"""klink doctor — offline-checkable behaviour (no live plugin)."""

from klink.doctor import run_doctor


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
    # No handshake/protocol check when the connection itself failed.
    assert "protocol" not in checks
