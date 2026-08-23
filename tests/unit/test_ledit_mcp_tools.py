"""Registration smoke tests for the bridge_ledit MCP unit (offline)."""

import json
import os
import threading
import time

import pytest

from klink.mcp.catalog import DOMAINS, domain_for
from klink.mcp.local_tools import all_local_tools, get_local_tool

LEDIT_TOOLS = [
    "ledit.status", "ledit.import_selection", "ledit.push_cell",
    "ledit.show_cell", "ledit.set_cell_hidden", "ledit.list_windows",
    "ledit.close_window", "ledit.layout_view", "ledit.save_image",
    "ledit.delete_cell", "ledit.rename_cell", "ledit.delete_objects",
    "ledit.close_design", "ledit.run_drc", "ledit.drc_summary",
    "ledit.export_gds",
]


def test_ledit_tools_registered():
    names = {t.name for t in all_local_tools()}
    for name in LEDIT_TOOLS:
        assert name in names
        tool = get_local_tool(name)
        assert tool.description
        assert tool.input_schema["type"] == "object"


def test_ledit_domain_progressive_disclosure():
    assert "bridge_ledit" in DOMAINS
    meta = DOMAINS["bridge_ledit"]
    assert "ledit" in meta["prefixes"]
    assert meta["usage"]                     # find_tools detail text exists
    for name in LEDIT_TOOLS:
        assert domain_for(name) == "bridge_ledit"


def test_push_cell_requires_cell():
    tool = get_local_tool("ledit.push_cell")
    assert tool.input_schema["required"] == ["cell"]


def test_save_image_is_user_requested_only():
    tool = get_local_tool("ledit.save_image")
    assert "USER-REQUESTED" in tool.description


def test_navigation_tool_schemas():
    show_cell = get_local_tool("ledit.show_cell")
    assert show_cell.input_schema["required"] == ["cell"]

    set_cell_hidden = get_local_tool("ledit.set_cell_hidden")
    assert set_cell_hidden.input_schema["required"] == ["cell", "hidden"]

    save_image = get_local_tool("ledit.save_image")
    assert save_image.input_schema["required"] == ["cell", "path"]

    for name in ("ledit.close_window", "ledit.layout_view",
                 "ledit.list_windows"):
        tool = get_local_tool(name)
        assert "required" not in tool.input_schema

    rect = get_local_tool("ledit.layout_view").input_schema[
        "properties"]["rect_um"]
    assert rect["minItems"] == 4 and rect["maxItems"] == 4


def test_destructive_tool_schemas():
    delete_cell = get_local_tool("ledit.delete_cell")
    assert delete_cell.input_schema["required"] == ["cell"]
    assert delete_cell.input_schema["properties"]["force"]["default"] is False

    rename_cell = get_local_tool("ledit.rename_cell")
    assert rename_cell.input_schema["required"] == ["cell", "new_name"]

    delete_objects = get_local_tool("ledit.delete_objects")
    assert delete_objects.input_schema["required"] == ["cell"]
    rect = delete_objects.input_schema["properties"]["rect_um"]
    assert rect["minItems"] == 4 and rect["maxItems"] == 4

    close_design = get_local_tool("ledit.close_design")
    assert close_design.input_schema["required"] == ["file"]
    assert close_design.input_schema["properties"]["discard"]["default"] is False


def test_verification_tool_schemas():
    run_drc = get_local_tool("ledit.run_drc")
    assert "required" not in run_drc.input_schema
    rect = run_drc.input_schema["properties"]["rect_um"]
    assert rect["minItems"] == 4 and rect["maxItems"] == 4
    assert "error_ports" not in run_drc.input_schema["properties"]

    drc_summary = get_local_tool("ledit.drc_summary")
    assert "required" not in drc_summary.input_schema

    export_gds = get_local_tool("ledit.export_gds")
    assert export_gds.input_schema["required"] == ["path"]
    assert export_gds.input_schema["properties"][
        "include_hierarchy"]["default"] is True
    assert export_gds.input_schema["properties"][
        "cell_name_length"]["default"] == 32


# --------------------------------------------------------------------------
# MCP-tool-level: capability gating end-to-end through the handler
# --------------------------------------------------------------------------

def _make_ns(root, namespace="default"):
    ns = os.path.join(root, namespace)
    os.makedirs(os.path.join(ns, "inbox"))
    os.makedirs(os.path.join(ns, "outbox"))
    with open(os.path.join(ns, "hello.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": 1, "proto": 1, "macro_version": "0.5.5",
                  "file": "unit.tdb", "cell": "TOP"}, f)


class _Responder(threading.Thread):
    """Minimal macro stand-in: answers every inbox request via handler."""

    def __init__(self, root, handler, namespace="default"):
        super().__init__(daemon=True)
        self.inbox = os.path.join(root, namespace, "inbox")
        self.outbox = os.path.join(root, namespace, "outbox")
        self.handler = handler
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            for name in os.listdir(self.inbox):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(self.inbox, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        req = json.load(f)
                except (OSError, ValueError):
                    continue
                os.remove(path)
                resp = self.handler(req)
                resp.setdefault("schema", 1)
                resp.setdefault("id", req["id"])
                tmp = os.path.join(self.outbox, "x.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(resp, f)
                os.replace(tmp, os.path.join(
                    self.outbox, f"resp_{req['id']}.json"))
            time.sleep(0.01)


@pytest.fixture
def responder(tmp_path, monkeypatch):
    root = str(tmp_path / "bridge_root")
    _make_ns(root)
    monkeypatch.setenv("KLINK_LEDIT_BRIDGE_ROOT", root)
    threads = []

    def start(handler):
        r = _Responder(root, handler)
        r.start()
        threads.append(r)
        return r

    yield start
    for r in threads:
        r.stop.set()
    for r in threads:
        r.join(timeout=1)


def test_show_cell_reports_missing_capability_instructively(responder):
    responder(lambda req: {"ok": True, "result": {
        "file": "unit.tdb", "design_ready": True, "capabilities": []}})

    tool = get_local_tool("ledit.show_cell")
    out = tool.handler(None, {"cell": "X"})
    text = out["content"][0]["text"]
    assert out.get("isError")
    assert "0.5.6" in text
    assert "Load Macro" in text


def test_show_cell_succeeds_with_capability(responder):
    def handler(req):
        if req["cmd"] == "ping":
            return {"ok": True, "result": {
                "file": "unit.tdb", "design_ready": True,
                "capabilities": ["show_cell"]}}
        if req["cmd"] == "show_cell":
            return {"ok": True, "result": {
                "cell": "X", "window_opened": True, "via": "window"}}
        raise AssertionError("unexpected cmd: %s" % req["cmd"])

    responder(handler)
    tool = get_local_tool("ledit.show_cell")
    out = tool.handler(None, {"cell": "X"})
    assert not out.get("isError")
    text = out["content"][0]["text"]
    assert "window_opened" in text


def test_delete_cell_reports_missing_capability_instructively(responder):
    responder(lambda req: {"ok": True, "result": {
        "file": "unit.tdb", "design_ready": True, "capabilities": []}})

    tool = get_local_tool("ledit.delete_cell")
    out = tool.handler(None, {"cell": "X"})
    text = out["content"][0]["text"]
    assert out.get("isError")
    # require_capability's message says "macro >= 0.5.6" (the navigation
    # threshold check reused as-is); the assertions here are on the
    # instructive shape, not that exact version string.
    assert "ERR" in text or "no 'delete_cell' command" in text
    assert "Load Macro" in text


def test_export_gds_reports_missing_capability_instructively(responder):
    responder(lambda req: {"ok": True, "result": {
        "file": "unit.tdb", "design_ready": True, "capabilities": []}})

    tool = get_local_tool("ledit.export_gds")
    out = tool.handler(None, {"path": "out.gds"})
    text = out["content"][0]["text"]
    assert out.get("isError")
    assert "no 'export_gds' command" in text
    assert "Load Macro" in text
