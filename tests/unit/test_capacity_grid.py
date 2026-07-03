"""Unit tests for the F4 capacity-grid router (grt-style substrate).

Guards the substrate that replaces the Hanan grid (which hit a scaling
wall with large device pads, STATUS Update 57):
  * legality is first-class and HARD (foreign pad / channel / via-on-
    device) so a returned route is legal by construction;
  * negotiation drives inter-net cell sharing to zero (no short);
  * the substrate is N-layer => 3D stacking is the general case, two
    layers are a special case (multi-device + 3D stacking).
"""

from collections import defaultdict

from klink.routing.grid.capacity_grid import (
    NetInput,
    ViaRule,
    build_capacity_grid,
    route_nets,
)


def _shared_cells(result):
    u = defaultdict(set)
    for net, cells in result.routes.items():
        for c in cells:
            u[c].add(net)
    return [c for c, w in u.items() if len(w) > 1]


def test_crossing_nets_separate_via_crossunder():
    via = ViaRule("M1/0", "M2/0", "via_x", (15.0, 15.0), 2.0)
    g = build_capacity_grid(
        layers=["M1/0", "M2/0"], bbox_um=(-10, -30, 60, 30), pitch_um=10.0,
        channel_boxes_um=[], pad_boxes_by_layer={},
        device_body_boxes_um=[], via_rules=[via], via_footprint_um=15.0)
    a = NetInput("A", [(0.0, 0.0, "M1/0"), (50.0, 0.0, "M1/0")])
    b = NetInput("B", [(30.0, -20.0, "M1/0"), (30.0, 20.0, "M1/0")])
    r = route_nets(g, [a, b])
    assert r.ok, r.problems
    assert _shared_cells(r) == []                 # no short by construction
    assert {c[2] for c in r.routes["B"]} == {0, 1}  # B crossed under


def test_foreign_pad_is_hard_keepout():
    # A's pad blocks B; B must route around it, never through it.
    via = ViaRule("M1/0", "M2/0", "via_x", (15.0, 15.0), 2.0)
    g = build_capacity_grid(
        layers=["M1/0", "M2/0"], bbox_um=(-10, -30, 60, 30), pitch_um=10.0,
        channel_boxes_um=[],
        pad_boxes_by_layer={"M1/0": [("A", (18.0, -8.0, 42.0, 8.0))]},
        device_body_boxes_um=[], via_rules=[via], via_footprint_um=15.0)
    b = NetInput("B", [(30.0, -20.0, "M1/0"), (30.0, 20.0, "M1/0")])
    r = route_nets(g, [b])
    assert r.ok, r.problems
    li = {l: i for i, l in enumerate(g.layers)}
    foreign = g.pad_cells[li["M1/0"]]["A"]
    # no B cell on M1 sits on A's pad
    assert not any((c[0], c[1]) in foreign and c[2] == li["M1/0"]
                   for c in r.routes["B"])


def test_no_via_on_device_body():
    via = ViaRule("M1/0", "M2/0", "via_x", (15.0, 15.0), 2.0)
    g = build_capacity_grid(
        layers=["M1/0", "M2/0"], bbox_um=(-10, -30, 60, 30), pitch_um=10.0,
        channel_boxes_um=[], pad_boxes_by_layer={},
        device_body_boxes_um=[(20.0, -10.0, 40.0, 10.0)],
        via_rules=[via], via_footprint_um=15.0)
    assert g.via_blocked                          # device zone marked
    a = NetInput("A", [(0.0, 0.0, "M1/0"), (50.0, 0.0, "M2/0")])
    r = route_nets(g, [a])
    assert r.ok, r.problems
    # every via (layer change at same cell) is outside the via-blocked set
    cells = r.routes["A"]
    for c1, c2 in zip(cells, cells[1:]):
        if c1[2] != c2[2]:
            assert (c1[0], c1[1]) not in g.via_blocked


def test_three_layer_stack_routes_through_two_vias():
    # 3D stacking: M1 -> M2 -> M3 with two via rules; a net whose access
    # points are on M1 and M3 must traverse both vias.
    vias = [ViaRule("M1/0", "M2/0", "v12", (9.0, 9.0), 2.0),
            ViaRule("M2/0", "M3/0", "v23", (9.0, 9.0), 2.0)]
    g = build_capacity_grid(
        layers=["M1/0", "M2/0", "M3/0"], bbox_um=(-10, -10, 60, 10),
        pitch_um=10.0, channel_boxes_um=[], pad_boxes_by_layer={},
        device_body_boxes_um=[], via_rules=vias, via_footprint_um=9.0)
    a = NetInput("A", [(0.0, 0.0, "M1/0"), (50.0, 0.0, "M3/0")])
    r = route_nets(g, [a])
    assert r.ok, r.problems
    assert {c[2] for c in r.routes["A"]} == {0, 1, 2}   # used all three tiers


def test_deterministic():
    via = ViaRule("M1/0", "M2/0", "via_x", (15.0, 15.0), 2.0)
    kw = dict(layers=["M1/0", "M2/0"], bbox_um=(-10, -30, 60, 30),
              pitch_um=10.0, channel_boxes_um=[], pad_boxes_by_layer={},
              device_body_boxes_um=[], via_rules=[via], via_footprint_um=15.0)
    a = NetInput("A", [(0.0, 0.0, "M1/0"), (50.0, 0.0, "M1/0")])
    b = NetInput("B", [(30.0, -20.0, "M1/0"), (30.0, 20.0, "M1/0")])
    r1 = route_nets(build_capacity_grid(**kw), [a, b])
    r2 = route_nets(build_capacity_grid(**kw), [a, b])
    assert r1.ok == r2.ok
    assert {k: list(v) for k, v in r1.routes.items()} == \
           {k: list(v) for k, v in r2.routes.items()}
