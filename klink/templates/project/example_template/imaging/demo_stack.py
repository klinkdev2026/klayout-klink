"""EXAMPLE-owned visual declaration for the imaging demos (edit for
YOUR process — klink ships no stacks)."""
from klink.domains.imaging.visual_stack import VisualLayer, VisualStack

STACK = VisualStack(
    name="demo-process",
    layers=(
        VisualLayer(layer="1/0", z0_um=-0.4, z1_um=0.0, name="well",
                    color="#e0d2ae", sem_grey=0.30, edge_glow=0.4,
                    recipe_symbol="well"),
        VisualLayer(layer="2/0", z0_um=0.0, z1_um=0.15, name="metal",
                    color="#aab8c4", metallic=0.9, sem_grey=0.75,
                    edge_glow=0.9, recipe_symbol="metal"),
    ),
    # engine-only materials (no mask layer of their own)
    recipe_styles={
        "pbulk": {"name": "substrate", "color": "#c8b89a"},
        "ox": {"name": "oxide", "color": "#b9c8d6", "alpha": 0.35},
    },
)
