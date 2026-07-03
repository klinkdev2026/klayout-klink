"""Stage T3 of the multilayer engine -- 3D global route = LAYER ASSIGNMENT (gap G3,
the centre).

Scoped-faithful port of OpenROAD FastRoute 3D (`grt/src/fastroute/maze3D.cpp`):
turn the 2D global route into layer-resolved guides. Design + source mapping +
scope fence are in this module's docstring set. Emits guides for FlexTA (T4);
does NOT assign tracks (T4) or detail-route (T5). No import from `backends/flexdr/`
(the frozen single-stack engine).
"""

from klink.routing.backends.pnr_multilayer.gr3d.layer_assign import (
    LayerStack,
    assign_layers,
    stack_from_profile,
)

__all__ = ["LayerStack", "assign_layers", "stack_from_profile"]
