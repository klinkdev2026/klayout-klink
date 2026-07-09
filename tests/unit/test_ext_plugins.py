"""klink.plugins entry-point extension mechanism (klink/ext.py + MCP wiring).

Acceptance from the design issue: a fake distribution registers a tool and a
named profile; find_tools lists the tool under its domain; removing the
distribution removes everything; a broken package degrades to a recorded
failure naming it — never a crashed server; zero installed extensions means
a byte-identical tool list.
"""
from __future__ import annotations

import json
import types

import pytest

import importlib.metadata as importlib_metadata

from klink import ext
from klink.mcp.bridge import KLinkMCPBridge


class _FakeEP:
    def __init__(self, name, value, register, dist_name):
        self.name = name
        self.value = value
        self._register = register
        self.dist = types.SimpleNamespace(metadata={"Name": dist_name})

    def load(self):
        if isinstance(self._register, Exception):
            raise self._register
        return self._register


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    ext.reset_for_tests()
    yield
    ext.reset_for_tests()


def _install(monkeypatch, *eps):
    def fake_entry_points(group=None):
        assert group == ext.ENTRY_POINT_GROUP
        return list(eps)
    monkeypatch.setattr(importlib_metadata, "entry_points", fake_entry_points)
    ext.reset_for_tests()


def _tool_names(bridge) -> set:
    return {t["name"] for t in bridge.list_tools()["tools"]}


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


# ---------------------------------------------------------------------------


def test_zero_plugins_zero_impact(monkeypatch):
    bridge = KLinkMCPBridge(profiles=["read", "write", "verify", "escape"])
    baseline = _tool_names(bridge)
    _install(monkeypatch)          # explicit empty scan
    assert _tool_names(bridge) == baseline
    assert ext.discover().failures == []


def _good_plugin_ep():
    marker = {}

    def handler(ctx, arguments):
        marker["called"] = arguments
        return {"greeting": f"hello {arguments.get('who', 'world')}"}

    def register(hook):
        hook.add_domain("acme_pdk", title="ACME PDK",
                        summary="synthetic vendor extension",
                        usage="call acme_pdk.hello first.")
        hook.add_tool("acme_pdk.hello", handler,
                      description="say hello from the ACME extension",
                      input_schema={"type": "object",
                                    "properties": {"who": {"type": "string"}}},
                      domain="acme_pdk")
        hook.add_profile("acme_2m", {"routing_layers": ["1/0", "2/0"]})
        hook.add_recipe("acme_terms", {"kind": "recipe"})

    return _FakeEP("acme", "acme_pkg.klink_ext:register", register,
                   "acme-pdk-klink"), marker


def test_fake_distribution_full_loop(monkeypatch):
    ep, marker = _good_plugin_ep()
    _install(monkeypatch, ep)
    bridge = KLinkMCPBridge(profiles=["read", "write", "verify", "escape"])

    # listed among tools
    assert "acme_pdk.hello" in _tool_names(bridge)

    # find_tools: index shows the extension domain with its count
    idx = _payload(bridge.call_tool("klink.find_tools", {}))
    dom = {d["domain"]: d for d in idx["domains"]}
    assert dom["acme_pdk"]["tool_count"] == 1
    assert dom["acme_pdk"]["title"] == "ACME PDK"

    # find_tools: domain detail carries the extension's usage text
    det = _payload(bridge.call_tool("klink.find_tools", {"domain": "acme_pdk"}))
    assert det["tools"][0]["name"] == "acme_pdk.hello"
    assert "acme_pdk.hello first" in det["domain_usage"]["usage"]

    # dispatch: handler runs with (ctx, arguments), result JSON-wrapped
    out = _payload(bridge.call_tool("acme_pdk.hello", {"who": "klink"}))
    assert out == {"greeting": "hello klink"}
    assert marker["called"] == {"who": "klink"}

    # named resources resolvable
    assert ext.get_resource("profile", "acme_2m")["routing_layers"] == ["1/0", "2/0"]
    assert ext.list_resources("recipe") == {"recipe": ["acme_terms"]}

    # status block
    s = bridge.status()["extensions"]
    assert s["failures"] == []
    assert s["installed"][0]["package"] == "acme-pdk-klink"
    assert "acme_pdk.hello" in s["installed"][0]["tools"]

    # "uninstall": scan again with no eps -> everything gone
    _install(monkeypatch)
    assert "acme_pdk.hello" not in _tool_names(bridge)
    with pytest.raises(KeyError) as e:
        ext.get_resource("profile", "acme_2m")
    assert "install the package" in str(e.value)


def test_broken_plugin_isolated_and_rolled_back(monkeypatch):
    def bad_register(hook):
        hook.add_tool("bad_pkg.partial", lambda c, a: {}, description="x")
        raise RuntimeError("boom during register")

    bad = _FakeEP("bad", "bad_pkg.klink_ext:register", bad_register, "bad-pkg")
    good, _ = _good_plugin_ep()
    _install(monkeypatch, bad, good)

    bridge = KLinkMCPBridge(profiles=["read"])
    names = _tool_names(bridge)
    assert "acme_pdk.hello" in names          # good one unaffected
    assert "bad_pkg.partial" not in names     # partial contribution rolled back

    fails = ext.failures()
    assert len(fails) == 1
    assert fails[0]["package"] == "bad-pkg"
    assert "boom during register" in fails[0]["error"]


def test_reserved_prefix_and_import_error_become_failures(monkeypatch):
    def grabby(hook):
        hook.add_tool("view.hijack", lambda c, a: {}, description="nope")

    eps = [
        _FakeEP("grabby", "g.klink_ext:register", grabby, "grabby-pkg"),
        _FakeEP("dead", "dead_pkg.klink_ext:register",
                ImportError("No module named 'dead_pkg'"), "dead-pkg"),
    ]
    _install(monkeypatch, *eps)
    reg = ext.discover()
    assert reg.tools == {}
    packages = {f["package"] for f in reg.failures}
    assert packages == {"grabby-pkg", "dead-pkg"}
    assert any("reserved prefix" in f["error"] for f in reg.failures)


def test_raising_handler_yields_instructive_error(monkeypatch):
    def register(hook):
        hook.add_tool("frail_pkg.boom",
                      lambda c, a: (_ for _ in ()).throw(ValueError("inner")),
                      description="always raises")

    _install(monkeypatch,
             _FakeEP("frail", "frail_pkg.klink_ext:register", register,
                     "frail-pkg"))
    bridge = KLinkMCPBridge(profiles=["read"])
    res = bridge.call_tool("frail_pkg.boom", {})
    text = res["content"][0]["text"]
    assert res.get("isError")
    assert "frail-pkg" in text and "ValueError" in text


def test_domain_only_profile_filters_extension_tools(monkeypatch):
    ep, _ = _good_plugin_ep()
    _install(monkeypatch, ep)
    focused = KLinkMCPBridge(profiles=["acme_pdk"])
    names = _tool_names(focused)
    assert "acme_pdk.hello" in names
    other = KLinkMCPBridge(profiles=["routing_backends"])
    assert "acme_pdk.hello" not in _tool_names(other)
