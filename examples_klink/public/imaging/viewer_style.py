"""YOUR 3D look — the file to edit when the model or its viewer is wrong.

The 3D exit writes two things that have a look, and klink ships numbers
for neither:

    the GLB     surface finish, and the colour of any material your
                stack never declared
    the page    a whole small UI — background, side panel, buttons,
                starting exposure

Both live here in a file you own. There is no default — building with no
style is refused, with an error naming this file.

===========================================================================
WHO DECIDES WHAT
===========================================================================

    demo_stack.py     WHAT each material IS  ->  color, alpha, metallic,
                                                 z0_um/z1_um
    viewer_style.py   the FINISH and the PAGE

Per-material colour is the STACK's. This file only supplies:

  * `roughness`, the one PBR value the stack does not carry — it is a
    whole-model finish, not a per-material fact;
  * `undeclared_color`, used for a material the recipe produced and the
    stack never mentioned. Those materials are also REPORTED back as
    `unstyled`, so seeing this colour is a prompt to add them to your
    stack's `recipe_styles`, not something to tune here.

===========================================================================
HOW TO USE IT
===========================================================================

From Python:

    from viewer_style import STYLE
    build_glb_fast(gds, STACK, "model.glb", STYLE)
    build_viewer_html("model.glb", "model.html", STYLE)

From an agent / the MCP tool:

    STYLE.save("viewer_style.json")
    imaging.render3d {stack: "stack.json", style: "viewer_style.json",
                      mode: "process", recipe: "...", output_dir: "figs"}

The viewer page is SELF-CONTAINED: the model, the viewer JS and these
colours are all inlined, so the .html opens by double-click with no
network and no server. Its colours are written into the page's CSS, so
they are validated as plain #RRGGBB before they get there.

===========================================================================
COMMON EDITS
===========================================================================

  the model looks like wet plastic
        material.roughness UP toward 0.8. Deposited films are not
        polished; 0.6 is a reasonable "as-fabricated" default and
        below ~0.3 everything turns into a mirror.

  a material came out this grey
        that is `undeclared_color`, and it means your STACK never
        declared that material. Add it to demo_stack.py's
        `recipe_styles` — the run lists the offenders as `unstyled`.

  the viewer is too dark to see the stack
        viewer.exposure UP (it is also a live slider in the page, so
        try it there first, then write the value you liked back here).

  I want the page to match my slides / my group's colours
        viewer.page, .panel, .panel_text, .button, .button_hover,
        .button_text. The page is a real little UI; keep the text
        colours contrasting with their backgrounds or the panel becomes
        unreadable.

  printing a figure from the viewer
        the page has a PNG export button; set viewer.page to your
        document's background first so the export drops straight in.
"""
from klink.domains.imaging.viewer_style import ViewerStyle

STYLE = ViewerStyle(
    name="demo-viewer",

    # =================================================================
    # MATERIAL — the GLB's finish
    # =================================================================
    material={
        # glTF PBR roughness, 0..1, applied to every material in the
        # model. 0 = mirror, 1 = chalk. Fab surfaces are matte-ish:
        # 0.6 reads as "as-deposited", 0.2 as "polished showpiece".
        "roughness": 0.6,

        # colour for a material the recipe produced but the stack never
        # declared. Deliberately a neutral grey: it should look
        # UNFINISHED, so it prompts you to declare the material rather
        # than passing for a design choice. Those materials come back
        # in the report as `unstyled`.
        "undeclared_color": "#8a8f96",
    },

    # =================================================================
    # VIEWER — the self-contained web page
    # =================================================================
    viewer={
        # canvas and document background. Dark makes a translucent
        # dielectric stack read best; go light if the figure is going
        # into a white paper.
        "page": "#14161a",

        # the right-hand control panel
        "panel": "#1f2328",
        "panel_text": "#d5d9de",

        # its buttons (reset view, export PNG, ...)
        "button": "#3a4756",
        "button_hover": "#4a5a6c",
        "button_text": "#e8ecf0",

        # starting exposure of the model-viewer camera. The page also
        # exposes it as a live slider — this is just where it begins.
        # Above ~1.5 the highlights on metal clip.
        "exposure": 0.9,

        # how dark the contact shadow under the model is, 0..1.
        # 0 floats the model in space; too high and a thin die
        # looks like it is sitting in a hole.
        "shadow_intensity": 0.6,
    },
)


if __name__ == "__main__":
    # `python viewer_style.py` writes viewer_style.json next to this file.
    # The MCP tools take a JSON PATH, not a Python object, so this is
    # the one step between editing the numbers above and calling a tool.
    import pathlib

    out = pathlib.Path(__file__).with_suffix(".json")
    STYLE.save(str(out))
    print(out)
