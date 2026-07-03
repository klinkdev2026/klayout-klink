from __future__ import annotations

from klink.domains.nanodevice.devices.hallbar import HallBarSpec, build_hallbar
from klink.domains.nanodevice.pipeline import build_hallbar_bundle, route_hallbar_offline

LYR = dict(device_layer="1/0", metal_layer="10/0", label_layer="6/0", route_layer="12/0")


def test_hallbar_offline_routing_uses_existing_hybrid_backend():
    bundle = build_hallbar(HallBarSpec(name="HBR", contact_count=4, **LYR))

    result = route_hallbar_offline(bundle, spacing_um=4.0)

    assert result["backend"] == "tapered_hybrid_many"
    assert result["ok"] is True
    assert len(result["routes"]) == 4
    assert result["sibling_overlaps"] == []
    assert all(route["net"].startswith("hbr_") for route in result["routes"])
    for route in result["routes"]:
        x0 = route["points_um"][0][0]
        assert all(abs(point[0] - x0) < 1e-6 for point in route["points_um"])
        assert not route["groups"][0]["corner_patches"]


def test_hallbar_with_writefield_obstacles_routes_without_wall_crossing():
    bundle = build_hallbar_bundle(
        HallBarSpec(name="WFHB", **LYR),
        writefield={
            "chip_bbox_um": [-95.0, -45.0, 95.0, 45.0],
            "writefield_size_um": [70.0, 120.0],
            "origin_um": [10.0, 0.0],
            "stitch_margin_um": 1.2,
        },
    )

    result = route_hallbar_offline(bundle, spacing_um=4.0)

    assert result["ok"] is True
    assert result["route_count"] == 6
    assert result["sibling_overlaps"] == []
    assert result["obstacle_hits"] == []
    assert bundle["writefield"]["report"]["obstacle_count"] == 4
    assert len(bundle["obstacle_boxes_um"]) == 4
