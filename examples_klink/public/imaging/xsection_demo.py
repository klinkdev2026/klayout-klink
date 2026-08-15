"""PUBLIC demo: headless process cross-section (klink imaging domain).

Writes a tiny 2-layer device layout, then sections it with demo.pyxs:
once as a single cut, once as a per-step film (the recipe's
``# klink-step:`` markers). Everything is deterministic and lands in
``_generated/`` with a machine-readable sidecar.

Needs: pip install klayout klayout-pyxs==0.1.13
Run:   python -m examples_klink.public.imaging.xsection_demo
"""
from pathlib import Path

import klayout.db as kdb

from klink.domains.imaging.xsection_driver import run_xsection

HERE = Path(__file__).parent
OUT = HERE / "_generated"; OUT.mkdir(exist_ok=True)
GDS = OUT / "demo_device.gds"
RECIPE = HERE / "demo.pyxs"


def write_demo_device(path):
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("DEMO")
    top.shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 6000, 4000))
    top.shapes(ly.layer(2, 0)).insert(kdb.Box(2000, -1000, 4000, 5000))
    ly.write(str(path))


def main():
    write_demo_device(GDS)
    cut = [[-1.0, 2.0], [7.0, 2.0]]

    single = run_xsection(str(GDS), str(RECIPE), cut,
                          output_dir=str(OUT), basename="xsec",
                          overwrite=True)
    mats = single["outputs"]["stages"][0]["materials"]
    print("single cut ->", single["outputs"]["files"][0]["path"])
    for m in mats:
        print(f"  {m['layer']:>6}  {m['name']:<10} {m['shapes']} shapes")

    from examples_klink.public.imaging.demo_stack import STACK
    film = run_xsection(str(GDS), str(RECIPE), cut,
                        output_dir=str(OUT), basename="film",
                        steps=True, overwrite=True,
                        render=True, stack=STACK)
    print("steps film:")
    for s in film["outputs"]["stages"]:
        print(f"  {s['step']:<16} {len(s['materials'])} materials")
    kinds = {}
    for f in film["outputs"]["files"]:
        kinds.setdefault(f["kind"], []).append(Path(f["path"]).name)
    for k, names in kinds.items():
        print(f"  {k}: {len(names)} file(s)"
              + (f" e.g. {names[-1]}" if names else ""))
    print("sidecars:", single["sidecar_path"], film["sidecar_path"])


if __name__ == "__main__":
    main()
