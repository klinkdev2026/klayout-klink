# The control surface

> 中文见 [control-plane.zh-CN.md](control-plane.zh-CN.md)

klink's control plane has two faces over one catalogue: a typed Python client
(`KLinkClient`) and the same methods exposed as MCP tools for agents. Both are
generated from the live server's method registry — there is no separate,
hand-maintained tool list that can drift.

## Discover, don't memorize

The method catalogue is queried live, never hard-coded:

```python
from klink import KLinkClient

with KLinkClient() as c:
    print([m["name"] for m in c.methods()["methods"]])
```

On the MCP side, `tools/list` advertises every tool, and **`klink.find_tools`**
navigates them:

- no arguments → the domain index (one line per area)
- `domain=<token>` → that area's tools plus detailed usage notes
- `query=<keywords>` → ranked matches across all tools

The areas, in index order: `connection_and_view`, `multi_session_transfer`,
`geometry_authoring`, `selection_and_send_memory`, `ports_and_anchors`,
`routing_backends`, `drc_and_lvs_verification`, `device_structdevice`,
`device_nanodevice`, `device_photonics`, `escape_hatch`.

`--profile` filters what the MCP server exposes along two orthogonal axes —
**intent** (`read` / `write` / `verify` / `escape` / `all`) and **domain**
(any area token above). The default is `read,write,verify,escape`; passing a
domain token narrows to that area. `klink.status` reports the active
interpreter, detected optional capabilities, and the KLayout connection state.

## Read state — geometry, not pixels

The read surface is the agent's evidence: `layout.info`, `cell.list` /
`cell.tree`, `layer.list`, `shape.query`, `instance.query`,
`pcell.libraries` / `pcell.list` / `pcell.info`, and `selection.get`.
Screenshots (`view.screenshot`) are a user-requested artifact only, never a
verification step.

## Author with batch RPCs

Never build a generated layout one RPC per object — the loop pays TCP, JSON,
dispatch, transaction, and GUI bookkeeping per call and is often hundreds of
times slower. Use the batch methods:

| Workload | Preferred RPC |
|---|---|
| Many boxes on one cell/layer | `shape.insert_boxes` |
| Mixed shapes in one cell | `shape.insert_many` (`box`, `polygon`, `path`, `text`) |
| Many child-cell instances | `instance.insert_many` |
| Many Basic/library PCell instances | `instance.insert_pcell_many` |

Singleton inserts exist for debugging one object. Edits are wrapped in
transactions; `edit.undo` / `edit.redo` / `edit.status` operate on them.

## Verify

`drc.run` and `lvs.run` run KLayout's native checks; the `structdevice.*`
tools add netlist-driven LVS for custom-device flows. Routing tools return
structured reports (`ok`, obstacle hits, sibling overlaps) — a route counts as
successful only when the report says so.

## Escape hatch — last, not first

`exec.python` runs a controlled `pya` snippet inside KLayout for the cases no
typed RPC covers; it still schedules recorder and layout-diff detection.
`events.channels` / `events.subscribe` expose the plugin's event stream.
Prefer typed RPCs whenever one exists — they validate input, return structured
errors with a `next_action`, and stay visible to the recorder as intent, not
opaque code.
