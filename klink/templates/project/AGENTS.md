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
- `custom_devices/` — build scripts you write
- `specs/` — `.klink` specs
- `out/` — generated artifacts

Read-only (shipped references — run them, don't edit them):

- `example_template/` — runnable, **self-contained** example scripts (neural electrode,
  EBL nanodevice, Hall bar) that import only `klink` (no process file, no PDK).
  Run one as-is, then copy it into `custom_devices/` and adapt.
- `recipes/README.md` — the per-domain menu of what klink can build.

**Never edit `klink` or the KLayout plugin.** They are installed packages
(pip + KLayout package manager). If something in klink seems wrong, report it;
do not patch installed code.

## Onboarding: discover the domain, then scaffold

There is no default project. On a fresh project:

1. **Interview the user** about what they are building until you can name the
   domain (e.g. EBL nanodevice, neural electrode, silicon photonics, digital
   P&R). Ask, don't assume.
2. **Pick the matching recipe** from `recipes/README.md`. Tell the user the
   recipe's *geometry tier* (self-contained / open-or-your-own / bring-your-
   own) and, if it needs their confidential geometry, ask them to supply it. If
   a **self-contained** example in `example_template/` already matches (neural
   electrode, EBL nanodevice, Hall bar), start from it — run it, then adapt a
   copy in `custom_devices/`.
3. **Scaffold** `pdk.py` for that process and a first `custom_devices/` script that
   imports `PROCESS` from `pdk.py` and calls the relevant klink API
   **explicitly** (klink ships no process default).
4. **Run and verify** with structured geometry/LVS queries (below).

The domain the user describes **becomes** this project's default.

## Process purity

`pdk.py` is the only home for process facts. Always pass `PROCESS` (and any
device library) **explicitly** into klink APIs. If a klink tool is called
without a process it returns an **instructive error** naming the next step —
read its `next_action` and follow it; do not invent a profile.

## Working rules (carry over from klink)

- **Errors are instructions.** klink tool errors carry a `next_action`. Follow
  it. This is the real safety net — it works even if you skip these docs.
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
