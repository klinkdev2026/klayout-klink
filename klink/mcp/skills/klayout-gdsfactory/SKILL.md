---
name: klayout-gdsfactory
description: >
  gdsfactory to KLayout bridge. Use when generating photonic/EDA components
  with gdsfactory and pushing them to KLayout for real-time display, geometry
  query, validation, and iteration.
allowed-tools: bash python
---

# gdsfactory to KLayout Bridge

gdsfactory generates components with Python. This skill adds the KLayout
integration layer through klink's klive-compatible port and structured MCP
verification.

## Click-to-connect on PDK layouts (use these two tools, nothing manual)

When the user wants ports connected on a foundry-PDK layout (blackbox cells
with waveguide stub ports), do NOT orchestrate harvest/pairing/routing
yourself. Two MCP tools do the whole job and persist state on disk:

1. User marks intent in KLayout: for each connection, they select ONE or
   TWO klink Port markers (the labeled triangles) and press the SEND
   toolbar action. Two markers in one SEND = one pair; single-marker SENDs
   pair up consecutively.
2. You call `photonics.connect` with `recent_sends=<number of SENDs>`.
   That is the entire call. It resolves ports exactly from the SEND
   events, auto-names nets, saves the net table to `.klink/specs/`,
   re-harvests ports from live instance positions, routes with
   gdsfactory, writes polygons, and zooms. Optional style fields:
   `width_um`, `radius_um`, `separation_um`, `route_layer`.
3. After the user MOVES components and asks to redo the wiring, call
   `photonics.reroute` with just the `cell` name (add `session` if the
   layout is not in the primary KLayout window). Connection intent is
   read from the persisted table — you do not need to remember anything.
4. If a result has `ok: false`, read `problems`: each entry states exactly
   what to do next (usually: tell the user which selection to redo).
   Never guess pairings yourself.

If port markers are not on screen yet, run `port.harvest_blackbox` first
(cell + tags map, or let `photonics.connect` auto-tag) so the user has
markers to click.

## Execution Environment

- Run in the SAME interpreter that has klink + gdsfactory.
- KLayout must be running with the klink plugin loaded.
- `c.show()` connects to `localhost:8082`.
- Always call `gf.clear_cache()` before regenerating a changed component.

## Basic Flow

```python
import gdsfactory as gf

gf.clear_cache()
gf.gpdk.PDK.activate()

c = gf.components.mmi1x2(width_mmi=5.0, length_mmi=25.0)
c.show()
```

## Verification Flow

After pushing to KLayout, verify with structured state:

```text
layout.info
cell.list / cell.tree
shape.query
layer counts
port and bbox checks
interaction.selection.recent/latest for sent user-selected problem areas
selection.get for exact current selection
```

Never call `view.screenshot` unless the user explicitly asks for a screenshot
or visual artifact in the current conversation. Do not start screenshot capture
automatically, do not use it as a fallback, and do not ask for it as a routine
verification step.

## Interaction Context

When a user selects a bad photonic route, port, component edge, or generated
region in KLayout and sends it with the `SEND` toolbar action, use MCP
interaction memory and structured queries:

```text
interaction.selection.recent
interaction.selection.latest
interaction.context
selection.get
shape.query
```

Use `interaction.selection.label` for important debug targets such as
`bad_mmi_port_gap` or `wrong_route_bundle_corner`. The memory is stored outside
the plugin under `.klink/sessions/<session-id>/`, so it can survive MCP/runtime
restart for the same agent session.

## Port Bridge

klink Port markers are compatible with gdsfactory routing through the client
Python layer:

```text
KLayout Port PCell / port.list dict
  -> klink.routing.port.port_to_gf(...)
  -> gf.Port
  -> gf.routing.route_bundle
  -> KLayout polygons or route backbones
```

Use these helpers instead of writing ad hoc conversion code:

- `klink.routing.backends.gdsfactory.gdsfactory_ports.route_gdsfactory_ports`
- `klink.routing.backends.gdsfactory.gdsfactory_ports.select_gdsfactory_port_groups`
- `klink.routing.backends.gdsfactory.gdsfactory_components.place_gdsfactory_components`

Port dict fields used by the bridge:

```text
name
center_um
orientation
width_um
target_layer
port_type
net
```

Supported routing selection:

- explicit `source` / `target` port names;
- `source_prefix` / `target_prefix`;
- `source_orientation` / `target_orientation`;
- one exactly-two-port `net`;
- all exactly-two-port nets.

Multi-port optical nets are intentionally rejected by the gdsfactory bridge.
Place an explicit splitter/MMI/Y-branch first, then route the resulting
point-to-point nets.

Route existing KLayout Port markers:

```python
from klink import KLinkClient
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

with KLinkClient().connect() as client:
    report = route_gdsfactory_ports(
        client,
        "TOP",
        port_layer="999/99",
        route_layer="10/0",
        output_mode="batch_polygons",
        clear=True,
        allow_crossing=False,
        all_two_port_nets=True,
    )
```

Pairing options:

```python
route_gdsfactory_ports(client, "TOP", source=["IN0"], target=["OUT0"])
route_gdsfactory_ports(client, "TOP", source_prefix="IN", target_prefix="OUT", pair_by="axis")
route_gdsfactory_ports(client, "TOP", source_orientation=0, target_orientation=180)
route_gdsfactory_ports(client, "TOP", net="sig0")
```

Place a gdsfactory component and expose its ports as ordinary Port PCells:

```python
from klink import KLinkClient
from klink.routing.backends.gdsfactory.gdsfactory_components import place_gdsfactory_components
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

marker = {
    "id": "SPL1",
    "component": "mmi1x2",
    "center_um": [55, 0],
    "rotation": 0,
    "params": {},
    "port_nets": {"o1": "net_in", "o2": "net_out0", "o3": "net_out1"},
}

with KLinkClient().connect() as client:
    place_gdsfactory_components(client, "TOP", [marker], target_layer="10/0")
    route_gdsfactory_ports(client, "TOP", route_layer="10/0", clear=False)
```

## Iteration Flow

1. Edit the gdsfactory script.
2. Run it with the project venv Python.
3. Push to KLayout with `c.show()` or write/load GDS.
4. Query and validate geometry through MCP.
5. If the user selects a bad area, bind the selection memory id, inspect
   coordinates and nearby shapes, then label the selection if it will be
   discussed across turns.
6. Update the generator or spec; regenerate after `gf.clear_cache()`.

## Large Designs / Export

```python
gdspath = c.write_gds("output/my_design.gds")
```

Then load into KLayout via layout RPC if needed.

## Notes

- Use `gf.components.<name>(**params)` for built-in parametric components.
- This skill covers KLayout integration and verification, not the full
  gdsfactory design reference.
- For Harness/PCell work, follow the `klayout` skill's selection-first and
  structured-verification discipline.
