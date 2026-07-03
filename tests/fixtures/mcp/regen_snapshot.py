"""Regenerate the klink-mcp tools/list characterization snapshot.

Run against LIVE KLayout + klink plugin (port 8765):

    python tests/fixtures/mcp/regen_snapshot.py

Captures the raw `meta.methods` specs and the golden `tools/list` output for
the default profile (`read,write,verify,escape`) and the `all` profile, and
proves the offline reproduction pipeline (`filter_methods` + `_to_mcp_tool` +
local tools) matches the live output byte-for-byte. Consumed by
`tests/unit/test_mcp_tools_snapshot.py` (the byte-parity oracle).

Only regenerate when the change to tools/list is INTENTIONAL. A surprise diff
here means a change altered a tool name or schema — investigate before
regenerating.
"""
from __future__ import annotations

import json
from pathlib import Path

from klink.mcp.bridge import KLinkMCPBridge
from klink.mcp.profiles import filter_methods

FIX = Path(__file__).resolve().parent
DEFAULT_PROFILE = ["read", "write", "verify", "escape"]


def reproduce(raw_specs, profiles):
    """Reproduce list_tools() offline, exactly as production does."""
    bridge = KLinkMCPBridge(profiles=list(profiles))
    bridge._tools = filter_methods(raw_specs, bridge._profiles)
    bridge._method_specs = {s["name"]: s for s in raw_specs}
    return bridge.list_tools()


def _write(name: str, payload) -> None:
    (FIX / name).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    live = KLinkMCPBridge(profiles=DEFAULT_PROFILE, port=8765)
    if not live.ensure_connected():
        raise SystemExit(
            f"cannot connect to KLayout on 8765: {live.status()['last_error']}"
        )
    raw_specs = live._client.methods()["methods"]
    live_default = live.list_tools()
    live.close()

    all_bridge = KLinkMCPBridge(profiles=["all"], port=8765)
    all_bridge.ensure_connected()
    live_all = all_bridge.list_tools()
    all_bridge.close()

    assert reproduce(raw_specs, DEFAULT_PROFILE) == live_default, (
        "offline reproduction differs from live (default profile)"
    )
    assert reproduce(raw_specs, ["all"]) == live_all, (
        "offline reproduction differs from live (all profile)"
    )

    _write("raw_method_specs.json", raw_specs)
    _write("tools_snapshot_default.json", live_default)
    _write("tools_snapshot_all.json", live_all)

    print(f"raw specs: {len(raw_specs)}")
    print(f"default tools/list: {len(live_default['tools'])}")
    print(f"all tools/list: {len(live_all['tools'])}")
    print("offline reproduction == live: OK")


if __name__ == "__main__":
    main()
