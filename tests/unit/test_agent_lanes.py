"""Consistency checks for klink/mcp/agents/lanes.json.

The lanes file is the harness-neutral source of truth for sub-agent tool
allowlists. These tests keep it honest against the real tool surface:
bridge-local tools (klink.mcp.local_tools) and server @method names parsed
statically from the plugin source (no pya import needed).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LANES_PATH = REPO / "klink" / "mcp" / "agents" / "lanes.json"
METHODS_DIR = REPO / "klink_plugin" / "python" / "klink_server" / "methods"


@pytest.fixture(scope="module")
def lanes() -> dict:
    return json.loads(LANES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def server_method_names() -> set:
    names = set()
    for path in METHODS_DIR.glob("*_m.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fname = getattr(func, "id", None) or getattr(func, "attr", None)
            if fname != "method" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
    assert len(names) > 50, "expected to parse the server method catalogue"
    return names


def _expand_lane_tools(lanes_doc: dict, lane: dict) -> list:
    shared = lanes_doc.get("shared_tool_sets", {})
    out = []
    for name in lane.get("mcp_tools", []):
        if name.startswith("@"):
            out.extend(shared[name[1:]])
        else:
            out.append(name)
    return out


def test_lanes_file_has_expected_lanes(lanes):
    assert set(lanes["lanes"]) == {
        "main",
        "layout-verify",
        "layout-route",
        "layout-build",
        "pya-exec",
    }


def test_all_lane_tools_exist(lanes, server_method_names):
    from klink.mcp.local_tools import all_local_tools

    # Importing the bridge registers its @local_tool decorators.
    import klink.mcp.bridge  # noqa: F401

    local_names = {tool.name for tool in all_local_tools()}
    known = server_method_names | local_names
    for lane_name, lane in lanes["lanes"].items():
        unknown = [t for t in _expand_lane_tools(lanes, lane) if t not in known]
        assert not unknown, f"lane {lane_name} references unknown tools: {unknown}"


def test_no_removed_context_aliases(lanes):
    text = LANES_PATH.read_text(encoding="utf-8")
    assert "interaction.context." not in text, (
        "interaction.context.* aliases were removed from the bridge in "
        "2026-06; lanes.json must use canonical interaction.selection.* names"
    )


def test_read_only_lanes_have_no_write_tools(lanes, server_method_names):
    write_markers = (
        "insert",
        "delete",
        "create",
        "rename",
        "mark",
        "update",
        "transform",
        "clear",
        "exec.",
        "set_layer",
        "repair",
    )
    for lane_name, lane in lanes["lanes"].items():
        if not lane.get("read_only"):
            continue
        tools = _expand_lane_tools(lanes, lane)
        bad = [t for t in tools if any(m in t for m in write_markers)]
        assert not bad, f"read-only lane {lane_name} has write tools: {bad}"
