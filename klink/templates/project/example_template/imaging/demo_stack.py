"""EXAMPLE-owned visual declaration for the imaging demos (edit for
YOUR process — klink ships no stacks).

Two kinds of entry:

``layers``        MASK layers (the GDS layers in demo_layout.py). They
                  carry the z range used by the fast 3D mode, the SEM
                  emission grey, and — via ``recipe_symbol`` — the name
                  of the material the recipe assigns them to.
``recipe_styles`` materials that exist only inside the recipe (no mask
                  layer of their own: substrate, oxides, implants,
                  plugs). Keyed by the recipe VARIABLE name.

Anything the recipe produces that is not declared here still renders —
in a deterministic auto-color, and it is reported back as ``unstyled``
so you know to declare it.
"""
from klink.domains.imaging.visual_stack import VisualLayer, VisualStack

STACK = VisualStack(
    name="demo-cmos",
    layers=(
        VisualLayer(layer="1/0", z0_um=-0.55, z1_um=0.0, name="n-well",
                    color="#e8dcbe", sem_grey=0.26, edge_glow=0.35,
                    recipe_symbol="nwell"),
        VisualLayer(layer="2/0", z0_um=-0.05, z1_um=0.02, name="active",
                    color="#b9d7a8", sem_grey=0.40, edge_glow=0.5,
                    recipe_symbol=""),
        VisualLayer(layer="3/0", z0_um=0.02, z1_um=0.18, name="poly",
                    color="#c94f4f", sem_grey=0.62, edge_glow=0.8,
                    recipe_symbol="poly"),
        VisualLayer(layer="4/0", z0_um=0.18, z1_um=0.75, name="contact",
                    color="#6e6e78", metallic=0.8, sem_grey=0.88,
                    edge_glow=1.0, recipe_symbol=""),
        VisualLayer(layer="6/0", z0_um=0.75, z1_um=0.97, name="metal-1",
                    color="#aab8c4", metallic=0.95, sem_grey=0.78,
                    edge_glow=0.9, recipe_symbol="metal1"),
    ),
    # engine-only materials, keyed by the recipe's variable names
    recipe_styles={
        "pbulk":    {"name": "p-substrate",  "color": "#c8b89a"},
        "fox":      {"name": "field oxide",  "color": "#9db4c8"},
        "gox":      {"name": "gate oxide",   "color": "#7fd4e0"},
        "silicide": {"name": "silicide",     "color": "#7a2b2b",
                     "metallic": 0.7},
        "nldd":     {"name": "n-LDD",        "color": "#bcd9b2"},
        "pldd":     {"name": "p-LDD",        "color": "#d9c2e8"},
        "spacer":   {"name": "spacer",       "color": "#dfe6ec"},
        "nsd":      {"name": "n+ S/D",       "color": "#96c87f"},
        "psd":      {"name": "p+ S/D",       "color": "#c9a0dc"},
        # the dielectrics are deliberately see-through: in the 3D
        # exits that is what lets you see the gates and plugs
        # buried inside the stack
        "ild":      {"name": "ILD",          "color": "#cfd8e0",
                     "alpha": 0.45},
        "tungsten": {"name": "W plug",       "color": "#6e6e78",
                     "metallic": 0.9},
        "imd":      {"name": "IMD",          "color": "#dde4ea",
                     "alpha": 0.45},
    },
)
