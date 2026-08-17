"""klink <-> L-Edit bridge test driver (standalone, no klink dependency).

Usage (after the macro is loaded in L-Edit):
    python driver.py ping
    python driver.py layers
    python driver.py demo        # layers + cell + box/polygon/wire/circle + 3x3 array
    python driver.py selection   # read whatever is selected in L-Edit right now
    python driver.py raw '{"cmd":"ping","params":{}}'

Importable too: `from driver import call` -> call(cmd, params) -> result
dict (raises on error envelopes, with the server's next_action text).
For real work prefer the supported client:
`from klink.bridges.ledit import LEditBridgeClient` (package installs as
`klayout-klink`, imports as `klink`).
"""
import json
import os
import sys
import time
import uuid

# KLINK_LEDIT_BRIDGE_ROOT relocates the exchange dir (sandbox / redirected
# profile / anywhere LOCALAPPDATA is not writable). The klink client and the
# macro both honour it, so all three ends agree.
_ROOT = os.environ.get("KLINK_LEDIT_BRIDGE_ROOT") or os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\klink_bridge"), "klink", "ledit_bridge")
NS = os.path.join(_ROOT, "default")
INBOX = os.path.join(NS, "inbox")
OUTBOX = os.path.join(NS, "outbox")
HELLO = os.path.join(NS, "hello.json")


def check_alive():
    if not os.path.exists(HELLO):
        raise SystemExit(
            f"no hello.json under {NS}\n"
            "-> is L-Edit running with the ledit_bridge.cpp macro loaded? "
            "(Tools > Macro > Load Macro... -> select the .cpp source)")
    age = time.time() - os.path.getmtime(HELLO)
    if age > 10:
        raise SystemExit(
            f"hello.json is stale ({age:.0f}s old)\n"
            "-> the bridge timer is not ticking. Most common cause: a MODAL "
            "DIALOG is open in L-Edit (error box, prompt) - close it. "
            "Otherwise: Tools > klink: Bridge Start")
    with open(HELLO, "r", encoding="utf-8") as f:
        return json.load(f)


def call(cmd, params=None, timeout=10.0):
    check_alive()
    rid = "req_" + uuid.uuid4().hex[:12]
    req = {"schema": 1, "id": rid, "cmd": cmd, "params": params or {}}
    tmp = os.path.join(INBOX, rid + ".json.tmp")
    final = os.path.join(INBOX, "req_" + rid + ".json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f)
    os.replace(tmp, final)

    resp_path = os.path.join(OUTBOX, "resp_" + rid + ".json")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(resp_path):
            time.sleep(0.05)  # let the atomic rename settle
            with open(resp_path, "r", encoding="utf-8") as f:
                resp = json.load(f)
            os.remove(resp_path)
            if not resp.get("ok"):
                err = resp.get("error", {})
                raise RuntimeError(
                    f"{cmd} failed: {err.get('message')}\n"
                    f"next_action: {err.get('next_action')}")
            return resp["result"]
        time.sleep(0.1)
    raise SystemExit(
        f"timeout waiting for response to {cmd} ({rid})\n"
        "-> check hello.json freshness, the L-Edit UI (dialog open blocks "
        "timers), and bridge.log in the namespace dir")


def demo():
    st = call("ping")
    print("ping ->", st)
    # NEVER draw into whatever design happens to be open (it may be the
    # user's). The demo creates its own scratch design and hard-verifies
    # the active design actually switched before touching anything.
    before = st.get("file", "")
    nd = call("new_design", {"name": "klink_demo"})
    print("new_design ->", nd)
    target = call("ping").get("file", "")
    if not target or target == before:
        raise SystemExit(
            f"demo aborted: new_design did not switch the active design "
            f"(still '{before or 'none'}').\n"
            "-> refusing to draw into a design the demo does not own. "
            "Update/reload the bridge macro (>=0.5.1), or switch to the "
            "new design in L-Edit and re-run.")
    guard = {"expect_file": target}
    for name, gl, rgb in [("WL", 1, [40, 110, 255]),
                          ("JUNC", 2, [255, 60, 60]),
                          ("BL", 3, [60, 200, 90])]:
        print(f"ensure_layer {name} ->", call(
            "ensure_layer", dict(guard, name=name, gds_layer=gl,
                                 gds_datatype=0)))
        print(f"set_layer_style {name} ->", call(
            "set_layer_style", dict(guard, layer=name, fill_rgb=rgb)))
    print("create_cell ->", call(
        "create_cell", dict(guard, name="klink_demo_unit")))
    items = [
        {"kind": "box", "layer": "WL", "bbox_um": [0, 0.4, 2.0, 0.6]},
        {"kind": "polygon", "layer": "JUNC",
         "points_um": [[0.8, 0.3], [1.2, 0.3], [1.2, 0.7], [0.8, 0.7]]},
        {"kind": "wire", "layer": "BL", "width_um": 0.2,
         "points_um": [[1.0, 0], [1.0, 1.0]]},
        {"kind": "circle", "layer": "JUNC", "center_um": [1.0, 0.5],
         "radius_um": 0.1},
    ]
    print("draw ->", call(
        "draw", dict(guard, cell="klink_demo_unit", items=items)))
    print("create_cell ->", call(
        "create_cell", dict(guard, name="klink_demo_top")))
    print("place_instance ->", call("place_instance", dict(
        guard, cell="klink_demo_top", child="klink_demo_unit",
        x_um=0, y_um=0, nx=3, ny=3, dx_um=3.0, dy_um=2.0)))
    print("\nDemo OK -> open cell klink_demo_top in L-Edit to inspect "
          f"(scratch design '{target}', your own design was not touched).")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    verb = sys.argv[1]
    if verb == "ping":
        print(json.dumps(call("ping"), indent=2))
    elif verb == "layers":
        print(json.dumps(call("get_layers"), indent=2))
    elif verb == "selection":
        print(json.dumps(call("get_selection"), indent=2))
    elif verb == "demo":
        demo()
    elif verb == "cell":
        print(json.dumps(call("get_cell", {"cell": sys.argv[2]}), indent=2))
    elif verb == "clear":
        print(json.dumps(call("clear_cell", {"cell": sys.argv[2]}), indent=2))
    elif verb == "style":
        # python driver.py style WL 40 110 255
        layer, r, g, b = sys.argv[2], *map(int, sys.argv[3:6])
        print(json.dumps(call("set_layer_style",
                              {"layer": layer, "fill_rgb": [r, g, b]}),
                         indent=2))
    elif verb == "raw":
        req = json.loads(sys.argv[2])
        print(json.dumps(call(req["cmd"], req.get("params")), indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
