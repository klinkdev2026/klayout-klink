"""PUBLIC test: the map -> place pipeline is deterministic and collision-free.
Offline, no lab data: maps an inline gate netlist via the synthetic library and
places the per-gate device groups in columns.
"""
from klink.domains.structdevice.logic_map import map_logic_to_devices
from klink.routing.grid.placer import place_columns

from synth_pdk import SYNTH_LIBRARY

DEVICE_BBOX = (-2.0, -1.0, 2.0, 1.0)


def _placed_bbox(origin, bbox=DEVICE_BBOX):
    dx, dy = origin
    x1, y1, x2, y2 = bbox
    return (dx + x1, dy + y1, dx + x2, dy + y2)


def _overlaps(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


NETLIST = {"gates": [
    {"type": "NAND2", "inputs": {"A": "a", "B": "b"}, "output": "m"},
    {"type": "NAND2", "inputs": {"A": "m", "B": "c"}, "output": "n"},
    {"type": "INV", "inputs": {"A": "n"}, "output": "y"},
]}


def test_map_then_place_is_deterministic_and_collision_free():
    first = map_logic_to_devices(NETLIST, SYNTH_LIBRARY)
    second = map_logic_to_devices(dict(NETLIST), SYNTH_LIBRARY)
    assert first == second

    groups = [group["instances"] for group in first["groups"]]
    # per-gate groups: NAND2 -> 3 devices, NAND2 -> 3, INV -> 2
    assert sorted(len(g) for g in groups) == [2, 3, 3]

    p1 = place_columns(groups, DEVICE_BBOX, col_pitch_um=8.0, row_pitch_um=4.0, y_top_um=0.0)
    p2 = place_columns(groups, DEVICE_BBOX, col_pitch_um=8.0, row_pitch_um=4.0, y_top_um=0.0)
    assert p1 == p2
    assert set(p1) == {inst["instance_id"] for inst in first["instances"]}

    boxes = {iid: _placed_bbox(origin) for iid, origin in p1.items()}
    ids = sorted(boxes)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            assert not _overlaps(boxes[left], boxes[right])

    # each gate's devices share a column (one x) and stack downward
    for group in groups:
        xs = {p1[iid][0] for iid in group}
        ys = [p1[iid][1] for iid in group]
        assert len(xs) == 1
        assert ys == sorted(ys, reverse=True)
