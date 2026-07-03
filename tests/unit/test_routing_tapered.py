"""Unit tests for klink.routing.backends.geometric.tapered — tapered router with variable-width polygons."""

from __future__ import annotations

import math

import pytest

from klink.routing.backends.geometric.tapered import (
    _find_bend_indices,
    _is_bend,
    build_trapezoid_polygon,
    commit_tapered_routes,
    compute_taper_ratio,
    compute_tapered_widths,
    polygon_hits_bboxes,
    route_tapered,
    strategy_back_load,
    strategy_front_load,
    strategy_uniform,
    validate_tapered_route,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _port(name="A", net="sig", center=None, orientation=0.0, width=4.0, port_type="electrical"):
    center = center or [0.0, 0.0]
    return {
        "name": name,
        "net": net,
        "center_um": [float(center[0]), float(center[1])],
        "orientation": float(orientation),
        "width_um": float(width),
        "port_type": port_type,
        "target_layer": "10/0",
    }


# ---------------------------------------------------------------------------
# _is_bend / _find_bend_indices
# ---------------------------------------------------------------------------


def test_is_bend_collinear_returns_false():
    assert _is_bend([0, 0], [5, 0], [10, 0]) is False


def test_is_bend_90_degree_returns_true():
    assert _is_bend([0, 0], [5, 0], [5, 5]) is True


def test_is_bend_45_degree_returns_true():
    assert _is_bend([0, 0], [10, 0], [20, 10]) is True


def test_is_bend_near_collinear_returns_false():
    # sub-nanometer deviation → still collinear
    assert _is_bend([0, 0], [10, 0], [20, 1e-12]) is False


def test_find_bend_indices_straight_line_returns_empty():
    points = [[0, 0], [50, 0], [100, 0]]
    assert _find_bend_indices(points) == []


def test_find_bend_indices_l_shape():
    points = [[0, 0], [50, 0], [50, 50]]
    assert _find_bend_indices(points) == [1]


def test_find_bend_indices_u_shape():
    points = [[0, 0], [50, 0], [50, 50], [0, 50]]
    assert _find_bend_indices(points) == [1, 2]


def test_find_bend_indices_two_points_returns_empty():
    assert _find_bend_indices([[0, 0], [10, 0]]) == []


# ---------------------------------------------------------------------------
# compute_taper_ratio
# ---------------------------------------------------------------------------


def test_ratio_zero_bends_returns_1():
    assert compute_taper_ratio(5.0, 2.0, 0) == 1.0


def test_ratio_single_bend_uniform():
    # one bend must carry the full change
    assert compute_taper_ratio(5.0, 2.0, 1) == pytest.approx(0.4)


def test_ratio_two_bends_uniform():
    # r² = 2/5 → r = sqrt(0.4) ≈ 0.6325
    assert compute_taper_ratio(5.0, 2.0, 2) == pytest.approx(0.6324555)


def test_ratio_equal_widths():
    assert compute_taper_ratio(4.0, 4.0, 3) == 1.0


def test_ratio_source_zero_returns_1():
    assert compute_taper_ratio(0.0, 5.0, 2) == 1.0


def test_ratio_target_zero_returns_1():
    assert compute_taper_ratio(5.0, 0.0, 2) == 1.0


def test_ratio_widening():
    # target > source → ratio > 1
    r = compute_taper_ratio(2.0, 8.0, 2)
    assert r > 1.0
    assert 2.0 * r * r == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# compute_tapered_widths
# ---------------------------------------------------------------------------


def test_widths_straight_two_points_same_width():
    widths = compute_tapered_widths([[0, 0], [100, 0]], 4.0, 4.0)
    assert widths == [4.0, 4.0]


def test_widths_straight_two_points_different():
    widths = compute_tapered_widths([[0, 0], [100, 0]], 5.0, 2.0)
    assert widths[0] == 5.0
    assert widths[-1] == 2.0


def test_widths_no_bend_linear_interpolation():
    # collinear points → linear interpolate
    widths = compute_tapered_widths([[0, 0], [50, 0], [100, 0]], 5.0, 2.0)
    assert widths[0] == 5.0
    assert widths[1] == pytest.approx(3.5)
    assert widths[2] == 2.0


def test_widths_one_bend_uniform():
    points = [[0, 0], [50, 0], [50, 50]]
    widths = compute_tapered_widths(points, 5.0, 2.0, strategy="uniform")
    assert widths[0] == 5.0
    assert widths[1] == 2.0  # bend here, full transition
    assert widths[2] == 2.0


def test_widths_two_bends_uniform():
    points = [[0, 0], [50, 0], [50, 50], [100, 50]]
    widths = compute_tapered_widths(points, 5.0, 2.0, strategy="uniform")
    r = (2.0 / 5.0) ** 0.5
    assert widths[0] == 5.0
    assert widths[1] == pytest.approx(5.0 * r)  # bend 1
    assert widths[2] == pytest.approx(2.0)       # bend 2
    assert widths[3] == 2.0


def test_widths_two_bends_front_load():
    points = [[0, 0], [50, 0], [50, 50], [100, 50]]
    widths = compute_tapered_widths(points, 5.0, 2.0, strategy="front_load")
    # first bend does ~50% of change: 5→3.5
    assert widths[1] == pytest.approx(3.5)
    assert widths[2] == pytest.approx(2.0)


def test_widths_two_bends_back_load():
    points = [[0, 0], [50, 0], [50, 50], [100, 50]]
    widths = compute_tapered_widths(points, 5.0, 2.0, strategy="back_load")
    # first bend gentle, last bend aggressive
    assert widths[1] == pytest.approx(3.5)
    assert widths[2] == pytest.approx(2.0)


def test_widths_custom_callable_strategy():
    points = [[0, 0], [50, 0], [50, 50], [100, 50]]

    def half_then_rest(bend_idx, num_bends, sw, tw):
        if bend_idx == 0:
            return (sw + tw) / 2.0
        return tw

    widths = compute_tapered_widths(points, 10.0, 2.0, strategy=half_then_rest)
    assert widths[1] == pytest.approx(6.0)
    assert widths[2] == pytest.approx(2.0)


def test_widths_unknown_strategy_raises():
    with pytest.raises(ValueError, match="unknown taper strategy"):
        compute_tapered_widths([[0, 0], [50, 0], [50, 50]], 5.0, 2.0, strategy="nonsense")


def test_widths_single_point():
    widths = compute_tapered_widths([[0, 0]], 5.0, 3.0)
    assert widths == [5.0]


def test_widths_target_width_set_on_last_point():
    points = [[0, 0], [50, 0], [50, 50]]
    widths = compute_tapered_widths(points, 10.0, 3.0, strategy="uniform")
    assert widths[-1] == 3.0


# ---------------------------------------------------------------------------
# build_trapezoid_polygon
# ---------------------------------------------------------------------------


def test_polygon_straight_single_segment():
    points = [[0, 0], [100, 0]]
    widths = [5.0, 2.0]
    poly = build_trapezoid_polygon(points, widths, corner_style="miter")
    # 4 vertices for a simple trapezoid
    assert len(poly) == 4
    # left side goes down (negative y), right side goes up (positive y)
    ys = [p[1] for p in poly]
    assert min(ys) < -1.0  # left edge at y ≈ -2.5
    assert max(ys) > 1.0   # right edge at y ≈ +2.5


def test_polygon_straight_single_segment_uniform_width_is_rectangle():
    points = [[0, 0], [100, 0]]
    widths = [4.0, 4.0]
    poly = build_trapezoid_polygon(points, widths)
    assert len(poly) == 4
    # all x at 0 or 100, all y at ±2.0
    xs = sorted({p[0] for p in poly})
    ys = sorted({p[1] for p in poly})
    assert xs == [0.0, 100.0]
    assert ys == [-2.0, 2.0]


def test_polygon_l_shape_miter():
    points = [[0, 0], [50, 0], [50, 50]]
    widths = [5.0, 5.0, 5.0]
    poly = build_trapezoid_polygon(points, widths, corner_style="miter")
    assert len(poly) >= 6
    # polygon is closed — first and last are different
    assert poly[0] != poly[-1]


def test_polygon_l_shape_bevel():
    points = [[0, 0], [50, 0], [50, 50]]
    widths = [5.0, 5.0, 5.0]
    poly_miter = build_trapezoid_polygon(points, widths, corner_style="miter")
    poly_bevel = build_trapezoid_polygon(points, widths, corner_style="bevel", miter_limit=1.5)
    # bevel cuts the miter → same # vertices for a 90° bend with these widths
    # (miter_dist ≈ 3.5 for half_w=2.5 at 90°, limit is 1.5*2.5=3.75, so miter is within limit)
    assert len(poly_bevel) >= 6


def test_polygon_l_shape_bevel_triggers_on_low_limit():
    points = [[0, 0], [50, 0], [50, 50]]
    widths = [10.0, 10.0, 10.0]  # wide → miter extends far
    poly_miter = build_trapezoid_polygon(points, widths, corner_style="miter")
    poly_bevel = build_trapezoid_polygon(points, widths, corner_style="bevel", miter_limit=1.0)
    # bevel with tight limit should produce more vertices (extra bevel face)
    assert len(poly_bevel) > len(poly_miter)


def test_polygon_l_shape_round():
    points = [[0, 0], [50, 0], [50, 50]]
    widths = [5.0, 5.0, 5.0]
    poly = build_trapezoid_polygon(points, widths, corner_style="round", arc_points=6)
    # round adds extra arc vertices on BOTH sides
    assert len(poly) >= 14  # more than miter's 6


def test_polygon_empty_for_insufficient_points():
    assert build_trapezoid_polygon([[0, 0]], [5.0]) == []
    assert build_trapezoid_polygon([], []) == []


def test_polygon_width_mismatch_returns_empty():
    assert build_trapezoid_polygon([[0, 0], [10, 0]], [5.0]) == []


# ---------------------------------------------------------------------------
# route_tapered
# ---------------------------------------------------------------------------


def test_route_tapered_straight_same_width():
    src = _port("A", center=[0, 0], orientation=0, width=4.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=4.0)
    route = route_tapered(src, tgt)
    assert route["backend"] == "tapered"
    assert route["num_bends"] == 0
    assert route["source_width_um"] == 4.0
    assert route["target_width_um"] == 4.0
    assert route["width_um"] == 4.0
    assert len(route["points_um"]) >= 2
    assert len(route["polygon_um"]) >= 4


def test_route_tapered_straight_different_widths():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=2.0)
    route = route_tapered(src, tgt)
    assert route["source_width_um"] == 5.0
    assert route["target_width_um"] == 2.0
    assert route["width_um"] == 2.0  # narrow for pathfinding
    assert route["strategy"] == "uniform"
    assert route["corner_style"] == "miter"
    # 4 centerline pts (center→launch→launch→center) → 2*4=8 polygon verts
    assert len(route["polygon_um"]) == 8


def test_route_tapered_with_inner_waypoint():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 40], orientation=180, width=2.0)
    route = route_tapered(src, tgt, [[60, 40]], strategy="uniform")
    # source_center → source_launch → waypoint → target_launch → target_center
    assert route["num_bends"] >= 1
    assert len(route["per_bend_ratios"]) == route["num_bends"]


def test_route_tapered_strategy_passthrough():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=2.0)
    route = route_tapered(src, tgt, strategy="front_load")
    assert route["strategy"] == "front_load"


def test_route_tapered_custom_callable_strategy():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=2.0)

    def my_strat(bend_idx, num_bends, sw, tw):
        return tw

    route = route_tapered(src, tgt, strategy=my_strat)
    assert route["strategy"] == "custom"


def test_route_tapered_corner_style_passthrough():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 40], orientation=180, width=2.0)
    route = route_tapered(src, tgt, [[60, 40]], corner_style="round", arc_points=12)
    assert route["corner_style"] == "round"
    # round produces more polygon vertices than miter
    route_miter = route_tapered(src, tgt, [[60, 40]], corner_style="miter")
    assert len(route["polygon_um"]) > len(route_miter["polygon_um"])


def test_route_tapered_includes_port_launch_stubs():
    src = _port("A", center=[0, 0], orientation=0, width=4.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=4.0)
    route = route_tapered(src, tgt)
    pts = route["points_um"]
    # source_center → source_launch → ... → target_launch → target_center
    assert pts[0] == [0.0, 0.0]
    assert pts[-1] == [100.0, 0.0]
    # source_launch is offset from source_center in the orientation direction
    assert route["source_launch_um"][0] > 0.0
    assert route["target_launch_um"][0] < 100.0


def test_route_tapered_preserves_port_orientation_while_breaking_terminal_hairpin():
    src = _port("NORTH", center=[0, 45], orientation=90, width=4.0)
    tgt = _port("PAD_N", center=[0, 115], orientation=90, width=4.0)

    route = route_tapered(src, tgt)
    points = route["points_um"]

    assert route["target_launch_um"] == [pytest.approx(0.0), 123.0]
    assert points[-2] == [pytest.approx(0.0), 123.0]
    assert points[-1] == [0.0, 115.0]
    assert points[-3][1] == pytest.approx(123.0)
    assert points[-3][0] != pytest.approx(0.0)
    assert len(route["polygon_um"]) >= 6


# ---------------------------------------------------------------------------
# polygon_hits_bboxes
# ---------------------------------------------------------------------------


def test_polygon_clear_of_bboxes():
    poly = [[0, -1], [10, -1], [10, 1], [0, 1]]
    bboxes = [[20, -5, 30, 5]]  # far away
    assert polygon_hits_bboxes(poly, bboxes) == []


def test_polygon_hits_overlapping_bbox():
    poly = [[0, -1], [10, -1], [10, 1], [0, 1]]
    bboxes = [[5, -2, 15, 2]]  # overlaps right half
    hits = polygon_hits_bboxes(poly, bboxes)
    assert len(hits) == 1
    assert hits[0]["bbox_um"] == [5.0, -2.0, 15.0, 2.0]


def test_polygon_hits_bbox_contained_inside_polygon():
    poly = [[0, -5], [20, -5], [20, 5], [0, 5]]
    bboxes = [[5, -1, 15, 1]]  # fully inside polygon
    hits = polygon_hits_bboxes(poly, bboxes)
    assert len(hits) == 1


def test_polygon_vertex_inside_bbox():
    poly = [[0, -1], [10, -1], [10, 1], [0, 1]]
    bboxes = [[8, 0, 20, 5]]  # overlaps with (10,1) vertex inside
    hits = polygon_hits_bboxes(poly, bboxes)
    assert len(hits) >= 1


def test_polygon_hits_empty_inputs():
    assert polygon_hits_bboxes([], [[1, 2, 3, 4]]) == []
    assert polygon_hits_bboxes([[0, 0], [1, 0], [1, 1]], []) == []


# ---------------------------------------------------------------------------
# validate_tapered_route
# ---------------------------------------------------------------------------


def test_validate_clean_route():
    src = _port("A", center=[0, 0], orientation=0, width=4.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=4.0)
    route = route_tapered(src, tgt)
    result = validate_tapered_route(route, obstacle_bboxes=[[200, 0, 300, 10]])
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_route_hitting_obstacle():
    src = _port("A", center=[0, 0], orientation=0, width=10.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=10.0)
    route = route_tapered(src, tgt)
    # obstacle right on the route path
    result = validate_tapered_route(route, obstacle_bboxes=[[40, -5, 60, 5]])
    assert result["valid"] is False
    assert len(result["obstacle_hits"]) >= 1


def test_validate_sibling_overlap():
    src = _port("A", center=[0, 0], orientation=0, width=4.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=4.0)
    route1 = route_tapered(src, tgt)
    # route2 is identical — they fully overlap
    result = validate_tapered_route(route1, other_polygons=[route1["polygon_um"]])
    assert result["valid"] is False
    assert result["sibling_overlaps"] >= 1


def test_validate_no_sibling_overlap_when_separated():
    src1 = _port("A", center=[0, 5], orientation=0, width=2.0)
    tgt1 = _port("B", center=[100, 5], orientation=180, width=2.0)
    src2 = _port("C", center=[0, -5], orientation=0, width=2.0)
    tgt2 = _port("D", center=[100, -5], orientation=180, width=2.0)
    route1 = route_tapered(src1, tgt1)
    route2 = route_tapered(src2, tgt2)
    result = validate_tapered_route(route1, other_polygons=[route2["polygon_um"]])
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# strategy functions (direct)
# ---------------------------------------------------------------------------


def test_strategy_uniform_single_bend():
    # one bend: carries the full change
    assert strategy_uniform(0, 1, 5.0, 2.0) == pytest.approx(2.0)


def test_strategy_uniform_two_bends():
    r = (2.0 / 5.0) ** 0.5
    assert strategy_uniform(0, 2, 5.0, 2.0) == pytest.approx(5.0 * r)
    assert strategy_uniform(1, 2, 5.0, 2.0) == pytest.approx(2.0)


def test_strategy_uniform_zero_bends_returns_target():
    assert strategy_uniform(0, 0, 5.0, 2.0) == pytest.approx(2.0)


def test_strategy_front_load_first_bend_aggressive():
    w = strategy_front_load(0, 3, 10.0, 1.0)
    # first bend does 50% of total change: 10→5.5
    assert w == pytest.approx(5.5)


def test_strategy_back_load_last_bend_aggressive():
    w_penultimate = strategy_back_load(2, 4, 10.0, 3.0)
    w_last = strategy_back_load(3, 4, 10.0, 3.0)
    # last bend snaps to target
    assert w_last == pytest.approx(3.0)
    # penultimate is still above target
    assert w_penultimate > 3.0


# ---------------------------------------------------------------------------
# commit_tapered_routes (FakeClient)
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal fake for commit_tapered_routes."""
    def __init__(self):
        self.calls = []

    def layer_ensure(self, layer, datatype, name=""):
        self.calls.append(("layer_ensure", layer, datatype))
        return {"layer_index": 0}

    def shape_delete(self, cell, layers, kinds, limit):
        self.calls.append(("shape_delete", cell, layers))
        return {"deleted": 0}

    def shape_insert_polygon(self, cell, layer, datatype, points_um):
        self.calls.append(("shape_insert_polygon", cell, len(points_um)))
        return {}

    def shape_insert_path(self, cell, layer, datatype, points_um, width_um,
                          begin_ext_um, end_ext_um, round_ends):
        self.calls.append(("shape_insert_path", cell))
        return {}


def test_commit_tapered_routes_inserts_polygons():
    client = _FakeClient()
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=2.0)
    route = route_tapered(src, tgt)
    result = commit_tapered_routes(client, "TEST_CELL", [route])
    assert result["inserted_polygons"] == 1
    assert result["inserted_paths_fallback"] == 0
    assert any("shape_insert_polygon" in str(c) for c in client.calls)


def test_commit_tapered_routes_falls_back_to_path():
    client = _FakeClient()
    # route without polygon_um → fallback to path
    route = {
        "points_um": [[0, 0], [100, 0]],
        "width_um": 4.0,
    }
    result = commit_tapered_routes(client, "TEST_CELL", [route])
    assert result["inserted_polygons"] == 0
    assert result["inserted_paths_fallback"] == 1
