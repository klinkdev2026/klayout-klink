# examples_klink/public/ — the PUBLIC example gallery (open, open-box runnable)

This is the curated, open-source example set — the public subset of
`examples_klink/`, the same way `docs/public/` is the public subset of the dev
docs. **Only this folder ships in the open-source repo** (the rest of
`examples_klink/` is dev-only).

Contract for anything in here:

- **open** — no NDA / proprietary-PDK content (no foundry PDK cells), no
  committed private GDS;
- **open-box runnable** — imports only `klink` (no dev-only PDK module, no
  device-geometry GDS), so it runs against a plain `pip install klayout-klink`
  + a live KLayout/plugin (some also run offline). Third-party libs a feature
  needs (e.g. gdsfactory) are an ordinary `pip install`.

## Buckets

- `demos/` — the flagship runnable examples + the **STARTERS** (neural_electrode,
  ebl_wraparound, hallbar, fill_region_demo, ...) that are ALSO bundled in the
  wheel / scaffolded by `klink init` into a user project's `example_template/`. `gf_mzi_module.py`
  is the gdsfactory-takeover flagship: a complete thermo-optic MZI (GCs,
  MMIs, heater arms, pads) written as an ordinary gf script, taken over by
  `import_gf_component` — klink re-routes its optics AND heater metal from
  one persisted net table; drag anything, `photonics.reroute` redraws both.
- `features/` — per-feature examples: routers (tapered / steiner / damped /
  global-channel stress), Port/Anchor walkthroughs, gdsfactory bridge (open
  `gf.gpdk`), measurement import, harness/probe generators.
- `smoke/` — a few real capability demos (draw, pcell zoo, cells & shapes,
  delete/undo, exec, route run). The dev diagnostic probes are NOT here.

## Migration to the shipped template

The wheel ships only the STARTER subset (`public/demos/`), scaffolded by
`klink init` into `example_template/`. Keep them in sync:

```bash
python examples_klink/public/sync_to_template.py
```

It copies `public/demos/*.py` → `klink/templates/project/example_template/` and asserts
byte-identity.

## Excluded (dev-only, NOT public)

NDA / proprietary-PDK (`demos/gf_pdk_loop.py` — a foundry PDK), bring-your-own-GDS P&R
(`build_*` / `halfadder_*` / `harvest_device_geometry` — need a private device
GDS via a dev-only PDK module), and the low-level dev diagnostic probes in
`smoke/`.
