"""PUBLIC demo: holes, keyhole cut-lines and hairline slits in 3D.

A GDS file cannot store a hole. Draw a plate with a hole in it and the
file records ONE ring whose outline walks in through a "keyhole"
cut-line, around the hole, and back out — that is the mysterious line
KLayout shows across your donut. Three facts follow, and this demo
shows all three:

  1. a ZERO-WIDTH cut-line is pure storage, never intent. Every klink
     3D exit dissolves it silently (like the built-in 2.5d view): the
     hole comes back, no seam, no warning.
  2. a slit with REAL width (>= 1 dbu) might be something you DREW — a
     nanogap electrode pair is a real device — so klink keeps it and
     puts a warning in the report instead of deciding for you. In a
     tapered process such a gap widens by 2*thickness*tan(taper): a
     1 nm slit under a 40-degree, 1.5 um film becomes a 2.5 um canyon.
  3. if the slit IS an artifact (some tools write keyholes 1 dbu
     wide), you say so once: weld_slits_dbu=2 welds it shut and the
     warning goes away.

Run:   python keyhole_3d_demo.py
Open:  _generated/keyhole_*.html in any browser (offline, double-click)
Needs: pip install klayout trimesh shapely mapbox-earcut
"""
import math
import os
import sys
from pathlib import Path

import klayout.db as kdb

from klink.domains.imaging.mesh3d import build_glb_fast
from klink.domains.imaging.viewer import build_viewer_html
from klink.domains.imaging.visual_stack import VisualStack

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from viewer_style import STYLE                            # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "_generated"; OUT.mkdir(exist_ok=True)
GDS = OUT / "keyhole_demo.gds"

# ---- the layout: two plates, two kinds of "line" ---------------------
# layer 10/0: a plate with a REAL drawn hole. Saved to GDS this becomes
#             the keyhole ring — the line you see in the editor.
# layer 11/0: the same plate, but the hole's connection to the outside
#             is a deliberate 1-dbu-wide slit — DRAWN geometry, the
#             kind a nanogap device legitimately has.
PLATE_UM = (12.0, 8.0)          # plate size
HOLE_R_UM = 2.0                 # hole radius
THICK_UM = 0.5                  # extruded thickness


def write_demo(path):
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("KEYHOLE_DEMO")
    um = 1000
    w, h = (int(v * um) for v in PLATE_UM)
    r = int(HOLE_R_UM * um)
    hole = kdb.Region(kdb.Polygon(
        [kdb.Point(w // 2 + int(r * math.cos(2 * math.pi * i / 64)),
                   h // 2 + int(r * math.sin(2 * math.pi * i / 64)))
         for i in range(64)]))

    donut = kdb.Region(kdb.Box(0, 0, w, h)) - hole
    for p in donut.each():
        top.shapes(ly.layer(10, 0)).insert(p)

    slit = kdb.Region(kdb.Box(w // 2, 0, w // 2 + 1, h // 2))
    for p in (donut - slit).each():
        top.shapes(ly.layer(11, 0)).insert(p)

    ly.write(str(path))


STACK = VisualStack.from_dict({
    "format": "klink_visual_stack_v1",
    "name": "keyhole demo",
    "layers": [
        {"name": "plate_with_hole", "layer": "10/0",
         "z0_um": 0.0, "z1_um": THICK_UM,
         "color": "#8f9fc4", "metallic": 0.8},
        {"name": "plate_with_nanogap", "layer": "11/0",
         "z0_um": THICK_UM + 0.3, "z1_um": 2 * THICK_UM + 0.3,
         "color": "#c49a8f", "metallic": 0.8},
    ],
})


def main():
    write_demo(GDS)

    # ---- default: artifacts dissolve, drawn slits stay + warn --------
    glb = OUT / "keyhole_default.glb"
    r = build_glb_fast(str(GDS), STACK, str(glb), STYLE)
    build_viewer_html(str(glb), str(OUT / "keyhole_default.html"),
                      STYLE, title="keyhole default", overwrite=True)
    print(f"default    {r['triangles']:>5} tris -> {glb.name} + html")
    print("  10/0 (zero-width keyhole ring): hole restored, silent")
    for w in r["warnings"]:
        print(f"  WARNING {w['layer']}: {w['note']}")
    assert [w["layer"] for w in r["warnings"]] == ["11/0"]

    # ---- explicit weld: "that slit is an artifact, close it" ---------
    glb2 = OUT / "keyhole_welded.glb"
    r2 = build_glb_fast(str(GDS), STACK, str(glb2), STYLE,
                        weld_slits_dbu=2)
    build_viewer_html(str(glb2), str(OUT / "keyhole_welded.html"),
                      STYLE, title="keyhole welded", overwrite=True)
    print(f"welded     {r2['triangles']:>5} tris -> {glb2.name} + html")
    print(f"  weld_slits_dbu=2: warnings = {r2['warnings'] or 'none'}")
    assert r2["warnings"] == []

    print("\nCompare the two viewers on layer 11/0: the default keeps "
          "the 1-dbu gap you drew; the welded one closed it. In a "
          "tapered process (see the 2D sections of imaging.xsection_run) that gap "
          "widens by 2*thickness*tan(taper) — keep it only on purpose, "
          "and prefer vertical sidewalls where it matters.")


if __name__ == "__main__":
    main()
