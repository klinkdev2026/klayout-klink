from __future__ import annotations

import pytest

from klink.domains.fabrication.marks import box_in_box, cross, cross_in_box, from_preset_groups, l_mark, placed_mark_group_items, vernier_pair


def _bboxes(items):
    return [item["bbox_um"] for item in items]


def test_cross_golden_geometry_counts_and_bboxes():
    items = cross(size_um=100, line_width_um=10)

    assert len(items) == 2
    assert _bboxes(items) == [[-50.0, -5.0, 50.0, 5.0], [-5.0, -50.0, 5.0, 50.0]]


def test_cross_in_box_adds_outer_box():
    items = cross_in_box(size_um=100, line_width_um=10, box_margin_um=20)

    assert len(items) == 3
    assert items[-1]["bbox_um"] == [-70.0, -70.0, 70.0, 70.0]


def test_l_mark_box_in_box_and_vernier_shapes():
    assert len(l_mark(arm_um=40, line_width_um=4)) == 2
    assert _bboxes(box_in_box(outer_um=50, inner_um=20)) == [
        [-25.0, -25.0, 25.0, 25.0],
        [-10.0, -10.0, 10.0, 10.0],
    ]
    assert len(vernier_pair(10, 11, 5, 1, "x")) == 10


def test_generated_presets_are_layer_grouped_fallbacks():
    groups = from_preset_groups("corner_composite")

    assert [group["layer"] for group in groups] == ["6/0", "7/0", "8/0"]
    assert [len(group["shape_items"]) for group in groups] == [3, 2, 14]
    placed = placed_mark_group_items(groups, [100, 200])
    assert {f"{item['layer']}/{item['datatype']}" for item in placed} == {"6/0", "7/0", "8/0"}


def test_mark_generators_validate_inputs():
    with pytest.raises(ValueError, match="size_um"):
        cross(size_um=0, line_width_um=1)
    with pytest.raises(ValueError, match="inner_um"):
        box_in_box(outer_um=10, inner_um=10)
    with pytest.raises(ValueError, match="axis"):
        vernier_pair(10, 11, 3, 1, "z")  # type: ignore[arg-type]
