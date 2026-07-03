# Your klink project

This is a **klink user project** scaffold. You drive it by talking to an AI
agent (Claude Code / Codex); the agent writes the layout-generation code for
*your* domain into this project. You do not edit klink itself.

> 中文见 [README.zh-CN.md](README.zh-CN.md)

## What you edit vs. what is installed

| You edit (this project) | Installed, never edit |
|---|---|
| `pdk.py` — your process (layers, vias, dimensions) | `klink` (pip package: mechanism + algorithms + MCP) |
| `custom_devices/` — your build scripts (agent-written) | the klink **KLayout plugin** (installed via KLayout's package manager) |
| `specs/` — your `.klink` specs | |
| `out/` — generated GDS/results | |

klink ships only **mechanism** and holds **zero process data**. Every
process-specific fact (layer numbers, device library, via stacks, DRC numbers)
lives here in `pdk.py`, and your build code passes it **explicitly** into the
klink APIs.

The project also ships read-only **`example_template/`** — runnable, self-contained
example scripts (neural electrode, EBL nanodevice, Hall bar) that import only
`klink` (no process file, no PDK). Run one as-is, then copy it into
`custom_devices/` and adapt.

## How you start a project

You don't pick a domain up front. **Tell the agent what you are building**
("I make EBL nanodevices" / "a neural electrode probe" / "a digital block from
Verilog" / "a silicon-photonics circuit on an open or my own PDK"). The agent:

1. interviews you to identify your domain,
2. scaffolds `pdk.py` + a first `custom_devices/` script from the matching
   **recipe** (see [`recipes/README.md`](recipes/README.md)),
3. runs it and verifies the result with structured geometry/LVS queries.

The domain you describe **becomes** this project's default — there is no
hard-coded default project.

## Setup

1. `pip install klayout-klink` (into the same Python that runs the MCP server).
2. Install the klink plugin into KLayout (package manager), then start KLayout.
3. Copy `mcp.example.json` into your agent's MCP config and edit the paths.
4. Open this folder with your agent and describe what you want to build.

## Where the geometry comes from (per recipe)

Recipes differ in what layout data they need — don't assume all need your
private files:

- **Self-contained** (EBL, neural): generate everything from `pdk.py` + code,
  nothing external.
- **Open or your own** (silicon photonics): can run out of the box on an
  **open-source gdsfactory PDK**, or point it at your **proprietary foundry
  PDK** instead.
- **Bring your own** (P&R): needs a transistor layout that is **yours and
  confidential**.

The template **never ships GDS**, and you must **never commit** a proprietary
GDS/PDK here. Recipes that use private geometry scaffold the *code*; you point
them at your own files at run time. Open PDKs are fine to depend on but still
don't belong in version control.
