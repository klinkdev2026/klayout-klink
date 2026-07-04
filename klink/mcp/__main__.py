"""
Entry point for ``klink-mcp``.

Start an MCP server that bridges Claude Code (or any MCP client)
to a running KLayout/klink instance.

Usage::

    klink-mcp --profile read,write
    python -m klink.mcp --profile read,write,verify,escape
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .bridge import KLinkMCPBridge
from .config import (
    DEFAULT_CALL_TIMEOUT,
    DEFAULT_KLINK_HOST,
    DEFAULT_KLINK_PORT,
    DEFAULT_LONG_CALL_TIMEOUT,
)
from .server import MCPServer

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_SKILL_NAMES = ["klayout", "klayout-gdsfactory"]
_PKG_DIR = Path(__file__).resolve().parent


def _install_skills(target_dir: str) -> None:
    """Copy skill directories from the package to *target_dir*."""
    dest = Path(target_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for name in _SKILL_NAMES:
        src_dir = _SKILLS_DIR / name
        dst_dir = dest / name
        if src_dir.is_dir():
            if dst_dir.exists():
                shutil.rmtree(dst_dir, ignore_errors=True)
            shutil.copytree(src_dir, dst_dir)
            print(f"  installed: {dst_dir}")
        else:
            print(f"  WARNING: skill not found: {src_dir}")
    print(f"Skills installed to {dest}")


def _install_claude_md(target_dir: str) -> None:
    """Install CLAUDE.md into *target_dir*, WITHOUT clobbering an existing one.

    A `klink init` project already ships its own project-level CLAUDE.md (read
    AGENTS.md, obey the project's pdk.py boundary); `--setup` must not overwrite
    it. Only write when absent.
    """
    src = _PKG_DIR / "CLAUDE.md"
    dest = Path(target_dir).resolve() / "CLAUDE.md"
    if not src.exists():
        print(f"WARNING: CLAUDE.md not found at {src}")
        return
    if dest.exists():
        print(f"CLAUDE.md already present at {dest}; leaving it untouched "
              "(a klink init project keeps its own). The packaged MCP operating "
              "rules live at klink/mcp/CLAUDE.md if you want to merge them.")
        return
    shutil.copy2(src, dest)
    print(f"CLAUDE.md installed to {dest}")


def _register_snippets(profile: str, session_id: str | None) -> str:
    """Build copy-paste MCP-registration commands/snippets for common agents,
    with THIS interpreter's path filled in (the #1 thing agents get wrong is
    which Python has klink). ``sys.executable`` is that interpreter.

    JSON/TOML strings go through ``json.dumps`` so a Windows path's backslashes
    are escaped correctly (a raw ``D:\\...`` is invalid JSON); the CLI lines use
    the raw path, which is what a shell wants. Output is ASCII-only to survive
    non-UTF-8 consoles.
    """
    import json

    py = sys.executable
    sess = session_id or "project-klink"
    mcp_args = ["-m", "klink.mcp", "--profile", profile, "--session-id", sess]
    cli_tail = "%s %s" % (py, " ".join(mcp_args))     # raw path: shell wants it
    cmd_j = json.dumps(py)                             # escaped: JSON/TOML want it
    args_j = json.dumps(mcp_args)
    json_block = (
        '{\n'
        '  "mcpServers": {\n'
        '    "klayout": {\n'
        '      "command": %s,\n'
        '      "args": %s\n'
        '    }\n'
        '  }\n'
        '}' % (cmd_j, args_j))
    vscode_block = (
        '{\n'
        '  "servers": {\n'
        '    "klayout": { "command": %s, "args": %s }\n'
        '  }\n'
        '}' % (cmd_j, args_j))
    zed_block = (
        '"context_servers": {\n'
        '  "klayout": { "command": %s, "args": %s, "env": {} }\n'
        '}' % (cmd_j, args_j))
    toml_block = (
        '[mcp_servers.klayout]\n'
        'command = %s\n'
        'args = %s' % (cmd_j, args_j))
    return "\n".join([
        "# klink MCP registration -- launches klink-mcp from THIS Python (has klink):",
        "#   %s" % py,
        "# After adding, RESTART your agent so it loads the new MCP server.",
        "# (KLayout must be running with the klink plugin; port 8765.)",
        "",
        "## Claude Code (CLI)",
        "claude mcp add klayout -- %s" % cli_tail,
        "",
        "## Codex (CLI)",
        "codex mcp add klayout -- %s" % cli_tail,
        "",
        "## Cursor / Windsurf / Claude Desktop (standard mcpServers JSON)",
        "## -- paste this block into the tool's MCP config file:",
        "#   Cursor:         ~/.cursor/mcp.json   (or project .cursor/mcp.json)",
        "#   Windsurf:       ~/.codeium/windsurf/mcp_config.json",
        "#   Claude Desktop: claude_desktop_config.json",
        "#   Other MCP agents (Trae, Cline, ...): same block -- see the agent's",
        "#   own docs for where its MCP config lives / how to add manually.",
        json_block,
        "",
        '## VS Code (.vscode/mcp.json -- note the "servers" key, not "mcpServers")',
        vscode_block,
        "",
        '## Zed (settings.json -- "context_servers")',
        zed_block,
        "",
        "## Codex config-file alternative (~/.codex/config.toml)",
        toml_block,
        "",
        "# Add gdsfactory to the SAME Python for photonics: "
        'pip install "klayout-klink[photonics]"',
    ])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="klink-mcp",
        description="MCP server bridging Claude Code to KLayout/klink",
    )
    ap.add_argument(
        "--profile",
        default="read,write,verify,escape",
        help=(
            "comma-separated profiles. Intent (capability): read, write, "
            "verify, escape, all. Domain (area): any catalog domain token, e.g. "
            "device_photonics, routing_backends (see klink.find_tools). Legacy "
            "aliases basic->read, draw->write, advanced->escape, drc->verify "
            "still work. Default: read,write,verify,escape."
        ),
    )
    ap.add_argument(
        "--host",
        default=DEFAULT_KLINK_HOST,
        help=f"klink TCP host (default: {DEFAULT_KLINK_HOST})",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=DEFAULT_KLINK_PORT,
        help=f"klink TCP port (default: {DEFAULT_KLINK_PORT})",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_CALL_TIMEOUT,
        help=f"ordinary RPC call timeout in seconds (default: {DEFAULT_CALL_TIMEOUT})",
    )
    ap.add_argument(
        "--long-timeout",
        type=float,
        default=DEFAULT_LONG_CALL_TIMEOUT,
        help=f"long-running RPC timeout in seconds (default: {DEFAULT_LONG_CALL_TIMEOUT})",
    )
    ap.add_argument(
        "--session-id",
        default=os.environ.get("KLINK_SESSION_ID"),
        help="interaction context session id (default: KLINK_SESSION_ID or 'default')",
    )
    ap.add_argument(
        "--context-root",
        default=os.environ.get("KLINK_CONTEXT_ROOT"),
        help="root directory for interaction context sessions (default: .klink/sessions)",
    )
    ap.add_argument(
        "--install-skills",
        metavar="DIR",
        default=None,
        help="copy klayout skill dirs to DIR (e.g. .claude/skills) and exit",
    )
    ap.add_argument(
        "--install-claude-md",
        metavar="DIR",
        default=None,
        help="copy CLAUDE.md to DIR (project root) and exit",
    )
    ap.add_argument(
        "--setup",
        metavar="DIR",
        default=None,
        help="install skills + CLAUDE.md into a project DIR and exit",
    )
    ap.add_argument(
        "--register",
        action="store_true",
        help="print exact MCP-registration commands/snippets for common agents "
             "(Claude Code, Codex, Cursor, Windsurf, Trae, VS Code, Zed, ...) "
             "with THIS interpreter's path filled in, then exit. Copy the line "
             "for your agent, run it, and restart the agent.",
    )
    args = ap.parse_args(argv)

    if args.register:
        print(_register_snippets(args.profile, args.session_id))
        return

    if args.setup:
        _install_skills(os.path.join(args.setup, ".claude", "skills"))
        _install_claude_md(args.setup)
        return

    if args.install_skills:
        _install_skills(args.install_skills)
        return

    if args.install_claude_md:
        _install_claude_md(args.install_claude_md)
        return

    profiles = [p.strip() for p in args.profile.split(",") if p.strip()]
    bridge = KLinkMCPBridge(
        profiles=profiles,
        host=args.host,
        port=args.port,
        call_timeout=args.timeout,
        long_call_timeout=args.long_timeout,
        session_id=args.session_id,
        context_root=Path(args.context_root) if args.context_root else None,
    )
    server = MCPServer(bridge)
    server.run()


if __name__ == "__main__":
    main()
