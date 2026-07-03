from __future__ import annotations

import io
import json

from klink.errors import KLinkTransportError
from klink.mcp import bridge as bridge_mod
from klink.mcp.bridge import KLinkMCPBridge
from klink.mcp.local_tools import all_local_tools
from klink.mcp.server import MCPServer


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def test_local_mcp_tools_are_registered_separately_from_plugin_rpc_methods():
    names = {tool.name for tool in all_local_tools()}

    assert "klink.status" in names
    assert "klink.session_list" in names
    assert "klink.session_status" in names
    assert "klink.session_use" in names
    assert "klink.session_set_klive_target" in names
    assert "klink.session_label" in names
    assert "klink.session_resolve" in names
    assert "klink.transfer_prepare" in names
    assert "klink.transfer_commit" in names
    assert "interaction.selection.recent" in names
    assert "interaction.context" in names
    # interaction.context.* aliases were removed; only the
    # canonical interaction.selection.* names and the combined
    # interaction.context tool remain.
    assert not {n for n in names if n.startswith("interaction.context.")}
    assert "routing.tapered_hybrid_cell" in names
    assert "routing.tapered_polygon_cell" in names
    assert "routing.gdsfactory_ports" in names
    assert "routing.steiner_cell" in names
    assert "routing.damped_segment_cell" in names
    assert "routing.damped_polygon_cell" in names
    assert "routing.damped_steiner_cell" in names
    assert "routing.global_channel_cell" in names


def test_bridge_lists_status_tools_when_klink_is_unavailable(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            raise KLinkTransportError("no klayout")

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", FailingClient)

    bridge = KLinkMCPBridge()

    assert bridge.ensure_connected() is False
    tools = bridge.list_tools()["tools"]
    assert {"klink.status", "klink.reconnect", "interaction.selection.recent"}.issubset(
        {tool["name"] for tool in tools}
    )
    assert bridge.status()["connected"] is False
    assert "no klayout" in bridge.status()["last_error"]


def test_bridge_closes_stale_client_after_transport_error(monkeypatch, sample_method_specs):
    clients = []

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            self.closed = False
            clients.append(self)

        def connect(self):
            return None

        def methods(self):
            return {"methods": sample_method_specs}

        def call(self, name, arguments, timeout=None):
            raise KLinkTransportError("socket broke")

        def close(self):
            self.closed = True

    monkeypatch.setattr(bridge_mod, "KLinkClient", BrokenClient)

    bridge = KLinkMCPBridge(profiles=["basic"])
    assert bridge.ensure_connected() is True

    result = bridge.call_tool("meta.ping", {})

    assert result["isError"] is True
    assert "socket broke" in _text(result)
    assert clients[0].closed is True
    assert bridge.status()["connected"] is False
    # When disconnected, only local tools list; klink.status must be present.
    # (tools/list order is not part of the contract — clients address by name.)
    assert "klink.status" in {t["name"] for t in bridge.list_tools()["tools"]}


def test_bridge_uses_short_timeout_for_ordinary_calls(monkeypatch, sample_method_specs):
    clients = []

    class RecordingClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
            clients.append(self)

        def connect(self):
            return None

        def methods(self):
            return {"methods": sample_method_specs}

        def call(self, name, arguments, timeout=None):
            self.calls.append((name, timeout))
            return {"ok": name}

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", RecordingClient)

    bridge = KLinkMCPBridge(profiles=["basic"], call_timeout=7.0, long_call_timeout=77.0)
    result = bridge.call_tool("meta.ping", {})

    assert json.loads(_text(result)) == {"ok": "meta.ping"}
    assert clients[0].calls == [("meta.ping", 7.0)]
    assert bridge.status()["last_call"] == {"name": "meta.ping", "timeout": 7.0}


def test_bridge_uses_long_timeout_for_long_and_heavy_calls(monkeypatch, sample_method_specs):
    specs = [
        *sample_method_specs,
        {
            "name": "layout.show_file",
            "description": "load layout",
            "params": {"type": "object"},
            "tags": ["layout", "write"],
            "long_running": True,
        },
        {
            "name": "shape.query",
            "description": "query shapes",
            "params": {"type": "object"},
            "tags": ["shape", "read"],
        },
    ]
    clients = []

    class RecordingClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
            clients.append(self)

        def connect(self):
            return None

        def methods(self):
            return {"methods": specs}

        def call(self, name, arguments, timeout=None):
            self.calls.append((name, timeout))
            return {"ok": name}

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", RecordingClient)

    bridge = KLinkMCPBridge(profiles=["all"], call_timeout=7.0, long_call_timeout=77.0)
    bridge.call_tool("layout.show_file", {"path": "x.gds"})
    bridge.call_tool("shape.query", {"cell": "TOP"})
    bridge.call_tool("exec.python", {"code": "1 + 1"})

    assert clients[0].calls == [
        ("layout.show_file", 77.0),
        ("shape.query", 77.0),
        ("exec.python", 77.0),
    ]


def test_bridge_status_tool_is_available_without_klink(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            raise KLinkTransportError("offline")

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", FailingClient)

    bridge = KLinkMCPBridge()
    result = bridge.call_tool("klink.status", {})
    status = json.loads(_text(result))

    assert status["connected"] is False
    assert status["host"] == "127.0.0.1"


def test_bridge_lists_registered_sessions(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "klayout-8766.json").write_text(
        json.dumps({
            "session_id": "klayout-8766",
            "host": "127.0.0.1",
            "rpc_port": 8766,
            "pid": 123,
            "last_seen": 9999999999,
        }),
        encoding="utf-8",
    )

    bridge = KLinkMCPBridge(registry_root=tmp_path)
    payload = json.loads(_text(bridge.call_tool("klink.session_list", {})))

    assert payload["count"] == 1
    assert payload["sessions"][0]["session_id"] == "klayout-8766"
    assert payload["registry_root"] == str(tmp_path)


def test_bridge_session_use_switches_rpc_target(monkeypatch, sample_method_specs, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "klayout-8766.json").write_text(
        json.dumps({
            "session_id": "klayout-8766",
            "host": "127.0.0.1",
            "rpc_port": 8766,
            "last_seen": 9999999999,
        }),
        encoding="utf-8",
    )
    clients = []

    class RecordingClient:
        def __init__(self, host, port, *args, **kwargs):
            self.host = host
            self.port = port
            self.closed = False
            clients.append(self)

        def connect(self):
            return None

        def methods(self):
            return {"methods": sample_method_specs}

        def on(self, name, handler):
            pass

        def subscribe(self, channels):
            return {"accepted": list(channels)}

        def close(self):
            self.closed = True

    monkeypatch.setattr(bridge_mod, "KLinkClient", RecordingClient)

    bridge = KLinkMCPBridge(profiles=["basic"], registry_root=tmp_path)
    payload = json.loads(_text(bridge.call_tool("klink.session_use", {"session_id": "klayout-8766"})))

    assert payload["ok"] is True
    assert payload["active_session_id"] == "klayout-8766"
    assert clients[-1].port == 8766
    assert bridge.status()["port"] == 8766


def test_bridge_session_set_klive_target_updates_registry_state(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "klayout-8767.json").write_text(
        json.dumps({
            "session_id": "klayout-8767",
            "host": "127.0.0.1",
            "rpc_port": 8767,
            "last_seen": 9999999999,
        }),
        encoding="utf-8",
    )

    bridge = KLinkMCPBridge(registry_root=tmp_path)
    payload = json.loads(_text(bridge.call_tool("klink.session_set_klive_target", {"session_id": "klayout-8767"})))

    assert payload["ok"] is True
    assert payload["klive_target_session"] == "klayout-8767"
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["klive_target_session"] == "klayout-8767"


def test_bridge_session_label_and_resolve(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "klayout-8765.json").write_text(
        json.dumps({
            "session_id": "klayout-8765",
            "host": "127.0.0.1",
            "rpc_port": 8765,
            "active_cell": "CAUSE_DEMO",
            "top_cells": ["TOP", "CAUSE_DEMO"],
            "last_seen": 9999999999,
        }),
        encoding="utf-8",
    )

    bridge = KLinkMCPBridge(registry_root=tmp_path)
    label = json.loads(_text(bridge.call_tool("klink.session_label", {
        "session_id": "klayout-8765",
        "label": "mzi source",
        "aliases": ["mzi", "source"],
        "description": "source layout with MZI cells",
    })))
    listed = json.loads(_text(bridge.call_tool("klink.session_list", {})))
    resolved = json.loads(_text(bridge.call_tool("klink.session_resolve", {"query": "mzi"})))

    assert label["ok"] is True
    assert listed["sessions"][0]["label"] == "mzi source"
    assert listed["sessions"][0]["aliases"] == ["mzi", "source"]
    assert resolved["session_id"] == "klayout-8765"
    assert resolved["match_type"] == "alias"


def test_bridge_session_label_falls_back_to_session_rpc_on_registry_write_permission(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "klayout-8765.json").write_text(
        json.dumps({
            "session_id": "klayout-8765",
            "host": "127.0.0.1",
            "rpc_port": 8765,
            "last_seen": 9999999999,
        }),
        encoding="utf-8",
    )
    calls = []

    class LabelClient:
        def __init__(self, host, port, *args, **kwargs):
            self.host = host
            self.port = port

        def connect(self):
            calls.append((self.host, self.port, "connect"))
            return self

        def close(self):
            calls.append((self.host, self.port, "close"))

        def session_label_set(self, session_id, label, *, aliases=None, description=None):
            calls.append((session_id, label, tuple(aliases or []), description))
            return {
                "ok": True,
                "session_id": session_id,
                "label": label,
                "aliases": list(aliases or []),
                "registry_state": {"session_labels": {session_id: {"label": label}}},
            }

    bridge = KLinkMCPBridge(registry_root=tmp_path)
    monkeypatch.setattr(bridge._sessions, "write_state", lambda updates: (_ for _ in ()).throw(PermissionError("denied")))
    monkeypatch.setattr("klink.mcp.bridge.KLinkClient", LabelClient)

    payload = json.loads(_text(bridge.call_tool("klink.session_label", {
        "session_id": "klayout-8765",
        "label": "source layout",
        "aliases": ["source"],
    })))

    assert payload["ok"] is True
    assert payload["label"] == "source layout"
    assert ("127.0.0.1", 8765, "connect") in calls
    assert ("klayout-8765", "source layout", ("source",), None) in calls


def test_bridge_session_resolve_reports_ambiguous_alias(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    for session_id, port in (("klayout-8765", 8765), ("klayout-8766", 8766)):
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({
                "session_id": session_id,
                "host": "127.0.0.1",
                "rpc_port": port,
                "last_seen": 9999999999,
            }),
            encoding="utf-8",
        )
    bridge = KLinkMCPBridge(registry_root=tmp_path)
    bridge.call_tool("klink.session_label", {"session_id": "klayout-8765", "label": "source", "aliases": ["demo"]})
    bridge.call_tool("klink.session_label", {"session_id": "klayout-8766", "label": "target", "aliases": ["demo"]})

    result = bridge.call_tool("klink.session_resolve", {"query": "demo"})

    assert result["isError"] is True
    assert "ambiguous session query" in _text(result)


def test_bridge_transfer_prepare_reads_source_and_dry_runs_target(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    for session_id, port in (("klayout-8765", 8765), ("klayout-8767", 8767)):
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({
                "session_id": session_id,
                "host": "127.0.0.1",
                "rpc_port": port,
                "last_seen": 9999999999,
            }),
            encoding="utf-8",
        )
    calls = []

    class TransferClient:
        def __init__(self, host, port, *args, **kwargs):
            self.port = port

        def connect(self):
            calls.append((self.port, "connect"))

        def close(self):
            calls.append((self.port, "close"))

        def layout_info(self, verbosity="normal"):
            calls.append((self.port, "layout.info", verbosity))
            return {"dbu": 0.001, "active_cell": "TOP"}

        def layer_list(self):
            calls.append((self.port, "layer.list"))
            return {"layers": [{"layer_index": 1, "layer": 1, "datatype": 0}]}

        def selection_get(self, **kwargs):
            calls.append((self.port, "selection.get", kwargs))
            return {
                "count": 1,
                "objects": [{
                    "kind": "shape",
                    "shape": {
                        "type": "box",
                        "layer_index": 1,
                        "bbox_dbu": [0, 0, 1000, 1000],
                    },
                }],
            }

        def layer_ensure(self, layer, datatype=0):
            calls.append((self.port, "layer.ensure", layer, datatype))
            return {"layer_index": 9}

        def shape_insert_many(self, cell, items, *, dry_run=False):
            calls.append((self.port, "shape.insert_many", cell, items, dry_run))
            return {"inserted": 0 if dry_run else len(items), "dry_run": dry_run}

        def transfer_pending_set(self, package):
            calls.append((self.port, "transfer.pending_set", package["package_id"]))
            return {"pending": True, "package_id": package["package_id"]}

    monkeypatch.setattr(bridge_mod, "KLinkClient", TransferClient)

    bridge = KLinkMCPBridge(registry_root=tmp_path)
    payload = json.loads(_text(bridge.call_tool("klink.transfer_prepare", {
        "source_session": "klayout-8765",
        "target_session": "klayout-8767",
        "target_cell": "TOP",
        "layer_map": {"1/0": "10/0"},
        "translate_um": [5, 0],
    })))

    assert payload["ok"] is True
    assert payload["pending"] is True
    assert payload["review"]["shape_count"] == 1
    assert payload["review"]["target_layers"] == ["10/0"]
    assert payload["target_dry_run"]["dry_run"] is True
    assert payload["target_pending"]["pending"] is True
    assert any(call[0:2] == (8765, "selection.get") for call in calls)
    assert any(call[0:2] == (8767, "layer.ensure") for call in calls)
    assert any(call[0:2] == (8767, "shape.insert_many") and call[-1] is True for call in calls)
    assert any(call[0:2] == (8767, "transfer.pending_set") for call in calls)


def test_bridge_transfer_prepare_accepts_session_aliases(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    for session_id, port in (("klayout-8765", 8765), ("klayout-8767", 8767)):
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({
                "session_id": session_id,
                "host": "127.0.0.1",
                "rpc_port": port,
                "last_seen": 9999999999,
            }),
            encoding="utf-8",
        )

    class TransferClient:
        def __init__(self, host, port, *args, **kwargs):
            self.port = port

        def connect(self):
            pass

        def close(self):
            pass

        def layout_info(self, verbosity="normal"):
            return {"dbu": 0.001}

        def layer_list(self):
            return {"layers": [{"layer_index": 1, "layer": 1, "datatype": 0}]}

        def selection_get(self, **kwargs):
            return {"objects": [{"kind": "shape", "shape": {"type": "box", "layer_index": 1, "bbox_dbu": [0, 0, 1, 1]}}]}

        def layer_ensure(self, layer, datatype=0):
            return {"layer_index": 10}

        def shape_insert_many(self, cell, items, *, dry_run=False):
            return {"inserted": 0 if dry_run else len(items), "dry_run": dry_run}

        def transfer_pending_set(self, package):
            return {"pending": True, "package_id": package["package_id"]}

    monkeypatch.setattr(bridge_mod, "KLinkClient", TransferClient)
    bridge = KLinkMCPBridge(registry_root=tmp_path)
    bridge.call_tool("klink.session_label", {"session_id": "klayout-8765", "label": "source layout", "aliases": ["source"]})
    bridge.call_tool("klink.session_label", {"session_id": "klayout-8767", "label": "target layout", "aliases": ["target"]})

    payload = json.loads(_text(bridge.call_tool("klink.transfer_prepare", {
        "source_session": "source",
        "target_session": "target",
        "target_cell": "TOP",
    })))

    assert payload["ok"] is True
    assert payload["source_session"] == "klayout-8765"
    assert payload["target_session"] == "klayout-8767"


def test_bridge_transfer_commit_writes_only_after_prepare(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    for session_id, port in (("klayout-8765", 8765), ("klayout-8767", 8767)):
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({
                "session_id": session_id,
                "host": "127.0.0.1",
                "rpc_port": port,
                "last_seen": 9999999999,
            }),
            encoding="utf-8",
        )
    writes = []

    class TransferClient:
        def __init__(self, host, port, *args, **kwargs):
            self.port = port

        def connect(self):
            pass

        def close(self):
            pass

        def layout_info(self, verbosity="normal"):
            return {"dbu": 0.001, "active_cell": "TOP"}

        def layer_list(self):
            return {"layers": [{"layer_index": 1, "layer": 1, "datatype": 0}]}

        def selection_get(self, **kwargs):
            return {
                "count": 1,
                "objects": [{
                    "kind": "shape",
                    "shape": {
                        "type": "box",
                        "layer_index": 1,
                        "bbox_dbu": [0, 0, 1000, 1000],
                    },
                }],
            }

        def layer_ensure(self, layer, datatype=0):
            return {"layer_index": 9}

        def shape_insert_many(self, cell, items, *, dry_run=False):
            writes.append((self.port, cell, items, dry_run))
            return {"inserted": 0 if dry_run else len(items), "dry_run": dry_run}

        def transfer_pending_set(self, package):
            return {"pending": True, "package_id": package["package_id"]}

    monkeypatch.setattr(bridge_mod, "KLinkClient", TransferClient)

    bridge = KLinkMCPBridge(registry_root=tmp_path)
    prepare = json.loads(_text(bridge.call_tool("klink.transfer_prepare", {
        "source_session": "klayout-8765",
        "target_session": "klayout-8767",
        "target_cell": "TOP",
    })))
    commit = json.loads(_text(bridge.call_tool("klink.transfer_commit", {
        "package_id": prepare["package_id"],
    })))

    assert commit["ok"] is True
    assert writes[0][0] == 8767
    assert writes[0][3] is True
    assert writes[1][0] == 8767
    assert writes[1][3] is False


def test_bridge_transfer_prepare_shallow_instance_uses_existing_rpc_reads(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    for session_id, port in (("klayout-8765", 8765), ("klayout-8767", 8767)):
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({
                "session_id": session_id,
                "host": "127.0.0.1",
                "rpc_port": port,
                "last_seen": 9999999999,
            }),
            encoding="utf-8",
        )
    calls = []

    class TransferClient:
        def __init__(self, host, port, *args, **kwargs):
            self.port = port

        def connect(self):
            pass

        def close(self):
            pass

        def layout_info(self, verbosity="normal"):
            return {"dbu": 0.001, "active_cell": "TOP"}

        def selection_get(self, **kwargs):
            return {
                "count": 1,
                "objects": [{
                    "cell": "SRC_TOP",
                    "kind": "instance",
                    "is_cell_inst": True,
                    "target_cell": "MZI",
                    "bbox_dbu": [100, 200, 1100, 1200],
                }],
            }

        def instance_query(self, parent, **kwargs):
            calls.append((self.port, "instance.query", parent, kwargs))
            return {
                "instances": [{
                    "child": "MZI",
                    "trans": {"dx_dbu": 1000, "dy_dbu": 2000, "rotation_deg": 90, "mirror": False},
                    "bbox_dbu": [100, 200, 1100, 1200],
                }]
            }

        def cell_list(self, **kwargs):
            calls.append((self.port, "cell.list", kwargs))
            return {"cells": [{"name": "TOP"}, {"name": "MZI"}]}

        def instance_insert_many(self, parent, items, *, dry_run=False):
            calls.append((self.port, "instance.insert_many", parent, items, dry_run))
            return {"inserted": 0 if dry_run else len(items), "dry_run": dry_run}

        def transfer_pending_set(self, package):
            calls.append((self.port, "transfer.pending_set", package["copy_mode"]))
            return {"pending": True, "package_id": package["package_id"]}

    monkeypatch.setattr(bridge_mod, "KLinkClient", TransferClient)

    bridge = KLinkMCPBridge(registry_root=tmp_path)
    payload = json.loads(_text(bridge.call_tool("klink.transfer_prepare", {
        "source_session": "klayout-8765",
        "target_session": "klayout-8767",
        "target_cell": "TOP",
        "copy_mode": "shallow_instance",
        "translate_um": [10, 0],
    })))

    assert payload["ok"] is True
    assert payload["review"]["copy_mode"] == "shallow_instance"
    assert payload["review"]["reused_target_cells"] == ["MZI"]
    assert payload["target_dry_run"]["dry_run"] is True
    assert any(call[0:2] == (8765, "instance.query") for call in calls)
    assert any(call[0:2] == (8767, "cell.list") for call in calls)
    assert any(call[0:2] == (8767, "instance.insert_many") and call[-1] is True for call in calls)
    assert any(call[0:2] == (8767, "transfer.pending_set") for call in calls)


def test_gdsfactory_mcp_tool_reports_interpreter_dependency(monkeypatch, sample_method_specs):
    class ConnectedClient:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            return None

        def methods(self):
            return {"methods": sample_method_specs}

        def close(self):
            pass

    def unavailable(*args, **kwargs):
        raise RuntimeError("gdsfactory is not installed in this Python environment")

    import klink.routing.backends.gdsfactory.gdsfactory_ports as gf_ports

    monkeypatch.setattr(bridge_mod, "KLinkClient", ConnectedClient)
    monkeypatch.setattr(gf_ports, "route_gdsfactory_ports", unavailable)

    bridge = KLinkMCPBridge(profiles=["basic"])
    result = bridge.call_tool("routing.gdsfactory_ports",
                              {"cell": "TOP", "route_layer": "10/0"})

    assert result["isError"] is True
    text = _text(result)
    # The error must name the exact MCP interpreter and the install command
    # so users (and agents) know which environment to fix.
    assert "gdsfactory backend unavailable in the MCP interpreter" in text
    import sys as _sys

    assert _sys.executable in text
    assert 'pip install gdsfactory' in text


def test_bridge_reconnects_on_next_call_after_transport_error(monkeypatch, sample_method_specs):
    clients = []

    class FlakyClient:
        def __init__(self, *args, **kwargs):
            self.index = len(clients)
            self.closed = False
            clients.append(self)

        def connect(self):
            return None

        def methods(self):
            return {"methods": sample_method_specs}

        def call(self, name, arguments, timeout=None):
            if self.index == 0:
                raise KLinkTransportError("first socket broke")
            return {"client_index": self.index, "name": name}

        def close(self):
            self.closed = True

    monkeypatch.setattr(bridge_mod, "KLinkClient", FlakyClient)

    bridge = KLinkMCPBridge(profiles=["basic"])
    first = bridge.call_tool("meta.ping", {})
    second = bridge.call_tool("meta.ping", {})

    assert first["isError"] is True
    assert "first socket broke" in _text(first)
    assert json.loads(_text(second)) == {"client_index": 1, "name": "meta.ping"}
    assert clients[0].closed is True
    assert bridge.status()["connected"] is True
    assert bridge.status()["connect_count"] == 2


def test_bridge_reconnect_tool_refreshes_dynamic_tools(monkeypatch, sample_method_specs):
    attempts = {"count": 0}

    class EventuallyAvailableClient:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise KLinkTransportError("offline")

        def methods(self):
            return {"methods": sample_method_specs}

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", EventuallyAvailableClient)

    bridge = KLinkMCPBridge(profiles=["basic"])
    assert bridge.ensure_connected() is False
    tool_names = {tool["name"] for tool in bridge.list_tools()["tools"]}
    assert {"klink.status", "klink.reconnect", "interaction.selection.recent"}.issubset(
        tool_names
    )

    result = bridge.call_tool("klink.reconnect", {})
    payload = json.loads(_text(result))
    tool_names = {tool["name"] for tool in bridge.list_tools()["tools"]}

    assert payload["ok"] is True
    assert "meta.ping" in tool_names
    assert "layout.info" in tool_names
    assert "klink.status" in tool_names


def test_bridge_records_explicit_selection_sent_events_in_session_memory(monkeypatch, sample_method_specs, tmp_path):
    clients = []

    class EventClient:
        def __init__(self, *args, **kwargs):
            self.handlers = {}
            clients.append(self)

        def connect(self):
            return None

        def methods(self):
            return {"methods": sample_method_specs}

        def on(self, name, handler):
            self.handlers[name] = handler

        def subscribe(self, channels):
            return {"accepted": list(channels)}

        def selection_get(self):
            return {"count": 0, "items": []}

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", EventClient)

    bridge = KLinkMCPBridge(profiles=["basic"], session_id="test-session", context_root=tmp_path)
    assert bridge.ensure_connected() is True

    clients[0].handlers["selection_changed"]({
        "cell": "TOP",
        "count": 1,
        "items": [{"kind": "shape", "layer": 9, "datatype": 0, "bbox_dbu": [0, 0, 1, 1]}],
    })
    assert bridge._context.latest() is None

    clients[0].handlers["selection_sent"]({
        "cell": "TOP",
        "count": 1,
        "items": [{"kind": "shape", "layer": 4, "datatype": 0, "bbox_dbu": [0, 0, 10, 10]}],
    })

    latest = json.loads(_text(bridge.call_tool("interaction.selection.latest", {})))
    assert latest["selection"]["id"] == "sel_0001"
    assert latest["selection"]["capture_reason"] == "selection_sent"
    assert latest["selection"]["layers"] == {"4/0": 1}

    context = json.loads(_text(bridge.call_tool("interaction.context", {})))
    assert context["current_selection"] == {"count": 0, "items": []}
    assert context["recent_selection"]["id"] == "sel_0001"


def test_interaction_tool_reconnects_and_subscribes_after_initial_offline(monkeypatch, sample_method_specs, tmp_path):
    clients = []

    class EventuallyAvailableEventClient:
        attempts = 0

        def __init__(self, *args, **kwargs):
            self.handlers = {}
            clients.append(self)

        def connect(self):
            type(self).attempts += 1
            if type(self).attempts == 1:
                raise KLinkTransportError("offline")

        def methods(self):
            return {"methods": sample_method_specs}

        def on(self, name, handler):
            self.handlers[name] = handler

        def subscribe(self, channels):
            return {"accepted": list(channels)}

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", EventuallyAvailableEventClient)

    bridge = KLinkMCPBridge(profiles=["basic"], session_id="late", context_root=tmp_path)
    assert bridge.ensure_connected() is False

    recent = json.loads(_text(bridge.call_tool("interaction.selection.recent", {})))
    assert recent["subscription_active"] is True
    assert clients[-1].handlers["selection_sent"]

    clients[-1].handlers["selection_sent"]({
        "cell": "TOP",
        "count": 1,
        "items": [{"kind": "shape", "layer": 5, "datatype": 0, "bbox_dbu": [1, 2, 3, 4]}],
    })
    latest = json.loads(_text(bridge.call_tool("interaction.selection.latest", {})))
    assert latest["selection"]["id"] == "sel_0001"
    assert latest["selection"]["layers"] == {"5/0": 1}


def test_bridge_subscribes_to_selection_sent_on_registered_sessions(monkeypatch, sample_method_specs, tmp_path):
    sessions_dir = tmp_path / "registry" / "sessions"
    sessions_dir.mkdir(parents=True)
    for session_id, port in (("klayout-8765", 8765), ("klayout-8766", 8766)):
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({
                "session_id": session_id,
                "host": "127.0.0.1",
                "rpc_port": port,
                "pid": port,
                "last_seen": 9999999999,
            }),
            encoding="utf-8",
        )
    clients = []

    class EventClient:
        def __init__(self, host="127.0.0.1", port=8765, *args, **kwargs):
            self.host = host
            self.port = port
            self.handlers = {}
            clients.append(self)

        def connect(self):
            return self

        def methods(self):
            return {"methods": sample_method_specs}

        def on(self, name, handler):
            self.handlers[name] = handler

        def subscribe(self, channels):
            return {"accepted": list(channels)}

        def close(self):
            pass

    monkeypatch.setattr(bridge_mod, "KLinkClient", EventClient)

    bridge = KLinkMCPBridge(
        profiles=["basic"],
        session_id="multi-events",
        context_root=tmp_path / "context",
        registry_root=tmp_path / "registry",
    )
    try:
        assert bridge.ensure_connected() is True
        status = bridge.status()["session_event_subscriptions"]
        assert status["active"] == ["klayout-8765", "klayout-8766"]

        client_8766 = next(c for c in clients if c.port == 8766)
        client_8766.handlers["selection_sent"]({
            "cell": "TOP",
            "count": 1,
            "items": [{"kind": "shape", "layer": 6, "datatype": 0, "bbox_dbu": [0, 0, 1, 1]}],
        })

        latest = json.loads(_text(bridge.call_tool("interaction.selection.latest", {})))
        assert latest["selection"]["id"] == "sel_0001"
        assert latest["selection"]["klayout_session_id"] == "klayout-8766"
        assert latest["selection"]["klayout_rpc_port"] == 8766
        assert latest["selection"]["klayout_pid"] == 8766
    finally:
        bridge.close()


def test_server_run_does_not_block_before_json_rpc_loop(monkeypatch):
    class FakeBridge:
        def __init__(self):
            self.closed = False

        def ensure_connected(self):
            return False

        def status(self):
            return {"last_error": "offline"}

        def list_tools(self):
            return {"tools": [{"name": "klink.status", "inputSchema": {"type": "object"}}]}

        def call_tool(self, name, arguments):
            return {"content": [{"type": "text", "text": "{}"}]}

        def close(self):
            self.closed = True

    bridge = FakeBridge()
    server = MCPServer(bridge)
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)

    server.run()

    response = json.loads(stdout.getvalue().strip())
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "klink-mcp"
    assert bridge.closed is True
    assert "serving MCP status tools only" in stderr.getvalue()


def test_server_tools_list_returns_builtin_tools_while_disconnected():
    class FakeBridge:
        def ensure_connected(self):
            return False

        def status(self):
            return {"last_error": "offline"}

        def list_tools(self):
            return {"tools": [{"name": "klink.status", "inputSchema": {"type": "object"}}]}

        def call_tool(self, name, arguments):
            raise AssertionError("not used")

    server = MCPServer(FakeBridge())
    response = server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert response["id"] == 2
    assert response["result"]["tools"][0]["name"] == "klink.status"
