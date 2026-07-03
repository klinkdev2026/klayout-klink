"""Stage T5 of the multilayer engine -- FlexDR detailed route on TrackGrid (the
verdict stage).

Incremental, minimum-surface port of the worker from the uniform ``CapacityGrid`` onto
the non-uniform ``TrackGrid`` (T1), Python-first. Design + source mapping + the
live-LVS protocol comparing this engine against the frozen single-stack engine are
in this module's docstring set.

Increment A (this module): the GEOMETRY surface only
(``TrackGridWorkerAdapter``) -- no legality, no maze, no DRC. No import from
``backends/flexdr/`` (the frozen single-stack engine).
"""

from klink.routing.backends.pnr_multilayer.dr.legality import BLOCK, load_legality
from klink.routing.backends.pnr_multilayer.dr.maze import (
    TrackMaze,
    checkerboard_tiles,
    nodes_from_t4,
)
from klink.routing.backends.pnr_multilayer.dr.trackgrid_adapter import (
    TrackGridWorkerAdapter,
)

__all__ = [
    "TrackGridWorkerAdapter",
    "load_legality",
    "BLOCK",
    "TrackMaze",
    "checkerboard_tiles",
    "nodes_from_t4",
]
