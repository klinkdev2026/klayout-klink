"""L-Edit bridge: file-exchange RPC client + conversion + T-Cell toolkit.

Transport contract (schema 1) is implemented by the single-file UPI macro
``example_template/ledit_bridge/ledit_bridge.cpp`` which the user loads as
SOURCE in L-Edit (Tools > Macro > Load Macro...; L-Edit compiles it
itself — never distribute compiled artifacts).

Modules:
- :mod:`.client`  — LEditBridgeClient: namespace discovery, hello-based
  liveness, request/response with instructive errors.
- :mod:`.adapter` — generic L-Edit->KLayout object conversion (capability
  matching, polygon fallback, layer mapping) and harvesting.
- :mod:`.tcell`   — T-Cell toolkit: parameter parsing from generator code,
  programmatic variant generation, byte-exact differential verification.
"""
from .client import LEditBridgeClient, LEditBridgeError
from .adapter import (build_layer_map, convert_object, selection_to_items,
                      harvest_boxes, merge_layer_name, nest_properties)
from .tree import (push_cell_tree, import_cell_tree, collect_klayout_tree,
                   collect_ledit_tree)
from .tcell import (parse_tcell_params, VariantFactory, DiffReport,
                    verify_differential)

__all__ = [
    "LEditBridgeClient", "LEditBridgeError",
    "build_layer_map", "convert_object", "selection_to_items",
    "harvest_boxes", "merge_layer_name", "nest_properties",
    "push_cell_tree", "import_cell_tree",
    "collect_klayout_tree", "collect_ledit_tree",
    "parse_tcell_params", "VariantFactory", "DiffReport",
    "verify_differential",
]
