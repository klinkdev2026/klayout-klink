# Getting started

klink lets an AI agent (Claude Code / Codex) drive the KLayout layout editor:
draw shapes, place PCells, route, and run LVS — for your process, through your
agent.

> 中文见 [getting-started.zh-CN.md](getting-started.zh-CN.md)

## Install

1. **KLayout + the klink plugin.** Install KLayout (desktop build from
   <https://www.klayout.de/build.html>) if you don't have it, then copy the
   repo's `klink_plugin/` folder into KLayout's `salt/` directory (exact
   commands are in the README's *Install KLayout Plugin* section) and start
   KLayout. The plugin runs an in-process RPC server.
2. **Install klink** into one Python (call it the *klink interpreter*).

   > **Not on PyPI yet** (publishing is imminent). Until it lands, install from a
   > clone of the repo: `pip install -e .` (or `pip install -e ".[photonics]"`
   > for the silicon-photonics recipe). The commands on this page show the
   > eventual `pip install klayout-klink` form.

   klink ships its Rust kernels as prebuilt wheels (Linux/macOS/Windows, CPython
   3.10–3.13): the core routing kernel installs with the package, and the
   multilayer routing engine's kernel is an optional `[fast]` extra with a
   pure-Python fallback (it runs either way, just slower on large designs). No
   third-party libs are bundled — the silicon-photonics recipe additionally needs
   `gdsfactory` in that **same** Python (`klayout-klink[photonics]` gets a tested
   one).
3. **Register the klink MCP server into your agent, then restart the agent.**
   klink ships the server; the one thing that varies is how your agent records
   it. Let klink write the exact command for you:

   ```bash
   klink-mcp --register
   ```

   It prints the copy-paste registration for **Claude Code, Codex, Cursor,
   Windsurf, VS Code, Zed** — plus the standard `mcpServers` JSON block that
   Claude Desktop, Trae, Cline and most other MCP agents accept (check your
   agent's docs for where its config lives) — with your klink interpreter's path
   already filled in (the thing agents most often get wrong). For example,
   Claude Code and Codex are one line each:

   ```bash
   claude mcp add klayout -- <klink-python> -m klink.mcp --profile read,write,verify,escape --session-id project-klink
   codex  mcp add klayout -- <klink-python> -m klink.mcp --profile read,write,verify,escape --session-id project-klink
   ```

   **Then restart your agent** — an MCP server is loaded at agent startup, so a
   running session won't see it until you restart. `klink.status` then reports
   the interpreter and capabilities so you can verify.

> **You do not need MCP to RUN the examples.** Every example is a plain
> `python -m ...` script (the exact commands are throughout this page) that
> talks to KLayout directly over the plugin's port — install klink and run it,
> no MCP required. MCP is the layer that lets your *agent* call klink as resident
> tools (faster and smoother than re-running scripts). Both paths use the same
> `pip install klayout-klink`.

## Your first runnable result (no GDS needed)

The EBL nanodevice recipe runs **fully offline** — no KLayout, no external
geometry:

```bash
python -m examples_klink.public.demos.ebl_wraparound
```

Real output (abridged): `"ok": true`, 40 electrodes, 12 patches, writefield of
16 fields / 11 windows / 20 crossings / **0 violations**, **0 overlaps**.

With KLayout + the plugin running, the neural-electrode recipe builds and
routes a probe (no GDS, just live Port/Anchor PCells):

```bash
python -m examples_klink.public.demos.neural_electrode --port <session-port> --elec-rows 4
```

Real output (abridged): `ok: True`, 48 ports, 24 nets routed (12 on `1/0` +
12 on `3/0`), **sibling-overlap 0**, **obstacle-hit 0**.

> A KLayout "port" is just a session — any port works; none has a special role.
> Use a session that is empty or test-owned, not your manual working tab.

## Starting your own project

Scaffold a project with the bundled CLI, then open it with your agent:

```bash
klink init my-chip
```

This writes `pdk.py`, `custom_devices/`, `recipes/`, `example_template/`, agent rules,
and a sample MCP config. Describe what you are building; the agent identifies
your domain and scaffolds `pdk.py` + a `custom_devices/` script from the
matching recipe. See
[project-model](project-model.md) and [recipes](recipes.md).

## What runs out of the box

Eight public demos run with no geometry from you — two fully offline (EBL
wraparound, Hall bar) and six against a live KLayout session. Four are
**starters** bundled in the wheel, so a `pip install` user runs them straight
from the scaffolded `example_template/` (`python example_template/<name>.py`):
**ebl_wraparound, hallbar, neural_electrode, gf_mzi_module**. The other four
(fit-device → P&R → LVS, hand-written netlist → P&R, multilayer P&R, probe-card
padframe) read the source tree, so they run from a clone of the repository. See
[demos](demos.md) for each command and measured output — none needs MCP to run.
