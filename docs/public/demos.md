# Demos and what they require

> 中文见 [demos.zh-CN.md](demos.zh-CN.md)

The public gallery has eight load-bearing demos under
`examples_klink/public/demos/`. None needs confidential geometry from you. Two
run fully offline; six need a live KLayout session (but still no external GDS);
one of those (the gdsfactory takeover) also needs gdsfactory in the same
interpreter. This page is honest about each.

Everything device- and process-specific lives in the example itself; `klink`
ships zero process constants. Copy a demo and edit its numbers for your own
process — the flow is identical.

> **How you run these depends on how you installed klink.** Eight examples are
> **starters** bundled in the wheel: `klink init <proj>` scaffolds them into
> `<proj>/example_template/`, grouped by category, and a `pip install` user runs
> them as `python example_template/<category>/<name>.py`:
>
> | category | starters |
> |---|---|
> | `nanodevice/` | ebl_wraparound, hallbar, neural_electrode |
> | `photonics/` | gf_mzi_module |
> | `passives/` | idc_capacitor, spiral_inductor, saw_idt_filter, baw_fbar_planview |
>
> The four digital P&R demos (**fit_device_pnr_lvs, chat_to_netlist_pnr,
> multilayer_pnr_lvs, padframe_pnr_lvs**) are **repo-only**: they read a bundled
> netlist and cross-import each other, so they run from a clone of the repository,
> not from a pip install. The command shown under each demo below is the repo
> `python -m …` form; if you are a starter user, run the
> `python example_template/<category>/<name>.py` form instead.

## Runs offline (no KLayout, no GDS)

### EBL nanodevice wraparound

```bash
python -m examples_klink.public.demos.ebl_wraparound          # [--live] [--keep]
```

A parametric electron-beam-lithography wraparound generator. Offline it prints
the generated bundle; `--live` writes to a KLayout session. Measured output:
`"ok": true`, 40 electrodes, 12 patches, writefield 16 fields / 11 windows /
20 crossings / **0 violations**, **0 overlaps**.

### Hall bar nanodevice

```bash
python -m examples_klink.public.demos.hallbar                 # [--live] [--keep]
```

A parametric Hall-bar generator. Offline it prints the semantic bundle plus the
routed result; `--live` writes a disposable KLayout cell (deleted unless
`--keep`).

## Passive-device geometry templates (offline; `--live` needs KLayout)

Four parametric passive-device templates — **starters** in the `passives/`
category, so a `pip install` user runs them as
`python example_template/passives/<name>.py` (repo clones can also use the
`python -m …` module form shown in the `# repo clone:` line under each). Offline
(the default) each writes a GDS under `test_outputs/` and prints a structured
self-check summary; `--live [--port <session-port>]` pushes a disposable cell
to a KLayout session instead. Every family marks klink Ports (999/99) on its
electrical terminals, so the routing backends work on them out of the box.
Each is a **geometry template, NOT a validated electrical/acoustic design** —
tune the numbers for your process and verify with your own models (SAW/BAW
make no frequency or material claims).

### IDC — interdigitated capacitor

```bash
python example_template/passives/idc_capacitor.py        # [--live --port <session-port>]
# repo clone: python -m examples_klink.public.passives.idc_capacitor
```

Two opposing bus bars with alternating fingers: pitch = finger width + gap,
and every finger stops `gap` short of the opposite bus. Measured output
(defaults, 10 fingers): merged regions **2** (no short), total width 33.5 µm,
2 ports (`P1`/`P2`).

### Square spiral inductor

```bash
python example_template/passives/spiral_inductor.py      # [--live --port <session-port>]
# repo clone: python -m examples_klink.public.passives.spiral_inductor
```

An outward-wound square spiral on the top metal; the trapped inner end
escapes through a via + crossunder strip running beneath the turns. Measured
output (defaults, 3 turns): **1 merged region per metal layer** (continuous
track, no self-short), underpass crosses 4 track segments, via fully inside
both the inner-end pad and the underpass, 2 ports (`OUT`/`IN`).

### SAW IDT filter

```bash
python example_template/passives/saw_idt_filter.py       # [--live --port <session-port>]
# repo clone: python -m examples_klink.public.passives.saw_idt_filter
```

Two identical IDTs facing each other along the acoustic axis (electrode
width = pitch/4, metallization ratio 0.5; uniform overlap — apodization not
modeled), with optional shorted-grating reflectors outside each IDT.
Measured output (defaults, 12 pairs at pitch 4 µm): **2 merged regions per
IDT** (no finger short), **1 per reflector grating**, electrode width
1.0 µm, 4 ports (`TX_P`/`TX_N`/`RX_P`/`RX_N`).

### BAW / FBAR plan view

```bash
python example_template/passives/baw_fbar_planview.py    # [--live --port <session-port>]
# repo clone: python -m examples_klink.public.passives.baw_fbar_planview
```

Plan view of a membrane-type resonator: the top electrode is an irregular
pentagon with **no two edges parallel** (the spurious-mode apodization
convention), deterministically scaled to the target active area; the bottom
electrode extends past it to its own pad on the opposite side, and a
descriptive `StackSpec` documents the intended vertical stack as data (plan
view only — no film stack is drawn). Measured output (defaults, 2000 µm²
target): no-two-edges-parallel **true**, pentagon area 1999.996 µm² (within
1%), top/bottom overlap = 100% of the pentagon, 2 ports (`TOP`/`BOT`).

## Runs live (KLayout + plugin, still no GDS)

### Neural-electrode harness

```bash
python -m examples_klink.public.demos.neural_electrode --port <session-port> --elec-rows 4
```

Self-contained probe generator: defines pad/via geometry and Port/Anchor
resources, then calls the tapered-hybrid router. Measured output (4 rows):
`ok: True`, 48 ports, 24 nets routed (12 on `1/0` + 12 on `3/0`),
**sibling-overlap 0**, **obstacle-hit 0**. Use an empty or test-owned session.

### Fit a device → digital place & route → LVS

```bash
python -m examples_klink.public.demos.fit_device_pnr_lvs --port <session-port>   # [--draw-only]
```

The full self-contained digital flow, IP-free: fit a parametric device PCell
from **synthetic** exemplar geometry, place it, run detailed routing, and verify
with live LVS. Measured output: routed 94/94, **LVS `match=True`**, 173 devices.
Swap in your own harvested exemplar boxes to fit your real device — the flow
does not change.

### Hand-written netlist → lint → place & route → LVS

```bash
python -m examples_klink.public.demos.chat_to_netlist_pnr --port <session-port>
```

The "describe it in chat, get a verified layout" flow: a 3-stage ring
oscillator netlist is written BY HAND (each requirement of the imagined
conversation maps to a few explicit netlist lines), validated by
`lint_netlist` (every structural mistake gets a fix-it message BEFORE any
geometry exists), then placed, routed, LVS-verified, and every stage node is
brought out as a bare labelled trace at the periphery. Measured output: lint
0 errors, routed 3/3, **LVS `match=True`**, 6 devices, all 3 taps
extraction-verified CONNECTED. Netlists are plain data — an agent (or you)
can write one for ANY topology, no logic synthesizer required.

### Multilayer place & route at scale

```bash
python -m examples_klink.public.demos.multilayer_pnr_lvs --port <session-port>
```

The scale demo: a bundled 766-device synthetic netlist (a toy 4-bit ALU, 268
gates, netted by an open logic synthesizer, remapped onto this gallery's
synthetic fitted devices) is linted, then compared on two example process
stacks with the built-in layer-count advisor — the public 3-layer process
from the fit-a-device demo above, and a second 7-layer example stack defined
in the demo itself (2 vertical + 2 horizontal clean signal layers over the
device terminals). The advisor prints the core-area cost of each so you can
see why a design this size wants the extra layers while the smaller demos
above are comfortable on 3. It then places, marks every one of the design's
20 primary ports (13 in on the west edge, 7 out on the east), and routes with
the multilayer routing engine. Measured output: routed 405/405 nets, **LVS
`match=True`**, 766 devices, all 20 ports extraction-verified CONNECTED, in
about 17 seconds end to end. Copy this file and edit `PUBLIC_MULTILAYER` for
your own layer stack — the flow does not change.

### Probe-card-first place & route

```bash
python -m examples_klink.public.demos.padframe_pnr_lvs --port <session-port>   # [--no-card]
```

The reversed-order hardware flow: the probe card / pad ring **exists first**
(positions frozen long ago) and the circuit must meet it — even when the card
interior is too small for the whole block. The same synthetic 4-bit adder
and fitted devices as the fit-a-device demo are linted, then a stand-in 20-pad
probe card is fabricated and **harvested back** with `pads_from_gds` (in real
life you skip the fabricate step and harvest your own card file). A plain
net→pad table assigns all 14 primary ports + VDD + GND (4 redundant pads stay
unused); because the card interior fits only half the rows, `place_grid(
forbid_y_bands=…)` splits the block **half inside / half below** the card's
bottom pad row, and `pdn_split_bands` threads one power grid per region bridged
by a spine strap. Measured output: routed 94/94, **LVS `match=True`**, 173
devices, half-in/half-out 85 inside / 80 below, all 16 assigned pads
extraction-verified CONNECTED and all 4 redundant pads isolated. `--no-card`
drops the card entirely: every port leaves as a bare labelled wire-end trace at
the periphery (inputs west, outputs east, snapped to routing-channel centres),
power on the auto-labelled PDN tie rails — routed 94/94, **LVS `match=True`**,
all 14 stubs CONNECTED. Copy this file and edit the pad table for your own card.

> A KLayout "port" is just a session — any port works; none has a special role.
> Use a session that is empty or test-owned, not your manual working tab.

## Silicon-photonics (gdsfactory bridge)

### gdsfactory takeover → editable photonic module

```bash
python -m examples_klink.public.demos.gf_mzi_module --port <session-port>
```

A complete thermo-optic MZI — tilted fiber GC → 1×2 MMI splitter → two thermal
phase-shifter arms (bottom mirrored) → 2×2 MMI combiner → offset output GCs,
plus heater pad rows and a fiber-loopback pair — written as an **ordinary
gdsfactory script**, then taken over by a single `import_gf_component` call. One
persisted net table then holds every kind of net: the script's own optics
(re-drawn by klink), the offset output bank restyled to `sbend`, the tilted GC
that a Manhattan router can't reach (`all_angle`), the loopback pair (`dubins`
arcs), and the heater→pad **electrical** nets on the metal layer. A single
`photonics.reroute` redraws all of them — so after you **drag any component in
the KLayout GUI, one reroute re-routes optics and metal together**. That drag →
reroute loop is the point: the layout stays live and editable, not a frozen
one-shot. From a plain shell you re-route with the `--reroute` flag:

```bash
# ... drag a component in KLayout ...
python -m examples_klink.public.demos.gf_mzi_module --port <session-port> --reroute
```

`--reroute` re-routes from the dragged positions **without rebuilding**, so it
keeps your edit. Re-running the script with **no** flag rebuilds the module from
the gdsfactory source and snaps every component back to its original spot,
undoing the drag — a common first-time surprise, so the flag is the fix. (An
agent with the MCP tools calls `photonics.reroute` directly instead.) Measured
output: import ok, 6 optical nets / 13 instances / 5 device cells; reroute ok,
12 routes, **0 crossings, 0 device-hits**.

This demo needs **gdsfactory in the same interpreter** that runs it (it builds
the module client-side before pushing to KLayout). The demos are pinned to the
tested line — `pip install "klayout-klink[photonics]"` gets a known-good
gdsfactory. If gdsfactory already lives in another venv, add klink into *that*
venv (`<that-venv>/python -m pip install klayout-klink`) and run from there —
do not sys.path-hack the repo into a foreign interpreter (that path leads to
version-mismatch and 1000×-off geometry). See the demo's own `## Requirements`
header for the full rule.

### Lower-level bridge examples

The gdsfactory-port routing examples live under `examples_klink/public/features/`
(e.g. `24_gdsfactory_route_ports.py`, `30_gdsfactory_routing_zoo.py`). They use
the open `gf.gpdk` — same interpreter rule as above, no proprietary PDK.

## Reference

- [getting-started](getting-started.md) — install, configure, first result.
- [recipes](recipes.md) — per-domain starting points.
- [project-model](project-model.md) — the `klink init` project scaffold.
