"""Unit tests for klink.routing.backends.geometric.tapered_segments — discrete per-segment path taper."""

from __future__ import annotations

import pytest

from klink.routing.backends.geometric.tapered_segments import (
    _pair_ports_by_net_tokens,
    _unsupported_multi_port_net_errors,
    _miter_corner,
    commit_tapered_hybrid_many,
    commit_tapered_segments,
    compute_segment_widths,
    route_tapered_hybrid,
    route_tapered_hybrid_many,
    route_tapered_segments,
)


def _port(name="A", net="sig", center=None, orientation=0.0, width=4.0, port_type="electrical"):
    center = center or [0.0, 0.0]
    return {
        "name": name, "net": net,
        "center_um": [float(center[0]), float(center[1])],
        "orientation": float(orientation), "width_um": float(width),
        "port_type": port_type, "target_layer": "10/0",
    }


def _assert_points_close(actual, expected):
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected):
        assert a == pytest.approx(e)


def _expanded_contains(point, bbox, margin):
    return (
        bbox[0] - margin <= point[0] <= bbox[2] + margin
        and bbox[1] - margin <= point[1] <= bbox[3] + margin
    )


# ---------------------------------------------------------------------------
# compute_segment_widths
# ---------------------------------------------------------------------------


def test_segment_widths_straight_two_points():
    points = [[0, 0], [100, 0]]
    widths = compute_segment_widths(points, 5.0, 2.0)
    assert len(widths) == 1
    assert widths[0] == 5.0


def test_segment_widths_one_bend():
    points = [[0, 0], [50, 0], [50, 50]]
    widths = compute_segment_widths(points, 5.0, 2.0, strategy="uniform")
    assert len(widths) == 2
    assert widths[0] == 5.0       # segment before bend: full source width
    assert widths[1] == 2.0       # segment after bend: target width


def test_segment_widths_two_bends():
    points = [[0, 0], [50, 0], [50, 50], [100, 50]]
    widths = compute_segment_widths(points, 5.0, 2.0, strategy="uniform")
    r = (2.0 / 5.0) ** 0.5
    assert len(widths) == 3
    assert widths[0] == pytest.approx(5.0)
    assert widths[1] == pytest.approx(5.0 * r)
    assert widths[2] == pytest.approx(2.0)


def test_segment_widths_same_width_all_equal():
    points = [[0, 0], [50, 0], [50, 50], [100, 50]]
    widths = compute_segment_widths(points, 4.0, 4.0)
    assert widths == [4.0, 4.0, 4.0]


def test_segment_widths_custom_strategy():
    points = [[0, 0], [50, 0], [50, 50], [100, 50]]

    def always_target(bend_idx, num_bends, sw, tw):
        return tw

    widths = compute_segment_widths(points, 10.0, 2.0, strategy=always_target)
    assert widths[0] == 10.0
    assert widths[1] == 2.0
    assert widths[2] == 2.0


# ---------------------------------------------------------------------------
# route_tapered_segments
# ---------------------------------------------------------------------------


def test_route_straight_same_width():
    src = _port("A", center=[0, 0], orientation=0, width=4.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=4.0)
    route = route_tapered_segments(src, tgt)
    assert route["backend"] == "tapered_segments"
    assert route["num_bends"] == 0
    assert route["per_bend_ratios"] == []
    segs = route["segments"]
    assert len(segs) >= 1
    assert segs[0]["width_um"] == 4.0


def test_route_straight_different_widths():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt)
    # Direct no-bend connection → linear interpolation across segments
    assert route["num_bends"] == 0
    widths = [s["width_um"] for s in route["segments"]]
    assert widths[0] == 5.0
    assert widths[-1] == pytest.approx(2.0, abs=0.5)


def test_route_with_waypoint():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 40], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt, [[60, 40]], strategy="uniform")
    assert route["num_bends"] >= 1
    widths = [s["width_um"] for s in route["segments"]]
    # first segment wide, last narrow
    assert widths[0] > widths[-1]


def test_route_segments_have_two_points_each():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 40], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt, [[60, 40]])
    for seg in route["segments"]:
        assert len(seg["points_um"]) == 2


def test_route_segments_chain_is_continuous():
    """Last point of segment k equals first point of segment k+1."""
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 40], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt, [[60, 40]])
    segs = route["segments"]
    for k in range(len(segs) - 1):
        assert segs[k]["points_um"][1] == segs[k + 1]["points_um"][0]


def test_route_includes_port_launch_stubs():
    src = _port("A", center=[0, 0], orientation=0, width=4.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=4.0)
    route = route_tapered_segments(src, tgt)
    pts = route["points_um"]
    assert pts[0] == [0.0, 0.0]
    assert pts[-1] == [100.0, 0.0]
    assert route["source_launch_um"][0] > 0.0
    assert route["target_launch_um"][0] < 100.0


def test_route_strategy_passthrough():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt, strategy="front_load")
    assert route["strategy"] == "front_load"


def test_route_custom_strategy_labeled():
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 0], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt, strategy=lambda bi, nb, sw, tw: tw)
    assert route["strategy"] == "custom"


def test_route_metadata():
    src = _port("A", center=[0, 0], orientation=0, width=10.0)
    tgt = _port("B", center=[100, 50], orientation=180, width=1.0)
    route = route_tapered_segments(src, tgt, [[60, 20]], strategy="uniform")
    assert route["source_width_um"] == 10.0
    assert route["target_width_um"] == 1.0
    assert route["width_um"] == 1.0  # min = narrow for pathfinding
    assert len(route["segment_widths_um"]) == len(route["segments"])
    assert len(route["per_bend_ratios"]) == route["num_bends"]


# ---------------------------------------------------------------------------
# route_tapered_hybrid corner geometry
# ---------------------------------------------------------------------------


def test_hybrid_miter_corner_uses_inner_edge_and_full_width_patch():
    points = [[0, 0], [10, 0], [10, 10]]
    poly, cut_in, cut_out = _miter_corner(points, [10.0, 10.0], 1)

    assert cut_in == pytest.approx(5.0)
    assert cut_out == pytest.approx(5.0)
    _assert_points_close(poly, [
        [5.0, -5.0],
        [15.0, -5.0],
        [15.0, 5.0],
        [5.0, 5.0],
    ])


def test_hybrid_miter_corner_uses_outer_miter_when_wide_side_must_cut_back():
    src = _port("A", center=[80, 40], orientation=90, width=6.0)
    tgt = _port("B", center=[150, 72], orientation=180, width=3.0)
    route = route_tapered_segments(src, tgt, [[80, 54]], strategy="uniform")

    poly, cut_in, cut_out = _miter_corner(
        route["points_um"], route["segment_widths_um"], route["bend_indices"][1]
    )

    assert cut_in == pytest.approx(2.002215619791609)
    assert cut_out == pytest.approx(2.501772849028754)
    assert len(poly) == 4
    _assert_points_close(poly, [
        [142.64690335146233, 69.41581795380944],
        [146.50177284902875, 70.5],
        [146.50177284902875, 73.5],
        [141.49822715097125, 73.5],
    ])


def test_hybrid_route_cuts_both_sides_of_left_turn():
    src = _port("A", center=[-10, 0], orientation=0, width=10.0)
    tgt = _port("B", center=[10, 20], orientation=270, width=10.0)

    route = route_tapered_hybrid(
        src, tgt, [[10, 0]], launch_length_um=10.0, strategy="uniform"
    )
    path_group = next(g for g in route["groups"] if g["kind"] == "path")
    segments = path_group["segments"]

    _assert_points_close(segments[1]["points_um"], [[0.0, 0.0], [5.0, 0.0]])
    _assert_points_close(segments[2]["points_um"], [[10.0, 5.0], [10.0, 10.0]])


def test_hybrid_many_assigns_corridor_lanes_without_example_side_logic():
    pairs = []
    for idx, (sy, dy) in enumerate([(10, 0), (24, 14), (38, 42), (52, 56)]):
        pairs.append({
            "net": f"sig{idx}",
            "source": _port(f"IN{idx}", f"sig{idx}", [14, sy], 0, width=3.0),
            "target": _port(f"PAD{idx}", f"sig{idx}", [110, dy], 180, width=8.0),
            "route_layer": "12/0",
        })
    anchors = [
        {
            "id": "LO",
            "kind": "corridor",
            "net": "sig0,sig1",
            "center_um": [45, 17],
            "width_um": 24,
            "path_points": "-15,-1;10,0;15,1",
        },
        {
            "id": "HI",
            "kind": "corridor",
            "net": "sig2,sig3",
            "center_um": [75, 49.5],
            "width_um": 24,
            "path_points": "-9,-0.5;6,0.5;9,-1",
        },
    ]

    result = route_tapered_hybrid_many(pairs, anchors=anchors, spacing_um=4.0)

    assert result["ok"] is False
    assert result["sibling_overlaps"]
    assert result["route_count"] == 4
    assert [r["corridor_id"] for r in result["routes"]] == ["LO", "LO", "HI", "HI"]
    assert result["lane_reports"][0]["pitch_um"] == 16.0
    assert result["routes"][0]["lane_offset_um"] < result["routes"][1]["lane_offset_um"]
    assert all(r["backend"] == "tapered_hybrid" for r in result["routes"])


def test_hybrid_many_routes_capacity_corridor_without_geometry_overlap():
    pairs = []
    for idx, y in enumerate([-45.0, -27.0, -9.0], start=1):
        pairs.append({
            "net": f"n{idx}",
            "source": _port(f"L{idx}", f"n{idx}", [0, y], 0, width=5.0),
            "target": _port(f"R{idx}", f"n{idx}", [180, y], 180, width=5.0),
            "route_layer": "12/0",
        })
    anchors = [{
        "id": "LOWER",
        "kind": "corridor",
        "net": "n1,n2,n3",
        "center_um": [90, -36],
        "width_um": 44,
        "path_points": "-55,0;55,0",
    }]

    result = route_tapered_hybrid_many(pairs, anchors=anchors, spacing_um=8.0, angle_mode="manhattan")

    assert result["ok"] is True
    assert result["sibling_overlaps"] == []
    assert result["lane_reports"][0]["pitch_um"] == pytest.approx(15.5)


def test_hybrid_many_reports_sibling_overlap_without_corridor_lane_split():
    pairs = [
        {
            "net": "a",
            "source": _port("A0", "a", [0, 0], 0, width=8.0),
            "target": _port("A1", "a", [100, 0], 180, width=8.0),
        },
        {
            "net": "b",
            "source": _port("B0", "b", [0, 2], 0, width=8.0),
            "target": _port("B1", "b", [100, 2], 180, width=8.0),
        },
    ]

    result = route_tapered_hybrid_many(pairs)

    assert result["ok"] is False
    assert result["sibling_overlaps"]


def test_hybrid_many_infers_bus_corridor_without_example_layout_logic():
    pairs = []
    for idx, y in enumerate([-300.0, -100.0, 100.0, 300.0], start=1):
        pairs.append({
            "net": f"n{idx}",
            "source": _port(f"L{idx}", f"n{idx}", [-1000, y], 0, width=30.0),
            "target": _port(f"R{idx}", f"n{idx}", [0, y / 10.0], 180, width=5.0),
            "route_layer": "1/0",
        })

    result = route_tapered_hybrid_many(pairs, anchors=[], spacing_um=20.0)

    assert result["ok"] is False
    assert result["sibling_overlaps"]
    assert [r["corridor_id"] for r in result["lane_reports"]] == ["AUTO_BUS_0", "AUTO_BUS_1"]
    assert result["lane_reports"][0]["pitch_um"] == 65.0
    assert result["routes"][0]["corridor_id"] == "AUTO_BUS_0"
    assert result["routes"][-1]["corridor_id"] == "AUTO_BUS_1"


def test_cell_pairing_understands_multi_net_ports_and_layer_inference():
    ports = [
        _port("L1", "n1", [-100, -10], 0, width=30.0) | {"target_layer": "1/0"},
        _port("L2", "n2", [-100, 10], 0, width=30.0) | {"target_layer": "1/0"},
        _port("R", "n1,n2", [0, 0], 180, width=5.0) | {"target_layer": "3/0"},
    ]

    pairs = _pair_ports_by_net_tokens(ports)

    assert [p["net"] for p in pairs] == ["n1", "n2"]
    assert [p["route_layer"] for p in pairs] == ["1/0", "3/0"]


def test_cell_pairing_reports_unsupported_multi_port_net_before_writeback():
    ports = [
        _port("ROOT", "bus", [0, 0], 0),
        _port("SINK0", "bus", [100, -20], 180),
        _port("SINK1", "bus", [100, 0], 180),
        _port("SINK2", "bus", [100, 20], 180),
    ]

    errors = _unsupported_multi_port_net_errors(ports)

    assert errors == [{
        "type": "unsupported_multi_port_net",
        "net": "bus",
        "port_count": 4,
        "ports": ["ROOT", "SINK0", "SINK1", "SINK2"],
        "message": "unsupported multi-port net bus: 4 ports; bus/Steiner routing is not implemented yet",
    }]


def test_cell_pairing_assigns_single_net_demands_to_candidate_sinks_by_distance():
    ports = [
        _port("IN0", "sig0", [14, 10], 0, width=3.0),
        _port("IN1", "sig1", [14, 24], 0, width=3.0),
        _port("PAD0", "", [110, 0], 180, width=8.0, port_type="candidate_sink"),
        _port("PAD1", "", [110, 14], 180, width=8.0, port_type="candidate_sink"),
        _port("PAD2", "", [110, 80], 180, width=8.0, port_type="candidate_sink"),
    ]

    pairs = _pair_ports_by_net_tokens(ports)

    assert [(p["net"], p["source"]["name"], p["target"]["name"], p["assignment"]) for p in pairs] == [
        ("sig0", "IN0", "PAD0", "candidate_sink_nearest"),
        ("sig1", "IN1", "PAD1", "candidate_sink_nearest"),
    ]


def test_cell_pairing_uses_ordered_loop_for_perimeter_candidate_sinks():
    ports = [
        _port("NORTH", "l0", [0, 45], 90),
        _port("EAST", "l1", [45, 0], 0),
        _port("SOUTH", "l2", [0, -45], 270),
        _port("WEST", "l3", [-45, 0], 180),
        _port("PAD_NW", "", [-100, 90], 180, port_type="candidate_sink"),
        _port("PAD_NE", "", [100, 90], 0, port_type="candidate_sink"),
        _port("PAD_SE", "", [100, -90], 0, port_type="candidate_sink"),
        _port("PAD_SW", "", [-100, -90], 180, port_type="candidate_sink"),
        _port("PAD_N", "", [0, 115], 90, port_type="candidate_sink"),
        _port("PAD_S", "", [0, -115], 270, port_type="candidate_sink"),
    ]

    pairs = _pair_ports_by_net_tokens(ports)
    result = route_tapered_hybrid_many(pairs, anchors=[], spacing_um=8.0)

    assert {p["assignment"] for p in pairs} == {"candidate_sink_ordered_loop"}
    assert result["lane_reports"] == []


def test_target_launch_orientation_is_preserved_while_breaking_terminal_hairpin():
    ports = [
        _port("NORTH", "l0", [0, 45], 90),
        _port("PAD_N", "", [0, 115], 90, port_type="candidate_sink"),
    ]

    pairs = _pair_ports_by_net_tokens(ports)
    result = route_tapered_hybrid_many(pairs, anchors=[], spacing_um=8.0)
    points = result["routes"][0]["points_um"]

    assert pairs[0]["target"]["orientation"] == 90.0
    assert points[-2] == [pytest.approx(0.0), 123.0]
    assert points[-1] == [0.0, 115.0]
    assert points[-3][1] == pytest.approx(123.0)
    assert points[-3][0] != pytest.approx(0.0)


def test_hybrid_many_can_force_manhattan_segments():
    pairs = [{
        "net": "m",
        "source": _port("A", "m", [0, 0], 0, width=4.0),
        "target": _port("B", "m", [40, 20], 180, width=4.0),
    }]

    result = route_tapered_hybrid_many(pairs, angle_mode="manhattan")

    assert result["ok"] is True
    assert result["angle_mode"] == "manhattan"
    for a, b in zip(result["routes"][0]["points_um"], result["routes"][0]["points_um"][1:]):
        assert a[0] == pytest.approx(b[0]) or a[1] == pytest.approx(b[1])


def test_hybrid_many_default_allows_arbitrary_straight_segments():
    pairs = [{
        "net": "m",
        "source": _port("A", "m", [0, 0], 0, width=4.0),
        "target": _port("B", "m", [40, 20], 180, width=4.0),
    }]

    result = route_tapered_hybrid_many(pairs)

    assert result["ok"] is True
    assert result["angle_mode"] == "any"
    assert any(
        a[0] != pytest.approx(b[0]) and a[1] != pytest.approx(b[1])
        for a, b in zip(result["routes"][0]["points_um"], result["routes"][0]["points_um"][1:])
    )


def test_hybrid_many_ignores_unrelated_corridor_when_deciding_auto_bus():
    pairs = []
    for idx, y in enumerate([-300.0, -100.0, 100.0, 300.0], start=1):
        pairs.append({
            "net": f"n{idx}",
            "source": _port(f"L{idx}", f"n{idx}", [-1000, y], 0, width=30.0),
            "target": _port(f"R{idx}", f"n{idx}", [0, y / 10.0], 180, width=5.0),
            "route_layer": "1/0",
        })
    anchors = [{
        "id": "HIGH_ONLY",
        "kind": "corridor",
        "net": "n19,n20",
        "center_um": [-500, 0],
        "width_um": 20,
        "path_points": "0,-100;0,100",
    }]

    result = route_tapered_hybrid_many(pairs, anchors=anchors, spacing_um=20.0)

    assert result["ok"] is False
    assert result["sibling_overlaps"]
    assert result["lane_reports"][0]["corridor_id"].startswith("AUTO_BUS")


def test_hybrid_many_rejects_explicit_corridor_when_lanes_do_not_fit():
    pairs = []
    for idx, y in enumerate([0.0, 200.0, 400.0], start=1):
        pairs.append({
            "net": f"n{idx}",
            "source": _port(f"L{idx}", f"n{idx}", [-1000, y], 0, width=30.0),
            "target": _port(f"R{idx}", f"n{idx}", [0, y], 180, width=5.0),
            "route_layer": "3/0",
        })
    anchors = [{
        "id": "NARROW",
        "kind": "corridor",
        "net": "n1,n2,n3",
        "center_um": [-500, 200],
        "width_um": 80.0,
        "path_points": "0,-300;0,300",
    }]

    result = route_tapered_hybrid_many(pairs, anchors=anchors, spacing_um=20.0)

    assert result["ok"] is False
    assert result["errors"] == ["corridor capacity exceeded"]
    assert result["planning_errors"][0]["corridor_id"] == "NARROW"
    assert result["planning_errors"][0]["required_width_um"] == pytest.approx(160.0)
    assert result["routes"] == []


def test_hybrid_many_routes_bend_anchor_around_obstacle_bbox():
    pairs = [{
        "net": "o",
        "source": _port("A", "o", [18, 5], 0, width=5.0),
        "target": _port("B", "o", [120, 5], 180, width=2.0),
        "route_layer": "12/0",
    }]
    anchors = [{
        "id": "BEND",
        "kind": "bend_region",
        "net": "o",
        "center_um": [69, 42],
        "radius_um": 21,
    }]
    obstacles = [[52, -18, 86, 28]]

    result = route_tapered_hybrid_many(pairs, anchors=anchors, obstacle_bboxes=obstacles)

    assert result["ok"] is True
    assert result["obstacle_hits"] == []
    route = result["routes"][0]
    assert [69.0, 42.0] in route["points_um"]
    assert any(p[1] > 28.0 for p in route["points_um"])


def test_hybrid_many_slides_corridor_gate_off_expanded_obstacle():
    pairs = [
        {
            "net": "B",
            "source": _port("P1", "B", [-20, 85], 180, width=5.0),
            "target": _port("P2", "B", [280, 35], 180, width=5.0),
            "route_layer": "104/0",
        },
        {
            "net": "C",
            "source": _port("Q1", "C", [-20, 75], 180, width=5.0),
            "target": _port("Q2", "C", [280, 55], 180, width=5.0),
            "route_layer": "104/0",
        },
    ]
    anchors = [{
        "id": "COR_HA",
        "kind": "corridor",
        "net": "B,C",
        "center_um": [75, 60],
        "path_points": "0,-30;0,30",
    }]
    obstacles = [[72.5, 37.5, 117.5, 42.5]]

    first = route_tapered_hybrid_many(
        pairs,
        anchors=anchors,
        obstacle_bboxes=obstacles,
        angle_mode="manhattan",
        validate_sibling_overlap=False,
    )
    second = route_tapered_hybrid_many(
        pairs,
        anchors=anchors,
        obstacle_bboxes=obstacles,
        angle_mode="manhattan",
        validate_sibling_overlap=False,
    )

    assert first == second
    assert first["ok"] is True
    assert first["obstacle_hits"] == []
    route_b = next(route for route in first["routes"] if route["net"] == "B")
    slid_gate = [80.75, 32.5]
    assert slid_gate in route_b["points_um"]
    assert 30.0 <= slid_gate[1] <= 90.0
    assert not _expanded_contains(slid_gate, obstacles[0], margin=2.5)


def test_hybrid_many_fully_blocked_corridor_gate_error_names_context():
    pairs = [{
        "net": "B",
        "source": _port("P1", "B", [-20, 85], 180, width=5.0),
        "target": _port("P2", "B", [280, 35], 180, width=5.0),
        "route_layer": "104/0",
    }]
    anchors = [{
        "id": "BLOCKED_COR",
        "kind": "corridor",
        "net": "B",
        "center_um": [75, 60],
        "path_points": "0,-30;0,30",
    }]
    obstacles = [[72.5, 27.5, 77.5, 92.5]]

    with pytest.raises(ValueError, match="corridor_id='BLOCKED_COR'.*searched_extent=y\\[30.000,90.000\\].*72.5"):
        route_tapered_hybrid_many(
            pairs,
            anchors=anchors,
            obstacle_bboxes=obstacles,
            angle_mode="manhattan",
        )


def test_hybrid_many_bend_anchor_forces_actual_turn_inside_region():
    pairs = [{
        "net": "b",
        "source": _port("A", "b", [0, 0], 0, width=4.0),
        "target": _port("B", "b", [100, 0], 180, width=4.0),
        "route_layer": "12/0",
    }]
    anchors = [{
        "id": "BEND",
        "kind": "bend_region",
        "net": "b",
        "center_um": [50, 30],
        "radius_um": 10,
    }]

    result = route_tapered_hybrid_many(pairs, anchors=anchors)

    assert result["ok"] is True
    route = result["routes"][0]
    center_index = route["points_um"].index([50.0, 30.0])
    prev_pt = route["points_um"][center_index - 1]
    center = route["points_um"][center_index]
    next_pt = route["points_um"][center_index + 1]
    v1 = [center[0] - prev_pt[0], center[1] - prev_pt[1]]
    v2 = [next_pt[0] - center[0], next_pt[1] - center[1]]
    cross = v1[0] * v2[1] - v1[1] * v2[0]
    assert abs(cross) > 0.0


def test_hybrid_many_obstacle_search_does_not_backtrack_behind_required_point():
    pairs = [{
        "net": "m",
        "source": _port("SRC", "m", [0, 0], 90, width=4.0),
        "target": _port("DST", "m", [80, 40], 180, width=4.0),
        "route_layer": "12/0",
    }]
    anchors = [{
        "id": "PASS",
        "kind": "waypoint_region",
        "net": "m",
        "center_um": [0, 20],
    }]
    obstacles = [[20, 10, 42, 35]]

    result = route_tapered_hybrid_many(pairs, anchors=anchors, obstacle_bboxes=obstacles)

    assert result["ok"] is True
    route = result["routes"][0]
    waypoint_idx = route["points_um"].index([0.0, 20.0])
    after_waypoint = route["points_um"][waypoint_idx + 1:]
    assert after_waypoint
    assert min(p[1] for p in after_waypoint) >= 20.0


# ---------------------------------------------------------------------------
# commit_tapered_segments (FakeClient)
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self):
        self.calls = []

    def layer_ensure(self, layer, datatype, name=""):
        self.calls.append(("layer_ensure", layer, datatype))
        return {"layer_index": 0}

    def shape_delete(self, cell, layers, kinds, limit):
        self.calls.append(("shape_delete", cell))
        return {"deleted": 0}

    def shape_insert_path(self, cell, layer, datatype, points_um, width_um,
                          begin_ext_um, end_ext_um, round_ends):
        self.calls.append(("shape_insert_path", cell, width_um))
        return {}


def test_commit_inserts_one_path_per_segment():
    client = _FakeClient()
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 40], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt, [[60, 40]])
    n_segs = len(route["segments"])
    result = commit_tapered_segments(client, "C", route)
    assert result["inserted_segments"] == n_segs
    path_calls = [c for c in client.calls if c[0] == "shape_insert_path"]
    assert len(path_calls) == n_segs


def test_commit_hybrid_many_batches_paths_and_patches():
    calls = []

    class BatchClient:
        def layer_ensure(self, layer, datatype, name=""):
            calls.append(("layer_ensure", layer, datatype, name))
            return {"layer_index": 7}

        def shape_delete(self, cell, layers, kinds, limit):
            calls.append(("shape_delete", cell, tuple(layers), tuple(kinds), limit))
            return {"deleted": 3}

        def shape_insert_many(self, cell, items, *, dry_run=False):
            calls.append(("shape_insert_many", cell, items, dry_run))
            return {"inserted": len(items)}

        def shape_insert_path(self, *args, **kwargs):
            raise AssertionError("hybrid many writeback must not use per-path RPC")

        def shape_insert_polygon(self, *args, **kwargs):
            raise AssertionError("hybrid many writeback must not use per-polygon RPC")

    planned = {
        "routes": [{
            "net": "n0",
            "route_id": "r0",
            "route_layer": "12/0",
            "groups": [{
                "kind": "path",
                "segments": [
                    {
                        "points_um": [[0.0, 0.0], [10.0, 0.0]],
                        "width_um": 4.0,
                        "is_first": True,
                    },
                    {
                        "points_um": [[10.0, 0.0], [10.0, 8.0]],
                        "width_um": 2.0,
                        "is_last": True,
                    },
                ],
                "corner_patches": [{
                    "bend_index": 1,
                    "polygon_um": [[9.0, -1.0], [11.0, -1.0], [11.0, 1.0], [9.0, 1.0]],
                }],
            }],
            "boundary_patches": [{
                "bend_index": 2,
                "polygon_um": [[9.0, 7.0], [11.0, 7.0], [11.0, 9.0], [9.0, 9.0]],
            }],
        }]
    }

    result = commit_tapered_hybrid_many(BatchClient(), "C", planned, clear=True)

    assert result["writeback"] == "batch"
    assert result["inserted"] == 4
    assert result["paths"] == 2
    assert result["patches"] == 2
    assert result["deleted"] == 3
    insert_calls = [c for c in calls if c[0] == "shape_insert_many"]
    assert len(insert_calls) == 1
    items = insert_calls[0][2]
    assert [item["kind"] for item in items] == ["path", "path", "polygon", "polygon"]
    assert items[0]["begin_ext_um"] == pytest.approx(2.0)
    assert items[0]["end_ext_um"] == pytest.approx(0.0)
    assert items[1]["begin_ext_um"] == pytest.approx(0.0)
    assert items[1]["end_ext_um"] == pytest.approx(1.0)


def test_commit_segments_have_different_widths():
    client = _FakeClient()
    src = _port("A", center=[0, 0], orientation=0, width=5.0)
    tgt = _port("B", center=[100, 40], orientation=180, width=2.0)
    route = route_tapered_segments(src, tgt, [[60, 40]], strategy="uniform")
    commit_tapered_segments(client, "C", route)
    path_calls = [c for c in client.calls if c[0] == "shape_insert_path"]
    widths = [c[2] for c in path_calls]
    # widths should decrease (or at least not all equal if bends > 0)
    if route["num_bends"] > 0:
        assert widths[0] != widths[-1]


def test_commit_empty_segments():
    client = _FakeClient()
    route = {"segments": []}
    result = commit_tapered_segments(client, "C", route)
    assert result["inserted_segments"] == 0
