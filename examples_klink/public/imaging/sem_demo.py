"""PUBLIC demo: SEM-style top views of the layout (imaging.sem_top's
Python twin).

Renders the demo device the way a secondary-electron micrograph would
roughly show it: per-layer grey emission levels + bright topography
rims from the VisualStack (sem_grey / edge_glow), litho corner
rounding, beam blur, seeded film grain, scanlines, vignette — the same
seed always gives the identical image. A false-color variant (layer
colors modulated by the SE brightness) is written too, plus a
"masks printed so far" sequence using the layers= subset.

Needs: pip install klayout numpy scipy pillow
Run:   python -m examples_klink.public.imaging.sem_demo
"""
import os
import sys
from pathlib import Path

from klink.domains.imaging.raster import render_sem_png

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from demo_stack import STACK                              # noqa: E402
from xsection_demo import GDS, OUT, write_demo_device     # noqa: E402


def main():
    write_demo_device(GDS)

    r = render_sem_png(str(GDS), STACK,
                       str(OUT / "sem_top.png"),
                       out_color=str(OUT / "sem_top_color.png"))
    print("full device:", r["layers_rendered"], "->",
          Path(r["grey"]).name, "+", Path(r["color"]).name)

    # per-mask sequence: what the top view looks like after each mask
    # has been printed (the layers= subset — pair it with the recipe's
    # klink-step order for a top-view process film)
    printed = []
    for vl in STACK.layers:
        printed.append(vl.layer)
        out = OUT / f"sem_after_{vl.layer.replace('/', '_')}.png"
        render_sem_png(str(GDS), STACK, str(out), layers=list(printed))
        print(f"after mask {vl.layer} ({vl.name}):", out.name)


if __name__ == "__main__":
    main()
