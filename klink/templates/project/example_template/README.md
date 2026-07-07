# example_template

Copy-and-adapt starter examples, scaffolded here by `klink init` and refreshed
by `klink update`. Each is fully self-contained (imports only `klink`, carries
its own layers) — copy one, change the numbers, and it is your device. Grouped
by category:

```
nanodevice/   hallbar, ebl_wraparound, neural_electrode
photonics/    gf_mzi_module                (needs gdsfactory)
passives/     idc_capacitor, spiral_inductor, saw_idt_filter, baw_fbar_planview
digital/      fit_device_pnr_lvs, padframe_pnr_lvs, chat_to_netlist_pnr,
              multilayer_pnr_lvs           (custom device -> P&R -> live LVS)
```

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
