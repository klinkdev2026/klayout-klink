"""Unit tests for the multilayer feature-grid router (F2).

The crossing proof is the heart: a net that CANNOT route on one layer
(another net's wire blocks it) routes by dipping to a second layer via
a declared via — the thing the half-adder needs.
"""

import pytest

from klink.routing.grid.feature_grid_3d import (
    ViaSpec,
    build_multilayer_grid,
    route_net_multilayer,
    shortest_path_3d,
)


VIA = ViaSpec(a="101/0", b="104/0", cell="via12_cell",
              footprint_um=(3.0, 3.0), cost_um=1.0)


def _term(name, x, y):
    return {"name": name, "point_um": (x, y)}


class TestCrossingProof:
    def test_blocked_on_one_layer_routes_via_second(self):
        # net B must go from (0,0) to (40,0) on 104, but a wall obstacle
        # on 104 spans the whole corridor — only a dip to 101 (clear)
        # can cross it.
        layers = ["101/0", "104/0"]
        terms = {"104/0": [_term("B.s", 0.0, 0.0), _term("B.e", 40.0, 0.0)]}
        obstacles = {
            # an impassable 104 wall between the terminals (full height
            # across the feature span so no in-plane detour exists)
            "104/0": [(18.0, -100.0, 22.0, 100.0)],
            "101/0": [],
        }
        out = route_net_multilayer(
            start_layer="104/0", goal_layer="104/0",
            start_um=(0.0, 0.0), goal_um=(40.0, 0.0),
            layers=layers, terminals_by_layer=terms,
            obstacles_by_layer=obstacles, vias=[VIA],
            width_um=5.0, min_spacing_um=2.0)
        assert "problems" not in out, out
        assert len(out["vias"]) == 2          # down to 101 and back up
        assert "101/0" in out["layers_used"]  # used the second plane

    def test_via_required_for_layer_change(self):
        # start on 101, goal on 104: the planes are disconnected without
        # a via -> no_path; with the declared via -> connected, 1 via.
        layers = ["101/0", "104/0"]
        terms = {"101/0": [_term("s", 0.0, 0.0)],
                 "104/0": [_term("g", 40.0, 0.0)]}
        obstacles = {"101/0": [], "104/0": []}
        common = dict(start_layer="101/0", goal_layer="104/0",
                      start_um=(0.0, 0.0), goal_um=(40.0, 0.0),
                      layers=layers, terminals_by_layer=terms,
                      obstacles_by_layer=obstacles,
                      width_um=5.0, min_spacing_um=2.0)
        with_via = route_net_multilayer(vias=[VIA], **common)
        assert "problems" not in with_via, with_via
        assert len(with_via["vias"]) == 1
        assert with_via["layers_used"] == ["101/0", "104/0"]
        no_via = route_net_multilayer(vias=[], **common)
        assert "problems" in no_via
        assert no_via["problems"][0]["type"] == "no_path"

    def test_unblocked_stays_on_one_layer_no_via(self):
        layers = ["101/0", "104/0"]
        terms = {"104/0": [_term("s", 0.0, 0.0), _term("e", 30.0, 0.0)]}
        obstacles = {"104/0": [], "101/0": []}
        out = route_net_multilayer(
            start_layer="104/0", goal_layer="104/0",
            start_um=(0.0, 0.0), goal_um=(30.0, 0.0),
            layers=layers, terminals_by_layer=terms,
            obstacles_by_layer=obstacles, vias=[VIA],
            width_um=5.0, min_spacing_um=2.0)
        assert out["vias"] == []              # a via is never free; unused
        assert out["layers_used"] == ["104/0"]
        assert out["length_um"] == 30.0


class TestViaCandidateGating:
    def test_large_via_landing_clipped_by_obstacle_is_rejected(self):
        # the landing-clear gate bites when the via footprint is wider
        # than the routing clearance margin (a small via's footprint is
        # already covered by node exclusion — a real design fact). A
        # 12um via near a 101 obstacle: the node survives as a point but
        # its footprint overlaps -> rejected naming the layer.
        big_via = ViaSpec(a="101/0", b="104/0", cell="big_via",
                          footprint_um=(12.0, 12.0), cost_um=1.0)
        layers = ["101/0", "104/0"]
        terms = {"101/0": [_term("s", 0.0, 0.0), _term("x", 40.0, 0.0)],
                 "104/0": [_term("g", 40.0, 0.0), _term("y", 0.0, 0.0)]}
        # obstacle ~5um from the (40,0) node: outside margin (4.5, node
        # survives) but inside the 6um footprint half-extent
        obstacles = {"101/0": [(45.0, -2.0, 49.0, 2.0)], "104/0": []}
        grid = build_multilayer_grid(
            layers=layers, terminals_by_layer=terms,
            obstacles_by_layer=obstacles, vias=[big_via],
            width_um=5.0, min_spacing_um=2.0)
        assert any("not clear on layer 101/0" in r[4]
                   for r in grid.rejected_vias), grid.rejected_vias

    def test_foreign_terminal_swallow_rejected(self):
        layers = ["101/0", "104/0"]
        terms = {"104/0": [_term("s", 0.0, 0.0), _term("e", 40.0, 0.0)]}
        obstacles = {"104/0": [], "101/0": []}
        # a foreign terminal exactly where a via would land
        grid = build_multilayer_grid(
            layers=layers, terminals_by_layer=terms,
            obstacles_by_layer=obstacles, vias=[VIA],
            width_um=5.0, min_spacing_um=2.0,
            foreign_terminals_nm=[(0, 0)])
        assert any("swallow a foreign terminal" in r[4]
                   for r in grid.rejected_vias)


class TestDeterminism:
    def test_twice_in_one_process_identical(self):
        layers = ["101/0", "104/0"]
        terms = {"104/0": [_term("s", 0.0, 0.0), _term("e", 40.0, 0.0)]}
        obstacles = {"104/0": [(18.0, -100.0, 22.0, 100.0)], "101/0": []}
        kw = dict(start_layer="104/0", goal_layer="104/0",
                  start_um=(0.0, 0.0), goal_um=(40.0, 0.0), layers=layers,
                  terminals_by_layer=terms, obstacles_by_layer=obstacles,
                  vias=[VIA], width_um=5.0, min_spacing_um=2.0)
        assert route_net_multilayer(**kw) == route_net_multilayer(**kw)
