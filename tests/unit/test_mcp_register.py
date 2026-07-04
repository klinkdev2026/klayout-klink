"""Lock `klink-mcp --register` output: the config snippets must be valid JSON
even when the interpreter path has Windows backslashes, Zed must use the flat
`command` string (not the old nested `command.path`), and the whole thing must
be ASCII so it survives a non-UTF-8 console."""
import json
import re
import sys

from klink.mcp.__main__ import _register_snippets


def _block(out: str, top_key: str) -> str:
    m = re.search(r'\{\s*"' + top_key + r'".*?\n\}', out, re.S)
    assert m, f"no {top_key!r} block in --register output"
    return m.group(0)


def test_json_blocks_valid_with_windows_path(monkeypatch):
    # A Windows path: its backslashes MUST be escaped in JSON/TOML or the block
    # is invalid (a raw C:\... has bad \escapes). This is the bug the snippet
    # builder guards by routing paths through json.dumps.
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\klink venv\Scripts\python.exe")
    out = _register_snippets("read,write,verify,escape", "project-klink")

    # standard mcpServers (Cursor/Windsurf/Claude Desktop) + VS Code "servers"
    mcp = json.loads(_block(out, "mcpServers"))
    assert mcp["mcpServers"]["klayout"]["command"].endswith("python.exe")
    assert "-m" in mcp["mcpServers"]["klayout"]["args"]
    json.loads(_block(out, "servers"))  # VS Code block must parse too


def test_zed_block_is_flat_command_string(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    out = _register_snippets("read,write", "s")
    # current Zed schema: {"command": "<str>", "args": [...]} -- NOT the old
    # nested {"command": {"path": ...}}
    assert '"context_servers"' in out
    assert '"command": {' not in out


def test_output_is_ascii(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    # em dashes / smart punctuation turn into mojibake on a gbk/cp1252 console
    _register_snippets("read,write", "s").encode("ascii")
