# Agent operating rules — klink user project

This file is the **single source** of agent rules for this project. It is for
any agent harness (Codex reads `AGENTS.md`; Claude Code reads `CLAUDE.md`,
which points here). klink itself is an installed package — these rules govern
how you work *in this project*, not how klink is built.

## Editable surface (hard boundary)

You may create and edit only:

- `pdk.py` — the process (layers, vias, dimensions) + the device library
  (`DEVICES`/`LIBRARY`) and the sizing CHOICE (`SIZING`) for P&R. Sizing is a
  design decision YOU specify here (klink ships the mechanism, never the choice).
- `custom_devices/` — code you write, in TWO strata (see its README):
  - `toolbox/` — reusable, verified tools (a real package;
    `__init__.py` is the index — read it BEFORE writing a generator,
    the tool may already exist);
  - `runs/<date>_<slug>/` — one folder per task holding its `run.py`,
    its `out/` artifacts, and its `notes.md` record TOGETHER.
- `specs/` — `.klink` specs
- `out/` — generated artifacts (legacy shared pile; prefer the
  current run's own `out/`)

**Run discipline** (this is how the project stays navigable):
START every task with `klink run new <slug>` — it creates the
date-stamped run folder (run.py + out/ + notes.md) and registers it
in `runs/INDEX.md` as "(in progress)" so the ledger can never miss a
folder. FINISH by filling `notes.md` (what was asked, what you did,
and REAL verification output — LVS/geometry numbers, never "looks
done"), replacing the ledger's "(in progress)" with a one-line
summary + PASS/FAIL, and a git commit. If you used the recorder, copy the replay script
into the run folder. When a run's code proves out and is the second
time this shape was needed, propose GRADUATING it into `toolbox/`
(docstring + export entry) — writing the same function twice is the
signal.

Read-only (shipped references — run them, don't edit them):

- `example_template/` — runnable starter scripts grouped by category
  (nanodevice, passives, photonics, layout, digital, imaging, ledit_bridge).
  The live list and how to run each is in `example_template/README.md` — read
  that, don't rely on any enumeration elsewhere. Run one as-is, then copy it
  into `custom_devices/` and adapt.
- **Tanner L-Edit** (not KLayout) — if the user says L-Edit, Tanner, `.tdb`
  or T-Cell, klink drives that editor too, over a file-exchange bridge.
  **First call `ledit.status`** — it answers "what is in L-Edit right
  now" (bridge liveness, open design, active cell, and the cell list
  when the macro supports it); never go hunting window titles or
  processes. Workflows live in `example_template/ledit_bridge/` (start
  with its `README.md`, then `draw_device_demo.py`).
  `python -m klink.doctor` reports the bridge's liveness alongside the
  KLayout checks.
  **L-Edit is where the design lives; KLayout is where klink's capability
  lives.** The bridge's command set is the transport (draw/read/place/
  manage), not the toolbox. When a user asks for something in L-Edit, first
  ask whether klink already does it on the KLayout side — routing, DRC,
  LVS, P&R, fill/boolean/density, gdsfactory, imaging, device fitting. If it
  does: read the cells out (`ledit.import_cell_tree`), do the work with the
  real klink API, push the result back (`ledit.push_cell_tree`, or a GDS via
  `import_gds`). The mistake this rule prevents is not only rebuilding a
  router out of `draw` calls — the quieter one is reading coordinates with
  `get_cell` and reasoning about them in Python because "it is only
  geometry". Measured: an agent doing exactly that reported a CRITICAL
  DESIGN ERROR on a correct NMOS ("n+ covers the gate"), which is simply how
  a self-aligned device is drawn. Geometry questions carry PROCESS meaning;
  `geometry.boolean` / `drc_run` encode the process, first-principles
  reasoning invents rules. A KLayout PCell arrives in
  L-Edit as static geometry unless you port it —
  `tcell_workflows.py from_pcell` scaffolds the T-Cell generator.
- `recipes/README.md` — the per-domain menu of what klink can build.

**Never edit `klink` or the KLayout plugin.** They are installed packages
(pip + KLayout package manager). If something in klink seems wrong, report it;
do not patch installed code.

## Onboarding: discover the domain, then scaffold

There is no default project. On a fresh project:

1. **Get the map first.** Call `klink.find_tools` with no arguments for the
   domain index; `klink.find_tools domain=<area>` for that area's tools and
   detailed usage; `query=<keywords>` to search. Tool descriptions and error
   `next_action`s are the authoritative, always-current reference — do this
   before assuming what klink can or cannot do.
2. **Interview the user** about what they are building until you can name the
   domain (e.g. EBL nanodevice, neural electrode, silicon photonics, digital
   P&R). Ask, don't assume.
3. **Pick the matching recipe** from `recipes/README.md`. Tell the user the
   recipe's *geometry tier* (self-contained / open-or-your-own / bring-your-
   own) and, if it needs their confidential geometry, ask them to supply it.
   If a starter in `example_template/` already matches (see
   `example_template/README.md` for the category list), start from it — run
   it, then adapt a copy in `custom_devices/`.
4. **Scaffold** `pdk.py` for that process and a first `custom_devices/` script that
   imports `PROCESS` from `pdk.py` and calls the relevant klink API
   **explicitly** (klink ships no process default).
5. **Run and verify** with structured geometry/LVS queries (below).

The domain the user describes **becomes** this project's default.

## Process purity

`pdk.py` is the only home for process facts. Always pass `PROCESS` (and any
device library) **explicitly** into klink APIs. If a klink tool is called
without a process it returns an **instructive error** naming the next step —
read its `next_action` and follow it; do not invent a profile.

## Working rules (carry over from klink)

- **Own your tab.** Before drawing anything, open your own layout tab with
  `view.new_tab` and work there. Never draw into, clear, or close a tab you
  did not create in this conversation — the user's open tabs are their work.
  If the user asks for edits to *their* layout, confirm which tab/cell first.
- **Errors are instructions.** klink tool errors carry a `next_action`. Follow
  it. This is the real safety net — it works even if you skip these docs.
- **Never guess the API — ask it.** `client.help("shape.query")` prints the
  LIVE schema: which parameters are valid, which are required, and **which
  keys come back**. That last part is what a function signature cannot tell
  you, and guessing it is how agents end up abandoning klink mid-task
  (`cell.create` returns `name`, not `cell`; `shape.query` returns `shapes`
  with `bbox_dbu`, not `bbox_um`). Any RPC name also works as a method:
  `client.view_new_tab(...)`. Note layer identity is spelled differently per
  tool — `shape.query` takes a `layers` array (`"L/D"` strings, indexes,
  or `{layer, datatype}`), `geometry.boolean` takes `"L/D"` or
  `{layer, datatype}`; `help()` says which.
- **Batch RPCs for generated layouts.** Never one RPC per object; use
  `shape.insert_boxes` / `shape.insert_many` / `instance.insert_many` /
  `instance.insert_pcell_many`.
- **Selection-first debugging, not screenshots.** Use `selection.get`,
  `shape.query`, `layout.info`, `cell.list/tree`, layer counts. Capture a
  screenshot only if the user explicitly asks for one.
- **Typed RPCs over `exec.python`.** Use `exec.python` only as an escape hatch
  for operations no typed RPC covers, and say why.
- **LVS-only real pass.** A layout/route counts as done only when live KLayout
  LVS returns `match=True`. Marker counts and "looks routed" do not count.
- **Ports are equal-capability sessions.** A KLayout port is just a session;
  no port has a special "working" or "LVS" role. Pass the session explicitly.

## Never commit confidential geometry

This project must never contain GDS/PDK content — neither a proprietary foundry
PDK nor your transistor layout. Point recipe code at those files at run time;
keep them out of version control. (Open PDKs are fine to depend on, still not
committed.)

## Full reference

For the complete klink tool surface and domain loops, see the published klink
docs (`docs/public/`) and `recipes/README.md`.
