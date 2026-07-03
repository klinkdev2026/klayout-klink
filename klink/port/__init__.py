"""Port inference and manipulation helpers.

This package contains pure Python logic used by client-side workflows. The
plugin layer should stay thin and expose RPC primitives only; orchestration
code here reads layout data through RPC, makes decisions, then writes Port
PCell instances back through RPC.
"""

from .inference import (
    NEAR_EDGE_ANGLE_TOLERANCE_DEG,
    NEAR_EDGE_CLUSTER_TOLERANCE_DEG,
    NEAR_EDGE_DISTANCE_RATIO,
    NEAR_EDGE_MIN_DBU,
    align_triangle_to_nearby_edge,
    angle_diff,
    infer_box_direction,
    infer_path_direction,
    infer_polygon_points,
    snap_to_angle_grid,
    triangle_base_geometry,
    undirected_angle_diff,
)
from .naming import auto_name, direction_prefix
from .validation import (
    duplicate_name_groups,
    duplicate_name_repairs,
    duplicate_names,
    off_grid_orientations,
)
from .workflow import (
    import_ports_from_layer,
    import_ports_from_selection,
    infer_shape_port,
    is_handdrawn_port_marker,
    recognize_handdrawn_ports,
    shape_edges,
)

__all__ = [
    "NEAR_EDGE_ANGLE_TOLERANCE_DEG",
    "NEAR_EDGE_CLUSTER_TOLERANCE_DEG",
    "NEAR_EDGE_DISTANCE_RATIO",
    "NEAR_EDGE_MIN_DBU",
    "align_triangle_to_nearby_edge",
    "angle_diff",
    "infer_box_direction",
    "infer_path_direction",
    "infer_polygon_points",
    "snap_to_angle_grid",
    "triangle_base_geometry",
    "undirected_angle_diff",
    "auto_name",
    "direction_prefix",
    "duplicate_name_groups",
    "duplicate_name_repairs",
    "duplicate_names",
    "import_ports_from_layer",
    "import_ports_from_selection",
    "infer_shape_port",
    "is_handdrawn_port_marker",
    "off_grid_orientations",
    "recognize_handdrawn_ports",
    "shape_edges",
]
