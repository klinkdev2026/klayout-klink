# Demos and what they require

> 中文见 [demos.zh-CN.md](demos.zh-CN.md)

The public gallery ships six load-bearing demos under
`examples_klink/public/demos/`. **All six run out of the box** — none needs
confidential geometry from you. Two run fully offline; four need a live KLayout
session (but still no external GDS). This page is honest about each.

Everything device- and process-specific lives in the example itself; `klink`
ships zero process constants. Copy a demo and edit its numbers for your own
process — the flow is identical.

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

> A KLayout "port" is just a session — any port works; none has a special role.
> Use a session that is empty or test-owned, not your manual working tab.

## Silicon-photonics routing (feature examples)

The gdsfactory bridge examples live under `examples_klink/public/features/`
(e.g. `24_gdsfactory_route_ports.py`, `30_gdsfactory_routing_zoo.py`). They use
the open `gf.gpdk`, so they need `pip install gdsfactory` in the same
interpreter — no proprietary PDK required.

## Reference

- [getting-started](getting-started.md) — install, configure, first result.
- [recipes](recipes.md) — per-domain starting points.
- [project-model](project-model.md) — the `klink init` project scaffold.
