from __future__ import annotations

from klink.domains.nanodevice.devices.hallbar import HallBarSpec, build_hallbar
from klink.domains.nanodevice.ebl.marks import alignment_cross_items, corner_alignment_marks
from klink.domains.nanodevice.pipeline import build_hallbar_bundle

# HallBarSpec requires process layers (klink ships none); tests supply them.
LYR = dict(device_layer="1/0", metal_layer="10/0", label_layer="6/0", route_layer="12/0")


def test_alignment_cross_items_are_batch_shape_payloads():
    items = alignment_cross_items([10, 20], arm_length_um=8, arm_width_um=2, layer="6/0", label="A")

    assert len(items) == 3
    assert items[0] == {"kind": "box", "layer": 6, "datatype": 0, "bbox_um": [6.0, 19.0, 14.0, 21.0]}
    assert items[2]["kind"] == "text"


def test_corner_alignment_marks_make_four_crosses_with_labels():
    items = corner_alignment_marks([0, 0, 100, 80], inset_um=10, arm_length_um=8, arm_width_um=2)

    assert len(items) == 12
    assert sum(1 for item in items if item["kind"] == "box") == 8
    assert sum(1 for item in items if item["kind"] == "text") == 4


def test_hallbar_basic_layout_ports_and_candidate_sink_pads():
    spec = HallBarSpec(name="HBX", contact_count=4, **LYR)
    bundle = build_hallbar(spec)

    assert bundle["report"]["contact_count"] == 4
    assert bundle["report"]["pad_count"] == 4
    assert bundle["report"]["anchor_count"] == 4
    contact_ports = bundle["contact_ports"]
    pad_ports = bundle["pad_ports"]
    assert all(port["net"] for port in contact_ports)
    assert all(port["port_type"] == "candidate_sink" for port in pad_ports)
    assert all(port["width_um"] == spec.contact_width_um for port in pad_ports)
    assert all(port["target_layer"] == spec.route_layer for port in bundle["port_marks"])
    assert all(anchor["kind"] == "waypoint_region" for anchor in bundle["anchor_marks"])


def test_hallbar_rejects_odd_contact_count():
    spec = HallBarSpec(name="BAD", contact_count=5, **LYR)
    try:
        build_hallbar(spec)
    except ValueError as exc:
        assert "even" in str(exc)
    else:
        raise AssertionError("odd Hall bar contact count should fail")


def test_hallbar_bundle_adds_writefield_obstacles_as_semantic_inputs():
    bundle = build_hallbar_bundle(
        HallBarSpec(name="HBF", contact_count=4, **LYR),
        writefield={
            "chip_bbox_um": [-100, -100, 100, 100],
            "writefield_size_um": 100,
            "stitch_margin_um": 1,
            "auto_crossing_window_span_um": 20,
        },
    )

    assert bundle["writefield"]["report"]["boundary_count"] == 2
    assert bundle["obstacle_boxes_um"]
    assert len(bundle["anchor_marks"]) == 6
