"""Offline tests for hierarchy transfer (klink/bridges/ledit/tree.py).

No L-Edit and no KLayout: a fake klink client stands in for the KLayout
side and a responder thread for the macro, so the ORDER and CONTENT of the
emitted ops can be asserted directly. Order is the whole correctness
argument here — a parent created before its child cannot be instanced.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from klink.bridges.ledit import LEditBridgeError, push_cell_tree
from klink.bridges.ledit.tree import (_chunk_items, _orientation,
                                      collect_klayout_tree)

from tests.unit.test_ledit_bridge import Responder, make_ns  # noqa: F401
from klink.bridges.ledit import LEditBridgeClient


class FakeClient:
    """Minimal stand-in for the klink KLayout client."""

    def __init__(self, cells, dbu=0.001, layers=None):
        # cells: {name: {"shapes": [...], "instances": [...]}}
        self.cells = cells
        self.dbu = dbu
        self.layers = layers or [
            {"layer_index": 0, "layer": 70, "datatype": 0, "name": "MET1"},
            {"layer_index": 1, "layer": 71, "datatype": 0, "name": ""},
        ]

    def call(self, method, params=None):
        if method == "layout.info":
            return {"dbu": self.dbu}
        if method == "layer.list":
            return {"layers": self.layers}
        raise AssertionError("unexpected call %r" % method)

    def instance_query(self, cell):
        return {"instances": self.cells.get(cell, {}).get("instances", [])}

    def shape_query(self, cell):
        return {"shapes": self.cells.get(cell, {}).get("shapes", []),
                "truncated": self.cells.get(cell, {}).get("truncated", False)}


def inst(child, dx=0, dy=0, rot=0.0, mirror=False, mag=1.0, array=None):
    return {"child": child, "array": array,
            "trans": {"dx_dbu": dx, "dy_dbu": dy, "rotation_deg": rot,
                      "mirror": mirror, "magnification": mag}}


def box(layer_index=0, bbox=(0, 0, 1000, 1000)):
    return {"type": "box", "layer_index": layer_index, "bbox_dbu": list(bbox)}


DIAMOND = {
    "TOP": {"instances": [inst("A"), inst("B", dy=5000)]},
    "A": {"instances": [inst("LEAF")]},
    "B": {"instances": [inst("LEAF", dx=2000)]},
    "LEAF": {"shapes": [box()]},
}


def collect_ops(client, cell, **kw):
    """Run push_cell_tree against a responder that records every op."""
    seen = []

    def handler(req):
        if req["cmd"] == "batch":
            for op in req["params"]["ops"]:
                seen.append((op["cmd"], op["params"]))
            return {"ok": True, "result": {"results": [], "completed": 0}}
        return {"ok": True, "result": {}}

    return seen, handler


@pytest.fixture
def bridge_with(tmp_path):
    started = []

    def start(handler):
        root = make_ns(tmp_path)
        r = Responder(root, handler)
        r.start()
        started.append(r)
        return LEditBridgeClient(root=root, poll_s=0.01)

    yield start
    for r in started:
        r.stop.set()
    for r in started:
        r.join(timeout=1)


def test_chunk_items_never_exceeds_budget():
    items = [{"kind": "polygon", "layer": "M1",
              "points_um": [[i, i]] * 20} for i in range(50)]
    chunks = _chunk_items(items, budget=2048)
    assert sum(len(c) for c in chunks) == len(items)     # nothing lost
    assert len(chunks) > 1
    for c in chunks:
        assert len(json.dumps(c)) <= 2048 + len(json.dumps(c[-1])) + 1


def test_orientation_only_accepts_orthogonal():
    assert _orientation(0.0, False) == 0
    assert _orientation(90.0, False) == 90
    assert _orientation(-90.0, False) == 270
    assert _orientation(360.0, False) == 0
    assert _orientation(37.5, False) is None


def test_collect_tree_is_children_first_and_dedupes_a_diamond():
    order, seen = collect_klayout_tree(FakeClient(DIAMOND), "TOP")
    assert order.index("LEAF") < order.index("A") < order.index("TOP")
    assert order.index("LEAF") < order.index("B")
    assert order.count("LEAF") == 1          # shared child visited once
    assert set(order) == {"TOP", "A", "B", "LEAF"}


def test_push_emits_layers_then_children_then_parents(bridge_with):
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    report = push_cell_tree(FakeClient(DIAMOND), bridge, "TOP",
                            expect_file="unit.tdb")

    kinds = [c for c, _ in seen]
    assert kinds[0] == "ensure_layer"        # layers exist before any draw
    created = [p["name"] for c, p in seen if c == "create_cell"]
    assert created.index("LEAF") < created.index("A") < created.index("TOP")

    # every place_instance must come after its child cell was created
    for i, (cmd, params) in enumerate(seen):
        if cmd != "place_instance":
            continue
        child_made = next(j for j, (c2, p2) in enumerate(seen)
                          if c2 == "create_cell" and p2["name"] == params["child"])
        assert child_made < i, "instanced %r before creating it" % params["child"]

    assert report["cells"][-1] == "TOP"
    assert report["instances"]["TOP"] == 2


def test_push_clears_each_cell_before_drawing_into_it(bridge_with):
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    push_cell_tree(FakeClient(DIAMOND), bridge, "TOP", expect_file="unit.tdb")

    order = [(c, p.get("cell") or p.get("name")) for c, p in seen]
    clear_at = order.index(("clear_cell", "LEAF"))
    draw_at = order.index(("draw", "LEAF"))
    assert clear_at < draw_at, "draw appends, so the clear must come first"


def test_push_guards_every_write_with_expect_file(bridge_with):
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    push_cell_tree(FakeClient(DIAMOND), bridge, "TOP", expect_file="unit.tdb")
    assert seen and all(p.get("expect_file") == "unit.tdb" for _, p in seen)


def test_push_reports_instances_it_cannot_express_exactly(bridge_with):
    cells = {
        "TOP": {"instances": [inst("LEAF", mag=2.0),
                              inst("LEAF", rot=37.0),
                              inst("LEAF", array={"na": 2, "nb": 2,
                                                  "a_dbu": [1000, 500],
                                                  "b_dbu": [0, 1000]}),
                              inst("LEAF")]},
        "LEAF": {"shapes": [box()]},
    }
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    report = push_cell_tree(FakeClient(cells), bridge, "TOP")

    reasons = " ".join(u["reason"] for u in report["unsupported_instances"])
    assert len(report["unsupported_instances"]) == 3
    assert "magnification" in reasons and "orthogonal" in reasons \
        and "skewed" in reasons
    # the one placeable instance still went through
    assert report["instances"]["TOP"] == 1
    assert all("fix" in u for u in report["unsupported_instances"])


def test_negative_array_step_compensates_the_origin(bridge_with):
    # A KLayout array may step in -x/-y; L-Edit's nx/ny grow in +x/+y only.
    # Taking abs() of the pitch silently MIRRORS the array about its origin,
    # so the copies land somewhere the source never had them.
    cells = {
        "TOP": {"instances": [inst("LEAF", dx=10000, dy=8000,
                                   array={"na": 3, "nb": 2,
                                          "a_dbu": [-2000, 0],
                                          "b_dbu": [0, -1000]})]},
        "LEAF": {"shapes": [box()]},
    }
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    report = push_cell_tree(FakeClient(cells), bridge, "TOP")

    assert not report["unsupported_instances"]
    place = next(p for c, p in seen if c == "place_instance")
    # source occupies x in {10, 8, 6}, y in {8, 7}: origin moves to the far
    # corner and the pitch turns positive, covering the SAME footprint
    assert place["nx"] == 3 and place["ny"] == 2
    assert place["dx_um"] == pytest.approx(2.0)
    assert place["dy_um"] == pytest.approx(1.0)
    assert place["x_um"] == pytest.approx(6.0)
    assert place["y_um"] == pytest.approx(7.0)


def test_positive_array_step_leaves_the_origin_alone(bridge_with):
    cells = {
        "TOP": {"instances": [inst("LEAF", dx=10000, dy=8000,
                                   array={"na": 3, "nb": 2,
                                          "a_dbu": [2000, 0],
                                          "b_dbu": [0, 1000]})]},
        "LEAF": {"shapes": [box()]},
    }
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    push_cell_tree(FakeClient(cells), bridge, "TOP")
    place = next(p for c, p in seen if c == "place_instance")
    assert place["x_um"] == pytest.approx(10.0)
    assert place["y_um"] == pytest.approx(8.0)
    assert place["dx_um"] == pytest.approx(2.0)


def test_zero_step_array_is_reported_not_collapsed(bridge_with):
    cells = {
        "TOP": {"instances": [inst("LEAF", array={"na": 4, "nb": 1,
                                                  "a_dbu": [0, 0],
                                                  "b_dbu": [0, 0]})]},
        "LEAF": {"shapes": [box()]},
    }
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    report = push_cell_tree(FakeClient(cells), bridge, "TOP")
    assert len(report["unsupported_instances"]) == 1
    assert "zero step" in report["unsupported_instances"][0]["reason"]
    assert report["instances"]["TOP"] == 0


def test_push_refuses_a_truncated_shape_query(bridge_with):
    cells = {"TOP": {"instances": [], "shapes": [box()], "truncated": True}}
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    with pytest.raises(LEditBridgeError) as exc:
        push_cell_tree(FakeClient(cells), bridge, "TOP")
    assert "import_gds" in exc.value.next_action


def test_unnamed_layers_fall_back_to_gds_numbers(bridge_with):
    cells = {"TOP": {"instances": [], "shapes": [box(layer_index=1)]}}
    seen, handler = collect_ops(None, None)
    bridge = bridge_with(handler)
    report = push_cell_tree(FakeClient(cells), bridge, "TOP")
    assert report["layers"] == {"L71D0": [71, 0]}
