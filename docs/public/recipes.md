# Recipes

> 中文见 [recipes.zh-CN.md](recipes.zh-CN.md)

A recipe is the reference implementation for one domain. The agent scaffolds
your project from the recipe matching the domain you describe. There is no
default recipe.

## Geometry tiers

- **Self-contained** — generated entirely from `pdk.py` + code. Runs with no
  geometry from you.
- **Open or your own** — runs on an open PDK; can also use your proprietary one.
- **Bring your own** — needs confidential geometry you supply at run time.

**Never commit GDS/PDK content** — open or not. Point code at the files at run
time.

## Catalog

| Domain | Tier | Reusable klink core | Runs today? |
|---|---|---|---|
| **EBL nanodevice** | Self-contained | `klink.domains.nanodevice.devices.wraparound.build_wraparound_demo` | ✅ offline |
| **Neural electrode harness** | Self-contained | tapered-hybrid router + `port.mark` / `anchor.mark` | ✅ with KLayout |
| **Silicon photonics** | Open or your own | `klink.routing.backends.gdsfactory.gdsfactory_ports.route_gdsfactory_ports` + photonics blackbox harvester | ✅ open `gf.gpdk` feature examples run with `pip install gdsfactory`; swap in your own PDK to route it |
| **Digital P&R → LVS** | Self-contained or your own | `map_logic_to_devices(...)` → place → FlexDR → live LVS | ✅ the fit-device demo runs on synthetic exemplars; swap in your own device geometry to fit and route yours. Verilog→gates needs an external yosys (`pip install yowasp-yosys`, a native `yosys` on PATH, or `KLINK_YOSYS=<path>`); the flow returns that exact fix if it is missing |

## Adding a domain

A new domain is just a `pdk.py` shaped for it plus a `custom_devices/` script that
imports your process and calls the relevant klink API explicitly. Copy the
closest catalog entry and adapt. You never edit klink to add a domain.
