"""The multilayer engine's faithful grid (Stage T1).

A non-uniform, track-resolution node grid ported from OpenROAD's
``FlexGridGraph`` (``drt/src/dr/FlexGridGraph.h``). Design + source mapping are in
this module's docstring set. This supersedes ``routing/grid/capacity_grid``
for the multilayer engine; it does NOT import from ``backends/flexdr/`` (the
frozen single-stack engine).
"""

from klink.routing.backends.pnr_multilayer.grid.track_grid import TrackGrid

__all__ = ["TrackGrid"]
