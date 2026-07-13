"""Stage-by-stage "three ways to get a Port" tutorial capture.

Mirrors examples_klink/public/demos/photonics/gf_ports.py's five scenarios
(hand-drawn -> Port, the same recipe drawn WRONG, gdsfactory's own ports,
a synthetic blackbox harvest, and the route/SEND/drag/reroute loop) -- the
exact sequence the starter demo runs with no flag -- but stops to
screenshot at points the demo itself never exposes as a return value, and
goes one step further than the shipped demo by actually PERFORMING the
"drag device A, then re-route" loop the demo's own docstring only tells a
human to do by hand in the GUI.

Reuse policy (CLAUDE.md: "a tutorial is a step-by-step expansion of its
demo, not a separate artifact"): scenarios 3 (gdsfactory's own ports) and 4
(blackbox harvest) call the demo's OWN `scenario_gf_auto` /
`scenario_blackbox` functions directly -- they already return the final
state we want to screenshot, so there is nothing to replicate. Scenarios 1,
2, and 5's "before conversion" / "before route" states are NOT returned by
the demo's `scenario_handdrawn` / `scenario_handdrawn_wrong` /
`scenario_route_loop` functions (each builds + converts/routes in one
call), so this script replicates their build steps verbatim -- same
literal coordinates, same constants imported FROM the demo module -- and
inserts a screenshot in the middle. The actual conversion/routing calls
(`recognize_handdrawn_ports`, `place_gdsfactory_components`,
`route_gdsfactory_ports`) are imported and called exactly as the demo
calls them, never reimplemented.

Two exec.python escape hatches, both justified the same way
tools/tutorial_capture/gf_mzi_module/draw_gf_mzi_tutorial.py's drag
simulation is (see its docstring + CLAUDE.md's "Prefer typed RPCs" rule --
no typed RPC repositions an ALREADY-PLACED shape or instance; `port.transform`
can edit a Port's orientation/width/net/etc but its params_schema has no
x/y field, see klink_plugin/.../methods/port_m.py):

    - moving device A's two waveguide boxes + its recognized Port instance
      P0 up by 20um, to simulate a GUI drag (gf-9-moved.png / gf-10-rerouted.png)

This script owns its own disposable tab lifecycle end to end (same pattern
as gf_mzi_module's capture script): opens ONE fresh tab via the typed
`view.new_tab` RPC, builds every scenario's cell as a sibling cell inside
it (exactly how the demo's own `main()` does it -- only scenario 1 opens a
tab; scenarios 2-5 just `cell_create` into whatever tab is already active),
screenshots throughout, then closes that tab and restores whatever tab was
current beforehand.

Unlike the OTHER tools/tutorial_capture/*/draw_*.py scripts (which default
to test_outputs/tutorial_capture/<name>/ and leave publishing to a manual
copy step), this one writes PNGs DIRECTLY into the docs website checkout's
asset directory by default -- that is what this capture job's own
instructions specified, and view.screenshot's mode="path" already writes
straight to an absolute path on disk (same machine as the KLayout process
in this dev setup). Override --out-dir if your website checkout is not at
the default path. Nothing here writes or touches website HTML.

Run against a live KLayout (klink plugin loaded) with gdsfactory in this
interpreter:

    python tools/tutorial_capture/gf_ports/draw_gf_ports_tutorial.py [--port 8765]
        [--out-dir D:\\klink_website\\assets\\tutorials\\gf-ports]
        [--skip-send-capture]

See tools/tutorial_capture/gf_ports/README.md for the gf-7-send*.png window
screenshot's Windows/maximized-window precondition.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# tools/tutorial_capture/gf_ports/draw_gf_ports_tutorial.py -> repo root is 3
# parents up (gf_ports/ -> tutorial_capture/ -> tools/ -> repo root). Needed
# on sys.path so `examples_klink.public.demos.*` (a repo-only module, not
# part of the installed klink package) is importable.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from klink import KLinkClient
from klink.port.workflow import recognize_handdrawn_ports
from klink.routing.backends.gdsfactory.gdsfactory_components import (
    place_gdsfactory_components,
)
from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports
from examples_klink.public.demos.photonics.gf_ports import (
    CELL_HAND, CELL_WRONG, CELL_AUTO, CELL_BB_CHILD, CELL_BB, CELL_LOOP,
    WG_LAYER, PORT_LAYER, ROUTE_LAYER, WG_WIDTH_UM,
    scenario_gf_auto, scenario_blackbox,
)

# Default output: the docs website checkout's asset directory (see module
# docstring for why this differs from the other tutorial_capture scripts).
# Override with --out-dir if the website checkout lives elsewhere.
DEFAULT_OUT = Path(r"D:\klink_website\assets\tutorials\gf-ports")

SEND_SCRIPT = Path(__file__).resolve().parent / "capture_send_window.ps1"

# Same box for the "before route" / "after 40um straight route" pair so a
# reader flips between gf-6/gf-8 and sees the SAME pixels connect.
LOOP_BBOX_UM = (-64.0, -3.0, 18.0, 3.0)
LOOP_PX = (1300, 360)

# Taller box for the "after +20um drag" / "after 60um step reroute" pair --
# needs to include the raised device A.
MOVE_BBOX_UM = (-64.0, -4.0, 18.0, 24.0)
MOVE_PX = (1200, 560)

# zoom_box used for the gf-7-send.png window screenshot (framed so the
# SEND'd link0 ports -- device A's P0 and the MMI's o1 -- are both on
# screen and readable).
SEND_ZOOM_UM = (-54.0, -4.0, -6.0, 4.0)
# selection.set_box's bbox_dbu -- integer DBU at the tab's dbu=0.001, i.e.
# um * 1000 -- covering both port centers ((-50,0) and (-10,0)) with a
# little y margin.
SEND_SELECTION_BBOX_DBU = [-51000, -1200, -9000, 1200]


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765,
                         help="klink RPC port of the live KLayout session (default: 8765)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT),
                         help="Directory to write PNGs into (default: %(default)s)")
    parser.add_argument(
        "--skip-send-capture", action="store_true",
        help="Skip the gf-7-send.png / gf-7-send-en.png full-window "
             "screenshot step (it needs a visible KLayout window maximized "
             "to exactly 1550x838 on THIS machine -- see README.md). All "
             "other PNGs are unaffected.",
    )
    return parser.parse_args()


def verify_tab(client, index):
    """Screenshot iron rule: verify the CURRENT tab is the disposable one we
    created, every time, right before we touch the view. Any pre-existing
    tab is the user's own session and must never be acted on."""
    tabs = client.call("view.list_tabs", {})
    cur = tabs["tabs"][tabs["current_index"]]
    assert tabs["current_index"] == index, (
        f"current tab index is {tabs['current_index']!r}, expected our "
        f"disposable tab at index {index!r} ({cur!r}) -- refusing "
        f"to act on a tab we did not create"
    )
    return cur


def snap(client, index, out_dir, name, bbox_um, width_px, height_px):
    """Always exact bbox_um clip (no aspect-ratio expansion) via mode="path"
    -- writes the PNG straight to out_dir/name, no base64 round trip."""
    verify_tab(client, index)
    path = os.path.join(out_dir, name)
    result = client.screenshot(
        mode="path", path=path, bbox_um=list(bbox_um),
        width_px=width_px, height_px=height_px,
    )
    print("    saved %s (%dx%d, %d bytes)"
          % (result["path"], result["width_px"], result["height_px"], result["bytes"]))
    return result


# ---------------------------------------------------------------------------
# Scenario 1 -- hand-drawn triangle marker -> recognized Port
# Replicates scenario_handdrawn()'s build steps verbatim (same literal
# coordinates/constants imported from the demo) because that function builds
# + recognizes in one call with no "before recognize" state to reuse.
# ---------------------------------------------------------------------------
def capture_scenario1(client, index, out_dir):
    print("\n[1] hand-drawn triangle -> Port: before/after conversion")
    client.cell_create(CELL_HAND)
    si = client.layer_ensure(1, 0)["layer_index"]
    pl = client.layer_ensure(999, 99)["layer_index"]
    w = WG_WIDTH_UM

    client.shape_insert_boxes(CELL_HAND, layer_index=si, boxes_um=[
        [-6, -2, 6, 2],
        [-10, -w / 2, -6, w / 2],
        [6, -w / 2, 10, w / 2],
    ])
    client.shape_insert_many(CELL_HAND, [
        {"kind": "polygon", "layer_index": pl,
         "points_um": [[-11, 0.0], [-10, -w / 2], [-10, w / 2]]},
        {"kind": "polygon", "layer_index": pl,
         "points_um": [[11, 0.0], [10, w / 2], [10, -w / 2]]},
    ])
    client.show_cell(CELL_HAND, zoom_fit=True)
    snap(client, index, out_dir, "gf-1-handdrawn.png", (-13, -4, 13, 4), 1300, 620)

    result = recognize_handdrawn_ports(
        client, CELL_HAND, layer=PORT_LAYER, direction_guess="long_edge",
        port_type="optical", delete_markers=True,
    )
    print("    recognized %d port(s), deleted %d marker(s)"
          % (result["recognized"], result["deleted_markers"]))
    snap(client, index, out_dir, "gf-2-converted.png", (-13, -4, 13, 4), 1300, 620)
    return result


# ---------------------------------------------------------------------------
# Scenario 2 -- the SAME recipe, drawn WRONG (cautionary, on purpose)
# ---------------------------------------------------------------------------
def capture_scenario2(client, index, out_dir):
    print("\n[2] WRONG hand-drawn marker: before/after conversion (cautionary)")
    client.cell_create(CELL_WRONG)
    si = client.layer_ensure(1, 0)["layer_index"]
    pl = client.layer_ensure(999, 99)["layer_index"]
    w = WG_WIDTH_UM

    client.shape_insert_boxes(CELL_WRONG, layer_index=si, boxes_um=[[-10, -w / 2, 0, w / 2]])
    client.shape_insert_many(CELL_WRONG, [
        {"kind": "polygon", "layer_index": pl,
         "points_um": [[1.5, 0.0], [0.0, -1.25], [0.0, 1.25]]},
    ])
    client.show_cell(CELL_WRONG, zoom_fit=True)
    snap(client, index, out_dir, "gf-3-err-before.png", (-11, -3.5, 3.5, 3.5), 900, 620)

    result = recognize_handdrawn_ports(
        client, CELL_WRONG, layer=PORT_LAYER, direction_guess="long_edge",
        port_type="optical", delete_markers=True,
    )
    snap(client, index, out_dir, "gf-3-err-after.png", (-11, -3.5, 3.5, 3.5), 900, 620)
    return result


# ---------------------------------------------------------------------------
# Scenario 3 -- gdsfactory's own ports. The demo function already returns
# the exact "after" state we want, so just call it and screenshot.
# ---------------------------------------------------------------------------
def capture_scenario3(client, index, out_dir, problems):
    print("\n[3] gdsfactory's own ports (mmi1x2) -- reusing demo scenario_gf_auto()")
    s3 = scenario_gf_auto(client, problems)
    snap(client, index, out_dir, "gf-4-auto.png", (-15, -2.6, 20.5, 2.6), 1100, 720)
    return s3


# ---------------------------------------------------------------------------
# Scenario 4 -- synthetic blackbox harvest. Same reuse story as scenario 3.
# ---------------------------------------------------------------------------
def capture_scenario4(client, index, out_dir, problems):
    print("\n[4] synthetic blackbox harvest -- reusing demo scenario_blackbox()")
    s4 = scenario_blackbox(client, problems)
    snap(client, index, out_dir, "gf-5-blackbox.png", (-6.25, -5, 6.25, 5), 1000, 740)
    return s4


# ---------------------------------------------------------------------------
# Scenario 5 -- route loop: build -> before-route -> SEND -> route -> drag -> reroute
# Replicates scenario_route_loop()'s build steps up to (but not including)
# its route_gdsfactory_ports() call verbatim, for the same "no exposed
# before-route state" reason as scenario 1/2 above. The route/reroute calls
# themselves import and call route_gdsfactory_ports exactly as the demo does.
# ---------------------------------------------------------------------------
MOVE_DEVICE_A_CODE = """
top_cell = layout.cell(%(cell)r)
dbu = layout.dbu
wg_li = layout.layer(1, 0)
dy_dbu = int(round(20.0 / dbu))

# Device A's two waveguide boxes both have center.x well below -46um; the
# MMI's own geometry lives inside its OWN child device cell (an instance of
# CELL_LOOP), not as direct shapes of CELL_LOOP, so this filter never
# touches it.
shapes_to_move = []
moved_shapes_cx = []
for shape in top_cell.shapes(wg_li).each():
    bbox = shape.bbox()
    cx_um = (bbox.left + bbox.right) / 2.0 * dbu
    if cx_um < -46.0:
        shapes_to_move.append(shape)
        moved_shapes_cx.append(cx_um)
for shape in shapes_to_move:
    shape.transform(pya.Trans(0, dy_dbu))

# Port P0 (device A's recognized hand-drawn port) sits at (-50, 0) -- the
# ONLY instance at that position (the MMI's own Port instances are marked
# at their own component-relative coordinates, all >= -10 in x).
moved_ports = []
for inst in list(top_cell.each_inst()):
    t = inst.dcplx_trans
    if abs(t.disp.x - (-50.0)) > 0.5 or abs(t.disp.y - 0.0) > 0.5:
        continue
    new_t = pya.DCplxTrans(t.mag, t.angle, t.mirror, t.disp + pya.DVector(0.0, 20.0))
    inst.dcplx_trans = new_t
    moved_ports.append([t.disp.x, t.disp.y, new_t.disp.x, new_t.disp.y])

(moved_shapes_cx, moved_ports)
""" % {"cell": CELL_LOOP}


def _move_device_a(client):
    """Simulate a GUI drag of device A by +20um in y. No typed RPC
    repositions an ALREADY-PLACED shape or instance in place --
    `port.transform` can edit a Port's orientation/width/net/etc but its
    params_schema has no x/y field (klink_plugin/.../methods/port_m.py) --
    so this is the same justified exec.python escape hatch
    gf_mzi_module/draw_gf_mzi_tutorial.py uses for its own drag simulation."""
    print("    moving device A +20um in y (simulated drag, exec.python)")
    result = client.exec_python(MOVE_DEVICE_A_CODE)
    if result.get("exception") is not None:
        raise RuntimeError("move exec.python failed: %s" % (result["exception"],))
    moved_shapes_cx, moved_ports = result["return_value"]
    print("    moved %d device-A shape(s) (cx_um=%s), %d Port instance(s): %s"
          % (len(moved_shapes_cx), moved_shapes_cx, len(moved_ports), moved_ports))
    if len(moved_shapes_cx) != 2:
        raise RuntimeError("expected to move 2 device-A shapes, moved %d" % len(moved_shapes_cx))
    if len(moved_ports) != 1:
        raise RuntimeError("expected to move exactly 1 Port instance (P0), moved %d" % len(moved_ports))


def capture_send_window(client, out_dir, args):
    """gf-7-send.png / gf-7-send-en.png: a full-window screenshot with a
    hand-annotated SEND callout, NOT a view.screenshot canvas clip (the SEND
    button and the cell tree sidebar are outside the layout canvas that
    view.screenshot renders). Uses the verified PowerShell helpers this
    directory ships (see README.md): foreground+CopyFromScreen (PrintWindow
    returns a stale cached frame for an occluded Qt GL canvas), then a JSON-
    driven annotate step for the CN/EN callout text, then a crop to the
    toolbar+sidebar band."""
    if args.skip_send_capture:
        print("    --skip-send-capture: leaving gf-7-send*.png untouched")
        return
    print("    SEND capture: zoom_box + selection_set_box + selection.send_context")
    client.zoom_box(bbox_um=list(SEND_ZOOM_UM))
    client.selection_set_box(CELL_LOOP, SEND_SELECTION_BBOX_DBU)
    send_result = client.call("selection.send_context", {})
    print("    selection.send_context ->", send_result)

    # This dev machine routinely has SEVERAL KLayout windows open at once
    # (multiple klink sessions/ports), often at the same maximized size, so
    # window TITLE/size alone cannot disambiguate which one is the process
    # behind --port. Ask the live server for its own OS process id and pass
    # it to the PowerShell side as the authoritative window filter.
    pid_result = client.exec_python("import os\nos.getpid()")
    if pid_result.get("exception") is not None:
        raise RuntimeError("could not read target process id: %s" % (pid_result["exception"],))
    target_pid = pid_result["return_value"]
    print("    target KLayout process id: %s" % (target_pid,))

    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(SEND_SCRIPT), "-OutDir", str(out_dir),
        "-TargetPid", str(target_pid),
    ]
    print("    running:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        print("    WARNING: gf-7-send.png / gf-7-send-en.png NOT regenerated "
              "(window capture step failed, see above). Re-run with the "
              "KLayout window visible and maximized to exactly 1550x838, or "
              "pass --skip-send-capture to skip this step deliberately.")
    else:
        print("    wrote gf-7-send.png / gf-7-send-en.png")


def capture_scenario5(client, index, out_dir, args):
    print("\n[5] route loop: build -> before-route -> SEND -> route -> drag -> reroute")
    si = client.layer_ensure(1, 0)["layer_index"]
    pl = client.layer_ensure(999, 99)["layer_index"]

    client.cell_create(CELL_LOOP)
    client.shape_insert_boxes(CELL_LOOP, layer_index=si, boxes_um=[
        [-62, -2, -54, 2],
        [-54, -0.25, -50, 0.25],
    ])
    client.shape_insert_many(CELL_LOOP, [
        {"kind": "polygon", "layer_index": pl,
         "points_um": [[-49, 0.0], [-50, 0.25], [-50, -0.25]]},
    ])
    recognize_handdrawn_ports(
        client, CELL_LOOP, layer=PORT_LAYER, direction_guess="long_edge",
        port_type="optical", net="link0", delete_markers=True,
    )
    place_gdsfactory_components(
        client, CELL_LOOP,
        [{
            "id": "MMI", "component": "mmi1x2", "center_um": [0, 0], "rotation": 0,
            "params": {}, "port_nets": {"o1": "link0", "o2": "outA", "o3": "outB"},
        }],
        target_layer=WG_LAYER, port_layer=PORT_LAYER, clear=False,
    )
    client.show_cell(CELL_LOOP, zoom_fit=True)
    snap(client, index, out_dir, "gf-6-beforeroute.png", LOOP_BBOX_UM, *LOOP_PX)

    capture_send_window(client, out_dir, args)

    report = route_gdsfactory_ports(
        client, CELL_LOOP, port_layer=PORT_LAYER, route_layer=ROUTE_LAYER,
        clear=True, all_two_port_nets=True,
    )
    client.show_cell(CELL_LOOP, zoom_fit=True)
    routes = report.get("routes", [])
    print("    routed %d net(s): %s"
          % (len(routes), [(r.get("source"), r.get("target"), r.get("length_um")) for r in routes]))
    snap(client, index, out_dir, "gf-8-routed.png", LOOP_BBOX_UM, *LOOP_PX)

    _move_device_a(client)
    snap(client, index, out_dir, "gf-9-moved.png", MOVE_BBOX_UM, *MOVE_PX)

    report2 = route_gdsfactory_ports(
        client, CELL_LOOP, port_layer=PORT_LAYER, route_layer=ROUTE_LAYER,
        clear=True, all_two_port_nets=True,
    )
    client.show_cell(CELL_LOOP, zoom_fit=True)
    routes2 = report2.get("routes", [])
    print("    re-routed %d net(s): %s"
          % (len(routes2), [(r.get("source"), r.get("target"), r.get("length_um")) for r in routes2]))
    snap(client, index, out_dir, "gf-10-rerouted.png", MOVE_BBOX_UM, *MOVE_PX)

    return report, report2


def main():
    args = _parse_args()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    problems: list[str] = []
    with KLinkClient(port=args.port).connect() as client:
        new_tab = client.new_tab(cell_name="TOP")
        our_index = new_tab["index"]
        previous_index = new_tab["previous_current_index"]
        print("opened disposable tab:", new_tab.get("title"), "index", our_index)

        try:
            capture_scenario1(client, our_index, out_dir)
            capture_scenario2(client, our_index, out_dir)
            capture_scenario3(client, our_index, out_dir, problems)
            capture_scenario4(client, our_index, out_dir, problems)
            capture_scenario5(client, our_index, out_dir, args)
        finally:
            client.call("view.close_tab", {"view_index": our_index})
            if previous_index != -1:
                client.call("view.activate_tab", {"index": previous_index})
                print("restored previous tab index", previous_index)
            else:
                print("no previous tab to restore (none was open)")

    print("\n==================== summary ====================")
    print("wrote 10 canvas PNGs (+ gf-7-send.png / gf-7-send-en.png unless "
          "--skip-send-capture) to", out_dir)
    if problems:
        print("\n%d check(s) FAILED:" % len(problems))
        for problem in problems:
            print("  -", problem)
        return 1
    print("all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
