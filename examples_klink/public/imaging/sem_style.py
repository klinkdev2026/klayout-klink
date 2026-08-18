"""YOUR SEM look — the file to edit when the micrograph is wrong.

klink ships zero numbers for how an SEM-style view looks. How bright a
topography rim gets, how much film grain, how fast the scanlines beat,
how hard the vignette falls off: all of it is taste, tuned by you against
your own device and your own idea of what your microscope produces. So
all of it lives here, in a file you own. There is no default and no
fallback — a render with no style is refused, with an error naming this
file.

===========================================================================
WHO DECIDES WHAT
===========================================================================

    demo_stack.py   WHAT each layer IS   ->  sem_grey, edge_glow, color
    sem_style.py    HOW the IMAGE looks  ->  everything below

Per-layer response is the STACK's job:

    sem_grey    how brightly that material emits secondary electrons,
                0..1. Metal is bright, oxide is dark. THIS is what makes
                one layer stand out from another.
    edge_glow   how strongly its edges flare, 0..1. Real SEM edges glow
                because a sloped edge emits toward the detector from
                more of its surface.
    color       used only for the false-colour output.

This file never touches those. If two layers are indistinguishable, fix
their `sem_grey` in the STACK — turning up `grain` here will not help.

===========================================================================
HOW TO USE IT
===========================================================================

From Python:

    from sem_style import STYLE
    from klink.domains.imaging.raster import render_sem_png
    render_sem_png(gds, stack, "sem.png", STYLE, out_color="sem_c.png")

From an agent / the MCP tool:

    STYLE.save("sem_style.json")
    imaging.sem_top {stack: "stack.json", style: "sem_style.json",
                     output_dir: "figs"}

Every field is REQUIRED — deleting one raises an error naming it, not a
klink default (there is none). Only `scale_bar` may be None, meaning "do
not draw one".

===========================================================================
COMMON EDITS
===========================================================================

  it looks too clean / too much like a drawing
        noise.grain UP (0.10 -> 0.18), beam.blur_px UP slightly. Real
        micrographs are noisy; a noiseless one reads as CAD.

  it looks too noisy to see anything
        noise.grain DOWN, and noise.scanline_amount DOWN.

  edges are blown out into white halos
        edges.inner_gain and edges.outer_gain DOWN, or edges.ceiling
        DOWN. (Check the STACK's edge_glow too — that is per layer.)

  small features are mush
        beam.blur_px DOWN and beam.corner_radius_um DOWN. Blur is the
        spot size; corner rounding is the litho resolution.

  the corners of the image are too dark
        vignette.amount DOWN, or vignette.knee UP (the knee is where the
        darkening starts, as a fraction of the radius).

  the false-colour version is washed out
        false_color.gain UP, false_color.floor DOWN.

  the same image every run?
        yes — noise.seed makes it reproducible. Change the seed for a
        different grain pattern, keep it to regenerate the same figure.

===========================================================================
SCALE BAR — do not delete it lightly
===========================================================================

A micrograph without a scale bar is a picture, not a measurement, and no
reviewer will accept it. klink can burn one in; the length is chosen
automatically as a round 1/2/5 number closest to `target_fraction` of the
image width, so it stays a sane number whatever you are looking at.

Set the whole block to None only when the figure will get its scale bar
somewhere else (a composite plate, a journal template).
"""
from klink.domains.imaging.sem_style import SemStyle

STYLE = SemStyle(
    name="demo-sem",

    # =================================================================
    # BACKGROUND — the substrate, i.e. everywhere no mask layer sits
    # =================================================================
    background={
        # emission level of bare substrate, 0..1. Keep it BELOW your
        # darkest layer's sem_grey or the layout will sink into it.
        "grey": 0.18,
        # false-colour tint of that same background. A desaturated
        # blue-grey reads as "substrate" without competing with the
        # layer colours from your stack.
        "color": "#5a5f6e",
    },

    # =================================================================
    # EDGES — the topography rim
    #
    # A real SEM edge glows: a sloped or vertical wall emits secondaries
    # toward the detector from more of its area than a flat top does.
    # klink fakes it with two rims, one just inside the shape and one
    # just outside, each scaled by the layer's own edge_glow.
    # =================================================================
    edges={
        # width of the inner rim in pixels — the bright band INSIDE the
        # shape's outline. Wider = a softer, more rounded-looking wall.
        "inner_px": 3,
        # width of the outer halo, in pixels, just outside the shape.
        # This is the light scattered onto the substrate next to a wall.
        "outer_px": 1,
        # how much of the layer's edge_glow lands on each rim. The
        # inner rim should dominate; if the outer one is comparable the
        # shapes start looking like they are floating.
        "inner_gain": 0.55,
        "outer_gain": 0.25,
        # rims are allowed to exceed pure white BEFORE the final clip,
        # so a bright rim on a bright layer still blooms rather than
        # flattening. 1.0 = no headroom at all.
        "ceiling": 1.6,
    },

    # =================================================================
    # BEAM — spot size and litho resolution
    # =================================================================
    beam={
        # gaussian blur in pixels: the electron spot. 0 gives an
        # unrealistically crisp image; above ~2 everything smears.
        "blur_px": 0.9,
        # corner rounding applied to every mask before rasterising, in
        # MICRONS. Photolithography cannot print a sharp 90 degree
        # corner, and nothing betrays a fake micrograph faster than a
        # perfectly square one. Set near your process resolution.
        "corner_radius_um": 0.05,
        # points used per rounded corner. 32 is smooth; lower it only
        # if you have tens of thousands of shapes and care about time.
        "corner_points": 32,
    },

    # =================================================================
    # NOISE — grain and scanlines
    # =================================================================
    noise={
        # fixed seed = the same image every run, which is what makes a
        # figure reproducible. Change it for a different grain pattern.
        "seed": 7,
        # amount of shot noise. 0 is a CAD drawing; 0.10 reads as a
        # decent-dwell-time image; 0.25 as a fast, noisy scan.
        "grain": 0.10,
        # Noise is signal-dependent in a real detector — brighter areas
        # carry more of it. Noise here is scaled by
        # (grain_floor + grain_gain * brightness), so grain_floor is how
        # much noise the DARK areas keep. Set gain to 0 for flat,
        # uniform grain.
        "grain_floor": 0.35,
        "grain_gain": 0.65,
        # horizontal scanline banding: amplitude, how many bands down
        # the image, and how much each line's phase wanders. Small
        # amounts read as "this was scanned"; large amounts as a
        # malfunction.
        "scanline_amount": 0.035,
        "scanline_frequency": 2.2,
        "scanline_jitter": 0.4,
    },

    # =================================================================
    # VIGNETTE — corner falloff
    # =================================================================
    vignette={
        # how dark the extreme corners go, 0..1. Subtle is the point:
        # it should be felt, not seen.
        "amount": 0.18,
        # where the darkening starts, as a fraction of the radius from
        # the centre. 0.55 leaves the middle half of the frame flat.
        "knee": 0.55,
    },

    # =================================================================
    # FALSE COLOUR — the second, coloured output
    # =================================================================
    # The colour image is the stack's per-layer `color` modulated by the
    # grey image: colour x (floor + gain x grey).
    false_color={
        # brightness the tint keeps where the grey image is black. Above
        # 0 the layer colours stay readable in the shadows; at 0 the
        # dark parts go pure black.
        "floor": 0.25,
        # how hard the grey image drives the colour. Above 1 the bright
        # areas saturate toward white, which is what makes it look like
        # a false-coloured micrograph rather than a flat map.
        "gain": 1.35,
    },

    # =================================================================
    # SCALE BAR
    # =================================================================
    scale_bar={
        # target length as a share of image width; klink then rounds to
        # the nearest 1/2/5 x 10^n micron value at or below it, so you
        # get "2 um", never "1.87 um".
        "target_fraction": 0.22,
        # bar and text colour. White reads on most micrographs; use a
        # dark colour if your layout is mostly bright metal.
        "color": "#ffffff",
        "thickness_px": 4,
        "font_px": 22,
        # distance from the bottom-right corner, in pixels.
        "margin_px": 18,

        # A contrast plate behind the bar. Without one, a white bar
        # landing on a bright metal rail is invisible — and an image you
        # cannot measure is the one thing a scale bar exists to prevent.
        # Blended, not pasted, so the layout stays faintly visible.
        # Set to None if your bottom-right corner is always dark.
        "plate": {"color": "#000000", "opacity": 0.55, "pad_px": 6},
        # extra caption drawn above the bar. "" for none. Chinese is
        # fine — the renderer picks a CJK-capable font and reports the
        # characters if no installed font has them.
        "label": "",
    },
)


if __name__ == "__main__":
    # `python sem_style.py` writes sem_style.json next to this file.
    # The MCP tools take a JSON PATH, not a Python object, so this is
    # the one step between editing the numbers above and calling a tool.
    import pathlib

    out = pathlib.Path(__file__).with_suffix(".json")
    STYLE.save(str(out))
    print(out)
