"""PUBLIC demo: headless process cross-section (klink imaging domain).

Sections the four-transistor CMOS row of ``demo_layout.py`` with the
flow in ``demo.pyxs`` — a simplified planar CMOS: n-well, LOCOS, gate
stack, LDD, spacer, S/D, ILD, W plugs, damascene metal-1.

Six things:

  1. one cut, GDS only — no style needed, because a section GDS has no
     look. This is the output you measure in KLayout;
  2. the same cut RENDERED, framed on the device, with a z ruler and a
     lateral scale bar;
  3. the per-step film: one frame per ``# klink-step:`` marker,
     assembled into a contact sheet and a GIF;
  4. the SAME frame under a second style, so you can see what the page
     declaration actually controls;
  5. an UNFRAMED render, to show why z_window_um is not optional in
     practice;
  6. a second cut line through a different part of the row — the cut is
     an argument, not a property of the process.

What comes from where:

    demo.pyxs         the PROCESS   (grow, implant, etch, planarize)
    demo_stack.py     the MATERIALS (name + colour per material)
    section_style.py  the PAGE      (page colour, ruler, bars, gradient)

Everything is deterministic: identical inputs give byte-identical GDS,
and every run writes a machine-readable sidecar with per-step material
counts and SHA256s.

Needs: pip install klayout klayout-pyxs==0.1.13
       pip install numpy scipy pillow      (for the render/film steps)
Run:   python -m examples_klink.public.imaging.xsection_demo
"""
import os
import sys
from pathlib import Path

from klink.domains.imaging.section_style import SectionStyle
from klink.domains.imaging.xsection_driver import run_xsection

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = Path(__file__).parent
OUT = HERE / "_generated"; OUT.mkdir(exist_ok=True)
GDS = OUT / "demo_device.gds"
RECIPE = HERE / "demo.pyxs"
# Frame the rasters on the device: the engine's substrate runs microns
# deep, so an UNFRAMED section is mostly bulk silicon with the films as
# a hairline at the top (step 5 below shows exactly that). Read YOUR
# window off your own stack's z ranges.
Z_WINDOW = (-1.1, 1.5)


def main():
    from demo_layout import CUT_UM, GATES_X, ROW_Y, write_demo_device
    from demo_stack import STACK
    from section_style import STYLE
    write_demo_device(GDS)

    # ---- 1. GDS only: no style, because there is nothing to style ----
    single = run_xsection(str(GDS), str(RECIPE), CUT_UM,
                          output_dir=str(OUT), basename="xsec",
                          overwrite=True)
    mats = single["outputs"]["stages"][0]["materials"]
    print("1. single cut (GDS only) ->",
          Path(single["outputs"]["files"][0]["path"]).name)
    for m in mats:
        print(f"     {m['layer']:>6}  {m['name']:<12} {m['shapes']} shapes")

    # ---- 2. the same cut, rendered ----------------------------------
    # the JSON is what the MCP tool (imaging.xsection_run style=...) eats
    STYLE.save(str(OUT / "section_style.json"))
    shot = run_xsection(str(GDS), str(RECIPE), CUT_UM,
                        output_dir=str(OUT), basename="framed",
                        overwrite=True, render=True, stack=STACK,
                        style=STYLE, z_window_um=Z_WINDOW, axis=True)
    png = [f for f in shot["outputs"]["files"]
           if f["kind"] == "section_png"][0]
    print(f"2. rendered + framed     -> {Path(png['path']).name}")

    # ---- 3. the per-step process film --------------------------------
    film = run_xsection(str(GDS), str(RECIPE), CUT_UM,
                        output_dir=str(OUT), basename="film",
                        steps=True, overwrite=True,
                        render=True, stack=STACK, style=STYLE,
                        z_window_um=Z_WINDOW, axis=True)
    print("3. per-step film:")
    for s in film["outputs"]["stages"]:
        print(f"     {s['step']:<26} {len(s['materials'])} materials")
    kinds = {}
    for f in film["outputs"]["files"]:
        kinds.setdefault(f["kind"], []).append(Path(f["path"]).name)
    for k, names in kinds.items():
        print(f"     {k}: {len(names)} file(s)"
              + (f" e.g. {names[-1]}" if names else ""))
    # Undeclared materials still render — in a stable auto colour — and
    # are REPORTED, so an empty list here means your stack covers the
    # whole recipe. A non-empty one is a prompt to add them to
    # demo_stack.py's recipe_styles.
    print("     undeclared (auto-coloured):",
          film["outputs"]["render"]["auto_colored"] or "none")
    if film["outputs"]["render"].get("font_warnings"):
        print("     LABELS WITH NO FONT:",
              film["outputs"]["render"]["font_warnings"])

    # ---- 4. the same section under a second style --------------------
    # A frozen declaration, so "try it darker" means "make another one".
    # Read this next to section_style.py: three edits, one picture.
    dark = SectionStyle.from_dict({
        **STYLE.to_dict(),
        "page": {**STYLE.to_dict()["page"], "background": "#12141a",
                 "geometry_shade": 0.05, "edge_darken": 0.35},
        "axis": {**STYLE.to_dict()["axis"],
                 "gutter_background": "#1b1e25",
                 "rule_color": "#8d9199", "tick_color": "#c7ccd3"},
        "scale_bar": {**STYLE.to_dict()["scale_bar"],
                      "color": "#e8ecf2"},
    })
    run_xsection(str(GDS), str(RECIPE), CUT_UM,
                 output_dir=str(OUT), basename="dark", overwrite=True,
                 render=True, stack=STACK, style=dark,
                 z_window_um=Z_WINDOW, axis=True)
    print("4. dark page style       -> dark.png")

    # ---- 5. why the z window matters ---------------------------------
    run_xsection(str(GDS), str(RECIPE), CUT_UM,
                 output_dir=str(OUT), basename="unframed",
                 overwrite=True, render=True, stack=STACK, style=STYLE,
                 axis=True)
    print("5. NO z_window           -> unframed.png "
          "(mostly bulk: compare with framed.png)")

    # ---- 6. a different cut line -------------------------------------
    # The cut is an argument. Here: vertical, straight down through the
    # second poly gate, so the section shows the gate stack end-on
    # instead of the row in profile.
    x = GATES_X[1]
    vertical = [[x, ROW_Y[0] - 1.2], [x, ROW_Y[1] + 1.2]]
    v = run_xsection(str(GDS), str(RECIPE), vertical,
                     output_dir=str(OUT), basename="vertical",
                     overwrite=True, render=True, stack=STACK,
                     style=STYLE, z_window_um=Z_WINDOW, axis=True)
    print(f"6. cut along x={x} um    -> vertical.png "
          f"({len(v['outputs']['stages'][0]['materials'])} materials)")

    print("sidecars:", Path(single["sidecar_path"]).name,
          Path(film["sidecar_path"]).name)


if __name__ == "__main__":
    main()
