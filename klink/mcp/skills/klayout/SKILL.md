---
name: klayout
description: >
  Expert KLayout layout editor operator. Use for any task involving viewing,
  creating, modifying, selecting, querying, validating, or debugging
  IC/microelectronic/photonic layouts in KLayout. Prefer exact selection and
  geometry queries over screenshots.
allowed-tools: bash python
---

# KLayout Layout Operations

You control KLayout through MCP tools provided by `klink-mcp`. Tool names
preserve their dotted RPC names, such as `layout.info`, `selection.get`, and
`shape.insert_box`.

## If you are unsure, call klink.guide first

`klink.guide {}` reports what is open, what intent state exists on disk,
the literal call for each user intention, and a suggested next action.
Every orchestrated tool result also carries `next_action` — follow it
verbatim. The workflow lives in tool results, not in your memory.

## Core Rule

Use structured layout state as the source of truth:

```text
selection.get
interaction.selection.latest / recent, when available
shape.query
layout.info
cell.list / cell.tree
layer counts
geometry validators
```

Never call `view.screenshot` unless the user explicitly asks for a screenshot
or visual artifact in the current conversation. Do not start screenshot capture
automatically, do not use it as a fallback, and do not ask for it as a routine
verification step.

## Interaction Context Memory

`klink-mcp` may expose session-scoped interaction memory tools. This memory is
external to the KLayout plugin and persists under:

```text
.klink/sessions/<session-id>/interaction_context.jsonl
```

Use these tools for explicit sent/pinned user intent. For the MVP, stored
memory is created when the user clicks the KLayout `SEND` toolbar action or
when an agent calls `selection.send_context`, not from every ordinary GUI
selection move:

| User phrase | First tool |
|---|---|
| sent GUI context: just sent, this area, here, that one | `interaction.selection.latest` or `interaction.selection.recent` |
| multiple recent items: these three, the previous few | `interaction.selection.recent` |
| exact current selection | `selection.get` |
| explicit id such as `sel_0006` | `interaction.selection.get` |
| important reusable region | `interaction.selection.label` |

Use only the canonical `interaction.selection.*` names plus the combined
`interaction.context` tool. The old `interaction.context.latest/recent/get/
label/clear_session` aliases were removed.

The default recent window is by order/count, usually the latest five selection
send events. Do not reject a selection only because it is older than 30 seconds.
Layout work often has long pauses.

If current KLayout selection and stored memory differ, inspect both with
`interaction.context` or separate calls. Do not silently assume one is right.

Do not use Bash to instantiate `InteractionContextStore` as a live memory
reader. That only reads the persisted file and does not subscribe to KLayout
events. Use the MCP tools above; if they are missing, report that the running
MCP server is stale or misconfigured and restart it.

## Electrical nets on device layouts (structdevice one-call tools)

For hand-drawn device layouts (transistor-style cells with gate/SD/channel
boxes, no Port markers anywhere), do NOT orchestrate terminal derivation or
net tracing manually. Three one-call tools own the whole chain; state lives
on disk under `.klink/specs/`:

| User intention | One call |
|---|---|
| "this wire is a net" (user selects wiring, presses SEND) | `structdevice.declare_nets` with `recent_sends` + `cell` |
| "wire up the declared nets" / "connect these" | `structdevice.connect_nets` with `cell` (attach points, vias, keepouts, routing, and LVS all happen inside; rolls back on mismatch) |
| "build this netlist as a circuit" | `structdevice.build_from_netlist` with `cell` + `netlist` (placement, wiring, LVS — one call; refuses existing cell names) |
| "is the wiring right?" / pre-tapeout check | `structdevice.lvs_check` with `cell` |
| "give me the machine-readable facts" | `structdevice.spec_write` with `cell` + `layer_roles` |

These tools ship NO process data (klink is process-agnostic; see CLAUDE.md
"klink process purity"). Called without a process they return an INSTRUCTIVE
error, never a guess: pass `conductors=[...]` + `vias=[...]` for connectivity,
or for a full build write/run an example that imports a profile from
`your pdk.py`. Read the error's `next_action`; never invent a
`ProcessProfile`.

Rules:

- ONE SEND on the wiring = ONE declared net. The tool resolves the SEND
  through derived connectivity (the selected wire's whole connected
  component, terminals included). Tell the user to select wiring SHAPES,
  not device instances; the tool's `problems` say exactly this when it
  happens.
- Read `problems` as instructions and relay them; never guess pairings or
  re-implement matching.
- `lvs_check` findings open/short/floating carry terminal-level evidence;
  ok=false means the drawn wiring disagrees with the declared intent — that
  is a real result to report, not a tool failure.
- Layer roles / conductor stacks are PER LAYOUT and REQUIRED: pass `conductors`
  + `vias` for your process (klink ships no default stack; omitting them returns
  an instructive error). Never assume a global stack.

## Core Concepts

- **Layout**: An open GDS/OAS file containing cells, layers, shapes, and
  instances.
- **Cell**: A hierarchical unit. Top cells are hierarchy roots.
- **Layer/Datatype**: Integer process layer pairs. Use `layer.ensure`.
- **Instance**: A placement of a normal cell or PCell.
- **PCell**: Parameterized cell. Discover with `pcell.libraries`,
  `pcell.list`, and `pcell.info`.
- **Selection**: The user's current geometric focus. Bind phrases like "this
  area", "the selected port", and "the region I just selected" to
  `selection.get` or interaction-context selection IDs.
- **DBU**: KLayout's internal integer unit. Always use `*_um` parameters.

## Selection-First Debugging

When the user points at a problem in KLayout:

1. If the user says "current selection", read `selection.get`.
2. If the user refers to sent GUI context, read
   `interaction.selection.recent` or `interaction.selection.latest` first.
3. Compare current selection with recent memory when ambiguity matters.
4. Query nearby geometry with `shape.query` using the selection bbox/cell/layer.
5. Diagnose from coordinates, layers, bboxes, shape kinds, and connectivity
   facts.
6. Do not use screenshots unless the user explicitly asks for one.

## Creating Layouts

1. Ensure layers with `layer.ensure(layer=L, datatype=D)`.
2. Create cells with `cell.create(name="...")`.
3. For small edits, use typed shape RPCs.
4. For generated layouts, use batch RPCs:
   - `shape.insert_boxes`
   - `shape.insert_many`
   - `instance.insert_many`
   - `instance.insert_pcell_many`
5. Verify with structured geometry checks.

## Batch RPCs

Use batch RPCs whenever creating many objects. Avoid one RPC per object except
for focused debugging.

| Workload | Use |
|---|---|
| Many boxes in one cell/layer | `shape.insert_boxes` |
| Mixed shapes in one cell | `shape.insert_many` |
| Many regular child-cell instances | `instance.insert_many` |
| Many PCell instances | `instance.insert_pcell_many` |

`shape.insert_many` accepts only shapes: `box`, `polygon`, `path`, and `text`.
PCell placement belongs to `instance.insert_pcell_many`.

## Harness / PCell Generation Discipline

Harness PCell work needs orchestration, not just geometry generation.

Required flow:

1. State parameters, formulas, layer ownership, array indexing, and symmetry
   assumptions before generating geometry.
2. Bind user marks and selected regions to `selection.get` or a stable
   `selection_id` when interaction context is available.
3. Generate into a disposable or clearly named cell first.
4. Verify using structured checks:
   - expected layer counts
   - bbox and pitch checks
   - relative formulas such as `via_y = pad_y + offset`
   - left/right or top/bottom symmetry
   - port centers on electrical connection geometry
   - base and expanded parameter cases, such as N=4 and N=6
5. When the user reports one error, scan all same-class generated objects for
   the same rule violation.
6. Write corrected rules into the spec or design document instead of leaving
   them only in chat history.

## Routing Backend Choice

Prefer MCP routing tools for standard reroutes:

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

Use `routing.tapered_hybrid_cell` for Harness/Anchor/corridor-aware routing.
It is the main backend for multi-net layout work and supports
`angle_mode="any" | "fortyfive" | "manhattan"`.

Use `routing.tapered_polygon_cell` for continuous variable-width polygon taper
routes. It is first-class and should preserve the same Port launch/orientation
and waypoint/bend/corridor Anchor contract as hybrid routing; the difference is
continuous polygon output instead of hybrid path-plus-patch output.

Use `routing.global_channel_cell` when ordinary hybrid routing fails because
the routing decision itself is global: multiple explicitly optional corridor
channels can serve the same nets and must be capacity-balanced, or netless
`candidate_sink` pads should be chosen by obstacle-aware routed cost. This
backend still writes tapered hybrid geometry and is an explicit stronger
option, not the default router.

Do not reinterpret ordinary `corridor` anchors as optional. A plain corridor is
a required pass-through path. For optional channels, the anchors must be marked
with a label token such as `choice_group=BUS`.

Use `routing.multilayer_escape_cell` only when the request allows layer escape
or a single-layer wall blockage makes ordinary routing impossible. It uses a
primary route layer, a bridge layer, and via boxes. It is not a full
multi-layer router: via enclosure, per-layer obstacles, layer costs, and
signoff DRC are still outside the current backend.

After routing, read the structured result. Treat the route as failed if
`ok=false`, any group reports errors, obstacle hits, sibling overlaps, or fewer
routes than expected. Router stress examples must mark expected failures as
failures instead of leaving invalid geometry for visual inspection.

Be explicit about limits. `routing.global_channel_cell` has assignment,
capacity split, and small-bundle route-order search, but not full negotiated
rip-up/reroute. `routing.damped_steiner_cell` is still trunk/branch topology,
not a full Steiner optimizer. Do not present these as signoff routers.

Use `routing.gdsfactory_ports` only when the active MCP Python can import
`gdsfactory`, usually the project `.\venv\Scripts\python.exe`. If unavailable,
report the interpreter/dependency issue instead of trying screenshots or
one-off scripts.

Use `routing.steiner_cell` for same-net fanout or bus nets with more than two
Ports. It is a separate topology router, not a hidden mode of pairwise routing.
It supports unequal Port widths, using each Port width for its branch and the
widest participating Port for the trunk. waypoint, bend, and corridor anchors
apply to the shared trunk.

Use the explicit damping tools only for quality routing requests such as "stay
farther from keepouts", "do not hug obstacles", or "this legal route is too
ugly/close":

```text
routing.damped_segment_cell
routing.damped_polygon_cell
routing.damped_steiner_cell
```

These tools are new backend choices, not default behavior. They accept
`damping_distance_um` and obstacle layers, preserve the same Port/Anchor
semantics as their corresponding non-damped backend, and should be selected by
the agent only when the user's intent calls for soft-clearance routing.

## gdsfactory Port Compatibility

For photonic routing, klink Port markers can be converted to `gf.Port` objects
by `klink.routing.port.port_to_gf`. Prefer the existing gdsfactory bridge over
custom route glue:

```text
route_gdsfactory_ports(...)
select_gdsfactory_port_groups(...)
place_gdsfactory_components(...)
```

The bridge uses Port fields such as `name`, `center_um`, `orientation`,
`width_um`, `target_layer`, `port_type`, and `net`. It supports point-to-point
optical routing by explicit names, prefixes, orientations, one net, or all
exactly-two-port nets.

Do not ask gdsfactory routing to solve a multi-port optical net as a star. Place
an explicit splitter/MMI/Y-branch, mark its generated ports, and route the
resulting two-port nets.

Route existing Port markers with gdsfactory:

```python
from klink import KLinkClient
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

with KLinkClient().connect() as client:
    route_gdsfactory_ports(
        client,
        "TOP",
        port_layer="999/99",
        route_layer="10/0",
        output_mode="batch_polygons",
        all_two_port_nets=True,
    )
```

Place a gdsfactory component and convert its ports into ordinary Port PCells:

```python
from klink import KLinkClient
from klink.routing.backends.gdsfactory.gdsfactory_components import place_gdsfactory_components

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
```

## Existing Layout Exploration

Use this sequence:

```text
layout.info
cell.list / cell.tree
selection.get, if the user selected a target
shape.query(cell=..., ...)
view.show_cell(cell=...), if navigation is needed
```

## pya Macros

Prefer typed RPCs for normal layout edits. Use `exec.python` only as an escape
hatch for unsupported KLayout API operations, compact diagnostics, or one-off
debugging scripts.

## Recorder

The recorder is a replay-script generator, not a literal RPC logger. Bulk RPCs
and `exec.python` mutations may record as expanded replay actions if that is
needed to rebuild the final layout.

Before starting a recording, check `recorder.status`. Do not stop or overwrite
a user recording that is already active.

## Destructive Operations

Only call `layout.clear` or `view.close_tab` on disposable test tabs/layouts
that you created for the task. Do not clear or close the user's manual working
tab.

## Reload Rule

If server-side RPCs changed under `klink_plugin/python/klink_server`, reload the
klink macro/plugin in KLayout. If MCP was already running, restart the MCP
server so the tool catalogue refreshes.

## Conventions

- Coordinates: `bbox_um`, `position_um`, `width_um`, `points_um`, always microns.
- Layers: `layer=L, datatype=D`.
- Cells: string names.
- Rotation: degrees, usually `0`, `90`, `180`, `270`.
- Unknown state: call `layout.info` first.
- Live RPC discovery: `meta.methods`.
