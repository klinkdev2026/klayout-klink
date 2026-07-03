from __future__ import annotations

import json
import time

from klink.mcp.session_registry import SessionRegistry


def test_session_registry_lists_fresh_sessions_and_filters_stale(tmp_path):
    registry = SessionRegistry(tmp_path, stale_after_s=10.0)
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    now = time.time()
    (sessions_dir / "fresh.json").write_text(
        json.dumps({"session_id": "klayout-8765", "host": "127.0.0.1", "rpc_port": 8765, "last_seen": now}),
        encoding="utf-8",
    )
    (sessions_dir / "stale.json").write_text(
        json.dumps({"session_id": "klayout-8766", "host": "127.0.0.1", "rpc_port": 8766, "last_seen": now - 99}),
        encoding="utf-8",
    )

    fresh = registry.list_sessions()
    all_sessions = registry.list_sessions(include_stale=True)

    assert [s["session_id"] for s in fresh] == ["klayout-8765"]
    assert {s["session_id"] for s in all_sessions} == {"klayout-8765", "klayout-8766"}
    assert registry.get("klayout-8766") is None
    assert registry.get("klayout-8766", include_stale=True)["stale"] is True


def test_session_registry_state_round_trips(tmp_path):
    registry = SessionRegistry(tmp_path)

    state = registry.write_state({"klive_target_session": "klayout-8766"})

    assert state["klive_target_session"] == "klayout-8766"
    assert registry.read_state()["klive_target_session"] == "klayout-8766"


def test_session_registry_write_state_does_not_depend_on_fixed_tmp_name(tmp_path):
    registry = SessionRegistry(tmp_path)
    tmp_path.joinpath("state.json.tmp").write_text("stale", encoding="utf-8")

    state = registry.write_state({"klive_target_session": "klayout-8767"})

    assert state["klive_target_session"] == "klayout-8767"
    assert registry.read_state()["klive_target_session"] == "klayout-8767"
    assert tmp_path.joinpath("state.json.tmp").read_text(encoding="utf-8") == "stale"
