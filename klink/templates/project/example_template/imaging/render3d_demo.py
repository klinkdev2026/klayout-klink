"""PUBLIC demo: layout -> 3D GLB + self-contained interactive viewer.

Builds the four-transistor CMOS row of ``demo_layout.py`` in 3D, five
ways, so the difference between "extruded layout" and "simulated
process" is visible rather than asserted:

  1. FAST mode — every stack layer extruded between its z0_um/z1_um.
     Cheap, exact to the layout, and completely flat: no LOCOS bird's
     beak, no tapered plug, no CMP dish, because none of that is in the
     GDS. This is a 3D drawing of your masks.
  2. PROCESS mode — the cross-section engine swept across the die and
     the slices stacked into a solid. Curvature comes from the RECIPE,
     so the bird's beak and the tapered contacts are simulated, not
     drawn.
  3. the same process model at fraction<1 — the sweep stops early and
     the exposed face is a true cross-section: a cutaway, for free.
  4. a COARSE sweep next to a fine one, so the staircase artefact
     between cut lines is something you have seen and can judge, not a
     surprise later.
  5. the same model under a second viewer style, to show what the page
     declaration controls.

Every viewer page is self-contained: model, viewer JS and palette are
inlined, so the .html opens by double-click with no server and no
network. The right-hand panel gives live colour / metallic / roughness /
exposure control and a PNG export.

What comes from where:

    demo.pyxs        the PROCESS   (only process mode reads it)
    demo_stack.py    the MATERIALS (colour, alpha, metallic, z range)
    viewer_style.py  the FINISH and the PAGE

Needs: pip install klayout trimesh shapely mapbox-earcut
       (+ klayout-pyxs==0.1.13 for process mode)
Run:   python -m examples_klink.public.imaging.render3d_demo
Open:  _generated/render3d_*.html in any browser.
"""
from pathlib import Path

import os
import sys

from klink.domains.imaging.mesh3d import build_glb_fast, build_glb_process
from klink.domains.imaging.viewer import build_viewer_html
from klink.domains.imaging.viewer_style import ViewerStyle

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from demo_layout import write_demo_device                 # noqa: E402
from demo_stack import STACK                              # noqa: E402
from viewer_style import STYLE                            # noqa: E402
from xsection_demo import GDS, OUT, RECIPE                # noqa: E402


def main():
    write_demo_device(GDS)
    STACK.save(str(OUT / "demo_stack.json"))
    # the JSONs are what the MCP tool (imaging.render3d style=...) eats
    STYLE.save(str(OUT / "viewer_style.json"))

    # ---- 1. fast: a 3D drawing of the masks --------------------------
    fast_glb = OUT / "render3d_fast.glb"
    r = build_glb_fast(str(GDS), STACK, str(fast_glb), STYLE)
    build_viewer_html(str(fast_glb), str(OUT / "render3d_fast.html"),
                      STYLE, title="demo fast", overwrite=True)
    print(f"1. fast      {r['triangles']:>6} tris  "
          f"{len(r['materials'])} materials -> {fast_glb.name} + html")

    # ---- 2/3. process: curvature from the recipe, plus a cutaway -----
    proc_glb = OUT / "render3d_process.glb"
    r = build_glb_process(str(GDS), STACK, str(RECIPE), str(proc_glb),
                          STYLE, slices=24, fraction=0.6)
    build_viewer_html(str(proc_glb), str(OUT / "render3d_process.html"),
                      STYLE, title="demo process cutaway",
                      overwrite=True)
    print(f"2. process   {r['triangles']:>6} tris  "
          f"{len(r['materials'])} materials, {r['slices']} slices, "
          f"cutaway at {r['fraction']:g} -> {proc_glb.name} + html")
    # An empty `unstyled` means your stack covers everything the recipe
    # makes. Anything listed here rendered in viewer_style's
    # `undeclared_color` and wants adding to demo_stack.recipe_styles.
    print(f"   undeclared materials: {r['unstyled'] or 'none'}")
    print(f"   slice pitch {r['slice_pitch_um'] * 1000:.0f} nm — "
          f"features finer than this are averaged away")

    # ---- 4. the honest limitation: sweep resolution -------------------
    # Between cut lines the model is a flat slab, so a coarse sweep
    # stairsteps anything that is not parallel to the sweep axis. Look
    # at both and pick your own trade.
    coarse = OUT / "render3d_coarse.glb"
    rc = build_glb_process(str(GDS), STACK, str(RECIPE), str(coarse),
                           STYLE, slices=6, fraction=1.0)
    build_viewer_html(str(coarse), str(OUT / "render3d_coarse.html"),
                      STYLE, title="demo coarse sweep", overwrite=True)
    print(f"4. coarse    {rc['triangles']:>6} tris  "
          f"{rc['slices']} slices, pitch "
          f"{rc['slice_pitch_um'] * 1000:.0f} nm -> {coarse.name} "
          f"(compare the gate edges with the 24-slice model)")

    # ---- 5. the same model, a light page -----------------------------
    light = ViewerStyle.from_dict({
        **STYLE.to_dict(),
        "viewer": {**STYLE.to_dict()["viewer"],
                   "page": "#f4f5f7", "panel": "#e3e6ea",
                   "panel_text": "#242830", "button": "#c3cad4",
                   "button_hover": "#adb6c2", "button_text": "#1c2028",
                   "exposure": 1.15},
    })
    build_viewer_html(str(proc_glb), str(OUT / "render3d_light.html"),
                      light, title="demo light page", overwrite=True)
    print("5. light page          -> render3d_light.html "
          "(same GLB, different declaration)")


if __name__ == "__main__":
    main()
