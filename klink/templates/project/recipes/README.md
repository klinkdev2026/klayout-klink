# Recipes — domain starting points

A recipe is the reference implementation for one domain. When the agent
identifies your domain during onboarding, it scaffolds `pdk.py` + a first
`custom_devices/` script from the matching recipe, then runs and verifies it.

There is **no default recipe**. The domain you describe becomes the default.

## Catalog

| Domain | Geometry tier | Reusable klink core | Notes |
|---|---|---|---|
| **EBL nanodevice** | Self-contained | `klink.domains.nanodevice.devices.wraparound.build_wraparound_demo` | Runs offline; electrode geometry + ports + corridor anchors + writefield tiling + validation. No external GDS. |
| **Neural electrode harness** | Self-contained | tapered-hybrid router (`route_tapered_hybrid_many`) + `port.mark` / `anchor.mark` | Pure-Python router; pads/vias → ports → corridor anchors → route. Needs live KLayout for Port/Anchor PCells; no external data. |
| **Silicon photonics** | Open **or** your own | `klink.routing.backends.gdsfactory.gdsfactory_ports.route_gdsfactory_ports` + `klink.domains.photonics.blackbox` harvester | Runs on an **open-source gdsfactory PDK** out of the box; swap in your **proprietary foundry PDK** by changing only `(cell-name set, stub layer, route layer)`. |
| **Digital P&R → LVS** | Bring your own | `map_logic_to_devices(...)` → place → FlexDR route → live LVS | Verilog → device netlist → route → `match=True`. Needs **your** transistor layout + a device library in `pdk.py`. The transistor GDS is yours and confidential. |

## Geometry tiers

- **Self-contained** — generated entirely from `pdk.py` + code. Ships and runs.
- **Open or your own** — runs on an open PDK; can also use your proprietary one.
- **Bring your own** — needs confidential geometry you supply at run time.

**Never commit GDS/PDK content to this project**, open or not — point the code
at the files at run time.

## Digital P&R → LVS: authoring the device library

The P&R recipe is device-AGNOSTIC. A "device" is ANY cell with an arbitrary
parameter set + terminals — klink assumes no parameter names or count. You
describe YOUR devices in `pdk.py` (the starter is `custom_devices/digital_pnr_lvs.py`):

- **`DEVICES`** — `key → {params, pcell, library, style, fit_table}`. `params`
  is any dict (`{w_um, l_um}`, `{w, l, fingers}`, `{w_nm, l_nm, ...}` — your
  choice). Units are carried by the parameter NAME; for a nm process use integer
  nm, not um decimals. The fitted PCell + fit table are YOUR confidential data,
  produced by the klink fitter from your exemplar cells, referenced by path.
- **`LIBRARY`** — gate → device-role expansion (for `map_logic_to_devices`).
- **`SIZING`** — a DESIGN choice YOU make (not a klink default): `AutoRatioSizing`
  widens series-stack drivers by scaling one named parameter, or `ExplicitSizing`
  maps `(gate, role) → key` by hand. The sizing MECHANISM is klink; the sizing
  CHOICE is yours, in `pdk.py`.
- **Terminals for LVS** are recipe-free on the build path: klink reads them from
  your harvested `device_geom.json` via `geom_terminal_provider`.

**Dependency:** Verilog synthesis needs an external yosys. The flow discovers it
and, if absent, returns the exact fix (`pip install yowasp-yosys`, a native
`yosys` on PATH, or `KLINK_YOSYS=<path>`). The starter checks this up front.

## Adding a recipe

A new domain is just: a `pdk.py` shaped for it + a `custom_devices/` script that
imports `PROCESS` from `pdk.py` and calls the relevant klink API explicitly.
Copy the closest catalog entry and adapt. You never edit klink to add a domain.
