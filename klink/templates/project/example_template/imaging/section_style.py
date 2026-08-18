"""YOUR cross-section look — the file to edit when the section is wrong.

klink ships zero numbers for how a section PNG looks: page colour, ruler
colour, how dark a material's outline is drawn, how much gradient a film
gets, how the label bar is proportioned, what an undeclared material
falls back to. All of it is taste, so all of it lives here in a file you
own. There is no default — rendering with no style is refused, with an
error naming this file.

===========================================================================
WHO DECIDES WHAT
===========================================================================

Three declarations feed a rendered section, and they answer different
questions:

    demo.pyxs        WHAT HAPPENS   ->  the process: grow, implant, etch
    demo_stack.py    WHAT things ARE ->  each material's name and colour
    section_style.py HOW the PAGE looks -> everything below

Material colours are NOT here. A section is coloured by matching each
engine material to your stack:

    stack layer with recipe_symbol="poly"   -> that layer's colour
    stack recipe_styles["fox"]              -> that entry's colour
    neither                                 -> an automatic colour, and
                                               the run REPORTS it as
                                               `auto_colored` so you know
                                               to declare it

So if a film comes out an odd colour, add it to `recipe_styles` in the
STACK. `auto_color` below only controls how those undeclared fallbacks
are generated, and seeing them at all is a signal, not a feature.

===========================================================================
HOW TO USE IT
===========================================================================

From Python:

    from section_style import STYLE
    run_xsection(gds, recipe, cut_um, output_dir=..., render=True,
                 stack=STACK, style=STYLE, axis=True,
                 z_window_um=(-0.9, 1.2))

From an agent / the MCP tool:

    STYLE.save("section_style.json")
    imaging.xsection_run {recipe: "...", cut_um: [[...],[...]],
                          output_dir: "figs", render: true,
                          style: "section_style.json", axis: true,
                          z_window_um: [-0.9, 1.2]}

The style is needed ONLY when you ask for pictures. `imaging.xsection_run`
without `render` writes section GDS and needs no style at all.

`scale_bar` and `label_bar` may each be None, meaning "do not draw it".
Every other field is required; deleting one raises an error naming it.

===========================================================================
COMMON EDITS
===========================================================================

  the picture is mostly bulk silicon, films are a hairline at the top
        that is not this file — pass z_window_um=(z_bottom, z_top) to
        the render. The engine's substrate runs microns deep; frame it.

  stacked films of similar colour blur into one another
        page.geometry_shade UP (each film gets a stronger top-to-bottom
        gradient), or page.edge_darken DOWN for a more visible outline.

  the outlines look like a cartoon
        page.edge_darken UP toward 1.0 (1.0 = no outline at all, the
        edge is drawn in the material's own colour).

  edges look like a staircase
        page.supersample must be > 1. Process profiles — tapers, bird's
        beaks, rounded implant fronts — alias badly when drawn flat.
        2 is the sweet spot; 3 costs 2.25x the pixels for little gain.

  the z ruler is cramped / the numbers are cut off
        axis.gutter_px UP, or axis.tick_font_px DOWN.

  I am putting this in a paper and the label bar is in the way
        label_bar = None. Keep the scale bar.

  a section with no scale is a picture, not a measurement
        so think twice before setting scale_bar = None.
"""
from klink.domains.imaging.section_style import SectionStyle

STYLE = SectionStyle(
    name="demo-section",

    # =================================================================
    # PAGE — the drawing surface and how solids are painted on it
    # =================================================================
    page={
        # paper behind the geometry, and the colour of anything the
        # engine left as void (air above the stack, an etched trench).
        # An off-white reads as paper; pure white reads as "missing".
        "background": "#faf9f6",

        # top-to-bottom gradient inside each material, 0..1. Without it
        # two films of similar colour sitting on each other merge into
        # one shape. 0 = flat fill; above ~0.3 it starts looking like
        # a 3D render instead of a section.
        "geometry_shade": 0.13,

        # each material's outline is its own colour multiplied by this.
        # 0.55 = a distinctly darker edge of the same hue, which keeps
        # the section readable in greyscale print. 1.0 = no outline.
        "edge_darken": 0.55,

        # draw at N x and downscale. Process profiles are curved and
        # they alias badly at 1x. Set 1 only when you are debugging and
        # want raw pixels.
        "supersample": 2,

        # a section thinner than this many pixels is padded up to it, so
        # a very shallow z window still produces a usable strip.
        "min_height_px": 40,
    },

    # =================================================================
    # AXIS — the z ruler down the left edge (drawn when axis=True)
    # =================================================================
    axis={
        # width of the ruler gutter in pixels. Too narrow and the tick
        # numbers collide with the geometry.
        "gutter_px": 74,
        "gutter_background": "#f4f3ef",
        # the vertical rule and its tick marks
        "rule_color": "#787a7e",
        # the numbers next to the ticks
        "tick_color": "#46484c",
        "tick_font_px": 17,
        # printed once, either in the label bar or at the bottom of the
        # gutter. Tick VALUES are computed; this is just the unit.
        "unit_text": "z / um",
    },

    # =================================================================
    # SCALE BAR — lateral, bottom right
    # =================================================================
    # The z ruler measures the vertical; this measures the horizontal.
    # Its length rounds to a 1/2/5 x 10^n micron value, so it reads
    # "2 um", never "1.87 um".
    scale_bar={
        "target_fraction": 0.22,
        "color": "#282a2e",
        "thickness_px": 3,
        "font_px": 17,
        "margin_px": 24,
        # contrast plate behind the bar, for when it lands on a bright
        # film instead of the page. None to switch it off.
        "plate": {"color": "#faf9f6", "opacity": 0.7, "pad_px": 5},
    },

    # =================================================================
    # LABEL BAR — the dark strip across the top carrying the step name
    # =================================================================
    # With steps=True the label is the `# klink-step:` name, so the film
    # is self-describing frame by frame. Set the block to None for a
    # bare image with no banner.
    label_bar={
        "height_px": 32,
        "background": "#181a1e",
        "text_color": "#e6ebf0",
        "font_px": 22,
        # the unit caption rides in the bar above the ruler, dimmer than
        # the step name so it does not compete with it
        "unit_text_color": "#969aa0",
        "unit_font_px": 15,
    },

    # =================================================================
    # AUTO COLOUR — the fallback for materials your STACK never declared
    # =================================================================
    # Derived from a hash of the material's name, so it is stable across
    # runs and two materials never swap colours between frames. Seeing
    # one of these is a prompt to add the material to your stack's
    # `recipe_styles` — the run lists them as `auto_colored`.
    auto_color={
        # keep it muted. Undeclared materials should be legible but
        # should not out-shout the ones you deliberately styled.
        "saturation": 0.45,
        "value": 0.75,
    },
)


if __name__ == "__main__":
    # `python section_style.py` writes section_style.json next to this file.
    # The MCP tools take a JSON PATH, not a Python object, so this is
    # the one step between editing the numbers above and calling a tool.
    import pathlib

    out = pathlib.Path(__file__).with_suffix(".json")
    STYLE.save(str(out))
    print(out)
