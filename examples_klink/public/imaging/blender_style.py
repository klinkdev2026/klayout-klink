"""YOUR Blender look — the file to edit when the render is wrong.

klink ships zero numbers for how a render looks. Sun energy, camera lens,
film transform, how a metal is mottled, how far an edge is bevelled: all
of it is taste, tuned against a particular device, so all of it lives
here in a file you own. There is no default and no fallback — a render
with no style is refused, with an error naming this file. Edit these
numbers, re-render, klink is untouched.

===========================================================================
WHO DECIDES WHAT  (read this first — it is the one confusing part)
===========================================================================

Two declarations feed a Blender render, and they answer different
questions:

    demo_stack.py    WHAT each material IS   ->  color, metallic, alpha
    blender_style.py HOW that material LOOKS ->  everything below

klink sorts every material into one of THREE classes, and it decides that
from the STACK, never from this file and never from a colour:

    alpha < 1.0        ->  "dielectric"   (a film you see through)
    metallic > 0.5     ->  "metal"
    otherwise          ->  "matte"        (substrate, wells, implants)

So: changing a number here can NEVER turn something into a metal. If your
tungsten renders like plastic, it is your STACK that forgot
`metallic=0.9`. If your oxide is opaque, your stack forgot `alpha`.

(This split exists because it was once broken. The die renderer used to
guess: "bright colour + metallic 0 must really be a metal" — which in a
normal CMOS stack metallised the substrate, both wells, every implant,
both oxides and the spacer, while missing the real metals, because
tungsten #6e6e78 and silicide #7a2b2b are DARK. It also destroyed the
declared transparency, so the plugs vanished inside a white ILD.)

===========================================================================
HOW TO USE IT
===========================================================================

From Python — pass the object:

    from blender_style import STYLE
    from klink.domains.imaging.blender_scene import render_die_glb
    render_die_glb(glb, "fig.png", "fig.blend", STYLE, camera="face")

From an agent / the MCP tool — write the JSON once, point at it:

    STYLE.save("blender_style.json")
    imaging.blender {mode: "die", glb: "...", style: "blender_style.json",
                     output_dir: "figs"}

Every field is REQUIRED. Deleting one does not fall back to a klink
default (there is none) — it raises an error naming the field. Two
things are allowed to be null, and both mean "skip this effect":
`material.metal.mottle` and `material.bevel`.

===========================================================================
COMMON EDITS  (start here before touching anything else)
===========================================================================

  the render is too dark / too bright
        staging.key_light.energy       and  staging.fill_light.energy
        Both, roughly together. key_light is the hard sun that casts the
        shadow; fill_light is the soft box that opens the shadows up.

  the metals look like plastic
        first check your STACK declared metallic > 0.5 for them.
        Then: material.metal.roughness DOWN (sharper reflection) and
        make sure material.metal.mottle is not null.

  I cannot see the gates / plugs through the oxide
        material.dielectric.transmission UP, .roughness DOWN, and check
        the STACK gave those layers alpha < 1. If the stack in question
        is many films thick, raise staging.film.transparent_max_bounces
        as well — a ray has to survive every one of them.

  the edges look computer-generated
        material.bevel.radius_fraction UP (try doubling it). This is the
        single biggest "is it a diagram or an object" knob.

  the figure is cropped / floating in space
        camera.margin. 1.0 = the bounding box exactly touches the frame.

  I want a plain, flat, diagram-like look
        material.metal.mottle = None, material.bevel = None,
        staging.film.look = "None".

===========================================================================
WHERE THESE NUMBERS CAME FROM
===========================================================================

`staging` and `camera` are the studio setup the klink imaging demos were
built with. `material` was measured off a hand-shaded reference scene (a
MoS2 transistor figure, shaded by hand in Blender by a person making a
paper figure) — because the settings that make a device figure read well
are not guessable. Two findings from it drive the block below:

  * its metals are never a flat colour. Every one runs
    Noise -> ColourRamp -> Base Color, bright stop at the material's own
    colour, dark stop near black. That mottling is what stops a metal
    reading as plastic.
  * its dielectric is not grey-with-alpha. It is a saturated colour with
    BOTH alpha and transmission, a low IOR, a thin coat, a trace of
    sheen, and the specular level pulled right down so the highlight does
    not blow out the film underneath.

`bevel` is NOT from that scene — it is added here because layout prisms
are perfectly sharp boxes, and a perfectly sharp edge catches no
highlight at all.
"""
from klink.domains.imaging.blender_style import BlenderStyle

STYLE = BlenderStyle(
    name="demo-studio",

    # =================================================================
    # MATERIAL — how a declared material becomes a shader
    #
    # One recipe per class. klink picks the class from your STACK (see
    # the header); these only say what that class looks like.
    # =================================================================
    material={

        # ---- metal: your stack said metallic > 0.5 ------------------
        "metal": {
            # 0 = mirror, 1 = chalk. Real deposited metal is not a
            # mirror; below ~0.2 a figure starts reflecting the studio
            # back at the reader and reads as chrome.
            "roughness": 0.35,

            # Mottling: procedural noise driving the base colour between
            # the material's own colour and a near-black, so the surface
            # catches light unevenly. Set the whole block to None for a
            # flat, diagrammatic metal.
            "mottle": {
                # grain size, in noise units across the model. Bigger =
                # finer speckle. ~15 gives a few dozen patches on a cell
                # row; drop to ~4 for broad cloudy variation.
                "noise_scale": 15.0,
                # octaves of detail. 1 = smooth blobs; 4+ = crunchy.
                "noise_detail": 1.0,
                # how rough the noise itself is, 0..1.
                "noise_roughness": 1.0,
                # Where the colour ramp's two stops sit, 0..1. The gap
                # between them is the gradient band: WIDE gap (0.23 ->
                # 0.87) = mostly the two extremes with a soft
                # transition; narrow gap = hard mottled patches.
                "ramp_low": 0.233,      # this stop = the stack's colour
                "ramp_high": 0.873,     # this stop = dark_color
                # the dark end of the mottle. Near-black keeps the
                # material's own hue recognisable; lighten it for a
                # subtler, dustier metal.
                "dark_color": "#000004",
            },
        },

        # ---- dielectric: your stack gave it alpha < 1 ---------------
        # Not "grey with alpha" — a film you are meant to see THROUGH.
        "dielectric": {
            # high roughness scatters the transmitted light, which is
            # what makes an oxide look like an oxide and not glass.
            "roughness": 0.79,
            # the see-through knob that actually matters. alpha alone
            # gives a flat wash; transmission bends light through the
            # film so what is underneath stays legible.
            "transmission": 0.29,
            # index of refraction. Real SiO2 is 1.46; 1.30 here refracts
            # LESS, which keeps the buried geometry from distorting.
            "ior": 1.30,
            # a thin clearcoat: the faint surface gloss that reads as
            # "polished film". Keep it small — a strong coat turns the
            # oxide into a mirror and hides the device again.
            "coat_weight": 0.078,
            "coat_roughness": 0.179,
            # a trace of fabric-like edge lift. Barely visible, but it
            # separates stacked films of the same colour.
            "sheen": 0.021,
            # specular pulled WAY down (default is 0.5). This is what
            # stops a highlight from blowing out the film and erasing
            # whatever you were trying to show underneath it.
            "specular_level": 0.112,
        },

        # ---- matte: everything else --------------------------------
        # Substrate, wells, implants, spacers. These should recede: they
        # are context, not subject. No shine of any kind.
        "matte": {
            "roughness": 0.55,
        },

        # ---- bevel: rounded-edge SHADING, no geometry change --------
        # Layout prisms are mathematically sharp, and a sharp edge
        # catches no highlight, so an extruded GDS reads as a diagram.
        # A Bevel node fakes a rounded edge in the normal only — no
        # extra polygons, no change to your dimensions.
        # Set to None to switch it off.
        "bevel": {
            # a FRACTION of the model's overall size, not microns — so
            # the same style works for a 10 um cell row and a 5 mm die.
            # 0.0015 of a 12 um row is about 18 nm of visual rounding.
            # Too big and thin films start looking melted.
            "radius_fraction": 0.0015,
            # ray samples for the bevel normal. 4 is clean; raise it if
            # you see speckle on curved edges, at some render cost.
            "samples": 4,
        },
    },

    # =================================================================
    # STAGING — lights, world, film
    # =================================================================
    staging={
        # The hard light. A SUN is directional: only its ANGLE matters,
        # never its position. euler_deg is the light's rotation in
        # degrees (X, Y, Z); X tilts it away from straight-down, Z spins
        # it around the model. This one comes in high and from the left,
        # which is where a reader expects a light to be.
        "key_light": {"energy": 6.0, "euler_deg": [52, -14, 130]},

        # The soft light. An AREA light: a glowing rectangle that opens
        # up the shadows the sun casts. Its energy is in watts and is
        # NOT comparable to the sun's number above — 1400 W over a panel
        # this size is a gentle fill, not a second sun.
        "fill_light": {
            "energy": 1400,
            # panel size as a fraction of the model size. Bigger panel =
            # softer, more wrapped shadows.
            "size_fraction": 1.0,
            # where the panel sits, as fractions of model size from the
            # model's centre: [x, y, z]. Here: left, front, above.
            "offset_fraction": [-0.4, 0.55, 0.9],
            "euler_deg": [-33, 18, 0],
        },

        # The environment. Everything not lit by the two lights above
        # picks up this colour, so it sets the shadow tone. Near-black
        # keeps shadows deep; raise it toward grey for a softer,
        # product-shot feel.
        "world_color": "#0a0b0e",

        # The shadow-catcher floor. It is INVISIBLE in the output (the
        # film is transparent) — only its shadow is rendered, so the PNG
        # drops onto any paper or slide background with a real contact
        # shadow. The colour only affects bounce light onto the model.
        "backdrop": {"color": "#2e3036", "roughness": 0.9},

        "film": {
            # Blender's colour management. "Filmic" rolls off highlights
            # like film instead of clipping them white — important here
            # because metal specularity clips instantly under "Standard".
            "view_transform": "Filmic",
            # contrast curve on top of that. "None" for a flat,
            # measurement-like image.
            "look": "Medium High Contrast",
            # how many times light may bounce before Cycles gives up.
            "max_bounces": 12,
            # bounces specifically through TRANSPARENT surfaces. A stack
            # is several translucent films in a row and a ray must
            # survive all of them to reach the metal underneath — set
            # this too low and the bottom of your stack goes black.
            "transparent_max_bounces": 8,
        },
    },

    # =================================================================
    # CAMERA
    # =================================================================
    camera={
        # focal length in mm on a 36 mm sensor. 58 is a mild telephoto:
        # near-parallel edges, little perspective distortion, which is
        # what a technical figure wants. 24 would splay the die corners.
        "lens_mm": 58,
        "sensor_mm": 36.0,
        # framing slack. klink solves the exact distance that fits your
        # bounding box, then multiplies by this. 1.0 = the box touches
        # the frame edges; 1.06 leaves a thin margin.
        "margin": 1.06,
        # WHERE the camera stands, as a direction from the model centre
        # (x, y, z; length is ignored, klink solves the distance). The
        # three names are the presets the tool's `camera=` accepts.
        "directions": {
            # three-quarter view from the front-right, slightly above
            "default": [0.55, -0.75, 0.45],
            # from BEHIND (+y), to look into a cutaway face made by
            # imaging.render3d fraction<1
            "face": [0.30, 0.85, 0.42],
            # near-plan view, tipped just enough to read thickness
            "top": [0.0, -0.15, 0.9],
        },
    },

    # =================================================================
    # LATTICE  (mode='figure' only)
    # =================================================================
    # Drawing scale for stack layers declared kind='lattice' — the ones
    # that render as an atomic mesh instead of a solid prism.
    #
    # This is NOT the physical lattice constant. Graphene's is 0.246 nm
    # and MoS2's 0.315 nm; at true scale a single atom is far smaller
    # than one pixel of a micron-wide figure, so the lattice is drawn
    # ENLARGED and the picture is a schematic of the material, not a
    # measurement of it. Say so in your caption.
    #
    # Bigger = fewer, larger atoms. Below ~0.03 on a several-micron
    # flake the atom count explodes and the render crawls.
    # A stack with no kind='lattice' layer never reads any of this.
    lattice={
        "a_um": 0.09,

        # ---- what each atom looks like ---------------------------
        # The motif library gives klink POSITIONS. These give it the
        # look. Mo blue-grey and S yellow are the convention in the
        # 2D-material literature — a convention, not a physical fact,
        # which is exactly why it is declared here and not in klink.
        # `radius_fraction` is of a_um above, so atoms scale with the
        # drawing lattice and never with the real one.
        #
        # A motif containing a species you have not declared is an
        # error naming it — klink has no palette for atoms.
        "species": {
            "C":  {"color": "#292b30", "radius_fraction": 0.16},
            "Mo": {"color": "#4a7594", "radius_fraction": 0.22},
            "S":  {"color": "#f2d447", "radius_fraction": 0.16},
        },

        # finish shared by every atom. A little metallic keeps the
        # spheres from reading as matte plastic beads; roughness
        # near 0 turns them into mirrors and the lattice disappears
        # into reflections of the studio.
        "atom": {"metallic": 0.1, "roughness": 0.35},

        # the sticks between them. radius_fraction of a_um again;
        # much above ~0.08 the bonds start hiding the atoms.
        "bond": {"color": "#8c949e", "roughness": 0.5,
                 "radius_fraction": 0.055},
    },
)


if __name__ == "__main__":
    # `python blender_style.py` writes blender_style.json next to this file.
    # The MCP tools take a JSON PATH, not a Python object, so this is
    # the one step between editing the numbers above and calling a tool.
    import pathlib

    out = pathlib.Path(__file__).with_suffix(".json")
    STYLE.save(str(out))
    print(out)
