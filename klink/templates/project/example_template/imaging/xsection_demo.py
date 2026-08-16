"""PUBLIC demo: headless process cross-section (klink imaging domain).

Sections the four-transistor CMOS row of ``demo_layout.py`` with the
flow in ``demo.pyxs``: once as a single cut, once as a per-step film
(one frame per ``# klink-step:`` marker — n-well, LOCOS, gate stack,
LDD, spacer, S/D, ILD, W plugs, damascene metal-1). Everything is
deterministic and lands in ``_generated/`` with a machine-readable
sidecar.

Needs: pip install klayout klayout-pyxs==0.1.13
       pip install numpy scipy pillow      (for the render/film step)
Run:   python -m examples_klink.public.imaging.xsection_demo
"""
import os
import sys
from pathlib import Path

from klink.domains.imaging.xsection_driver import run_xsection

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = Path(__file__).parent
OUT = HERE / "_generated"; OUT.mkdir(exist_ok=True)
GDS = OUT / "demo_device.gds"
RECIPE = HERE / "demo.pyxs"
# frame the rasters on the device: the engine's substrate runs microns
# deep, so an unframed section is mostly bulk. Read YOUR window off
# your own stack's z ranges.
Z_WINDOW = (-1.1, 1.5)


def main():
    from demo_layout import CUT_UM, write_demo_device
    from demo_stack import STACK
    write_demo_device(GDS)

    single = run_xsection(str(GDS), str(RECIPE), CUT_UM,
                          output_dir=str(OUT), basename="xsec",
                          overwrite=True)
    mats = single["outputs"]["stages"][0]["materials"]
    print("single cut ->", single["outputs"]["files"][0]["path"])
    for m in mats:
        print(f"  {m['layer']:>6}  {m['name']:<12} {m['shapes']} shapes")

    film = run_xsection(str(GDS), str(RECIPE), CUT_UM,
                        output_dir=str(OUT), basename="film",
                        steps=True, overwrite=True,
                        render=True, stack=STACK,
                        z_window_um=Z_WINDOW, axis=True)
    print("steps film:")
    for s in film["outputs"]["stages"]:
        print(f"  {s['step']:<26} {len(s['materials'])} materials")
    kinds = {}
    for f in film["outputs"]["files"]:
        kinds.setdefault(f["kind"], []).append(Path(f["path"]).name)
    for k, names in kinds.items():
        print(f"  {k}: {len(names)} file(s)"
              + (f" e.g. {names[-1]}" if names else ""))
    if film["outputs"].get("auto_colored"):
        print("  undeclared materials (auto-colored):",
              film["outputs"]["auto_colored"])
    print("sidecars:", single["sidecar_path"], film["sidecar_path"])


if __name__ == "__main__":
    main()
