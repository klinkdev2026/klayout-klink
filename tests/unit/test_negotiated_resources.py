from klink.routing.backends.negotiated.negotiated_resources import (
    CorridorGateResource,
    FlankResource,
    LaunchZoneResource,
    NetPlanError,
    ResourceCostTable,
    all_claims,
    corridor_gate_claims,
    flank_claims,
    launch_zone_claims,
    segment_envelope_claims,
)


def _port(name, *, instance="X1", terminal=None, center=(80.75, 35.0), orientation=0, width=5.0):
    return {
        "name": name,
        "instance": instance,
        "terminal": terminal or name,
        "center_um": center,
        "orientation_deg": orientation,
        "width_um": width,
    }


def _table_for(claims_by_net):
    table = ResourceCostTable()
    for net, claims in claims_by_net.items():
        for claim in claims:
            table.add_claim(net, claim)
    return table


def test_half_adder_interlocks_name_resources_and_costs(capsys):
    flank_a = {
        "net": "B_to_X1_B",
        "ports": [_port("X1.B", terminal="B", center=(80.75, 35.0))],
    }
    flank_b = {
        "net": "NAB_to_X1_A",
        "ports": [_port("X1.A", terminal="A", center=(80.75, 35.0))],
    }
    flank_claim_a = flank_claims(flank_a, allowed_sides_by_port={"X1.B": "right"})[0]
    flank_claim_b = flank_claims(flank_b, allowed_sides_by_port={"X1.A": "right"})[0]
    assert flank_claim_a.key == ("X1", "B", "right")
    assert flank_claim_b.key == ("X1", "A", "right")
    same_flank_claim_b = FlankResource(instance="X1", terminal="B", side="right")

    flank_table = _table_for({"B_to_X1_B": [flank_claim_a], "NAB_to_X1_A": [same_flank_claim_b]})
    assert flank_table.overused_resources() == [("X1", "B", "right")]
    assert flank_table.is_overused(("X1", "B", "right"))
    assert flank_table.present_cost(("X1", "B", "right"), 2.0) > flank_table.present_cost(("X1", "B", "right"), 1.0)
    assert flank_table.net_cost("B_to_X1_B", 2.0) > flank_table.net_cost("B_to_X1_B", 1.0)

    corridor_a = {
        "net": "SUM_LEFT",
        "ports": [_port("SUM_LEFT.src")],
        "corridor": {"id": "HA_CENTER_CORRIDOR"},
    }
    corridor_b = {
        "net": "SUM_RIGHT",
        "ports": [_port("SUM_RIGHT.src")],
        "corridor": {"id": "HA_CENTER_CORRIDOR"},
    }
    corridor_c = {
        "net": "CARRY",
        "ports": [_port("CARRY.src")],
        "corridor": {"id": "HA_UPPER_CORRIDOR"},
    }
    corridor_table = _table_for(
        {
            "SUM_LEFT": corridor_gate_claims(corridor_a),
            "SUM_RIGHT": corridor_gate_claims(corridor_b),
            "CARRY": corridor_gate_claims(corridor_c),
        }
    )
    assert ("HA_CENTER_CORRIDOR", "entry") in corridor_table.overused_resources()
    assert ("HA_CENTER_CORRIDOR", "exit") in corridor_table.overused_resources()
    assert not corridor_table.is_overused(("HA_UPPER_CORRIDOR", "entry"))
    assert corridor_table.net_cost("SUM_LEFT", 2.0) > corridor_table.net_cost("SUM_LEFT", 1.0)

    mid_plan = {
        "net": "MID",
        "ports": [_port("X1.B_launch", instance="X1", terminal="B", center=(80.75, 35.0))],
    }
    blocking_b = {
        "net": "B",
        "ports": [_port("B.src", instance="XB", terminal="D", center=(70.0, 35.0))],
        "segments": [{"a": (70.0, 35.0), "b": (120.0, 45.0), "width_um": 5.0}],
        "claimed_launch_zones": [{"net": "MID", "port_name": "X1.B_launch"}],
    }
    launch_table = _table_for(
        {
            "MID": launch_zone_claims(mid_plan),
            "B": launch_zone_claims(blocking_b),
        }
    )
    assert launch_table.overused_resources() == [("MID", "X1.B_launch")]
    assert launch_table.net_cost("B", 2.0) > launch_table.net_cost("B", 1.0)

    repeat_table = _table_for(
        {
            "B_to_X1_B": [flank_claim_a],
            "NAB_to_X1_A": [same_flank_claim_b],
            "SUM_LEFT": [CorridorGateResource("HA_CENTER_CORRIDOR", "entry")],
            "SUM_RIGHT": [CorridorGateResource("HA_CENTER_CORRIDOR", "entry")],
        }
    )
    repeat_table.bump_history(1.0)
    repeat_table.clear_occupancy()
    repeat_table.add_claim("B_to_X1_B", flank_claim_a)
    repeat_table.add_claim("NAB_to_X1_A", same_flank_claim_b)
    repeat_table.bump_history(1.0)
    assert repeat_table.history_cost(("X1", "B", "right")) > repeat_table.history_cost(("HA_CENTER_CORRIDOR", "entry"))

    print(
        "PASS negotiated_resources interlocks "
        f"overused={len(flank_table.overused_resources()) + len(corridor_table.overused_resources()) + len(launch_table.overused_resources())}"
    )
    captured = capsys.readouterr()
    assert "PASS negotiated_resources interlocks overused=4" in captured.out


def test_segment_envelope_quantization_and_deterministic_claim_order():
    plan = {
        "net": "B",
        "ports": [_port("B.src", instance="XB", terminal="D", center=(70.0, 35.0))],
        "corridor": {"id": "HA_CENTER_CORRIDOR"},
        "segments": [{"a": (70.0, 35.0), "b": (120.0, 45.0), "width_um": 5.0}],
    }
    claims = all_claims(plan, spacing_um=2.5, allowed_sides_by_port={"B.src": "right"})
    claims_again = all_claims(plan, spacing_um=2.5, allowed_sides_by_port={"B.src": "right"})
    assert claims == claims_again
    assert [type(claim).__name__ for claim in claims] == [
        "FlankResource",
        "LaunchZoneResource",
        "CorridorGateResource",
        "CorridorGateResource",
        "SegmentEnvelopeResource",
    ]
    assert claims[-1].key == (65000, 30000, 125000, 50000)
    assert all(isinstance(value, int) for value in claims[-1].key)

    table_a = _table_for({"B": claims})
    table_b = _table_for({"B": claims_again})
    assert table_a.overused_resources() == table_b.overused_resources()
    assert table_a.net_cost("B", 3.0) == table_b.net_cost("B", 3.0)


def test_cost_table_uses_net_occupancy_and_history_persists_after_clear():
    resource = LaunchZoneResource("MID", "X1.B_launch")
    table = ResourceCostTable()
    table.add_claim("B", resource)
    table.add_claim("B", resource)
    assert table.occupancy(resource.key) == 1
    assert not table.is_overused(resource.key)

    table.add_claim("MID", resource)
    assert table.occupancy(resource.key) == 2
    assert table.present_cost(resource.key, 2.0) == 4.0
    table.bump_history(0.5)
    table.clear_occupancy()
    assert table.occupancy(resource.key) == 0
    assert table.history_cost(resource.key) == 0.5
    assert table.present_cost(resource.key, 2.0) == 0.0


def test_malformed_net_plans_raise_instructive_errors():
    try:
        launch_zone_claims({"net": "B"})
    except NetPlanError as exc:
        assert "non-empty ports list" in str(exc)
    else:
        raise AssertionError("missing ports did not fail")

    bad_orientation = {
        "net": "B",
        "ports": [_port("B.src", orientation="east")],
    }
    try:
        launch_zone_claims(bad_orientation)
    except NetPlanError as exc:
        assert "orientation_deg" in str(exc)
    else:
        raise AssertionError("bad orientation did not fail")

    bad_side = {
        "net": "B",
        "ports": [_port("B.src")],
    }
    try:
        flank_claims(bad_side, allowed_sides_by_port={"B.src": "diagonal"})
    except NetPlanError as exc:
        assert "left/right/up/down" in str(exc)
    else:
        raise AssertionError("bad side did not fail")

    bad_segment = {
        "net": "B",
        "ports": [_port("B.src")],
        "segments": [{"a": (0.0, 0.0), "b": (1.0, 1.0), "width_um": 0.0}],
    }
    try:
        segment_envelope_claims(bad_segment, spacing_um=1.0)
    except NetPlanError as exc:
        assert "width_um must be > 0" in str(exc)
    else:
        raise AssertionError("bad segment did not fail")
