# DRC and LVS with klink — from first deck to production runset

How to write and run design-rule checks (DRC) and layout-versus-schematic
(LVS) against a live KLayout session — and what a *production-grade* runset
actually looks like, modeled on the open PDKs that ship real KLayout decks
(IHP SG13G2, GlobalFoundries GF180MCU, SkyWater SKY130 — all Apache-2.0 and
worth reading in full).

> 中文见 [drc-lvs.zh-CN.md](drc-lvs.zh-CN.md)
> Handing this to an agent? Give it the condensed recipe instead:
> [drc-lvs-agent-handout.md](drc-lvs-agent-handout.md)

Both checks run **inside KLayout** — klink does not reimplement them. DRC
scripts execute on KLayout's official DRC engine through the `drc.run` RPC;
LVS uses KLayout's native connectivity extraction and netlist comparer. klink
adds the transport, the structured results, and (for DRC) a generator that
derives a starter deck from the same `ProcessProfile` that drives routing and
LVS.

The document is layered: §1 is the language crash course, §2 is the anatomy
of a production runset rule by rule, §3 the same-source starter deck, §4
production LVS, §5 klink's LVS path, §6 the house rules. Skim §1, live in §2
and §4.

---

## 1. The DRC language in ten lines

A DRC script is a Ruby-DSL "runset" executed by KLayout. The authoritative
references are the official manual (*DRC basics*, *DRC runsets*) and the *DRC
reference* pages on `klayout.de`:

```ruby
report("my checks")                 # open a report database (do this FIRST)
m1 = input(101, 0)                  # read layer 101/0 (merged polygons)

m1.width(2.0).output("w1", "M1 width < 2.0 um")       # min width
m1.space(2.0).output("s1", "M1 space < 2.0 um")       # min spacing
cut = input(102, 0)
cut.enclosed(m1, 0.5).output("e1", "cut enclosure in M1 < 0.5 um")

dev = input(29, 0).sized(10.0)      # grow a region by 10 um
errs = m1.space(2.0).polygons       # edge-pair markers -> polygons
errs.outside(dev).output("s2", "spacing outside device regions")
```

Three facts that save hours:

- **A floating-point number means micrometers; an integer means database
  units.** `width(2.0)` checks 2 µm; `width(2)` checks 2 dbu (≈ 2 nm at the
  common 0.001 dbu) and silently passes everything. Production decks write
  explicit units (`0.16.um`, `300.nm`); at minimum always write the decimal
  point.
- **`report(...)` must come before any `.output("name", "desc")`** — string
  outputs file violations into the report database that `report` opens.
- **Checks accept a metric**: `euclidian` (default; true shortest distance,
  measures diagonally across corners), `projection` (parallel edge overlap
  only), `square`. Production metal rules in shipping PDK decks use
  `euclidian` explicitly; §3 explains when `projection` is the right gate.

---

## 2. Anatomy of a production DRC runset

Open any shipping KLayout deck (IHP's `ihp-sg13g2.drc` is ~530 lines of
scaffold plus per-topic rule files; GF180MCU is organized the same way) and
you find the same six-part shape. This is the part hand-rolled "check a few
widths" scripts miss.

### 2.1 Header: run modes, switches, logging

Production decks are parameterized with `$variables` (passed by the runner)
and choose an execution strategy up front:

```ruby
# execution strategy — pick ONE:
deep                        # hierarchical: subcells checked once, not per instance
tiles(500.um)               # OR tiled: bounded memory on huge layouts
tile_borders(30.um)         #    overlap so cross-tile violations aren't missed
threads(Etc.nprocessors)    # parallel tiles
flat                        # OR flat: simplest, fine for small layouts

verbose(true)               # per-rule timing in the log
```

Official constraint: **`deep` and `tiles` are mutually exclusive** — tiling
disables hierarchical mode and vice versa. Real decks expose the choice as a
switch (`$run_mode` = `deep` / `tiling` / `flat`) and default to `deep`; they
also gate rule groups behind "tables" switches (`$no_beol`, `$no_offgrid`,
`$tables = "metal1 via1"`) so a designer can run one section fast during
iteration and the full deck at signoff.

### 2.2 Rule values live OUTSIDE the rules

The deck contains rule *logic*; the *numbers* come from a data file keyed by
rule ID (IHP loads a JSON of `{"M1_a": 0.16, "M1_b": 0.18, ...}`). This is
what keeps a deck reviewable: a process tweak is a data diff, not a code
diff. klink's profile-derived deck (§3) is the same idea — the profile IS the
data file.

### 2.3 Rule IDs match the design-rule manual

Every check is named after its DRM section and embeds the value in the
description. Verbatim shape from a shipping deck:

```ruby
# Rule M1.a: Min. Metal1 width is 0.16µm
m1_a_l = metal1_drw.width(0.16.um, euclidian)
m1_a_l.output('M1.a', '5.16. M1.a: Min. Metal1 width: 0.16 μm.')
m1_a_l.forget
```

Three habits to copy: the DRM ID as the category (`M1.a`), the human sentence
with the number in the description, and **`.forget` after every rule** —
big decks free each intermediate layer explicitly or memory balloons.

### 2.4 Derived layers: booleans BEFORE checks

Real rules rarely apply to a drawn layer directly; they apply to *derived*
layers computed in a dedicated section before any check runs:

```ruby
nactiv  = activ_drw.not(psd_drw.join(nsd_block))    # n-type active
pactiv  = activ_drw.and(psd_drw)                    # p-type active
ngate   = nactiv.and(pwell).and(gatpoly_drw)        # NMOS gate = active & poly
poly_con = gatpoly_drw.not(res_mk)                  # poly that conducts (not resistor)
CHIP    = extent.sized(0.0)                         # the layout extent itself
```

`and/not/join(or)/xor`, `sized`, `holes`, `with_holes`, `interacting`,
`covering`, `not_outside`, `texts(pattern)` are the working vocabulary.
Derivations are also where device recognition happens (marker layer + text
label → device region) — the same derived layers later feed LVS extraction,
which is why production DRC and LVS decks share their derivation files.

### 2.5 The full rule taxonomy

A production deck covers far more than width/space. Each class below shows
the official idiom, taken from shipping decks:

**Width / space / notch** (single layer):

```ruby
m1.width(0.16.um, euclidian).output('M1.a', '...')
m1.space(0.18.um, euclidian).output('M1.b', '...')   # space includes notches
m1.isolated(0.18.um).output(...)                     # different polygons only
m1.notch(0.18.um).output(...)                        # within one polygon only
```

**Two-layer relations**:

```ruby
via1.enclosed(m1, 0.05.um).output('V1.d', '...')     # via sits inside metal
m1.enclosing(via1, 0.05.um).output(...)              # same rule, other POV
gatpoly.separation(cont, 0.11.um).output(...)        # min distance a<->b
a.overlap(b, 0.2.um).output(...)                     # min overlap depth
```

**Spacing tables / parallel-run-length (the "wide metal needs more space"
family)** — the production idiom derives the wide subset with a
shrink-regrow, then checks separation with a run-length qualifier:

```ruby
# Min. space of Metal1 lines if at least one line is wider than 0.3 um
# and the parallel run is more than 1.0 um: 0.22 um
wide_m1 = metal1_drw.sized(-0.15.um).sized(0.15.um)      # keeps only wide metal
metal1_drw.sep(wide_m1, 0.22.um,
               projection_limits(1.001.um, nil)).output('M1.e', '...')
```

`sized(-w/2).sized(w/2)` erases anything narrower than `w` — that is the
standard wide-metal derivation. `projection_limits(lmin, lmax)` restricts the
check to edge pairs whose parallel run is in range — that is the LEF
SPACINGTABLE PARALLELRUNLENGTH semantic, natively.

**Angle-dependent rules** — select edges first, then check:

```ruby
bent45 = metal1_drw.edges.with_angle(45, absolute).with_length(0.501.um, nil)
bent45.width(0.20.um, euclidian).output('M1.g', '45-degree bent width')
```

**Minimum area** — `with_area` selects polygons by area interval; the
violating set is everything below the floor:

```ruby
metal1_drw.with_area(0, 0.09).output('M1.d', 'Min. Metal1 area 0.09 um^2')
```

**Off-grid** — every vertex on the manufacturing grid:

```ruby
metal1_drw.ongrid(0.005).output('M1_Offgrid', 'off 5 nm drawing grid')
```

**Density** — window-based metal density with `with_density(range,
tile_size, tile_step, boundary)`; shipping decks wrap it to add boundary
backup windows, but the primitive is official. Density runs are usually a
SEPARATE deck (they force tiling mode).

**Antenna** — needs connectivity first (`connect(...)` chain from gate up
through each metal), then `antenna_check(gate, metal, ratio, [diodes])`.
Also a separate deck in practice.

**Connectivity-dependent DRC** — some rules only make sense on nets
(latch-up distances to well taps, guard-ring rules). Decks declare
`connect(...)` pairs mid-deck and then use net-aware selections; note there
is **no built-in net-aware `space` check** — different-net spacing beyond
the geometric rule is constructed from `nets` + property-constrained
booleans when truly needed.

### 2.6 Waivers

Fabs waive specific violations via marker layers. The deck subtracts marker
regions from specific rules — **explicitly, per rule, never globally**:

```ruby
waived = input(63, 99)                               # waiver marker layer
viol = m1.space(0.18.um, euclidian).polygons
viol.outside(waived).output('M1.b', '...')
```

Every waiver layer is documented in the deck header; an undocumented
exclusion is a hole in your signoff, not a convenience.

---

## 3. The same-source starter deck: DRC from your ProcessProfile

klink's profile-derived deck is the §2 architecture at day-0 scale: rule
values externalized (in the profile), categories named after their source
field, exclusions explicit. If you route with a `ProcessProfile`, you get a
correct starter deck for free:

```python
from klink.routing.grid.profile_drc import run_drc

res = run_drc(c, profile)               # width/space per routing layer +
print(res["ok"], res["total"])          # cut enclosure per via, all from
for cat in res["categories"]:           # the SAME profile the router and
    print(cat["name"], cat["count"])    # LVS read
```

| rule | value | source field |
|---|---|---|
| width per routing layer | ≥ `wire_width_um` | the drawn wire width |
| space per routing layer | ≥ `wire_clear_um` | different-net clearance |
| cut enclosure per via, both metals | ≥ `litho_tol_um` | via cut inset |

Two knobs, and the reasoning behind them:

- `metrics="projection"` (default) checks exactly what a Manhattan grid
  router promises — parallel-edge clearances — and does not fire on
  right-angle corner artifacts. Production metal rules use `euclidian`
  (§2.3); switch to it when your drawn geometry is meant to satisfy
  corner-to-corner rules too, and expect findings in dense metal that the
  router never promised to avoid. Both runs are one keyword apart; running
  both tells you which class each finding belongs to.
- `exclude_around=(layer_spec, size_um)` scopes width/space away from device
  regions (grown from a marker layer such as the profile's channel layer).
  Device-internal geometry — a source/drain gap smaller than the wire
  clearance — follows *device* rules, not routing rules; production decks
  scope metal rules around device markers the same way (§2.6). Via-enclosure
  checks are never excluded. Declare the exclusion in your example so
  reviewers see it.

Runnable end-to-end proof (positive control, negative control, and the full
deck over the fit-device starter's layout):

```bash
python -m examples_klink.public.features.profile_drc_gate --port <session-port> [--check-demo]
```

Measured on a live session:

```text
[positive control] legal scene: ok=True violations=0
[negative control] bad scene: violations=1 fired=['space_21_0']
[demo gate] DEMO_ADD4: ok=True violations=0
RESULT: PASS (deck passes legal geometry, catches the planted violation)
```

**Where the starter deck ends**: it knows nothing about min-area, density,
antenna, angle, off-grid, spacing tables, or your device rules — those need
process facts the profile doesn't carry. As your process matures, grow the
deck by hand along §2 (keep the profile-derived rules as the routing
section; add DRM-ID'd sections per §2.3–2.5). The three-gate property —
router, DRC, LVS reading one process declaration — is preserved as long as
the routing section keeps deriving from the profile.

### Running any deck through klink

```python
res = c.drc_run(deck_text, output_rdb="<path>.lyrdb", result_mode="summary")
ok = res["exception"] is None and res["rdb_summary"]["total_items"] == 0
```

`$output_rdb` inside the deck is substituted server-side from the
`output_rdb` argument. `result_mode="full"` also returns per-item detail.
The verdict discipline is all-or-nothing; a deck that raised did not pass.

---

## 4. Production LVS: the full pipeline

KLayout's LVS scripts (`.lvs`, same Ruby DSL plus netlist functions) follow
one canonical pipeline in every shipping PDK. Read IHP's `sg13g2.lvs` once
and you can read them all:

```text
source(layout) ──> derive layers ──> connect() ──> extract_devices()
        ──> align ──> netlist options ──> compare(schematic) ──> report_lvs
```

**1. Derivations** — the same boolean layer algebra as DRC §2.4, shared
between the DRC and LVS decks so both judges see identical device regions.

**2. Connectivity**:

```ruby
connect(poly_con, cont_drw)          # layer-to-layer (via/contact)
connect(metal1_con, via1_drw)
connect_global(psub, 'VSS')          # global net: substrate is VSS everywhere
connect_implicit('VDD*')             # join same-labelled nets without geometry
```

Note the *_con derivations: production decks connect `metal1.not(metal1_res)`
— the resistor-marked metal is NOT a conductor, it is a device. Connectivity
correctness is 80% of LVS debugging.

**3. Device extraction** — geometry → device instances with terminals:

```ruby
extract_devices(mos4('sg13_lv_nmos'),
                { 'SD' => nsd_fet,     # source/drain diffusion
                  'G'  => ngate_lv,    # gate region (active & poly)
                  'tS' => nsd_fet,     # terminal recognition layers
                  'tD' => nsd_fet,
                  'tG' => poly_con,
                  'W'  => pwell })     # bulk/well (4th terminal)
```

Official extractor classes: `mos3/mos4`, `dmos3/dmos4`,
`resistor(name, sheet_rho)`, `capacitor(name, area_cap)`, `diode`,
`bjt3/bjt4`. The extractor computes device parameters (W, L, A, P) from the
geometry — those are what `tolerance(...)` later compares.

**4. Schematic + alignment + reductions** — read the reference netlist and
normalize both sides before comparing:

```ruby
schematic('my_block.cir')       # SPICE reference
align                           # flatten cells present on only one side
netlist.simplify                # canonical reductions
netlist.combine_devices        # merge series/parallel devices (fingers!)
netlist.make_top_level_pins
netlist.purge                   # drop floating/unused
max_res(1e9)                    # ignore extreme parasitic-style elements
min_caps(1e-18)
```

**5. Compare + escape hatches**:

```ruby
success = compare && flag_missing_ports   # strict: top ports must be labelled
tolerance('sg13_lv_nmos', 'W', absolute: 5.nm)   # parameter compare tolerance
same_nets('TOP', 'VDD', 'VDD!')           # declared net equivalences
equivalent_pins('MY_MACRO', 'A', 'B')     # interchangeable pins
blank_circuit('SRAM_*')                   # blackbox IP: interface only
```

**6. Verdict** — `compare` returns a boolean; production runners log an
unambiguous PASS/FAIL line and exit nonzero on mismatch. `report_lvs(path)`
writes the cross-probing database (open in KLayout's Netlist Browser).

Hierarchy note: production LVS runs `deep` — devices inside a cell are
extracted once, not per instance, and the comparer works circuit-by-circuit.
This is also what makes `blank_circuit` blackboxing possible.

---

## 5. klink's LVS path (the routing flow's judge)

For klink-routed circuits, you usually don't have a SPICE schematic — you
have *declared nets* (which terminals belong together, from your netlist).
klink's `lvs_check` compares that declaration against KLayout's native
extraction of the drawn geometry:

```python
from klink.domains.structdevice.orchestrators import lvs_check

res = lvs_check(
    c, "MY_TOP",
    declared=[{"net": "n1", "terminals": ["X1.S", "X2.G"]}, ...],
    mode="lvsdb",
    connectivity=profile.connectivity_spec(),   # conductors+vias, same profile
    terminal_provider=...,                       # where each terminal sits
    placement=..., device_terms=...,
)
assert res["ok"] and res["match"]
```

Under the hood this builds the same `connect(...)` graph as §4 step 2 from
the profile's conductor/via lists and reconciles nets AND devices — the
extraction is KLayout's, so the router cannot grade its own homework.
`mode="lvsdb"` writes a native `.lvsdb`; open it with the `view.show_lvsdb`
RPC for two-way layout ↔ netlist cross-probing.

When you need transistor-level device recognition (real MOS extraction with
W/L parameter compare against SPICE), that is §4 — write a real `.lvs` deck
with `extract_devices`; the derivation discipline you built for DRC carries
over unchanged.

Measured output of the fit-device starter (the flow this page's demo checks):

```text
[public] FlexDR ok=True routed=94/94 markers=0
[public] LVS ok=True match=True devices=173
```

---

## 6. House rules (they keep the gates honest)

1. **All-or-nothing verdicts.** DRC passes at zero violations with no
   exception; LVS passes at `match=True` / `compare` true. No "close enough".
2. **Never delete a rule to make a run pass.** Scope it (waiver layer,
   `exclude_around`, `layers=[...]`) with the reason written next to it —
   an undeclared exclusion is a silent hole in your signoff.
3. **Rule IDs and values are data, not prose.** Name categories after your
   DRM (or profile field), keep the numbers in one place, embed the value in
   the description so the report is self-explaining.
4. **Fix the geometry or the declaration, not the judge.** A real finding
   means the drawing or the declared netlist is wrong; editing the deck is
   the last resort and needs a written reason.
5. **Structured evidence only.** Verdicts come from RPC results and report
   databases, not screenshots.
6. **Read the masters.** The IHP SG13G2 / GF180MCU / SKY130 KLayout decks are
   open source and production-grade; when in doubt about an idiom, find it
   there before inventing it.
