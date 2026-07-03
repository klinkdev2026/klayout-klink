from __future__ import annotations

from klink.domains.nanodevice.devices.wraparound import build_wraparound_demo


def test_ebl_wraparound_demo_has_expected_complexity():
    bundle = build_wraparound_demo({
        "flake": (30, 0), "m1": (10, 0), "m2": (11, 0), "pad": (20, 0),
        "via": (40, 0), "label": (6, 0), "patch": "113/0"})

    assert bundle["report"]["ports"] == 16
    assert bundle["report"]["anchors"] == 19
    assert bundle["report"]["patches"] == 12
    assert bundle["report"]["wf_obstacles"] == 17
    assert bundle["report"]["wf_crossings"] == 20
    assert bundle["report"]["wf_crossing_violations"] == 0
    assert bundle["report"]["route_centerline_overlaps"] == 0
    assert bundle["writefield"]["report"]["field_count"] == 16

    by_layer = {}
    for item in bundle["shape_items"]:
        by_layer[(item["layer"], item.get("datatype", 0))] = by_layer.get((item["layer"], item.get("datatype", 0)), 0) + 1
    assert by_layer[(30, 0)] == 1
    assert by_layer[(10, 0)] >= 16
    assert by_layer[(11, 0)] >= 16
    assert by_layer[(20, 0)] == 8
    assert by_layer[(40, 0)] == 8
    assert by_layer[(113, 0)] == 12
    assert by_layer[(900, 0)] == 17
