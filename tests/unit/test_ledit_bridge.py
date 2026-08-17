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


def test_load_instructions_carry_the_absolute_macro_path(tmp_path):
    # A blind-test agent had to hunt for a 90-character path because the
    # message only named the file. The user picks it in a GUI dialog.
    from klink.bridges.ledit.client import bundled_macro_path
    macro = bundled_macro_path()
    assert os.path.isfile(macro), "packaged macro missing: %s" % macro

    c = LEditBridgeClient(root=make_ns(tmp_path, hello=False))
    with pytest.raises(LEditBridgeError) as e:
        c.call("ping")
    assert macro in e.value.next_action
    assert "not enough" in e.value.next_action     # running != loaded


def test_user_facing_messages_stay_ascii(tmp_path):
    # Windows consoles (cp936/cp1252) render an em dash as '?', which a
    # blind-test agent duly reported as unreadable guidance.
    c = LEditBridgeClient(root=make_ns(tmp_path, hello=False))
    with pytest.raises(LEditBridgeError) as e:
        c.call("ping")
    text = str(e.value) + e.value.next_action + json.dumps(c.status())
    offenders = sorted({ch for ch in text if ord(ch) > 127})
    assert not offenders, "non-ASCII in user-facing text: %s" % offenders


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


def test_new_design_refuses_when_active_design_unchanged(live_bridge):
    # macro < 0.5.1 could create the design yet leave the user's design
    # active (all writes would land there) — the client must refuse.
    def handler(req):
        if req["cmd"] == "ping":
            return {"ok": True, "result": {"file": "user_design.tdb"}}
        return {"ok": True, "result": {"file": "scratch"}}
    c = live_bridge(handler)
    with pytest.raises(LEditBridgeError) as e:
        c.new_design("scratch")
    assert "0.5.1" in str(e.value)
    assert "user_design.tdb" in str(e.value)


def test_new_design_ok_when_active_design_switches(live_bridge):
    state = {"file": "user_design.tdb"}

    def handler(req):
        if req["cmd"] == "new_design":
            state["file"] = req["params"]["name"]
            return {"ok": True, "result": {"file": state["file"]}}
        return {"ok": True, "result": {"file": state["file"]}}
    c = live_bridge(handler)
    out = c.new_design("scratch")
    assert out["file"] == "scratch"


def test_open_design_reopening_active_design_is_fine(live_bridge):
    c = live_bridge(lambda req: {
        "ok": True, "result": {"file": "user_design.tdb"}})
    out = c.open_design(r"C:\x\user_design.tdb")
    assert out["file"] == "user_design.tdb"


def test_timeout_is_instructive(tmp_path):
    root = make_ns(tmp_path)  # hello fresh but nobody answers
    c = LEditBridgeClient(root=root, poll_s=0.02)
    with pytest.raises(LEditBridgeError) as e:
        c.call("ping", timeout=0.2)
    assert "bridge.log" in str(e.value)


def _poly_item(k, npts=24):
    """A polygon shaped like flattened layout output — the payload that
    actually blows the request cap (250 of these measured 104 KiB live,
    while 250 boxes stay under it)."""
    return {"kind": "polygon", "layer": "M1",
            "points_um": [[k + i * 0.11, k + i * 0.13] for i in range(npts)]}


def test_oversize_request_fails_fast_without_touching_inbox(tmp_path):
    # The macro would neither answer nor delete such a request: it stalls
    # the caller for a full timeout and leaves a file re-read every tick.
    root = make_ns(tmp_path)
    c = LEditBridgeClient(root=root, poll_s=0.02)
    huge = {"cell": "TOP", "items": [_poly_item(i) for i in range(400)]}
    with pytest.raises(LEditBridgeError) as e:
        c.call("draw", huge, timeout=0.2)
    assert e.value.code == "ERR_REQUEST_TOO_LARGE"
    assert "KiB" in str(e.value)
    assert os.listdir(c.inbox) == []          # nothing left behind


def test_draw_chunks_oversize_payload_and_sums_results(live_bridge):
    seen = []

    def handler(req):
        n = len(req["params"]["items"])
        seen.append(n)
        return {"ok": True, "result": {"drawn": n, "layers_created": 1,
                                       "cell": req["params"].get("cell")}}

    c = live_bridge(handler)
    items = [_poly_item(i) for i in range(400)]
    out = c.draw(items, cell="TOP", expect_file="unit.tdb")

    assert out["requests"] > 1                # actually split
    assert out["drawn"] == 400                # nothing lost or double-counted
    assert out["layers_created"] == out["requests"]
    assert sum(seen) == 400
    assert out["cell"] == "TOP"


def test_bound_guard_rides_on_every_command(live_bridge):
    # The convenience wrappers have no room for expect_file, which used to
    # push callers back to raw call() with hand-built dicts — and one
    # forgotten guard is a write into the user's own design.
    seen = []

    def handler(req):
        seen.append((req["cmd"], req["params"].get("expect_file")))
        return {"ok": True, "result": {"cells": [], "layers": []}}

    c = live_bridge(handler).bind_file("mine.tdb")
    c.ensure_layer("MET1", 68, 20)
    c.create_cell("TOP")
    c.clear_cell("TOP")
    c.list_cells()
    assert seen == [("ensure_layer", "mine.tdb"), ("create_cell", "mine.tdb"),
                    ("clear_cell", "mine.tdb"), ("list_cells", "mine.tdb")]


def test_bound_guard_rides_on_every_batch_op(live_bridge):
    # Each op resolves its own target design inside the macro, so guarding
    # only the envelope would leave the ops unguarded.
    seen = []

    def handler(req):
        for op in req["params"]["ops"]:
            seen.append((op["cmd"], op["params"].get("expect_file")))
        return {"ok": True, "result": {"results": [], "completed": 0}}

    c = live_bridge(handler).bind_file("mine.tdb")
    c.batch([("create_cell", {"name": "A"}),
             ("draw", {"cell": "A", "items": [{"kind": "box", "layer": "M1",
                                               "bbox_um": [0, 0, 1, 1]}]})])
    assert seen == [("create_cell", "mine.tdb"), ("draw", "mine.tdb")]


def test_explicit_guard_wins_over_the_bound_one(live_bridge):
    seen = []

    def handler(req):
        seen.append(req["params"].get("expect_file"))
        return {"ok": True, "result": {}}

    c = live_bridge(handler).bind_file("mine.tdb")
    c.call("create_cell", {"name": "A", "expect_file": "other.tdb"})
    assert seen == ["other.tdb"]


def test_bind_active_refuses_when_no_design_is_open(live_bridge):
    c = live_bridge(lambda req: {"ok": True, "result": {"file": ""}})
    with pytest.raises(LEditBridgeError) as e:
        c.bind_active()
    assert "new_design" in e.value.next_action


def test_import_gds_pads_to_the_2048_byte_block(live_bridge, tmp_path):
    # L-Edit's reader wants the classic Calma block padding; KLayout does not
    # write it. Unpadded, the import aborts at EOF behind a MODAL dialog and
    # leaves empty cell shells (observed live, 40 s frozen bridge).
    sent = {}

    def handler(req):
        sent.update(req["params"])
        return {"ok": True, "result": {"cells_added": 3}}

    c = live_bridge(handler)
    gds = tmp_path / "hier.gds"
    gds.write_bytes(b"X" * 466)
    out = c.import_gds(str(gds))

    assert out["padded_copy"].endswith(".ledit.gds")
    assert os.path.getsize(out["padded_copy"]) % 2048 == 0
    assert sent["path"] == out["padded_copy"]
    assert gds.read_bytes() == b"X" * 466        # caller's file untouched


def test_import_gds_leaves_an_aligned_file_alone(live_bridge, tmp_path):
    c = live_bridge(lambda req: {"ok": True, "result": {"cells_added": 1}})
    gds = tmp_path / "aligned.gds"
    gds.write_bytes(b"X" * 4096)
    out = c.import_gds(str(gds))
    assert "padded_copy" not in out


def test_draw_rejects_a_single_item_too_big_to_ever_send(live_bridge):
    c = live_bridge(lambda req: {"ok": True, "result": {"drawn": 0}})
    with pytest.raises(LEditBridgeError) as e:
        c.draw([_poly_item(0, npts=4000)], cell="TOP")
    assert e.value.code == "ERR_REQUEST_TOO_LARGE"
    assert "GDS" in e.value.next_action       # names the real escape route


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


def test_verify_differential_reexported_from_bridge():
    # canonical home moved to klink.domains.structdevice.pcell_diff
    # (harness tests live in tests/public/test_pcell_diff.py); the bridge
    # keeps the name as a backward-compatible re-export.
    from klink.domains.structdevice.pcell_diff import (
        verify_differential as canonical)
    assert verify_differential is canonical
