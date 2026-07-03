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
2. **`pip install klayout-klink`** into the Python that will run the MCP server.
   This brings klink + its own Rust kernels (prebuilt wheels for Linux/macOS/
   Windows on CPython 3.10–3.13); no third-party libs are bundled. The
   silicon-photonics recipe additionally needs `gdsfactory` in that **same**
   Python — install it yourself.
3. **Configure your agent's MCP server** to launch `python -m klink.mcp` (a
   sample config, `mcp.example.json`, is written by `klink init` — see below).

The `command` Python in the MCP config must be the one that has klink (and
gdsfactory, if you use photonics). `klink.status` reports the interpreter and
capabilities so you can verify.

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

All four public demos run with no geometry from you — two fully offline
(EBL wraparound, Hall bar) and two against a live KLayout session
(neural-electrode harness, fit-device → P&R → LVS, which uses synthetic
exemplars). See [demos](demos.md) for each demo's exact command and measured
output.
