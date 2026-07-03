from __future__ import annotations

import math

from klink.port.inference import (
    align_triangle_to_nearby_edge,
    infer_polygon_points,
    snap_to_angle_grid,
)
from klink.port.naming import auto_name
from klink.port.validation import (
    duplicate_name_repairs,
    duplicate_names,
    off_grid_orientations,
)
from klink.port.workflow import (
    infer_shape_port,
    is_handdrawn_port_marker,
    shape_edges,
)


def test_triangle_inference_snaps_drawn_marker_to_45_degree_grid():
    orient, width = infer_polygon_points(
        [[77148, 6931], [76858, 1285], [70166, 8716]],
        dbu=0.001,
    )

    assert orient == 45.0
    assert width > 0


def test_nearby_edge_alignment_overrides_grid_and_projects_center():
    marker = [
        [109423, 106773],
        [108268, 104773],
        [111732, 106773],
    ]
    orient, _ = infer_polygon_points(marker, dbu=0.001)
    assert orient == 135.0

    aligned = align_triangle_to_nearby_edge(
        marker,
        edges=[[100000, 100000, 120000, 111547]],
        inferred_orientation=orient,
    )

    assert aligned["attached"] is True
    assert abs(aligned["orientation"] - 120.0) < 1e-3
    assert aligned["center"] is not None
    assert aligned["edge"] == [100000.0, 100000.0, 120000.0, 111547.0]
    # The marker base midpoint is not already on the edge. Attached center is
    # the projection onto the nearby edge, so it should move.
    assert aligned["center"] != (110000.0, 105773.0)


def test_conflicting_nearby_edges_fall_back_to_drawn_grid_orientation():
    marker = [
        [109423, 106773],
        [108268, 104773],
        [111732, 106773],
    ]
    orient, _ = infer_polygon_points(marker, dbu=0.001)

    aligned = align_triangle_to_nearby_edge(
        marker,
        edges=[
            [100000, 100000, 120000, 111547],
            [108000, 104000, 108000, 108000],
        ],
        inferred_orientation=orient,
        angle_tolerance_deg=90.0,
    )

    assert aligned["attached"] is False
    assert aligned["orientation"] == 135.0
    assert aligned["center"] is None


def test_snap_to_angle_grid_is_exact_for_expected_octants():
    assert snap_to_angle_grid(43.0) == 45.0
    assert snap_to_angle_grid(359.0) == 0.0
    assert snap_to_angle_grid(226.0) == 225.0


def test_auto_name_uses_unique_handles_and_requested_style():
    assert auto_name(90, {"P0"}, style="index") == "P1"
    assert auto_name(90, {"N0"}, style="direction") == "N1"
    assert auto_name(0, {"e0"}, port_type="electrical", style="type") == "e1"


def test_validation_reports_duplicate_names_and_off_grid_orientation():
    ports = [
        {"name": "P0", "orientation": 0},
        {"name": "P0", "orientation": 44.8},
        {"name": "P2", "orientation": 91.2},
    ]

    assert duplicate_names(ports) == [{"name": "P0", "count": 2}]
    repairs = duplicate_name_repairs(ports)
    assert repairs[0]["old_name"] == "P0"
    assert repairs[0]["new_name"] == "P1"

    off_grid = off_grid_orientations(ports, tolerance_deg=0.1)
    assert [(p["name"], p["nearest_grid_orientation"]) for p in off_grid] == [
        ("P0", 45.0),
        ("P2", 90.0),
    ]


def test_workflow_infers_marker_shape_and_attaches_to_edge():
    marker = {
        "type": "polygon",
        "layer_index": 9,
        "bbox_dbu": [108268, 104773, 111732, 106773],
        "points_dbu": [
            [109423, 106773],
            [108268, 104773],
            [111732, 106773],
        ],
    }
    box = {
        "type": "box",
        "layer_index": 1,
        "bbox_dbu": [0, 0, 10, 20],
    }
    assert len(shape_edges(box)) == 4

    ref_edge = [100000, 100000, 120000, 111547]
    inferred = infer_shape_port(marker, dbu=0.001, edges=[ref_edge])

    assert abs(inferred["orientation"] - 120.0) < 1e-3
    assert inferred["attached"] is True
    assert inferred["access_mode"] == "edge"
    assert inferred["slide_allowed"] is True
    assert inferred["slide_edge"] == "100000,100000,120000,111547"
    x, y = inferred["center_dbu"]
    x0, y0, x1, y1 = ref_edge
    distance = abs((y1 - y0) * x - (x1 - x0) * y + x1 * y0 - y1 * x0)
    distance /= math.hypot(y1 - y0, x1 - x0)
    assert distance <= 1.0


def test_shape_edges_extracts_polygon_edges():
    ref = {
        "type": "polygon",
        "layer_index": 1,
        "bbox_dbu": [100000, 100000, 120000, 112413],
        "points_dbu": [
            [100000, 100000],
            [120000, 111547],
            [119500, 112413],
            [99500, 100866],
        ],
    }

    assert len(shape_edges(ref)) == 4


def test_handdrawn_marker_accepts_only_triangular_polygons():
    assert is_handdrawn_port_marker({
        "type": "polygon",
        "points_dbu": [[0, 0], [10, 0], [5, 5]],
    })
    assert not is_handdrawn_port_marker({
        "type": "polygon",
        "points_dbu": [[0, 0], [10, 0], [10, 10], [0, 10]],
    })
    assert not is_handdrawn_port_marker({
        "type": "box",
        "bbox_dbu": [0, 0, 10, 10],
    })
