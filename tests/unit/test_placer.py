import pytest

from klink.routing.grid.placer import PlacementError, place_columns


def _placed_bbox(origin, bbox):
    dx, dy = origin
    x1, y1, x2, y2 = bbox
    return (dx + x1, dy + y1, dx + x2, dy + y2)


def _overlaps(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def test_place_columns_preserves_group_order_and_spacing():
    groups = [
        {"name": "col0", "instances": ["Xload", "Xdrv"]},
        {"name": "col1", "instances": ["Yload", "Ydrv"]},
    ]
    bbox = (-2.0, -1.0, 2.0, 1.0)

    placed = place_columns(groups, bbox, col_pitch_um=10.0, row_pitch_um=4.0, y_top_um=20.0)

    assert placed == {
        "Xload": (0.0, 20.0),
        "Xdrv": (0.0, 16.0),
        "Yload": (10.0, 20.0),
        "Ydrv": (10.0, 16.0),
    }
    assert placed["Xload"][0] == placed["Xdrv"][0]
    assert placed["Xload"][1] > placed["Xdrv"][1]
    assert placed["Yload"][0] - placed["Xload"][0] == 10.0

    boxes = {iid: _placed_bbox(origin, bbox) for iid, origin in placed.items()}
    ids = sorted(boxes)
    for index, left in enumerate(ids):
        for right in ids[index + 1:]:
            assert not _overlaps(boxes[left], boxes[right])
    assert boxes["Yload"][0] - boxes["Xload"][2] == 6.0


def test_place_columns_supports_per_instance_bboxes_and_is_deterministic():
    groups = [["A0", "A1"], ["B0"]]
    bboxes = {
        "A0": (0.0, 0.0, 3.0, 2.0),
        "A1": (0.0, 0.0, 2.0, 1.0),
        "B0": (0.0, 0.0, 4.0, 2.0),
    }

    first = place_columns(groups, bboxes, col_pitch_um=8.0, row_pitch_um=3.0)
    second = place_columns(groups, bboxes, col_pitch_um=8.0, row_pitch_um=3.0)

    assert first == second
    assert first["A0"] == (0.0, 0.0)
    assert first["A1"] == (0.0, -3.0)
    assert first["B0"] == (8.0, 0.0)


def test_place_columns_rejects_duplicate_and_too_small_pitch():
    with pytest.raises(PlacementError, match="appears in more than one"):
        place_columns([["X1"], ["X1"]], (-1.0, -1.0, 1.0, 1.0), col_pitch_um=4.0, row_pitch_um=4.0)
    with pytest.raises(PlacementError, match="col_pitch_um"):
        place_columns([["X1"], ["X2"]], (0.0, 0.0, 5.0, 1.0), col_pitch_um=4.0, row_pitch_um=2.0)
    with pytest.raises(PlacementError, match="row_pitch_um"):
        place_columns([["X1", "X2"]], (0.0, 0.0, 1.0, 5.0), col_pitch_um=2.0, row_pitch_um=4.0)
