"""Imaging domain: one VisualStack declaration, every exit.

Design doc: docs/IMAGING_FAMILY_DESIGN.md.  Exits (built knife by
knife): xsection (process cross-section via the klayout_pyxs engine),
render3d (GLB + self-contained web viewer), sem_top (SEM-style top
view), blender (paper-grade figures).  All heavy dependencies are
OPTIONAL and imported lazily; a missing one raises an instructive
error naming the exact pip command.

klink ships MECHANISM only: VisualStack instances, .pyxs recipes and
layer maps are example/project-owned.
"""

from klink.domains.imaging.visual_stack import (
    VisualStack,
    VisualLayer,
    VisualStackError,
)

__all__ = [
    "VisualLayer",
    "VisualStack",
    "VisualStackError",
]
