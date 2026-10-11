"""MCP registration of the toolbar-tool RPCs (tool.run / tool.list / tool.get /
buttons.list / buttons.reload) across the three layers: catalog domain
``tools``, the intent profiles, and the agent lanes. The plugin tags these
``tools`` / ``buttons`` (not read/write), so without the explicit profile rule
they would be silently dropped from every intent profile -- exactly the gap
this guards. Specs are parsed statically from the plugin source (no pya).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from klink.mcp.bridge import KLinkMCPBridge
from klink.mcp.catalog import DOMAINS, domain_for, domain_tokens
from klink.mcp.profiles import filter_methods

REPO = Path(__file__).resolve().parents[2]
METHODS_DIR = REPO / "klink_plugin" / "python" / "klink_server" / "methods"
LANES = REPO / "klink" / "mcp" / "agents" / "lanes.json"

READ_TOOLS = {"tool.list", "tool.get", "buttons.list"}
WRITE_TOOLS = {"tool.run", "buttons.reload"}
ALL_TOOLS = READ_TOOLS | WRITE_TOOLS


def _plugin_specs() -> list[dict]:
    """meta.methods-like specs for the five RPCs, from the plugin source."""
    specs = []
    for fname in ("tools_m.py", "buttons_m.py"):
        tree = ast.parse((METHODS_DIR / fname).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "method" and node.args):
                continue
            name = node.args[0].value
            kw = {k.arg: k.value for k in node.keywords}
            tags = ast.literal_eval(kw["tags"]) if "tags" in kw else []
            schema = ast.literal_eval(kw["params_schema"]) if "params_schema" in kw else {"type": "object"}
            specs.append({"name": name, "description": name, "params": schema,
                          "tags": tags, "mutates": "mutates" in kw})
    return specs


@pytest.fixture(scope="module")
def specs() -> list[dict]:
    got = _plugin_specs()
    assert {s["name"] for s in got} == ALL_TOOLS, (
        "plugin tool/button RPC set changed; update the tools domain, "
        "profiles._TOOLS_READ/_TOOLS_WRITE and lanes.json")
    return got


def test_tools_domain_declared_and_maps_every_rpc():
    assert "tools" in DOMAINS and "tools" in domain_tokens()
    meta = DOMAINS["tools"]
    assert set(meta["prefixes"]) == {"tool", "buttons"}
    usage = meta["usage"]
    # the facts an agent in a user project cannot find anywhere else
    for needle in ("klink.cli tool install", "klink.tools.install",
                   "interactive=false", "recipes/tools.md", "example_template/tools/"):
        assert needle in usage, needle
    for name in ALL_TOOLS:
        assert domain_for(name) == "tools"


def test_intent_axis_splits_read_and_write(specs):
    read = {s["name"] for s in filter_methods(specs, ["read"])}
    write = {s["name"] for s in filter_methods(specs, ["write"])}
    assert read == READ_TOOLS
    assert write == WRITE_TOOLS
    default = {s["name"] for s in filter_methods(specs, ["read", "write", "verify", "escape"])}
    assert default == ALL_TOOLS
    assert {s["name"] for s in filter_methods(specs, ["tools"])} == ALL_TOOLS
    assert {s["name"] for s in filter_methods(specs, ["verify", "escape"])} == set()


def test_mutating_rpcs_are_the_write_ones(specs):
    # tool.run is the only one declared mutates=True in the plugin; it must
    # never land in a read-only profile.
    mutating = {s["name"] for s in specs if s["mutates"]}
    assert mutating <= WRITE_TOOLS


def test_find_tools_domain_tools_lists_all_five(specs):
    bridge = KLinkMCPBridge(profiles=["read", "write", "verify", "escape"])
    bridge._tools = filter_methods(specs, bridge._profiles)
    bridge._method_specs = {s["name"]: s for s in specs}
    res = bridge.call_tool("klink.find_tools", {"domain": "tools"})
    out = json.loads(res["content"][0]["text"])
    assert out["domain_usage"]["domain"] == "tools"
    assert {t["name"] for t in out["tools"]} == ALL_TOOLS


def test_lanes_allow_tools():
    lanes = json.loads(LANES.read_text(encoding="utf-8"))["lanes"]
    assert ALL_TOOLS <= set(lanes["main"]["mcp_tools"])
    assert {"tool.run", "tool.list", "tool.get"} <= set(lanes["layout-build"]["mcp_tools"])
    for name, lane in lanes.items():
        if lane.get("read_only"):
            assert not (WRITE_TOOLS & set(lane["mcp_tools"])), name


def test_claude_build_adapter_mirrors_lane():
    adapter = REPO / ".claude" / "agents" / "layout-build.md"
    if not adapter.is_file():  # dev-only Claude Code adapter; the public tree ships lanes.json only
        pytest.skip("dev-only .claude/agents adapter not present")
    text = adapter.read_text(encoding="utf-8-sig")
    for name in ("tool_run", "tool_list", "tool_get"):
        assert f"mcp__klayout__{name}" in text
