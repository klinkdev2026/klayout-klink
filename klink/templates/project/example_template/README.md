# example_template

Copy-and-adapt starter examples, scaffolded here by `klink init` and refreshed
by `klink update`. Each is fully self-contained (imports only `klink`, carries
its own layers) — copy one, change the numbers, and it is your device. Grouped
by category:

```
nanodevice/   hallbar, ebl_wraparound, neural_electrode
photonics/    gf_mzi_module, gf_ports      (need gdsfactory + live KLayout)
passives/     idc_capacitor, spiral_inductor, saw_idt_filter, baw_fbar_planview
layout/       fill_region_demo             (needs a live KLayout session)
digital/      fit_device_pnr_lvs, padframe_pnr_lvs, chat_to_netlist_pnr,
              multilayer_pnr_lvs           (custom device -> P&R -> live LVS)
imaging/      xsection_demo, render3d_demo, sem_demo, blender_demo
ledit_bridge/ draw_device_demo, tcell_workflows, driver
                                           (Tanner L-Edit, not KLayout)
```

## Tanner L-Edit, not KLayout (`ledit_bridge/`)

klink drives a second editor. If the user says **L-Edit / Tanner / .tdb /
T-Cell**, this is the folder — read `ledit_bridge/README.md` first.

```bash
# 1. in L-Edit: Tools > Macro > Load Macro... -> ledit_bridge/ledit_bridge.cpp
python -m klink.doctor                              # reports the bridge too
python example_template/ledit_bridge/driver.py ping # macro version + capabilities
python example_template/ledit_bridge/draw_device_demo.py --new my_lib
```

`draw_device_demo.py` is the copy-and-adapt starter: a static two-finger
NMOS across five layers, with design targeting, idempotent redraw, one
batched request, and read-back verification. `tcell_workflows.py` covers
the parametric T-Cell loop (read / variants / writeback / verify / fit).

## Run one

```bash
# nanodevice / passives write a GDS under test_outputs/ and print a self-check:
python example_template/passives/saw_idt_filter.py
python example_template/nanodevice/hallbar.py

# push into a running KLayout session instead (klink plugin loaded):
python example_template/passives/saw_idt_filter.py --live --port <session-port>
```

The passive-device files (`passives/`) are **geometry templates, not validated
electrical/acoustic designs** — tune the numbers for your process and verify
with your own models.

## Digital place-and-route → LVS (`digital/`)

The `digital/` family is the end-to-end flow: fit a custom device from exemplar
geometry, place it from a netlist, route it, and verify with **live LVS**. These
need a running KLayout session (they do real P&R + extraction), so pass a port:

```bash
python example_template/digital/fit_device_pnr_lvs.py --port <session-port>
python example_template/digital/padframe_pnr_lvs.py --port <session-port> [--no-card]
```

They cross-import within the folder and read the bundled `*.devnet.json`
netlists next to them; keep the folder together when you copy it.
