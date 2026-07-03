# klink agent lanes

`lanes.json` is the **harness-neutral source of truth** for how klink's MCP
tool surface is split between a primary conversation ("main lane") and
restricted sub-agent roles. It is not Claude Code specific: any agent harness
that can connect an MCP client and restrict tools per role (Claude Code,
Codex CLI, Cursor, Cline, a custom Agent SDK frontend, ...) can implement
these lanes.

## Why lanes

The klink MCP server exposes ~98 tools when KLayout is connected. Deferred
tool loading makes the raw count cheap, but it does not stop the main
conversation from doing sub-operations inline and flooding its context with
route reports and per-object calls. Lanes solve the real problem: **who is
allowed to see and call what**.

- `main` — strategy, user-intent binding (interaction context), session and
  transfer orchestration, destructive ops with confirmation.
- `layout-verify` — read-only structured verification + DRC. Safe to run
  autonomously; cheap model.
- `layout-route` — routing backends; digests big lane reports and returns
  summaries.
- `layout-build` — declarative spec execution with batch RPC discipline.
- `pya-exec` — `exec.python` escape hatch, explicitly dispatched only.

## Name mapping

`lanes.json` uses canonical klink MCP tool names (`shape.insert_boxes`).
Harnesses transform names; e.g. Claude Code exposes
`mcp__<server>__shape_insert_boxes` (dots → underscores, server prefix).
`@read_core` inside a lane's `mcp_tools` expands to the shared
`shared_tool_sets.read_core` list.

## Adapters

- Claude Code: `.claude/agents/layout-verify.md`, `layout-route.md`,
  `layout-build.md`, `pya-exec.md` in the project root. Frontmatter
  `tools:` is the allowlist; body is the lane's operating rules. Keep them
  consistent with `lanes.json` when editing either side.
- Other harnesses: read `lanes.json` directly, expand `@read_core`, map
  names, and attach the `rules` strings to the role's system prompt.
  `harness_tools` uses neutral capability names (`file-read`, `file-write`,
  `grep`, `glob`, `shell`) — map them to whatever the harness calls them.

## Build/verify spec contract (draft v1)

The main lane communicates with `layout-build` / `layout-verify` through
declarative spec files under `.klink/specs/`, not free-form prose. Draft
shape:

```json
{
  "spec_version": 1,
  "task": "harness_demo_rows8",
  "target_cell": "HARNESS_ROWS8",
  "disposable": true,
  "selection_refs": ["sel_0006"],
  "layers": ["1/0", "3/0", "999/99", "999/1"],
  "operations": [
    {"op": "boxes", "layer": "1/0", "boxes_um": [[0, 0, 10, 1]]},
    {"op": "shapes", "items": [{"kind": "path", "layer": "3/0",
      "points_um": [[0, 5], [10, 5]], "width_um": 0.2}]},
    {"op": "pcells", "library": "Basic", "items": []},
    {"op": "ports", "items": [{"name": "P_IN0", "net": "in0",
      "position_um": [0, 0], "orientation_deg": 180, "width_um": 2.0}]},
    {"op": "anchors", "items": []}
  ],
  "verify": {
    "expected": {"port_count": 24, "layer_shape_counts": {"1/0": 8}},
    "rules": [
      "each fanout trace has a unique channel",
      "vias sit at pad centers on top of trace endpoints"
    ]
  }
}
```

Conventions:

- `selection_refs` carry resolved interaction-context snapshots into the
  sub-agent; sub-agents do not query interaction memory themselves.
- `disposable: true` means the build agent may clear/rebuild the target cell;
  otherwise it is surgical-edit only.
- `verify.expected` is machine-checkable; `verify.rules` are checklist rules
  for layout-verify to evaluate with geometry queries.
- A build result report states object counts per layer/kind; a verify report
  states rule id, pass/fail, and evidence.

The contract is intentionally minimal; extend it in this file first, then in
consumers.
