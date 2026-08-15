"""PUBLIC demo: layout -> 3D GLB + self-contained interactive viewer.

Builds the same tiny device as xsection_demo, declares a
klink_visual_stack_v1 (EXAMPLE-owned — edit for your process), then:
  - fast mode: stack extrusion,
  - process mode: xsection-engine sweep with fraction=0.6 (cutaway —
    the exposed face is a true cross-section).

Needs: pip install klayout trimesh shapely mapbox-earcut
       (+ klayout-pyxs==0.1.13 for process mode)
Run:   python -m examples_klink.public.imaging.render3d_demo
Open:  _generated/render3d_*.html in any browser (offline, drag to
       rotate; right panel = colors/metallic/roughness/PNG export).
"""
from pathlib import Path

import os
import sys

from klink.domains.imaging.mesh3d import build_glb_fast, build_glb_process
from klink.domains.imaging.viewer import build_viewer_html

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from demo_stack import STACK                              # noqa: E402
from xsection_demo import (GDS, OUT, RECIPE,              # noqa: E402
                           write_demo_device)


def main():
    write_demo_device(GDS)
    stack_path = OUT / "demo_stack.json"
    STACK.save(str(stack_path))

    fast_glb = OUT / "render3d_fast.glb"
    r = build_glb_fast(str(GDS), STACK, str(fast_glb))
    build_viewer_html(str(fast_glb), str(OUT / "render3d_fast.html"),
                      title="demo fast", overwrite=True)
    print(f"fast: {r['triangles']} tris ->", fast_glb.name, "+ html")

    proc_glb = OUT / "render3d_process.glb"
    r = build_glb_process(str(GDS), STACK, str(RECIPE), str(proc_glb),
                          slices=24, fraction=0.6)
    build_viewer_html(str(proc_glb), str(OUT / "render3d_process.html"),
                      title="demo process cutaway", overwrite=True)
    print(f"process cutaway: {r['triangles']} tris, unstyled="
          f"{r['unstyled']} ->", proc_glb.name, "+ html")


if __name__ == "__main__":
    main()
