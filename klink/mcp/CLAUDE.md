# klink — AI-native control plane for KLayout

Let Claude Code directly control the KLayout layout editor: draw shapes,
manage cells/layers, place instances, run pya macros, and generate layouts
with gdsfactory.

## Architecture

```
Claude Code
├── MCP: klink-mcp           ← klink RPCs exposed as MCP tools
├── Skill: klayout            ← RPC + pya macro domain knowledge
└── Skill: klayout-gdsfactory ← gdsfactory → KLayout bridge
       │
       ▼
KLayout (klink_plugin)        ← port 8765 (RPC) + 8082 (klive-compat)
```

Do not rely on a hard-coded RPC count. Query the live server with
`meta.methods`; MCP tools are generated from that catalogue and filtered by
profile.

## Setup

KLayout must be running with the klink plugin loaded. Configure Claude Code
with the MCP server:

```json
{
  "mcpServers": {
    "klayout": {
      "command": "<python-that-has-klink>",
      "args": [
        "-m",
        "klink.mcp",
        "--profile",
        "read,write,verify,escape",
        "--session-id",
        "project-klink"
      ],
      "env": {
        "KLINK_CONTEXT_ROOT": "<your-project>/.klink/sessions"
      }
    }
  }
}
```

The `klink-mcp` console script and `python -m klink.mcp` are equivalent; both
must run in the Python environment where `klayout-klink` is installed.

## Environment

- klink core is pure Python; its only runtime dependency is its own Rust kernel
  (prebuilt wheel, byte-parity with the pure-Python reference)
- gdsfactory must be in the SAME interpreter that runs `klink.mcp`
- klink RPC port: 8765
- klive-compat port: 8082
- interaction context memory: `.klink/sessions/<session-id>/interaction_context.jsonl`

## Selection-first layout debugging

For layout debugging and Harness/PCell work, never call `view.screenshot`
unless the user explicitly asks for a screenshot/visual artifact in the
current conversation. Screenshots are not a fallback and must not be started
automatically. Prefer:

```text
selection.get
interaction.selection.latest / recent, when available
shape.query
layout.info
cell.list / cell.tree
layer counts
geometry validators
```

Screenshots are user-requested artifacts only. The core KLayout debugging loop
should bind user intent to selections, shapes, cells, layers, and coordinates.

## Interaction context memory

`klink-mcp` owns session-scoped selection memory outside the KLayout plugin.
For the MVP, memory is explicit: the user clicks the KLayout `SEND` toolbar
action, or an agent calls `selection.send_context`, and MCP records the emitted
`selection_sent` event as a stable id such as `sel_0006` under
`.klink/sessions/<session-id>/interaction_context.jsonl`.

When the user refers to sent/pinned GUI context, for example "just sent",
"this area", "here", "that one", or "the selected thing I sent", first use:

```text
interaction.selection.recent
interaction.selection.latest
interaction.context
```

The old `interaction.context.latest/recent/get/label/clear_session` aliases
were removed; only the canonical `interaction.selection.*` names
and the combined `interaction.context` tool exist.

Do not instantiate `InteractionContextStore` from Bash or read the JSONL file
as a substitute for the MCP tools. The JSONL file is persistence only; the
running MCP bridge is what subscribes to explicit `selection_sent` events.

Use `selection.get` for exact current KLayout state, especially when the user
says "current selection". If current selection and memory differ, inspect both. The
default recent window is order-based, not time-based; do not discard a
selection only because it is older than 30 seconds.

For important references, call `interaction.selection.label` with a short
label and description so later turns can refer to the stable id. Only call
`interaction.selection.clear_session` after explicit user intent, with
confirmation equal to the session id.

Important named regions should become explicit context objects, not loose
phrases. The design target is `interaction.marker.*`: promote a sent selection
such as "upper electrode array" or "danger zone" into a stable named layout
context object. Until marker tools exist, use `interaction.selection.label` to
attach the human nickname and description to the sent selection id.

## Routing MCP tools

For live tapered/harness routing, prefer MCP tools over ad-hoc Python snippets:

```text
routing.tapered_hybrid_cell
routing.tapered_polygon_cell
routing.steiner_cell
routing.damped_segment_cell
routing.damped_polygon_cell
routing.damped_steiner_cell
routing.global_channel_cell
routing.multilayer_escape_cell
routing.gdsfactory_ports
```

`routing.tapered_hybrid_cell` is the main Harness/Anchor backend. It reads
Port/Anchor PCells, candidate sinks, route layers, and the obstacle layers you
pass (`obstacle_layers`; klink ships no default keepout layer), then validates
and writes routes. It supports `angle_mode`: `any`, `fortyfive`, or
`manhattan`.

`routing.tapered_polygon_cell` is the continuous tapered polygon backend. Treat
it as a first-class taper backend, not a weak fallback. It preserves Port
orientation and launch rules, supports waypoint/bend/corridor anchors through
the same planner as hybrid routing, then writes one continuous taper polygon per
route.

`routing.steiner_cell` is for multi-terminal nets: same net with more than two
Ports, bus fanout, or one-to-many topology. It writes a rectilinear trunk plus
branches and accepts an optional `root_ports` mapping such as
`{"bus": "ROOT"}`. It preserves unequal Port widths: each branch uses its own
Port width and the trunk uses the widest participating Port. waypoint, bend,
and corridor anchors constrain the shared trunk. Do not use pairwise tapered
routing for a multi-terminal net unless the topology has already been split
into explicit two-port nets.

Use the `routing.damped_*` tools only when the user asks for routes that stay
farther from keepouts/pins/existing geometry, or when a legal default route is
too close to obstacles. These are explicit quality backends and must not be
treated as the default behavior:

```text
routing.damped_segment_cell   -> hybrid path+patch output
routing.damped_polygon_cell   -> continuous taper polygon output
routing.damped_steiner_cell   -> multi-terminal topology with damped legs
```

They take `damping_distance_um` and obstacle layers. The examples for these
tools build only Ports/Anchors/obstacles and invoke the MCP tools; route
intelligence belongs inside `klink.routing.damped`.

Use `routing.global_channel_cell` when the layout needs a stronger assignment
step before ordinary geometry routing:

- multiple explicitly optional corridor channels can serve the same nets and
  capacity/load balancing matters
- one side has netless `candidate_sink` Ports and the chosen pad should depend
  on obstacle-aware routed cost

This backend still writes tapered hybrid geometry. It is a new explicit router,
not a replacement for `routing.tapered_hybrid_cell`. If it reports sibling
overlap after assignment and small-bundle route-order search, the remaining
missing feature is full multi-route rip-up/reroute or negotiated global cost.

Do not treat ordinary `corridor` anchors as optional. A plain corridor is a
required pass-through path. Only corridors marked with a label token such as
`choice_group=BUS` are alternative channels for `routing.global_channel_cell`.

Use `routing.multilayer_escape_cell` only when single-layer routing is blocked
and a bridge layer plus vias are acceptable. It writes primary-layer approach
segments, bridge-layer crossing segments, and via boxes. It is intentionally
narrow: wall-style escape only, not full multi-layer signoff routing. Do not
hide this behavior inside `routing.tapered_hybrid_cell`.

After any routing call, inspect the structured result. Do not call a route
successful if `ok=false`, `obstacle_hit_count>0`, sibling overlap counts are
nonzero, route_count is short, or the tool reports `route failed`. For router
stress examples, expected failures must be named as failures; do not present
them as completed routes.

Be honest about router limits. `routing.global_channel_cell` is not a full
negotiated congestion router; `routing.damped_steiner_cell` is still a
trunk/branch heuristic; `routing.multilayer_escape_cell` does not model via
enclosure, layer costs, or per-layer obstacles. When these limits matter,
report them instead of claiming signoff-quality routing.

`routing.gdsfactory_ports` is conditional: it requires the MCP Python
interpreter to import `gdsfactory`. Use it for photonic route_bundle flows,
not Harness/Anchor corridor routing.

Harness/PCell generation must include explicit parameter/spec declaration,
structured geometry verification, same-class error scanning, and rule updates
back into the spec or docs.

## gdsfactory Port compatibility

klink Port markers can drive gdsfactory routing through the external Python
bridge:

```text
port.list dict
  -> klink.routing.port.port_to_gf(...)
  -> gf.Port
  -> gf.routing.route_bundle
  -> KLayout polygons / route backbones
```

Prefer the existing helpers over custom one-off glue:

- `route_gdsfactory_ports(...)` routes KLayout Port markers by net, explicit
  names, prefixes, or orientations.
- `select_gdsfactory_port_groups(...)` handles deterministic pairing.
- `place_gdsfactory_components(...)` turns component markers such as `mmi1x2`
  into KLayout polygons and ordinary Port PCells.

Important boundary: gdsfactory photonic backend handles point-to-point optical
nets. A net with three or more ports needs an explicit splitter/MMI/Y-branch,
then the resulting exactly-two-port nets can be routed.

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

With a profile that includes `write`, MCP exposes the batch RPCs:

| Workload | Preferred RPC |
|---|---|
| Many boxes on one cell/layer | `shape.insert_boxes` |
| Mixed shapes in one cell | `shape.insert_many` |
| Many child-cell instances | `instance.insert_many` |
| Many Basic/library PCell instances | `instance.insert_pcell_many` |

Use these for generated layouts, port markers, PCell arrays, fill, and route
commit style writes. Avoid one RPC per object except for debugging.

`shape.insert_many` is shape-only (`box`, `polygon`, `path`, `text`). PCells are
instances, so use `instance.insert_pcell_many`.

After adding/changing server-side RPC methods, reload the klink plugin in
KLayout and restart the MCP server so the tool list refreshes.

## Recorder and destructive operations

The recorder is a replay-script generator, not a literal RPC logger. Bulk RPCs
and `exec.python` may record as expanded replay actions if that recreates the
final layout state.

Only call `layout.clear` and `view.close_tab` on disposable test tabs/layouts,
not on the user's current manual working tab.

## Key files

- `klink/client.py` — Python client
- `klink/mcp/` — MCP bridge
- `klink_plugin/python/klink_server/` — KLayout in-process server
- `klink/mcp/skills/klayout/SKILL.md` — packaged KLayout skill
- `.claude/skills/klayout/SKILL.md` — skill copy installed by `klink-mcp --setup`
