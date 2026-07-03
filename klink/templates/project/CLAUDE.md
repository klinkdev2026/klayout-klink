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
- Worked example: `example_template/gf_mzi_module.py` (complete thermo-optic
  MZI: optical + sbend + all-angle + dubins + electrical nets in ONE
  persisted net table; requires gdsfactory in the MCP interpreter and a
  live KLayout).
