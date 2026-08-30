"""PUBLIC demo: layout -> 3D GLB + self-contained interactive viewer.

Builds the four-transistor CMOS row of ``demo_layout.py`` in 3D. The
model is an EXTRUSION of the layout: every stack layer's polygons
pulled between their z0_um/z1_um. That is exactly what a 3D drawing
of your masks can honestly claim — shapes stay the shapes you drew (a
circle is a smooth prism, not a staircase), and nothing is invented.

What an extrusion does NOT contain: process curvature. The LOCOS
bird's beak, tapered contact holes, CMP dishes — none of that is in
the GDS, so none of it is in this model. When you need process TRUTH,
cut a 2D cross-section with ``xsection_demo.py`` / the
``imaging.xsection_run`` tool: the engine simulates the recipe along
your cut line and the section is exact. 3D shows the design; 2D
sections show the process. (A 3D "process sweep" mode existed once
and was retired: stacking 1D-engine sections into a body made a
staircase of everything it claimed to show.)

Every viewer page is self-contained: model, viewer JS and palette are
inlined, so the .html opens by double-click with no server and no
network. The right-hand panel gives live colour / metallic / roughness /
exposure control and a PNG export.

Two things the extrusion CAN carry, both declared, never guessed:

  * ``sidewall_deg`` on a stack layer lofts its walls to a smooth
    tilt (top face pulled inward by thickness*tan(angle)) — this demo
    declares 5 degrees on contact and metal-1, the same angle
    demo.pyxs etches with. A polygon too narrow to survive the tilt
    is drawn vertical and reported in ``warnings``.
  * ``cutaway_um=[x0, y0, x1, y1]`` cuts the FINISHED model to a
    region: built whole first, then boolean-cut, so the section shows
    only on the cut faces (needs pip install manifold3d).

What comes from where:

    demo_stack.py    the MATERIALS (colour, alpha, metallic, z range,
                     sidewall_deg — every number a process fact YOURS)
    viewer_style.py  the FINISH and the PAGE

Needs: pip install klayout trimesh shapely mapbox-earcut
       (+ manifold3d for the cutaway step)
Run:   python -m examples_klink.public.imaging.render3d_demo
Open:  _generated/render3d_*.html in any browser.
"""
from pathlib import Path

import os
import sys

from klink.domains.imaging.mesh3d import build_glb_fast
from klink.domains.imaging.viewer import build_viewer_html
from klink.domains.imaging.viewer_style import ViewerStyle

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from demo_layout import write_demo_device                 # noqa: E402
from demo_stack import STACK                              # noqa: E402
from viewer_style import STYLE                            # noqa: E402
from xsection_demo import GDS, OUT                        # noqa: E402


def main():
    write_demo_device(GDS)
    STACK.save(str(OUT / "demo_stack.json"))
    # the JSONs are what the MCP tool (imaging.render3d style=...) eats
    STYLE.save(str(OUT / "viewer_style.json"))

    # ---- 1. the model: a 3D drawing of the masks ---------------------
    glb = OUT / "render3d_fast.glb"
    r = build_glb_fast(str(GDS), STACK, str(glb), STYLE)
    build_viewer_html(str(glb), str(OUT / "render3d_fast.html"),
                      STYLE, title="demo fast", overwrite=True)
    print(f"1. model     {r['triangles']:>6} tris  "
          f"{len(r['materials'])} materials -> {glb.name} + html")
    # warnings list kept hairline slits (drawn 1-dbu gaps and the
    # like); an empty list means the layout had none
    print(f"   warnings: {r['warnings'] or 'none'}")

    # ---- 2. the same model, cut open ---------------------------------
    # built whole, then boolean-cut: the section appears ONLY on the
    # cut faces — everything kept stays exactly the drawn model
    try:
        cut_glb = OUT / "render3d_cutaway.glb"
        rc = build_glb_fast(str(GDS), STACK, str(cut_glb), STYLE,
                            cutaway_um=[-0.8, 1.9, 11.2, 4.7])
        build_viewer_html(str(cut_glb),
                          str(OUT / "render3d_cutaway.html"),
                          STYLE, title="demo cutaway", overwrite=True)
        print(f"2. cutaway   {rc['triangles']:>6} tris  kept "
              f"y >= 1.9 um -> {cut_glb.name} + html "
              f"(the cut face runs through the gates)")
    except Exception as exc:                # instructive: names the pip
        print(f"2. cutaway   skipped: {exc}")

    # ---- 3. the same model, a light page -----------------------------
    light = ViewerStyle.from_dict({
        **STYLE.to_dict(),
        "viewer": {**STYLE.to_dict()["viewer"],
                   "page": "#f4f5f7", "panel": "#e3e6ea",
                   "panel_text": "#242830", "button": "#c3cad4",
                   "button_hover": "#adb6c2", "button_text": "#1c2028",
                   "exposure": 1.15},
    })
    build_viewer_html(str(glb), str(OUT / "render3d_light.html"),
                      light, title="demo light page", overwrite=True)
    print("3. light page          -> render3d_light.html "
          "(same GLB, different declaration)")

    print("\nProcess curvature (bird's beak, tapered plugs) is not in "
          "the GDS, so it is not in this model. For process truth run "
          "xsection_demo.py — the 2D sections are engine-exact.")


if __name__ == "__main__":
    main()
