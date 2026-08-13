"""Registration smoke tests for the bridge_ledit MCP unit (offline)."""

from klink.mcp.catalog import DOMAINS, domain_for
from klink.mcp.local_tools import all_local_tools, get_local_tool

LEDIT_TOOLS = ["ledit.status", "ledit.import_selection", "ledit.push_cell"]


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
