# klink — AI-native control plane for KLayout

Let an AI agent (Claude Code / Codex) directly control the KLayout layout
editor: draw shapes, manage cells/layers, place instances, run pya macros, and
generate layouts with gdsfactory.

## Architecture

```
AI agent
├── MCP: klink-mcp           ← klink RPCs exposed as MCP tools
├── Skill: klayout            ← RPC + pya macro domain knowledge
└── Skill: klayout-gdsfactory ← gdsfactory → KLayout bridge
       │
       ▼
KLayout (klink_plugin)        ← port 8765 (RPC) + 8082 (klive-compat)
```

Do not rely on a hard-coded RPC count. Query the live server:

```python
from klink import KLinkClient

with KLinkClient().connect() as c:
    print([m["name"] for m in c.methods()["methods"]])
```

MCP tools are organized along two orthogonal axes (`klink/mcp/catalog.py`):
**intent/capability** (`read`/`write`/`verify`/`escape`/`all`) and
**domain/area** (`device_photonics`, `routing_backends`, …). `tools/list`
always advertises every tool; call **`klink.find_tools`** to navigate them
(no args → domain index; `domain=<token>` → that area's tools; `query=<keywords>`
→ ranked matches). `--profile` accepts both axes — default
`read,write,verify,escape`.

## Setup

Install the package (`pip install klayout-klink`) and the KLayout salt plugin,
then configure your agent's MCP server:

```json
{
  "mcpServers": {
    "klayout": {
      "command": "<python-that-has-klink>",
      "args": ["-m", "klink.mcp", "--profile", "read,write,verify,escape",
               "--session-id", "project-klink"],
      "env": {
        "KLINK_CONTEXT_ROOT": "<your-project>/.klink/sessions"
      }
    }
  }
}
```

Interpreter rule: optional libraries (gdsfactory, klayout, numpy, …) must live
in the SAME Python that runs `klink.mcp`. klink does not bundle them — install
the one a feature needs yourself; the tool error names it. `klink.status`
reports `interpreter` and `capabilities` so you can verify.

## Environment

- klink core is pure Python; its only runtime dependencies are its own two Rust
  kernels (prebuilt wheels, byte-parity with the pure-Python reference), so
  `pip install klayout-klink` brings klink + both accelerators. They are
  speed-only (pure-Python fallbacks exist); `pip install --no-deps` gives the
  pure-Python core alone.
- gdsfactory must be in the SAME interpreter that runs `klink.mcp`.
- klink RPC port: 8765; klive-compat port: 8082.
- interaction context memory: `.klink/sessions/<session-id>/interaction_context.jsonl`

## Agent Operating Rules

### Tool design rule

Any tool documented for agents to call follows: one user intention = one call,
state persisted on disk not in agent memory, errors are instructions (carry a
`next_action`), validate-before-mutate. Reference implementation:
`photonics.connect` / `photonics.reroute`.

### klink process purity

`klink/` ships only MECHANISM: the `ProcessProfile`, `ConnectivitySpec`, and
`StackSpec` classes plus the routing/LVS algorithms. It holds ZERO process data
— no hardcoded layers, devices, DRC numbers, or PDK instances. Every
process-specific fact is example- or project-owned: your `pdk.py` (scaffolded by
`klink init`) holds the layers / vias / device library and is passed EXPLICITLY
into the klink APIs.

To build a circuit you WRITE OR RUN an example that imports the process + device
library from your `pdk.py` and passes them EXPLICITLY into the klink APIs — never
bake process/device values into `klink/`. A complete, self-contained reference
that owns its own process (layers / spacing / vias) and a synthetic device is:

```text
examples_klink/public/demos/digital/fit_device_pnr_lvs.py
```

It fits a parametric device PCell from exemplar geometry, places it, runs
detailed routing, and verifies with live LVS — importing only `klink`. Copy it
and edit the numbers for YOUR process.

Agent-facing consequence: the `structdevice.*` MCP tools ship NO process, device
library, or terminal recipe. Called without them they return an INSTRUCTIVE
error (not a crash) naming the exact next step. READ the error's `next_action`
and follow it; do NOT invent a `ProcessProfile` or device library yourself.

### Selection-first layout debugging

Never call `view.screenshot` unless the user explicitly asks for a
screenshot/visual artifact in the current conversation. Prefer structured
geometry state:

```text
selection.get
interaction.selection.latest / recent, when available
shape.query
layout.info
cell.list / cell.tree
layer counts
```

Screenshots are user-requested artifacts only, not agent evidence.

### Interaction context memory

`klink-mcp` keeps session-scoped selection memory outside the KLayout plugin.
Memory is recorded when the user clicks the KLayout `SEND` toolbar action or an
agent calls `selection.send_context`. Use the memory tools whenever the user
refers to context they sent/pinned ("just sent", "this area", "here",
"that one"):

```text
interaction.selection.recent   -> latest stored selections, default latest 5
interaction.selection.latest   -> latest stored selection
interaction.selection.get      -> exact stored id
interaction.selection.label    -> attach label/description to an important id
interaction.context            -> current selection plus recent memory
```

Use `selection.get` when the user says "current selection" or you need exact
current KLayout state. Do not treat age as the default invalidation rule;
resolve by order/count first, with the latest five selections as the default
window.

### Prefer typed RPCs over raw `exec.python`

Use normal RPC methods for layout operations whenever possible. Use
`exec.python` / `pyexec` only as an escape hatch for unsupported KLayout `pya`
operations, debugging, or compact one-off scripts.

### Use batch RPCs for generated layouts

Never create large layouts with one RPC per object unless you are debugging a
single-object behavior. The single-object loop is often hundreds of times
slower.

| Workload | Preferred RPC |
|---|---|
| Many boxes on one cell/layer | `shape.insert_boxes` |
| Mixed shapes in one cell | `shape.insert_many` |
| Many child-cell instances | `instance.insert_many` |
| Many Basic/library PCell instances | `instance.insert_pcell_many` |

```python
from klink import KLinkClient

with KLinkClient() as c:
    cell = c.cell_create("batch_demo")["cell"]
    li = c.layer_ensure(1, 0)["layer_index"]
    boxes = [[i * 2, 0, i * 2 + 1, 1] for i in range(1000)]
    c.shape_insert_boxes(cell, layer_index=li, boxes_um=boxes)
```

For unknown PCell parameters, inspect first: `c.pcell_libraries()`,
`c.pcell_list("Basic")`, `c.pcell_info("CIRCLE", library="Basic")`.

Boundaries: `shape.insert_many` is shape-only (`box`, `polygon`, `path`,
`text`); child-cell placement belongs to `instance.insert_many`; PCell placement
belongs to `instance.insert_pcell_many`.

### Routing MCP tools

Prefer the typed routing tools over ad-hoc snippets:

```text
routing.tapered_hybrid_cell    -> main Harness/Anchor backend
routing.tapered_polygon_cell   -> continuous tapered polygon
routing.steiner_cell           -> multi-terminal nets
routing.damped_*               -> stay farther from keepouts/pins
routing.global_channel_cell    -> capacity-aware channel assignment
routing.multilayer_escape_cell -> bridge-layer + via escape (narrow)
routing.gdsfactory_ports       -> photonic route_bundle (needs gdsfactory)
```

After any routing call, inspect the structured result. Do not call a route
successful if `ok=false`, `obstacle_hit_count>0`, sibling-overlap is nonzero, or
the tool reports `route failed`. Be honest about router limits.

### gdsfactory Port compatibility

klink Port markers drive gdsfactory routing through the external Python bridge.
Prefer the existing helpers over custom glue:

```python
from klink import KLinkClient
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

with KLinkClient().connect() as client:
    report = route_gdsfactory_ports(
        client, "TOP", route_layer="10/0",
        output_mode="batch_polygons", clear=True, all_two_port_nets=True,
    )
```

A net with three or more optical ports needs an explicit splitter/MMI/Y-branch;
the resulting exactly-two-port nets are then routed.

### Recorder semantics

The recorder is a replay-script generator, not a literal RPC logger. Before
starting recorder tests, check no user recording is active
(`c.recorder_status()`).

### Destructive RPC safety

`layout.clear` and `view.close_tab` must be tested only on disposable test tabs
or test-owned layouts. Do not clear or close the user's current working tab.

### Reload after server RPC changes

If you add or rename server-side RPC methods under
`klink_plugin/python/klink_server/methods/`, reload the klink plugin in KLayout
before running integration tests, or the live server returns
`ERR_UNKNOWN_METHOD`.

## Key files

- `klink/client.py` — Python client
- `klink/mcp/` — MCP bridge
- `klink/mcp/catalog.py` — MCP tool catalog + `find_tools`
- `klink/domains/` — domain packages (photonics, nanodevice, structdevice,
  measurement), each behind one-call orchestrators
- `klink/spec/` — klink.spec.json v1 contract
- `klink_plugin/python/klink_server/` — KLayout in-process server
- `examples_klink/public/` — the open, open-box-runnable example gallery
- `docs/public/` — release documentation

## Layout Design Pitfalls

General lessons that apply to any layout with array + fanout + pad structure.

### Fanout routing

- **Pitch mismatch.** Array pitch and probe-pad pitch are different scales.
  Fanout routing is the bridge — never drop pads directly onto array lines.
- **Channel isolation.** Every fanout trace needs its own independent channel.
  If two traces on the same layer share an x or y coordinate, KLayout merges
  them into one shape (electrical short). Use diagonal paths so each trace takes
  a different route.
- **Path end-cap mismatch.** `pya.Path` end caps are perpendicular to the path
  direction; a 45° diagonal meeting a horizontal rectangle leaves a seam. Fix
  with a 4-point straight–diagonal–straight pattern so the caps align with the
  rectangle/pad edges.

### Via placement

A via must sit at the pad center, directly on top of the routing-trace endpoint.
If the trace doesn't reach the pad center, the via is electrically floating.

### Surgical editing

When fixing a layout, only clear and redraw the broken layers (e.g. via
`exec.python` calling `top.shapes(li).clear()` on specific layers) rather than
undoing everything. This preserves correct layers and saves time.

### Common mistakes checklist

- [ ] Pads sized for probing (≥8 µm) with sufficient spacing (≥2 µm gap)
- [ ] Each fanout trace has a unique route, no shared coordinates
- [ ] Path end caps align with rectangles (straight–diagonal–straight)
- [ ] Vias are on the trace, at the pad center
- [ ] All layers present before drawing (check with `layer.list`)
- [ ] Alignment marks at all four corners
- [ ] Labels outside pad area, not overlapping
