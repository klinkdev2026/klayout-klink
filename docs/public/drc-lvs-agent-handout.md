# Agent handout: writing KLayout DRC and LVS through klink

> 中文版:[drc-lvs-agent-handout.zh-CN.md](drc-lvs-agent-handout.zh-CN.md)
> Humans: the full tutorial is [drc-lvs.md](drc-lvs.md). This page is written
> to be pasted, whole, into an agent's context. It is self-contained.

You are operating a live KLayout session through klink (MCP tools or the
`KLinkClient` Python client). Follow this recipe exactly. Do not invent DRC
methods — the whitelist below is the complete vocabulary you may use; every
entry is documented in the official KLayout DRC reference and verified
against a live session.

## Rules that override everything else

1. **Dimensions: always write a decimal point.** `width(2.0)` = 2 µm.
   `width(2)` = 2 *database units* (≈ 2 nm) — it passes everything and tells
   you nothing. If you write an integer dimension you have made an error.
2. **`report("name")` is always the first line** of a deck. `.output("cat",
   "desc")` files violations into it; output before report fails.
3. **Verdicts are all-or-nothing.** A DRC run passes only if the response's
   `exception` is `None` AND `total_items` == 0. LVS passes only on
   `match=True`. Never report success otherwise; never weaken a rule to make
   a run pass. If a rule must be scoped, say so explicitly and why.
4. **Never verify with screenshots.** The RPC results are the evidence.
5. If you need a construct not in the whitelist, STOP and ask, or run a
   2-line probe deck first to confirm the syntax on the live engine.

## DRC construct whitelist

| construct | meaning | example |
|---|---|---|
| `report("name")` | open report DB (first line) | `report("checks")` |
| `report("name", $output_rdb)` | same, server substitutes the rdb path | — |
| `input(L, D)` | layer L/D as merged polygons | `m1 = input(101, 0)` |
| `.width(x.0 [, metric])` | min width check | `m1.width(2.0, projection)` |
| `.space(x.0 [, metric])` | min spacing check | `m1.space(2.0, projection)` |
| `a.enclosed(b, x.0)` | a must sit ≥ x inside b | `cut.enclosed(m1, 0.5)` |
| `a.enclosing(b, x.0)` | a must extend ≥ x beyond b | — |
| `a.separation(b, x.0)` | min distance between two layers | — |
| `.sized(x.0)` | grow a region | `dev = input(29,0).sized(10.0)` |
| `.polygons` | error markers → polygons | `errs = m1.space(2.0).polygons` |
| `.outside(region)` | keep markers fully outside region | `errs.outside(dev)` |
| `.output("cat", "desc")` | file violations under a category | — |
| `&`, `.and()`, `.not()` | booleans between layers | `gate = active & poly` |
| metric names | `projection` (parallel edges only), `euclidian` (default, corners too), `square` | — |

## Recipe: run a DRC deck

1. Discover layers first (`layer.list` / `layer_list`) — never guess layer
   numbers.
2. Write the deck: `report(...)` first, one `.output(...)` per rule, decimal
   points on every dimension.
3. Run it with a report file and read the summary:

```python
res = client.drc_run(deck, output_rdb="<path>.lyrdb", result_mode="summary")
# gate:
ok = res["exception"] is None and res["rdb_summary"]["total_items"] == 0
```

4. On violations: report each category name and count; do NOT re-run with
   weakened numbers. Investigate the geometry (shape queries) instead.
5. `projection` vs `euclidian`: grid-routed Manhattan metal is judged by what
   the router promises — `projection`. Use `euclidian` when asked for the
   stricter fab-style measure and expect right-angle corner findings.

## Recipe: profile-derived deck (preferred when a ProcessProfile exists)

If the project routes with a `ProcessProfile`, do not hand-write rules — the
generator derives them from the same profile the router and LVS use:

```python
from klink.routing.grid.profile_drc import run_drc
res = run_drc(client, profile)          # optionally:
#   metrics="euclidian"
#   exclude_around=(profile.channel_layer, <size_um>)   # device regions
assert res["ok"], res["categories"]
```

`exclude_around` is the ONLY sanctioned way to scope width/space away from
device-internal geometry (device gaps follow device rules, not routing
rules). Always state the exclusion and its size when you use it. Via
enclosure checks are never excluded.

A working reference with a positive AND negative control (run it to see the
expected shape of a healthy gate):

```bash
python -m examples_klink.public.features.profile_drc_gate --port <port>
```

## Recipe: LVS

```python
from klink.domains.structdevice.orchestrators import lvs_check
res = lvs_check(client, top_cell,
                declared=declared_nets,                  # [{"net": ..., "terminals": [...]}]
                mode="lvsdb",
                connectivity=profile.connectivity_spec(),
                terminal_provider=..., placement=..., device_terms=...)
ok = res["ok"] and res["match"]
```

- `declared` comes from the netlist you were given — never fabricate nets.
- `connectivity` comes from the profile — never hand-write conductor/via
  lists when a profile exists.
- If `match` is False: report which nets/devices mismatched (the result
  carries them); the fix is in the geometry or the declaration, not in the
  connectivity spec.
- `mode="lvsdb"` writes a database the human can open with the
  `view.show_lvsdb` tool — offer it, don't screenshot.

## If the tool returns an error

klink errors are instructions: read the message and `next_action` field and
do what it says (usually a missing argument, a missing dependency with the
exact pip command, or a live-session precondition). Do not retry the same
call unchanged, and do not invent workarounds around a named precondition.
