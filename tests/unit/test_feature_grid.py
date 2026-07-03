from klink.routing.grid.feature_grid import (
    FeatureGridError,
    build_feature_grid,
    route_net,
    shortest_path,
)


def _term(name, x, y):
    return {"name": name, "point_um": (x, y)}


def test_gold1_obstacle_routes_only_with_spacing_escape_lines(capsys):
    terminals = [_term("A", 0.0, 0.0), _term("B", 10.0, 0.0)]
    obstacles = [(4.0, -1.0, 6.0, 1.0)]
    width = 1.0
    spacing = 1.0

    no_escape = build_feature_grid(
        terminals,
        obstacles,
        width_um=width,
        min_spacing_um=spacing,
        include_escape_lines=False,
    )
    assert shortest_path(no_escape, (0, 0), (10000, 0)) is None

    result = route_net(terminals, obstacles, width_um=width, min_spacing_um=spacing)
    assert "problems" not in result
    assert result["points_um"][0] == (0.0, 0.0)
    assert result["points_um"][-1] == (10.0, 0.0)
    assert result["segment_count"] == 5
    assert result["length_um"] == 15.0
    assert any(y in {-2.5, 2.5} for _, y in result["points_um"])
    assert all(point not in {(4.0, 0.0), (6.0, 0.0)} for point in result["points_um"])

    print(f"PASS feature_grid gold1 segments={result['segment_count']} length={result['length_um']:.1f}um")
    captured = capsys.readouterr()
    assert "PASS feature_grid gold1 segments=5 length=15.0um" in captured.out


def test_gold2_unblocked_13um_route_is_one_straight_segment(capsys):
    terminals = [_term("OUT", 0.0, 0.0), _term("MID", 13.0, 0.0)]
    result = route_net(terminals, [], width_um=1.0, min_spacing_um=0.0)
    assert result == {
        "points_um": [(0.0, 0.0), (13.0, 0.0)],
        "length_um": 13.0,
        "segment_count": 1,
    }

    print(f"PASS feature_grid gold2 segments={result['segment_count']} length={result['length_um']:.1f}um")
    captured = capsys.readouterr()
    assert "PASS feature_grid gold2 segments=1 length=13.0um" in captured.out


def test_p2_malformed_inputs_raise_instructive_errors():
    try:
        route_net([_term("A", 0.0, 0.0)], [], width_um=1.0, min_spacing_um=0.0)
    except FeatureGridError as exc:
        assert "at least two terminals" in str(exc)
    else:
        raise AssertionError("one-terminal route did not fail")

    try:
        route_net([_term("A", 0.0, 0.0), _term("B", 1.0, 0.0)], [(2.0, 2.0, 1.0, 3.0)], width_um=1.0, min_spacing_um=0.0)
    except FeatureGridError as exc:
        assert "x1<x2 and y1<y2" in str(exc)
    else:
        raise AssertionError("bad bbox did not fail")

    try:
        route_net([{"name": "A", "point_um": ("x", 0.0)}, _term("B", 1.0, 0.0)], [], width_um=1.0, min_spacing_um=0.0)
    except FeatureGridError as exc:
        assert "point_um[0]" in str(exc)
    else:
        raise AssertionError("bad coordinate did not fail")


def test_p8_same_input_produces_byte_identical_result():
    terminals = [_term("A", 0.0, 0.0), _term("B", 10.0, 0.0)]
    obstacles = [(4.0, -1.0, 6.0, 1.0)]
    first = route_net(terminals, obstacles, width_um=1.0, min_spacing_um=1.0)
    second = route_net(terminals, obstacles, width_um=1.0, min_spacing_um=1.0)
    assert first == second

    graph_a = build_feature_grid(terminals, obstacles, width_um=1.0, min_spacing_um=1.0)
    graph_b = build_feature_grid(terminals, obstacles, width_um=1.0, min_spacing_um=1.0)
    assert graph_a.nodes == graph_b.nodes
    assert graph_a.edges == graph_b.edges
    assert dict(graph_a.adjacency) == dict(graph_b.adjacency)


def test_p10_node_order_edges_and_nm_quantization_are_deterministic():
    terminals = [_term("A", 0.0014, 0.0), _term("B", 3.0014, 0.0)]
    obstacles = [(1.0004, -0.5, 2.0004, 0.5)]
    graph = build_feature_grid(terminals, obstacles, width_um=1.0, min_spacing_um=0.25)
    assert graph.nodes == tuple(sorted(graph.nodes, key=lambda item: (item[1], item[0])))
    assert graph.edges == tuple(sorted(graph.edges, key=lambda item: (item[0], item[1], item[2])))
    assert (1, 0) in graph.nodes
    assert (3001, 0) in graph.nodes
    assert any(node[1] == -1250 for node in graph.nodes)
    assert all(isinstance(coord, int) for node in graph.nodes for coord in node)


def test_multi_terminal_route_documents_split_before_f1():
    result = route_net(
        [_term("A", 0.0, 0.0), _term("B", 1.0, 0.0), _term("C", 2.0, 0.0)],
        [],
        width_um=1.0,
        min_spacing_um=0.0,
    )
    assert result["problems"][0]["type"] == "unsupported_terminal_count"
    assert "split multi-terminal nets first" in result["problems"][0]["message"]
