"""_atomic_write_json must never leak ``*.tmp`` files (Windows replace races).

Covers both the MCP-side and the KLayout-plugin-side copies, which must stay
behaviourally identical.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

from klink.mcp import session_registry as mcp_registry  # noqa: E402
from klink_server import session_registry as plugin_registry  # noqa: E402

MODULES = pytest.mark.parametrize(
    "mod", [mcp_registry, plugin_registry], ids=["mcp", "plugin"]
)


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    for mod in (mcp_registry, plugin_registry):
        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(mod, "_SWEPT_DIRS", set())


def _tmp_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.tmp"))


@MODULES
def test_transient_permission_error_is_retried(mod, tmp_path, monkeypatch):
    target = tmp_path / "sessions" / "klayout-8765.json"
    real_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "Access is denied")
        return real_replace(self, dst)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    mod._atomic_write_json(target, {"session_id": "klayout-8765", "n": 1})

    assert calls["n"] == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"session_id": "klayout-8765", "n": 1}
    assert _tmp_files(target.parent) == []


@MODULES
def test_permanent_failure_raises_and_leaves_no_tmp(mod, tmp_path, monkeypatch):
    target = tmp_path / "sessions" / "klayout-8765.json"
    calls = {"n": 0}

    def always_denied(self, dst):
        calls["n"] += 1
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", always_denied)

    with pytest.raises(PermissionError):
        mod._atomic_write_json(target, {"session_id": "klayout-8765"})

    assert calls["n"] == mod._REPLACE_ATTEMPTS
    assert _tmp_files(target.parent) == []
    assert not target.exists()


@MODULES
def test_sweep_removes_old_tmp_and_keeps_fresh(mod, tmp_path):
    old = tmp_path / "klayout-8765.json.111.deadbeef.tmp"
    fresh = tmp_path / "klayout-8765.json.222.cafebabe.tmp"
    keep_json = tmp_path / "klayout-8765.json"
    for p in (old, fresh, keep_json):
        p.write_text("{}", encoding="utf-8")
    past = time.time() - 2 * 3600
    os.utime(old, (past, past))
    os.utime(keep_json, (past, past))

    removed = mod._sweep_stale_tmp(tmp_path, max_age_s=3600)

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert keep_json.exists()


@MODULES
def test_write_path_sweeps_stale_tmp_once_per_directory(mod, tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    stale = sessions / "klayout-8765.json.111.deadbeef.tmp"
    stale.write_text("{}", encoding="utf-8")
    past = time.time() - 2 * 3600
    os.utime(stale, (past, past))

    mod._atomic_write_json(sessions / "klayout-8765.json", {"session_id": "a"})
    assert not stale.exists()

    # Second write in the same process does not re-scan the directory.
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (past, past))
    mod._atomic_write_json(sessions / "klayout-8765.json", {"session_id": "b"})
    assert stale.exists()
    assert json.loads((sessions / "klayout-8765.json").read_text(encoding="utf-8")) == {"session_id": "b"}


def test_sweep_tolerates_missing_directory(tmp_path):
    for mod in (mcp_registry, plugin_registry):
        assert mod._sweep_stale_tmp(tmp_path / "does-not-exist") == 0
