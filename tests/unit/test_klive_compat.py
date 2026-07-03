from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

from klink_server.klive_forward import (  # noqa: E402
    KliveCompatError,
    choose_klive_target_session,
    forward_klive_request,
)
from klink_server.session_registry import KLayoutSessionRegistry  # noqa: E402


def _write_session(root, session_id: str, port: int, *, last_seen: float | None = None):
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "host": "127.0.0.1",
        "rpc_port": port,
        "last_seen": time.time() if last_seen is None else last_seen,
    }
    (sessions / f"{session_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_choose_klive_target_session_uses_configured_target(tmp_path):
    _write_session(tmp_path, "klayout-8765", 8765)
    _write_session(tmp_path, "klayout-8766", 8766)
    registry = KLayoutSessionRegistry(tmp_path)
    registry.write_state({"klive_target_session": "klayout-8766"})

    session = choose_klive_target_session(registry)

    assert session["session_id"] == "klayout-8766"
    assert session["rpc_port"] == 8766


def test_choose_klive_target_session_falls_back_to_single_live_session(tmp_path):
    _write_session(tmp_path, "klayout-8765", 8765)

    session = choose_klive_target_session(KLayoutSessionRegistry(tmp_path))

    assert session["session_id"] == "klayout-8765"


def test_choose_klive_target_session_rejects_ambiguous_multi_session_without_target(tmp_path):
    _write_session(tmp_path, "klayout-8765", 8765)
    _write_session(tmp_path, "klayout-8766", 8766)

    with pytest.raises(KliveCompatError, match="multiple live KLayout sessions"):
        choose_klive_target_session(KLayoutSessionRegistry(tmp_path))


def test_forward_klive_request_forwards_layout_show_file_to_target(tmp_path):
    _write_session(tmp_path, "klayout-8767", 8767)
    registry = KLayoutSessionRegistry(tmp_path)
    calls = []

    def fake_rpc_call(**kwargs):
        calls.append(kwargs)
        return {"loaded": kwargs["params"]["path"], "type": "open", "cells": 3}

    response = forward_klive_request(
        {"gds": "C:/tmp/a.gds", "keep_position": False, "technology": "DemoTech"},
        registry=registry,
        rpc_call=fake_rpc_call,
        timeout=12.0,
    )

    assert response["target_session"] == "klayout-8767"
    assert response["target_port"] == 8767
    assert response["file"] == "C:/tmp/a.gds"
    assert calls == [{
        "host": "127.0.0.1",
        "port": 8767,
        "method": "layout.show_file",
        "params": {"path": "C:/tmp/a.gds", "mode": "new", "keep_position": False, "technology": "DemoTech"},
        "timeout": 12.0,
    }]


def test_forward_klive_request_rejects_local_target_to_avoid_deadlock(tmp_path):
    _write_session(tmp_path, "klayout-8765", 8765)
    registry = KLayoutSessionRegistry(tmp_path)

    with pytest.raises(KliveCompatError, match="local klive-compatible server"):
        forward_klive_request(
            {"gds": "C:/tmp/a.gds"},
            registry=registry,
            avoid_session_id="klayout-8765",
        )
