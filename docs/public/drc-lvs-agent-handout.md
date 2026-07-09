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

All verified on a live session. Metric names: `euclidian` (default, true
shortest distance incl. corners), `projection` (parallel edges only),
`square`.

| construct | meaning | example |
|---|---|---|
| `report("name")` | open report DB (first line) | `report("checks")` |
| `report("name", $output_rdb)` | same, server substitutes the rdb path | — |
| `input(L, D)` | layer L/D as merged polygons | `m1 = input(101, 0)` |
| `.width(x.0 [, metric])` | min width check | `m1.width(2.0, euclidian)` |
| `.space(x.0 [, metric])` | min spacing (incl. notches) | `m1.space(2.0, projection)` |
| `.isolated(x.0)` | spacing, different polygons only | — |
| `.notch(x.0)` | spacing inside one polygon only | — |
| `a.enclosed(b, x.0)` | a must sit ≥ x inside b | `cut.enclosed(m1, 0.5)` |
| `a.enclosing(b, x.0)` | a must extend ≥ x beyond b | — |
| `a.separation(b, x.0 [, opts])` | min distance between two layers | — |
| `a.overlap(b, x.0)` | min overlap depth | — |
| `.sized(x.0)` | grow (negative = shrink) a region | `dev = input(29,0).sized(10.0)` |
| `.sized(-w).sized(w)` | wide-feature derivation (erases anything narrower than 2w) | `wide = m1.sized(-1.5).sized(1.5)` |
| `wide.space(x.0, projection_limits(l.0, nil))` | spacing-table / parallel-run-length rule between wide features (verified form) | `wide.space(3.0, projection_limits(5.001, nil))` |
| `.edges.with_angle(a, absolute)` | select edges by angle | `m1.edges.with_angle(45, absolute)` |
| `.with_length(l.0, nil)` | filter edges by length | — |
| `.with_area(0, a)` | polygons below a minimum area | `m1.with_area(0, 0.09)` |
| `.ongrid(g)` | vertices off the manufacturing grid | `m1.ongrid(0.005)` |
| `.polygons` | error markers → polygons | `errs = m1.space(2.0).polygons` |
| `.outside(region)` | keep markers fully outside region | `errs.outside(dev)` |
| `.output("cat", "desc")` | file violations under a category | — |
| `.forget` | free an intermediate layer (big decks) | `m1_a_l.forget` |
| `&`, `.and()`, `.not()`, `.join()`, `.xor()` | layer booleans | `gate = active & poly` |
| `deep` / `tiles(x.um)` + `tile_borders(y.um)` + `threads(n)` / `flat` | run mode (deep and tiles are mutually exclusive) | — |
| `connect(a, b)` / `connect_global(l, "NET")` / `antenna_check(...)` | connectivity + antenna (separate deck in practice) | — |

## Production-deck shape (copy this structure, not ad-hoc checks)

When asked for more than a quick check, structure the deck the way shipping
PDK decks (IHP SG13G2 / GF180MCU — read them) do:

```ruby
report("BLOCK signoff", $output_rdb)
deep                                     # or tiles(...) for huge layouts

# -- derived layers FIRST (booleans), then rules --
m1     = input(101, 0)
wide_m1 = m1.sized(-0.15).sized(0.15)

# Rule M1.a: min width 0.16 um       <- rule ID matches the DRM
r = m1.width(0.16, euclidian)
r.output("M1.a", "M1.a: Min. M1 width 0.16 um")
r.forget

# Rule M1.e: wide-metal spacing table (space between wide features)
r = wide_m1.space(0.22, projection_limits(1.001, nil))
r.output("M1.e", "M1.e: wide-M1 spacing (PRL > 1.0 um) 0.22 um")
r.forget
```

Rule IDs come from the user's design-rule manual (or the profile field);
values stay in one place (variables at the top / the profile); every rule
ends with `.forget`.

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

## Recipe: full LVS deck (transistor-level, SPICE reference)

When the user has a SPICE schematic and real devices, that is a KLayout
`.lvs` script (official LVS DSL), pipeline fixed as: derive layers →
`connect(a,b)` / `connect_global(l,"VSS")` / `connect_implicit("VDD*")` →
`extract_devices(mos4('name'), {'SD'=>..,'G'=>..,'tS'=>..,'tD'=>..,'tG'=>..,'W'=>..})`
→ `schematic('ref.cir')` → `align` → reductions (`netlist.simplify`,
`netlist.combine_devices`, `netlist.purge`, `max_res(1e9)`, `min_caps(1e-18)`)
→ `success = compare` (+ `flag_missing_ports`) → `report_lvs(path)`. Escape
hatches: `tolerance(dev, param, ...)`, `same_nets`, `equivalent_pins`,
`blank_circuit('IP_*')` for blackboxing. Extractor classes: mos3/mos4,
dmos3/dmos4, resistor(name, sheet_rho), capacitor(name, area_cap), diode,
bjt3/bjt4. Do not invent others; when unsure, read a shipping deck (IHP
sg13g2.lvs) before writing.

## Recipe: klink LVS (declared nets, the routing flow's judge)

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
