"""PUBLIC demo: paper-grade Blender renders (headless bpy).

Two figures from the same declarations the other imaging demos use:
  1. mode='figure': a 2D-material device at 1:1 layout coordinates —
     MoS2 flake as its ATOMIC lattice, Au electrodes as exact layout
     prisms, explicit Si/SiO2 slabs;
  2. mode='die': the render3d process-cutaway GLB restaged on a
     transparent film with a shadow catcher.
Both also save a .blend — open it in desktop Blender to adjust
camera/lights/materials by hand and re-render.

Needs: pip install klayout numpy scipy shapely bpy  (+ trimesh and
klayout-pyxs==0.1.13 for the die figure, built by render3d_demo)
Run:   python -m examples_klink.public.imaging.blender_demo
"""
from pathlib import Path

import klayout.db as kdb

from klink.domains.imaging.blender_scene import (render_device_figure,
                                                 render_die_glb)
from klink.domains.imaging.visual_stack import VisualLayer, VisualStack

HERE = Path(__file__).parent
OUT = HERE / "_generated"; OUT.mkdir(exist_ok=True)

FIG_STACK = VisualStack(name="mos2-fet", layers=(
    VisualLayer(layer="10/0", z0_um=0.03, z1_um=0.09, name="MoS2",
                kind="lattice", motif="mos2"),
    VisualLayer(layer="20/0", z0_um=0.02, z1_um=0.16, name="Au",
                color="#d4af37", metallic=1.0),
))
SLABS = [{"name": "SiO2", "z0_um": -0.28, "z1_um": 0.0,
          "color": "#a1b8cc", "alpha": 0.85},
         {"name": "Si", "z0_um": -0.7, "z1_um": -0.28,
          "color": "#1a1c20"}]


def write_device(path):
    ly = kdb.Layout(); ly.dbu = 0.001
    top = ly.create_cell("MOS2FET")
    um = 1000
    flake = [(0.55, 0.55), (1.5, 0.32), (2.55, 0.62), (3.0, 1.35),
             (2.45, 2.05), (1.25, 2.25), (0.5, 1.7)]
    top.shapes(ly.layer(10, 0)).insert(kdb.Polygon(
        [kdb.Point(int(x * um), int(y * um)) for x, y in flake]))
    for x0, x1 in [(0.12, 0.95), (2.62, 3.45)]:
        top.shapes(ly.layer(20, 0)).insert(
            kdb.Box(int(x0 * um), 550, int(x1 * um), 2000))
    ly.write(str(path))


def main():
    gds = OUT / "mos2_fet.gds"
    write_device(gds)
    r = render_device_figure(
        str(gds), FIG_STACK, str(OUT / "blender_figure.png"),
        str(OUT / "blender_figure.blend"), slabs=SLABS,
        lattice_a_um=0.09)
    print(f"figure: {r['atoms']} atoms, {r['bonds']} bonds, "
          f"{r['solids']} solids -> blender_figure.png/.blend")

    glb = OUT / "render3d_process.glb"
    if glb.exists():
        r = render_die_glb(str(glb), str(OUT / "blender_die.png"),
                           str(OUT / "blender_die.blend"),
                           camera="face")
        print(f"die: {r['meshes']} meshes -> blender_die.png/.blend")
    else:
        print("die figure skipped (run render3d_demo first for the GLB)")


if __name__ == "__main__":
    main()
