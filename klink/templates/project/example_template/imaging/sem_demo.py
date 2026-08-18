"""PUBLIC demo: SEM-style top views (imaging.sem_top's Python twin).

Five things, in the order you will want them:

  1. the finished device, greyscale + false colour, with a burnt-in
     scale bar;
  2. a MASK-BY-MASK sequence — what the wafer surface looks like after
     each mask has printed — assembled into a contact sheet AND an
     animated GIF, the top-view counterpart of xsection_demo's
     process film;
  3. a DETAIL crop at high magnification, to show that the scale bar
     re-scales itself instead of lying;
  4. the SAME device under three different styles, side by side, so you
     can see exactly which knob does what before you edit yours;
  5. the style written out as JSON, which is what the MCP tool eats.

Everything about how these LOOK comes from `sem_style.py` next to this
file. Everything about what each LAYER is (its SE brightness, its edge
glow, its false colour) comes from `demo_stack.py`. klink itself holds
no appearance numbers at all — a render with no style is refused.

Needs: pip install klayout numpy scipy pillow
Run:   python -m examples_klink.public.imaging.sem_demo
"""
import os
import sys
from pathlib import Path

from klink.domains.imaging.raster import film_strip, render_sem_png
from klink.domains.imaging.sem_style import SemStyle

# same-directory imports work both as a package module and as a copied
# `klink init` starter script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from demo_layout import write_demo_device                 # noqa: E402
from demo_stack import STACK                              # noqa: E402
from sem_style import STYLE                               # noqa: E402
from xsection_demo import GDS, OUT                        # noqa: E402


def variant(base: SemStyle, **edits) -> SemStyle:
    """A copy of `base` with a few nested fields overridden.

    Styles are frozen declarations, so 'try it a bit cleaner' means
    'make another one'. Pass dotted paths:

        variant(STYLE, **{"noise.grain": 0.0})
    """
    d = base.to_dict()
    for path, value in edits.items():
        block, key = path.split(".")
        if d.get(block) is None:
            raise SystemExit(f"{block} is switched off in this style")
        d[block][key] = value
    return SemStyle.from_dict(d)


def main():
    write_demo_device(GDS)

    # ---- 1. the finished device --------------------------------------
    # the JSON is what the MCP tool (imaging.sem_top style=...) reads
    STYLE.save(str(OUT / "sem_style.json"))
    r = render_sem_png(str(GDS), STACK, str(OUT / "sem_top.png"), STYLE,
                       out_color=str(OUT / "sem_top_color.png"))
    bar = r.get("scale_bar") or {}
    print(f"1. finished device  {r['size'][0]}x{r['size'][1]} px, "
          f"{r['um_per_px'] * 1000:.1f} nm/px, layers "
          f"{r['layers_rendered']}")
    print(f"   scale bar: {bar.get('text', 'off')}  -> "
          f"{Path(r['grey']).name} + {Path(r['color']).name}")

    # ---- 2. mask-by-mask process film --------------------------------
    # `layers=` restricts the render to a subset of the stack, so
    # feeding it a growing prefix walks the wafer forward one mask at a
    # time. Pair the order with your recipe's `# klink-step:` markers
    # and the top view lines up with the cross-section film.
    frames, printed = [], []
    for vl in STACK.layers:
        printed.append(vl.layer)
        out = OUT / f"sem_after_{vl.layer.replace('/', '_')}.png"
        render_sem_png(str(GDS), STACK, str(out), STYLE,
                       layers=list(printed))
        frames.append(str(out))
        print(f"2. after mask {vl.layer:<5} ({vl.name}) -> {out.name}")
    fs = film_strip(frames, str(OUT / "sem_film.png"),
                    str(OUT / "sem_film.gif"), duration_ms=850)
    print(f"   {fs['frames']} frames -> sem_film.png + sem_film.gif")

    # ---- 3. a high-magnification detail ------------------------------
    # Two different knobs, and mixing them up is the usual mistake:
    #   width_px   more PIXELS over the same field  -> sharper
    #   window_um  a smaller FIELD                  -> magnified
    # Only the second changes what the scale bar says, because only the
    # second changes how many microns a pixel covers. Here: the two
    # left-hand transistors, from the CUT_UM line's neighbourhood.
    sharper = render_sem_png(str(GDS), STACK,
                             str(OUT / "sem_sharper.png"), STYLE,
                             width_px=2 * r["size"][0])
    detail = render_sem_png(str(GDS), STACK, str(OUT / "sem_detail.png"),
                            STYLE, window_um=(0.6, 0.0, 4.6, 3.8))
    print(f"3. width_px x2      {sharper['um_per_px'] * 1000:.2f} nm/px, "
          f"scale bar still {sharper['scale_bar']['text']}")
    print(f"   window 4x3.8 um  {detail['um_per_px'] * 1000:.2f} nm/px, "
          f"scale bar now {detail['scale_bar']['text']}")

    # ---- 4. the same device under three styles -----------------------
    # Read these next to sem_style.py: each line is one knob, and the
    # PNGs show what it costs you.
    styles = {
        # a long-dwell, low-noise acquisition: clean but flat
        "clean": variant(STYLE, **{"noise.grain": 0.02,
                                   "noise.scanline_amount": 0.005,
                                   "beam.blur_px": 0.5}),
        # a fast, noisy scan with a hot detector: rims bloom
        "fast": variant(STYLE, **{"noise.grain": 0.22,
                                  "noise.scanline_amount": 0.08,
                                  "edges.inner_gain": 0.85,
                                  "vignette.amount": 0.30}),
        # a flat "layout-like" look: no grain, no scanlines, no vignette
        "flat": variant(STYLE, **{"noise.grain": 0.0,
                                  "noise.scanline_amount": 0.0,
                                  "vignette.amount": 0.0,
                                  "beam.blur_px": 0.2}),
    }
    for name, style in styles.items():
        out = OUT / f"sem_style_{name}.png"
        render_sem_png(str(GDS), STACK, str(out), style)
        print(f"4. style '{name}' -> {out.name}")

    # ---- 5. what a stack change (not a style change) looks like ------
    # If two layers are hard to tell apart, the fix is sem_grey in the
    # STACK, not anything in the style. Proof: same style, one layer's
    # emission pushed up.
    from klink.domains.imaging.visual_stack import VisualStack
    louder = VisualStack.from_dict({
        **STACK.to_dict(),
        "layers": [dict(d, sem_grey=(0.95 if d["layer"] == "3/0"
                                     else d["sem_grey"]))
                   for d in STACK.to_dict()["layers"]],
    })
    render_sem_png(str(GDS), louder, str(OUT / "sem_poly_hot.png"), STYLE)
    print("5. poly sem_grey 0.62 -> 0.95 (a STACK edit) -> "
          "sem_poly_hot.png")


if __name__ == "__main__":
    main()
