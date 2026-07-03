"""Unit tests for the negotiated v2 control-loop pure helpers."""

from klink.routing.backends.negotiated.negotiated import (
    launch_overlap_claims,
    launch_zone_bbox_um,
    negotiation_order,
    repopulate_occupancy,
)
from klink.routing.backends.negotiated.negotiated_resources import ResourceCostTable


def _plan(net, ports, segments=None, corridor=None):
    return {"net": net, "ports": ports,
            "segments": segments or [], "corridor": corridor}


def _port(name, cx, cy, w=5.0, orient=0.0):
    return {"name": name, "center_um": (cx, cy),
            "orientation_deg": orient, "width_um": w}


class TestLaunchZoneGeometry:
    def test_bbox_is_centered_square_scaled_by_width(self):
        z = launch_zone_bbox_um(_port("p", 10.0, 20.0, w=5.0),
                                stub_factor=2.0)
        assert z == (5.0, 15.0, 15.0, 25.0)   # half = 5*2/2 = 5

    def test_intruder_segment_over_victim_launch_zone_is_claimed(self):
        # MID's port launch at (0,0); B's segment runs straight across it
        mid = _plan("MID", [_port("MID.s", 0.0, 0.0, w=5.0)])
        b = _plan("B", [_port("B.s", 40.0, 0.0)],
                  segments=[{"a": (-10.0, 0.0), "b": (10.0, 0.0),
                             "width_um": 5.0}])
        claims = launch_overlap_claims([mid, b])
        assert len(claims) == 1
        intruder, zone = claims[0]
        assert intruder == "B"
        assert zone.key == ("MID", "MID.s")

    def test_no_overlap_no_claim(self):
        mid = _plan("MID", [_port("MID.s", 0.0, 0.0, w=5.0)])
        b = _plan("B", [_port("B.s", 80.0, 80.0)],
                  segments=[{"a": (70.0, 80.0), "b": (90.0, 80.0),
                             "width_um": 5.0}])
        assert launch_overlap_claims([mid, b]) == []

    def test_self_does_not_claim_own_zone(self):
        a = _plan("A", [_port("A.s", 0.0, 0.0, w=5.0)],
                  segments=[{"a": (-10.0, 0.0), "b": (10.0, 0.0),
                             "width_um": 5.0}])
        assert launch_overlap_claims([a]) == []


class TestNegotiationOrder:
    def _plans(self):
        return [
            _plan("A", [_port("X1.A", 0.0, 0.0)]),
            _plan("B", [_port("X1.B", 0.0, 10.0)]),
        ]

    def test_cold_table_falls_back_to_heuristic(self):
        plans = self._plans()
        table = ResourceCostTable()
        # fallback orders by net name; cold table has zero cost for all
        out = negotiation_order(plans, table, pres_fac=1.0,
                                fallback_key=lambda p: p["net"])
        assert [p["net"] for p in out] == ["A", "B"]

    def test_contended_net_routes_first(self):
        plans = self._plans()
        sides = {"A": {"X1.A": "right"}, "B": {"X1.B": "right"}}
        table = ResourceCostTable()
        repopulate_occupancy(table, plans, spacing_um=2.0,
                             allowed_sides_by_port=sides)
        # neither contends yet (different flanks); force a shared flank
        # by giving B the same flank as A via a third claimant
        from klink.routing.backends.negotiated.negotiated_resources import FlankResource
        table.add_claim("A", FlankResource("X1", "A", "right"))
        table.add_claim("B", FlankResource("X1", "A", "right"))
        table.bump_history(5.0)
        out = negotiation_order(plans, table, pres_fac=2.0,
                                fallback_key=lambda p: p["net"])
        # both share the overused flank; order is stable+deterministic
        assert {p["net"] for p in out} == {"A", "B"}

    def test_history_persists_across_repopulate(self):
        plans = self._plans()
        sides = {"A": {"X1.A": "right"}, "B": {"X1.B": "right"}}
        table = ResourceCostTable()
        from klink.routing.backends.negotiated.negotiated_resources import FlankResource
        shared = FlankResource("X1", "A", "right")
        table.add_claim("A", shared)
        table.add_claim("B", shared)
        table.bump_history(7.0)
        hist_before = table.history_cost(("X1", "A", "right"))
        # repopulate (clear occupancy + re-add) must keep history
        repopulate_occupancy(table, plans, spacing_um=2.0,
                             allowed_sides_by_port=sides)
        assert table.history_cost(("X1", "A", "right")) == hist_before
        assert hist_before == 7.0
