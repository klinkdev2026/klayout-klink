from __future__ import annotations

from klink.anchor.inference import (
    infer_anchor_marker,
    is_box_marker,
    is_corridor_marker,
    is_triangle_marker,
    triangle_incircle,
)
from klink.anchor.naming import auto_id


def test_triangle_marker_infers_flexible_bend_region_from_incircle():
    shape = {
        "type": "polygon",
        "bbox_dbu": [0, 0, 10000, 8660],
        "points_dbu": [[0, 0], [10000, 0], [5000, 8660]],
    }

    assert is_triangle_marker(shape)
    incircle = triangle_incircle(shape["points_dbu"], dbu=0.001)
    assert incircle is not None
    assert [round(v) for v in incircle["center_dbu"]] == [5000, 2887]
    assert round(incircle["radius_um"], 3) == 2.887

    inferred = infer_anchor_marker(shape, dbu=0.001, default_net="sig0")

    assert inferred is not None
    assert inferred["kind"] == "bend_region"
    assert inferred["mode"] == "flexible"
    assert inferred["net"] == "sig0"
    assert round(inferred["radius_um"], 3) == 2.887
    assert inferred["center_dbu"] == [5000, 2887]


def test_box_marker_infers_waypoint_region():
    shape = {
        "type": "box",
        "bbox_dbu": [1000, 2000, 11000, 8000],
    }

    assert is_box_marker(shape)
    inferred = infer_anchor_marker(shape, dbu=0.001)

    assert inferred is not None
    assert inferred["kind"] == "waypoint_region"
    assert inferred["center_dbu"] == [6000, 5000]
    assert inferred["width_um"] == 10.0
    assert inferred["height_um"] == 6.0


def test_path_marker_infers_corridor_with_relative_points():
    shape = {
        "type": "path",
        "bbox_dbu": [0, 0, 20000, 10000],
        "points_dbu": [[0, 0], [10000, 10000], [20000, 0]],
        "width_dbu": 3000,
    }

    assert is_corridor_marker(shape)
    inferred = infer_anchor_marker(shape, dbu=0.001)

    assert inferred is not None
    assert inferred["kind"] == "corridor"
    assert inferred["center_dbu"] == [10000, 5000]
    assert inferred["width_um"] == 3.0
    assert inferred["path_points"] == "-10,-5;0,5;10,-5"


def test_invalid_polygon_is_not_anchor_marker():
    shape = {
        "type": "polygon",
        "bbox_dbu": [0, 0, 10000, 10000],
        "points_dbu": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]],
    }

    assert not is_triangle_marker(shape)
    assert infer_anchor_marker(shape, dbu=0.001) is None


def test_auto_id_uses_unique_handles():
    assert auto_id(set()) == "A0"
    assert auto_id({"A0", "A1"}) == "A2"
    assert auto_id({"B0"}, prefix="B") == "B1"
