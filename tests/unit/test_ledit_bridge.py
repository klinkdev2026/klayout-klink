"""Offline unit tests for klink.bridges.ledit — no L-Edit, no KLayout.

The bridge transport is a directory pair, so a responder thread standing
in for the macro exercises the real client end-to-end.
"""
import json
import os
import threading
import time

import pytest

from klink.bridges.ledit import (
    LEditBridgeClient, LEditBridgeError, build_layer_map, convert_object,
    selection_to_items, harvest_boxes, merge_layer_name, parse_tcell_params,
    VariantFactory, verify_differential)


# --------------------------------------------------------------------------
# transport fixtures
# --------------------------------------------------------------------------

def make_ns(tmp_path, namespace="default", hello=True):
    root = tmp_path / "bridge_root"
    ns = root / namespace
    (ns / "inbox").mkdir(parents=True)
    (ns / "outbox").mkdir(parents=True)
    if hello:
        (ns / "hello.json").write_text(json.dumps(
            {"schema": 1, "proto": 1, "macro_version": "test",
             "file": "unit.tdb", "cell": "TOP"}), encoding="utf-8")
    return str(root)


class Responder(threading.Thread):
    """Minimal macro stand-in: answers every inbox request via handler."""

    def __init__(self, ns_root, handler, namespace="default"):
        super().__init__(daemon=True)
        self.inbox = os.path.join(ns_root, namespace, "inbox")
        self.outbox = os.path.join(ns_root, namespace, "outbox")
        self.handler = handler
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            for name in os.listdir(self.inbox):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(self.inbox, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        req = json.load(f)
                except (OSError, ValueError):
                    continue
                os.remove(path)
                resp = self.handler(req)
                resp.setdefault("schema", 1)
                resp.setdefault("id", req["id"])
                tmp = os.path.join(self.outbox, "x.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(resp, f)
                os.replace(tmp, os.path.join(
                    self.outbox, f"resp_{req['id']}.json"))
            time.sleep(0.01)


@pytest.fixture
def live_bridge(tmp_path):
    root = make_ns(tmp_path)
    responders = []

    def start(handler):
        r = Responder(root, handler)
        r.start()
        responders.append(r)
        return LEditBridgeClient(root=root, poll_s=0.02)

    yield start
    for r in responders:
        r.stop.set()
    for r in responders:
        r.join(timeout=1)


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

def test_missing_hello_is_instructive(tmp_path):
    root = make_ns(tmp_path, hello=False)
    c = LEditBridgeClient(root=root)
    assert not c.alive()
    with pytest.raises(LEditBridgeError) as e:
        c.call("ping")
    assert "Load Macro" in str(e.value)


def test_stale_hello_is_instructive(tmp_path):
    root = make_ns(tmp_path)
    c = LEditBridgeClient(root=root)
    old = time.time() - 120
    os.utime(c.hello_path, (old, old))
    assert not c.alive()
    with pytest.raises(LEditBridgeError) as e:
        c.call("ping")
    assert "Bridge Start" in str(e.value)


def test_call_roundtrip(live_bridge):
    c = live_bridge(lambda req: {
        "ok": True, "result": {"echo": req["cmd"],
                               "params": req["params"]}})
    r = c.call("ping", {"x": 1})
    assert r == {"echo": "ping", "params": {"x": 1}}


def test_error_envelope_surfaces_next_action(live_bridge):
    c = live_bridge(lambda req: {
        "ok": False, "error": {"code": "ERR_BRIDGE",
                               "message": "no visible cell",
                               "next_action": "open a cell"}})
    with pytest.raises(LEditBridgeError) as e:
        c.call("get_selection")
    assert e.value.next_action == "open a cell"


def test_timeout_is_instructive(tmp_path):
    root = make_ns(tmp_path)  # hello fresh but nobody answers
    c = LEditBridgeClient(root=root, poll_s=0.02)
    with pytest.raises(LEditBridgeError) as e:
        c.call("ping", timeout=0.2)
    assert "bridge.log" in str(e.value)


# --------------------------------------------------------------------------
# adapter
# --------------------------------------------------------------------------

LAYER_TABLE = [
    {"name": "Metal1", "gds_layer": 49, "gds_datatype": 0, "special": False},
    {"name": "Poly", "gds_layer": 46, "gds_datatype": -1, "special": False},
    {"name": "Handdrawn", "gds_layer": -1, "gds_datatype": -1,
     "special": False},
    {"name": "Grid Layer", "gds_layer": -1, "gds_datatype": -1,
     "special": True},
]


def test_build_layer_map_auto_assign_skips_special():
    mapping, auto = build_layer_map(LAYER_TABLE)
    assert mapping["Metal1"] == (49, 0)
    assert mapping["Poly"] == (46, 0)          # datatype -1 -> 0
    assert "Grid Layer" not in mapping          # special excluded
    assert auto == {"Handdrawn": 50}            # first free above used range
    assert mapping["Handdrawn"] == (50, 0)


def layer_of(name):
    mapping, _ = build_layer_map(LAYER_TABLE)
    return mapping.get(name, (999, 99))


def test_convert_capability_matching():
    route, item = convert_object(
        {"kind": "box", "layer": "Metal1", "bbox_um": [0, 0, 1, 1]},
        layer_of)
    assert (route, item["kind"], item["layer"]) == ("shape", "box", 49)

    route, item = convert_object(
        {"kind": "wire", "layer": "Poly", "width_um": 0.2,
         "points_um": [[0, 0], [1, 0]]}, layer_of)
    assert (route, item["kind"]) == ("shape", "path")

    route, item = convert_object(
        {"kind": "circle", "layer": "Metal1", "center_um": [1, 1],
         "radius_um": 0.5}, layer_of)
    assert (route, item["pcell"]) == ("pcell", "CIRCLE")

    # unknown kind with an outline degrades to polygon (never dropped)
    route, item = convert_object(
        {"kind": "torus", "layer": "Poly",
         "points_um": [[0, 0], [1, 0], [1, 1], [0, 1]]}, layer_of)
    assert (route, item["kind"]) == ("shape", "polygon")

    route, why = convert_object({"kind": "label", "layer": "Poly"}, layer_of)
    assert route == "fail" and "label" in why


def test_selection_to_items_reports_failures():
    shapes, pcells, failures = selection_to_items([
        {"kind": "box", "layer": "Metal1", "bbox_um": [0, 0, 1, 1]},
        {"kind": "circle", "layer": "Poly", "center_um": [0, 0],
         "radius_um": 1},
        {"kind": "ruler", "layer": "Poly"},
    ], layer_of)
    assert (len(shapes), len(pcells), len(failures)) == (1, 1, 1)


def test_harvest_boxes_sorted_integer_nm():
    got = harvest_boxes({"objects": [
        {"kind": "box", "layer": "A", "bbox_um": [1.0, 0, 2.0, 0.5]},
        {"kind": "box", "layer": "A", "bbox_um": [0, 0, 1.0, 0.5]},
        {"kind": "polygon", "layer": "B",
         "points_um": [[0, 0], [2, 0], [1, 3]]},
        {"kind": "instance", "cell": "child"},
    ]})
    assert got["A"] == [[0, 0, 1000, 500], [1000, 0, 2000, 500]]
    assert got["B"] == [[0, 0, 2000, 3000]]     # outline bbox


def test_nest_properties_rebuilds_tree():
    from klink.bridges.ledit import nest_properties
    flat = {"System": "<grp>", "System.Hide In Lists": True,
            "System.TCell Code": "code...",
            "A.B.C": 1, "A.B.D": 2, "plain": "x"}
    nested = nest_properties(flat)
    assert nested["System"][""] == "<grp>"          # group's own value kept
    assert nested["System"]["Hide In Lists"] is True
    assert nested["A"]["B"] == {"C": 1, "D": 2}     # deep nesting rebuilt
    assert nested["plain"] == "x"


def test_merge_layer_name_policy():
    assert merge_layer_name("", "Poly") == "Poly"          # fill empty
    assert merge_layer_name("Poly", "Poly") == "Poly"      # equal -> keep
    assert merge_layer_name("GanM1", "Metal1") == "GanM1|Metal1"  # append
    # idempotent: importing again never grows the name
    assert merge_layer_name("GanM1|Metal1", "Metal1") == "GanM1|Metal1"
    assert merge_layer_name("Poly", "") == "Poly"          # no incoming


# --------------------------------------------------------------------------
# tcell toolkit
# --------------------------------------------------------------------------

NFET_SNIPPET = '''
    LCell cellCurrent = (LCell)LMacro_GetNewTCell();
    double L = LCell_GetParameterAsDouble(cellCurrent, "L");
    LCoord lc_L = LCell_GetParameterAsCoord(cellCurrent, "L", 1.);
    double W = LCell_GetParameterAsDouble(cellCurrent, "W");
    int M = LCell_GetParameterAsInt(cellCurrent, "M");
'''


def test_parse_tcell_params():
    assert parse_tcell_params(NFET_SNIPPET) == {
        "L": "double", "W": "double", "M": "int"}


class FakeBridge:
    """Records instance_tcell calls; returns deterministic variant names."""

    def __init__(self):
        self.calls = []

    def call(self, cmd, params=None):
        self.calls.append((cmd, params))
        if cmd == "instance_tcell":
            return {"cell": f"V{len(self.calls)}",
                    "instance_name": "", "bbox_um": [0, 0, 1, 1]}
        return {}


def test_variant_factory_caches_by_params():
    b = FakeBridge()
    f = VariantFactory(b, "GEN")
    v1 = f.variant({"W": 5, "L": 2})
    v2 = f.variant({"L": 2, "W": 5})            # same params, any order
    v3 = f.variant({"L": 3, "W": 5})
    assert v1 == v2 and v1 != v3
    gen_calls = [c for c in b.calls if c[0] == "instance_tcell"]
    assert len(gen_calls) == 2                   # cache hit for v2


def test_verify_differential_byte_exact_and_mismatch():
    truth = lambda p: {"A": [[0, 0, int(p["W"] * 1000), 1000]]}
    good = lambda p: {"A": [[0, 0, int(p["W"] * 1000), 1000]]}
    off_by_one = lambda p: {"A": [[0, 0, int(p["W"] * 1000) + 1, 1000]]}

    r = verify_differential(good, truth, [{"W": 1}, {"W": 2.5}])
    assert r.all_ok and "ALL BYTE-EXACT" in r.summary()

    r = verify_differential(off_by_one, truth, [{"W": 1}])
    assert not r.all_ok
    assert "first diff" in r.summary()
