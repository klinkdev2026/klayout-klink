# Claude Code — read AGENTS.md

The operating rules for this project are in **[AGENTS.md](AGENTS.md)** (single
source, harness-neutral). Read it and follow it.

Claude-Code specifics:

- The klink MCP server is configured via `mcp.example.json` (copy into your
  Claude Code MCP config and edit the paths).
- Delegate bulk work to sub-agents where available (build / route / verify
  lanes); keep the main conversation for intent, scaffolding, and summaries.
- Everything else — the editable surface, onboarding/domain-discovery flow,
  process purity, batch RPCs, selection-first debugging, LVS-only pass, and the
  never-commit-GDS rule — is in AGENTS.md.

## Tanner L-Edit workflow

If the user mentions L-Edit, Tanner, `.tdb`, or T-Cell, klink drives that
editor too — never go hunting window titles, processes, or recent-file
lists:

- **First call `ledit.status`.** It answers "what is in L-Edit right
  now": bridge liveness, the open design (.tdb path), the active cell,
  and the design's cell list (T-Cells flagged) when the macro supports
  it.
- Tool map for the bridge: `klink.find_tools domain=bridge_ledit`.
- Workflows (draw, cell/hierarchy transfer both ways, T-Cell
  read/variants/writeback/verify/fit/to_pcell):
  `example_template/ledit_bridge/README.md`.
- Route the WORK, not reimplement it: import cells into KLayout
  (`ledit.import_cell_tree`), use the real klink tools there (routing,
  DRC, LVS, geometry, device fitting), push results back. The bridge is
  transport, not a toolbox.

## Photonics / gdsfactory workflow

For photonic circuits, prefer the one-call orchestrators over ad-hoc glue:

- User has a FINISHED gdsfactory script -> `photonics.import_gf`
  (script path; devices become draggable instances, its routes are
  replaced by klink-owned nets). After it: drag in KLayout, then
  `photonics.reroute` with just the cell name.
- Foundry blackbox cells with stub-marker ports -> `port.harvest_blackbox`
  + `photonics.connect` (pass YOUR pdk.py wg_layer/stub_size_um/route_layer).
- Choosing a gf routing strategy (`routing.gdsfactory_ports` router=):
  call `klink.find_tools domain=routing_backends` for the cheat sheet.
  A parameter the router cannot honor returns an error naming the routers
  that do. Optical nets are re-drawn with euler bends when klink has to
  detour around a device; a route is only reported ok when it crosses
  nothing and cuts no device body.
- Worked example: `example_template/photonics/gf_mzi_module.py` (complete thermo-optic
  MZI: optical + sbend + all-angle + dubins + electrical nets in ONE
  persisted net table; requires gdsfactory in the MCP interpreter and a
  live KLayout).
