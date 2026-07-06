# example_template

Copy-and-adapt starter examples, scaffolded here by `klink init` and refreshed
by `klink update`. Each is fully self-contained (imports only `klink`, carries
its own layers) — copy one, change the numbers, and it is your device. Grouped
by category:

```
nanodevice/   hallbar, ebl_wraparound, neural_electrode
photonics/    gf_mzi_module                (needs gdsfactory)
passives/     idc_capacitor, spiral_inductor, saw_idt_filter, baw_fbar_planview
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

## Heavier examples (not here)

End-to-end digital place-and-route to LVS (custom-device fitting, probe-card
padframes) reads bundled netlists and cross-imports, so those live in the
repository at `examples_klink/public/demos/` and run from a clone, not from this
scaffolded template.
