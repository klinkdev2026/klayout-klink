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
                                      collect_klayout_tree,
                                      validate_draw_items)

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
        return {"ok": True, "result": _side_result(req)}

    return seen, handler


def _side_result(req, capabilities=("delete_cell",), cells=()):
    """Answers for the bookkeeping calls push_cell_tree makes around the
    batch (ping for capabilities, list_cells for the pre-existing set)."""
    if req["cmd"] == "ping":
        return {"file": "unit.tdb", "capabilities": list(capabilities)}
    if req["cmd"] == "list_cells":
        return {"cells": [{"name": n} for n in cells]}
    return {}


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


def test_push_reports_zero_width_wires_the_macro_accepted(bridge_with):
    # A width-0 KLayout path (a klink marker outline) is sent as a
    # zero-width wire, not refused client-side; the macro's per-draw
    # zero_width_wires counts are summed into the report.
    marker = {"MARK": {"shapes": [
        {"type": "path", "layer_index": 0, "width_dbu": 0,
         "points_dbu": [[0, 0], [1000, 0], [1000, 1000], [0, 0]]}]}}
    sent = []

    def handler(req):
        if req["cmd"] != "batch":
            return {"ok": True, "result": _side_result(req)}
        results = []
        for op in req["params"]["ops"]:
            one = {}
            if op["cmd"] == "draw":
                sent.extend(op["params"]["items"])
                zero = sum(1 for it in op["params"]["items"]
                           if it["kind"] == "wire" and it["width_um"] == 0)
                one = {"drawn": len(op["params"]["items"])}
                if zero:
                    one["zero_width_wires"] = zero
            results.append(one)
        return {"ok": True, "result": {"results": results,
                                       "completed": len(results)}}

    report = push_cell_tree(FakeClient(marker), bridge_with(handler), "MARK")
    assert [it["kind"] for it in sent] == ["wire"]
    assert sent[0]["width_um"] == 0
    assert report["zero_width_wires"] == 1
    assert report["shapes"]["MARK"] == 1


def test_push_validates_every_item_before_sending_anything(bridge_with):
    # a zero-area box in the LAST cell must cost nothing: no batch at all
    bad = dict(DIAMOND)
    bad["TOP"] = {"instances": [inst("A"), inst("B", dy=5000)],
                  "shapes": [box(bbox=(5, 5, 5, 9))]}
    seen, handler = collect_ops(None, None)
    with pytest.raises(LEditBridgeError) as ei:
        push_cell_tree(FakeClient(bad), bridge_with(handler), "TOP")
    assert ei.value.code == "ERR_INVALID_ITEM"
    assert "cell 'TOP' items[0]" in str(ei.value)
    assert seen == []                        # nothing reached the macro


def test_validate_draw_items_rules():
    ok = [{"kind": "box", "layer": "M", "bbox_um": [0, 0, 1, 1]},
          {"kind": "wire", "layer": "M", "width_um": 0,
           "points_um": [[0, 0], [1, 1]]},
          {"kind": "polygon", "layer": "M",
           "points_um": [[0, 0], [1, 0], [1, 1]]}]
    validate_draw_items("X", ok)             # zero-width wire is legal
    for item in (
        {"kind": "box", "bbox_um": [0, 0, 1, 1]},                  # no layer
        {"kind": "wire", "layer": "M", "width_um": -1,
         "points_um": [[0, 0], [1, 1]]},                           # negative
        {"kind": "wire", "layer": "M", "width_um": 1,
         "points_um": [[0, 0]]},                                   # 1 point
        {"kind": "polygon", "layer": "M", "points_um": [[0, 0], [1, 1]]},
        {"kind": "circle", "layer": "M", "radius_um": 0},
        {"kind": "text", "layer": "M"},                            # unknown
    ):
        with pytest.raises(LEditBridgeError) as ei:
            validate_draw_items("X", [item])
        assert ei.value.code == "ERR_INVALID_ITEM"


def _failing_batch_world(existing_cells, capabilities=("delete_cell",),
                         fail_at="place_instance"):
    """A macro stand-in whose batch fails at the first `fail_at` op, and
    whose get_cell reports the leaf as half-drawn (count 0)."""
    seen = []
    present = list(existing_cells)           # list_cells tracks creation

    def handler(req):
        if req["cmd"] == "batch":
            for i, op in enumerate(req["params"]["ops"]):
                seen.append((op["cmd"], op["params"]))
                if op["cmd"] == "create_cell" and \
                        op["params"]["name"] not in present:
                    present.append(op["params"]["name"])
                if op["cmd"] == fail_at:
                    return {"ok": False, "error": {
                        "code": "ERR_BRIDGE",
                        "message": "ops[%d]: %s failed" % (i, fail_at),
                        "next_action": "fix it"}}
            return {"ok": True, "result": {"results": [], "completed": 0}}
        if req["cmd"] == "get_cell":
            return {"ok": True, "result": {"count": 0, "objects": []}}
        if req["cmd"] == "delete_cell":
            seen.append(("delete_cell", req["params"]))
            present.remove(req["params"]["cell"])
            return {"ok": True, "result": {"deleted": True}}
        return {"ok": True, "result": _side_result(
            req, capabilities, present)}

    return seen, handler


def test_failed_push_rolls_back_created_cells_and_names_clobbered_ones(
        bridge_with):
    # LEAF existed before (user's); A, B, TOP are created by this call
    seen, handler = _failing_batch_world(existing_cells=["LEAF"])
    with pytest.raises(LEditBridgeError) as ei:
        push_cell_tree(FakeClient(DIAMOND), bridge_with(handler), "TOP",
                       expect_file="unit.tdb")
    exc = ei.value
    # the batch stopped at the first place_instance, so only the cells
    # created BEFORE that point exist -- exactly those must be rolled back
    created = [p["name"] for c, p in seen if c == "create_cell"
               and p["name"] != "LEAF"]
    assert created                           # at least one child was made
    deleted = [p["cell"] for c, p in seen if c == "delete_cell"]
    assert set(deleted) == set(created)
    assert "LEAF" not in deleted             # never delete what was there
    assert all(p["force"] and p["expect_file"] == "unit.tdb"
               for c, p in seen if c == "delete_cell")
    assert set(exc.rolled_back) == set(created)
    assert exc.clobbered == ["LEAF"]         # cleared, count 0 != 1 expected
    assert "rolled_back" in str(exc) and "CLOBBERED" in str(exc)


def test_failed_push_on_an_old_macro_reports_what_it_left_behind(bridge_with):
    seen, handler = _failing_batch_world(existing_cells=[],
                                         capabilities=())
    with pytest.raises(LEditBridgeError) as ei:
        push_cell_tree(FakeClient(DIAMOND), bridge_with(handler), "TOP")
    assert not [c for c, _ in seen if c == "delete_cell"]
    created = {p["name"] for c, p in seen if c == "create_cell"}
    assert "LEAF" in created
    assert set(ei.value.left_behind) == created
    assert "LEFT BEHIND" in str(ei.value) and "0.5.7" in str(ei.value)


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
