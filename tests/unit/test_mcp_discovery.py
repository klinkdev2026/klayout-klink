"""Tests for klink.find_tools discovery + domain/intent profiles.

See klink/mcp/catalog.py.
"""
from __future__ import annotations

import json

from klink.mcp.bridge import KLinkMCPBridge
from klink.mcp.catalog import domain_tokens
from klink.mcp.local_tools import all_local_tools
from klink.mcp.profiles import filter_methods


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _bridge(profiles, specs):
    """Bridge with plugin RPCs injected offline (no KLayout)."""
    bridge = KLinkMCPBridge(profiles=profiles)
    bridge._tools = filter_methods(specs, bridge._profiles)
    bridge._method_specs = {s["name"]: s for s in specs}
    return bridge


def test_find_tools_is_registered():
    assert "klink.find_tools" in {t.name for t in all_local_tools()}


def test_find_tools_index_mode(sample_method_specs):
    bridge = _bridge(["read", "write"], sample_method_specs)
    out = json.loads(_text(bridge.call_tool("klink.find_tools", {})))
    assert out["mode"] == "index"
    assert {d["domain"] for d in out["domains"]} == set(domain_tokens())
    assert out["total_tools"] == len(bridge.list_tools()["tools"])


def test_find_tools_domain_mode_returns_usage(sample_method_specs):
    bridge = _bridge(["all"], sample_method_specs)
    out = json.loads(_text(bridge.call_tool("klink.find_tools", {"domain": "device_photonics"})))
    assert out["mode"] == "search"
    assert out["domain_usage"]["domain"] == "device_photonics"
    assert out["domain_usage"]["usage"]
    assert {t["name"] for t in out["tools"]} == {
        "port.harvest_blackbox", "photonics.import_gf",
        "photonics.connect", "photonics.reroute",
    }


def test_find_tools_query_ranks_without_usage_dump(sample_method_specs):
    bridge = _bridge(["all"], sample_method_specs)
    out = json.loads(_text(bridge.call_tool("klink.find_tools", {"query": "reroute"})))
    assert out["mode"] == "search"
    assert "photonics.reroute" in {t["name"] for t in out["tools"]}
    # broad query returns represented domain tokens, NOT every domain's usage
    assert "domains_represented" in out
    assert "domain_usage" not in out


def test_find_tools_unknown_domain_errors(sample_method_specs):
    bridge = _bridge(["all"], sample_method_specs)
    result = bridge.call_tool("klink.find_tools", {"domain": "does_not_exist"})
    assert result["isError"] is True
    assert "unknown domain" in _text(result)


def test_domain_only_profile_focuses_local_tools(sample_method_specs):
    bridge = _bridge(["device_photonics"], sample_method_specs)
    names = {t["name"] for t in bridge.list_tools()["tools"]}
    assert {"port.harvest_blackbox", "photonics.connect", "photonics.reroute"} <= names
    # always-on core survives a domain-only profile
    assert {"klink.find_tools", "klink.status", "klink.reconnect"} <= names
    # other-domain local tools and out-of-domain plugin RPCs are filtered out
    assert "structdevice.lvs_check" not in names
    assert "meta.ping" not in names


def test_legacy_profile_aliases_match_modern_names(sample_method_specs):
    legacy = {t["name"] for t in _bridge(["basic", "draw", "drc", "advanced"], sample_method_specs).list_tools()["tools"]}
    modern = {t["name"] for t in _bridge(["read", "write", "verify", "escape"], sample_method_specs).list_tools()["tools"]}
    assert legacy == modern
