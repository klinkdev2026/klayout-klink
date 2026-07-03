# klink-mcp — Claude Code to KLayout Bridge

MCP server that exposes klink RPC methods as Claude Code tools, plus skills
that teach Claude EDA domain knowledge.

## Quick Start

### 1. Install the package

```bash
pip install klayout-klink
```

### 2. Set up a project

```bash
klink-mcp --setup /path/to/your/project
```

This installs:
- `.claude/skills/klayout/SKILL.md`        — RPC + pya macro skill
- `.claude/skills/klayout-gdsfactory/SKILL.md` — gdsfactory → KLayout bridge skill
- `CLAUDE.md`                                   — project memory file

Or install individually:

```bash
klink-mcp --install-skills /path/to/project/.claude/skills
klink-mcp --install-claude-md /path/to/project
```

### 3. Register the MCP server with Claude Code

```bash
claude mcp add klayout -- python -m klink.mcp --profile read,write,verify,escape
```

Interpreter rule: optional libraries (e.g. `gdsfactory` for
`routing.gdsfactory_ports`) must be installed into the **same Python that
runs `klink.mcp`** — install with `pip install gdsfactory` in that
environment (klink does not bundle it). Use the
`klink.status` tool to verify: it reports `interpreter` (the exact
executable) and `capabilities` (which libraries are importable). When a
capability is missing, the tool error names the interpreter and the exact
pip command to fix it.

During development from a checkout of this repository, prefer the project-local
module form with `PYTHONPATH` pointing at the workspace. A project-level
`.mcp.json` then looks like:

```json
{
  "command": "<python-that-has-klink>",
  "args": ["-m", "klink.mcp", "--profile", "read,write,verify,escape", "--session-id", "project-klink"],
  "env": {
    "PYTHONPATH": "<path-to-this-repo>",
    "KLINK_CONTEXT_ROOT": "<path-to-this-repo>/.klink/sessions"
  }
}
```

Do not use an old installed `klink-mcp.exe` while testing workspace changes;
it can miss MCP bridge-local tools such as `interaction.selection.recent`.

This is a **one-time setup**. Claude Code stores it in `~/.claude.json` scoped to
this project. Do NOT manually edit JSON files — use this command.

### 4. Start KLayout

KLayout must be running with the klink plugin loaded (ports 8765 and 8082).
The MCP server can start before KLayout. Until KLayout is reachable it exposes
local `klink.status` / `klink.reconnect` tools, then refreshes the full tool
list when it reconnects.

### 5. Launch Claude Code

```bash
cd /path/to/your/project
claude
```

Claude auto-discovers `CLAUDE.md`, both skills, and the MCP tools (with the
default `read,write,verify,escape` profile this is the profile-filtered server
RPCs + the bridge-local tools incl. `klink.find_tools`; do not hard-code the
count — query `meta.methods` or call `klink.find_tools`).

## Profiles

`--profile` selects which tools are exposed along TWO orthogonal axes.

**Intent (capability)** — selects plugin RPCs by what they DO. Cross-domain, so
the four capabilities together span every area; the default is built from these,
and ALL local tools are always included for any intent profile.

| Intent   | What it exposes |
|----------|----------------|
| `read`   | Read-only exploration: `layout.info`, `cell.list`, `shape.query`, `view.*`, `pcell.*`, recorder |
| `write`  | Layout editing: `shape.insert_*`, `cell.create`, `layer.ensure`, `instance.insert`, `edit.undo` |
| `verify` | Run checks: `drc.run`, `lvs.run` |
| `escape` | Escape hatches: `exec.python`, `exec.reset`, `events.*` |
| `all`    | Everything (no filtering) |

**Domain (area)** — `--profile <domain_token>` narrows to one area (e.g.
`--profile device_photonics` or `--profile routing_backends`), restricting BOTH
plugin RPCs and local tools to that domain (plus an always-on core:
`klink.find_tools` / `klink.status` / `klink.reconnect`). The domain tokens and
their detailed per-area usage live in `klink/mcp/catalog.py`; the
**`klink.find_tools`** tool is the runtime way to navigate them (no args → domain
index; `domain=<token>` → that area's tools + usage; `query=<keywords>` → ranked
matches). tools/list always advertises every tool — find_tools is for navigation
and on-demand detail, not a gate.

Default: `--profile read,write,verify,escape`. Combine with commas; mix axes,
e.g. `--profile read,device_photonics`. Legacy aliases still work: `basic`→read,
`draw`→write, `advanced`→escape, `drc`→verify.

## Timeouts

`klink-mcp` uses separate ordinary and long-running RPC timeouts:

```bash
klink-mcp --profile read,write --timeout 15 --long-timeout 180
```

- ordinary calls: `--timeout`, default 15 seconds;
- long/heavy calls: `--long-timeout`, default 180 seconds.

Heavy calls include `shape.query`, `view.screenshot`, `layout.show_file`,
`layout.save_file`, `exec.*`, `drc.*`, and any RPC marked `long_running` by
`meta.methods`.

## What Claude can do

- **Inspect layouts**: `layout.info`, `cell.tree`, `shape.query`
- **Draw shapes**: boxes, polygons, paths, text
- **Place instances**: regular cells and PCells (CIRCLE, ROUND_PATH, TEXT, etc.)
- **Take screenshots**: visual feedback loop
- **Run pya macros**: full KLayout Python API via `exec.python`
- **Generate with gdsfactory**: Claude writes a gdsfactory script, runs it with
  Bash, `c.show()` pushes to KLayout via klive-compat (port 8082)

## Requirements

- Python >= 3.10
- klink-mcp: zero additional dependencies (stdlib only)
- KLayout running with klink plugin loaded (ports 8765, 8082)
- gdsfactory skill: gdsfactory installed in a venv

## Troubleshooting

**"No MCP servers configured" in Claude Code**
→ Run `claude mcp add klayout -- klink-mcp --profile read,write` from the
terminal (not the `/mcp` slash command inside Claude Code).

**Claude writes Python code manually instead of using MCP tools**
→ MCP server not running. Check `claude mcp list` to see if `klayout` is
registered. Also verify KLayout is open with klink plugin loaded.

**MCP tools appear but return connection errors**
→ KLayout not running or klink plugin not loaded. Start KLayout, then call
`klink.reconnect` or refresh the MCP tool list. Use `klink.status` to inspect
the last connection error.

**Slow performance**
→ This means MCP isn't working and Claude is falling back to manual Python
scripts. Follow the setup steps above.
