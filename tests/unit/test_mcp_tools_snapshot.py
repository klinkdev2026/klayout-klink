"""Characterization lock for the klink-mcp tool surface.

`tests/fixtures/mcp/` holds a captured snapshot of the live `tools/list`
output plus the raw `meta.methods` specs it was built from. These tests
reconstruct `bridge.list_tools()` OFFLINE (no KLayout) by feeding the captured
raw specs through the exact production pipeline (`filter_methods` +
`_to_mcp_tool` + local tools) and assert it matches the golden snapshot
tool-for-tool: same set of tool NAMES, and for each name an identical
description + inputSchema.

The contract this oracle locks is "names + inputSchema". It is intentionally
order-INSENSITIVE: tools/list order is not part of the MCP contract (clients
address tools by name), and moving handlers between modules reshuffles
local-tool registration order while the same handlers register. Comparing by
name keeps the oracle strict on every name add/remove and every schema or
description change. When the tool surface changes intentionally, regenerate
the fixtures and update this test's intent.

Regenerate: `python tests/fixtures/mcp/regen_snapshot.py` (needs KLayout +
plugin live on port 8765).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from klink.mcp.bridge import KLinkMCPBridge
from klink.mcp.profiles import filter_methods

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "mcp"

DEFAULT_PROFILE = ["read", "write", "verify", "escape"]


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw_specs() -> list[dict]:
    return _load("raw_method_specs.json")


def _reproduce(raw_specs: list[dict], profiles: list[str]) -> dict:
    """Reproduce list_tools() exactly as production does, offline."""
    bridge = KLinkMCPBridge(profiles=list(profiles))
    bridge._tools = filter_methods(raw_specs, bridge._profiles)
    bridge._method_specs = {s["name"]: s for s in raw_specs}
    return bridge.list_tools()


@pytest.mark.parametrize(
    "profiles, snapshot_file",
    [
        (DEFAULT_PROFILE, "tools_snapshot_default.json"),
        (["all"], "tools_snapshot_all.json"),
    ],
)
def test_list_tools_matches_snapshot(raw_specs, profiles, snapshot_file):
    golden = _load(snapshot_file)
    got = _reproduce(raw_specs, profiles)

    golden_by_name = {t["name"]: t for t in golden["tools"]}
    got_by_name = {t["name"]: t for t in got["tools"]}

    missing = sorted(set(golden_by_name) - set(got_by_name))
    extra = sorted(set(got_by_name) - set(golden_by_name))
    assert not missing and not extra, (
        f"tools/list name set drifted from {snapshot_file}: "
        f"missing={missing} extra={extra}. If this is an intentional "
        f"change, regenerate via tests/fixtures/mcp/regen_snapshot.py."
    )
    changed = [n for n in golden_by_name if got_by_name[n] != golden_by_name[n]]
    assert not changed, (
        f"tools/list description/inputSchema drifted from {snapshot_file} for "
        f"{changed}. If intentional, regenerate via "
        f"tests/fixtures/mcp/regen_snapshot.py."
    )


def test_snapshot_tool_names_are_unique_and_nonempty():
    golden = _load("tools_snapshot_all.json")
    names = [t["name"] for t in golden["tools"]]
    assert names, "snapshot has no tools"
    assert len(names) == len(set(names)), "duplicate tool names in snapshot"
    assert all(t.get("inputSchema") for t in golden["tools"]), "tool missing inputSchema"
