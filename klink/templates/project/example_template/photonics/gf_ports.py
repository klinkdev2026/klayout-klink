"""Three ways to get a klink Port, plus the route/drag/reroute loop.

A photonic layout mixes devices from very different sources: a shape you
drew by hand, a foundry-style blackbox cell that only exposes a waveguide
stub convention, and a component that already speaks gdsfactory's own
port protocol. Before anything can be routed, ALL of them need to become
the same thing on the KLayout side: a klink Port marker (a small PCell on
``999/99`` carrying center/orientation/width/net). This demo builds one
of each, in the same layout tab, then routes two of them together and
shows the drag -> ``--reroute`` loop.

    1. hand-drawn -> Port (custom device). You draw a 3-point triangle
       marker next to your own waveguide shape -- base = port width,
       apex = direction the port faces -- and
       ``klink.port.workflow.recognize_handdrawn_ports`` turns it into a
       standard Port. This is the general-purpose escape hatch for any
       device that has no other port convention at all.

    2. the SAME recipe, drawn WRONG (cautionary, kept on purpose). The
       triangle's base is supposed to equal the waveguide width; here it
       is drawn far too wide (2.5 um base against a 0.8 um guide). The
       recognizer still returns *a* port -- it does not know your
       waveguide width -- but the numbers are visibly off (width and
       orientation both wrong) and the port is not edge-attached. This
       scenario is printed with a WRONG label and a warning so the
       lesson survives a copy-paste of this script.

    3. gdsfactory's OWN ports (standard component). Placing a real
       gdsfactory component (``mmi1x2``) through
       ``place_gdsfactory_components`` needs no hand-drawn markers at
       all -- gdsfactory already knows where its ports are, and this
       helper reads them straight off the component and marks klink
       Ports at the same coordinates.

    4. a synthetic blackbox cell, harvested by STUB CONVENTION. Many
       foundry PDK blackbox cells mark their optical ports as small
       stub boxes on the waveguide layer at the cell boundary (here:
       0.5x0.5 um stubs on the same waveguide layer as scenario 1/2,
       sitting on a body drawn on a separate layer). No markers, no
       gdsfactory API -- ``klink.domains.photonics.blackbox
       .harvest_instance_ports`` derives ports straight from the live
       instance geometry, so dragging the instance in the GUI and
       re-harvesting always tracks the truth.

    5. route two of them together, then DRAG and ``--reroute``. The
       hand-drawn port from scenario 1's recipe (built again, on its own
       net) and the MMI's ``o1`` port share net ``link0``; one
       ``route_gdsfactory_ports`` call draws a straight 40 um guide
       between them. Building the whole module again always snaps every
       device back to its scripted position -- so to keep an edit, drag
       the device in the KLayout GUI and re-run THIS script with
       ``--reroute``: it re-reads the (now dragged) Port positions and
       re-routes WITHOUT rebuilding anything.

Run against a live KLayout (klink plugin) with gdsfactory in this
interpreter (see Requirements below if that is not your setup):

    python example_template/photonics/gf_ports.py [--port 8765]

Then drag the custom device (or the MMI) in scenario 5's cell
(``GF_PORTS_LOOP``) in the KLayout GUI, and re-route from the new
position WITHOUT rebuilding:

    python example_template/photonics/gf_ports.py --port 8765 --reroute

Every geometric value in this script (layer numbers, widths, distances)
is a synthetic number owned by the script itself, not a foundry process
fact -- klink ships no process data of its own; see CLAUDE.md's "klink
process purity" rule if you are adapting this for a real PDK.

## Requirements -- read this if the demo won't run / draws wrong

This script is a klink RPC *client*: it builds/queries geometry with
gdsfactory (scenarios 3 and 5's MMI) and klink in THIS Python process,
then pushes it to a running KLayout over TCP. So there is exactly one
rule:

    the interpreter that runs THIS script needs BOTH klink and gdsfactory.

KLayout itself (the GUI + the klink plugin) is a SEPARATE process reached
on --port; it needs neither klink-the-client nor gdsfactory. Two clean
ways to get one interpreter that has both:

  1. one venv, both libs (simplest):
         pip install "klayout-klink[photonics]"      # klink + a tested gdsfactory
     then run this script with that venv's python.

  2. gdsfactory already lives in another venv (a tool venv, a PDK venv, ...):
         <that-venv>/python -m pip install klayout-klink   # klink is pure-Python
         <that-venv>/python example_template/photonics/gf_ports.py
     i.e. add klink INTO the gdsfactory venv and run from there.

Do NOT sys.path-hack the klink repo into a random interpreter and
monkey-patch klink internals to paper over a gdsfactory API gap -- that
is exactly how you get 1000x-off geometry (gf.Port's `center` unit
contract varies by version). The demos support gdsfactory >=9.0,<10 for
this reason (every 9.x line is CI-tested); [photonics] installs a tested
one. If you must use a different gdsfactory, expect to adjust the
script, not klink.
"""

from __future__ import annotations

import argparse
import sys

# ---------------------------------------------------------------------------
# Every layer / cell name here is a synthetic value this SCRIPT owns -- there
# is no process behind them, they only need to be internally consistent.
# ---------------------------------------------------------------------------
CELL_HAND = "GF_PORTS"            # scenario 1: hand-drawn triangle -> Port
CELL_WRONG = "GF_PORTS_WRONG"     # scenario 2: the SAME recipe, drawn wrong
CELL_AUTO = "GF_PORTS_AUTO"       # scenario 3: gdsfactory's own ports
CELL_BB_CHILD = "SYNTH_BB"        # scenario 4: synthetic blackbox child cell
CELL_BB = "GF_PORTS_BB"           # scenario 4: parent cell holding the instance
CELL_LOOP = "GF_PORTS_LOOP"       # scenario 5: route + drag + --reroute

WG_LAYER = "1/0"                  # waveguide-ish layer (scenarios 1/2/4/5)
BB_BODY_LAYER = "60/0"            # blackbox cell body (opaque, not a port cue)
PORT_LAYER = "999/99"             # klink's reserved Port-marker layer
ROUTE_LAYER = "12/0"              # where the scenario-5 route is drawn

WG_WIDTH_UM = 0.8                 # waveguide width used by scenarios 1/2
STUB_SIZE_UM = 0.5                # blackbox stub-box side (scenario 4)


def _check(problems: list, label: str, actual, expected, tol: float = 1e-3) -> None:
    """Compare actual vs expected and print OK/MISMATCH; record failures."""
    ok = abs(float(actual) - float(expected)) <= tol
    print("      [%s] %s = %s (expected %s)" %
          ("OK" if ok else "MISMATCH", label, actual, expected))
    if not ok:
        problems.append("%s: got %s, expected %s" % (label, actual, expected))


def _check_bool(problems: list, label: str, actual, expected: bool) -> None:
    ok = bool(actual) == expected
    print("      [%s] %s = %s (expected %s)" %
          ("OK" if ok else "MISMATCH", label, actual, expected))
    if not ok:
        problems.append("%s: got %s, expected %s" % (label, actual, expected))


# ---------------------------------------------------------------------------
# Scenario 1 -- hand-drawn triangle marker -> recognized Port
# ---------------------------------------------------------------------------
def scenario_handdrawn(client, problems: list) -> dict:
    from klink.port.workflow import recognize_handdrawn_ports

    print("\n[1] hand-drawn triangle -> Port (custom device)")
    client.new_tab(cell_name=CELL_HAND)
    si = client.layer_ensure(1, 0)["layer_index"]
    pl = client.layer_ensure(999, 99)["layer_index"]
    w = WG_WIDTH_UM

    # a straight waveguide: a wide "device" box with a narrow guide stub on
    # each end, plus one triangle marker per end (base = guide width, apex
    # points outward -- that is the whole marker convention).
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
    result = recognize_handdrawn_ports(
        client, CELL_HAND, layer=PORT_LAYER, direction_guess="long_edge",
        port_type="optical", delete_markers=True,
    )
    client.show_cell(CELL_HAND, zoom_fit=True)

    ports = {p["name"]: p for p in result["ports"]}
    print("    recognized %d port(s), deleted %d marker(s)"
          % (result["recognized"], result["deleted_markers"]))
    p0 = ports.get("P0")
    p1 = ports.get("P1")
    if p0 is None or p1 is None:
        problems.append("scenario 1: expected ports P0 and P1, got %s" % sorted(ports))
    else:
        _check(problems, "P0.center_x_um", p0["center_um"][0], -10.0)
        _check(problems, "P0.center_y_um", p0["center_um"][1], 0.0)
        _check(problems, "P0.orientation_deg", p0["orientation"], 180.0)
        _check(problems, "P0.width_um", p0["width_um"], 0.8)
        _check_bool(problems, "P0.attached", p0.get("attached"), True)
        _check(problems, "P1.center_x_um", p1["center_um"][0], 10.0)
        _check(problems, "P1.center_y_um", p1["center_um"][1], 0.0)
        _check(problems, "P1.orientation_deg", p1["orientation"], 0.0)
        _check(problems, "P1.width_um", p1["width_um"], 0.8)
    return {"cell": CELL_HAND, "recognized": result["recognized"], "ports": ports}


# ---------------------------------------------------------------------------
# Scenario 2 -- the SAME recipe, drawn WRONG (cautionary, on purpose)
# ---------------------------------------------------------------------------
def scenario_handdrawn_wrong(client, problems: list) -> dict:
    from klink.port.workflow import recognize_handdrawn_ports

    print("\n[2] WRONG hand-drawn marker (cautionary -- do not copy this part)")
    client.cell_create(CELL_WRONG)
    si = client.layer_ensure(1, 0)["layer_index"]
    pl = client.layer_ensure(999, 99)["layer_index"]
    w = WG_WIDTH_UM

    client.shape_insert_boxes(CELL_WRONG, layer_index=si, boxes_um=[[-10, -w / 2, 0, w / 2]])
    # BUG ON PURPOSE: the triangle base (2.5 um, from y=-1.25 to y=1.25) does
    # NOT match the 0.8 um waveguide it is supposed to mark. The recognizer
    # has no idea what the waveguide width "should" be -- it just measures
    # the marker -- so it happily returns a port with the WRONG width, and
    # (because the oversized base no longer sits flush against the guide's
    # edge) the port also comes back not edge-attached.
    client.shape_insert_many(CELL_WRONG, [
        {"kind": "polygon", "layer_index": pl,
         "points_um": [[1.5, 0.0], [0.0, -1.25], [0.0, 1.25]]},
    ])
    result = recognize_handdrawn_ports(
        client, CELL_WRONG, layer=PORT_LAYER, direction_guess="long_edge",
        port_type="optical", delete_markers=True,
    )
    client.show_cell(CELL_WRONG, zoom_fit=True)

    ports = {p["name"]: p for p in result["ports"]}
    p0 = ports.get("P0")
    if p0 is None:
        problems.append("scenario 2: expected port P0, got %s" % sorted(ports))
    else:
        print("    WARNING: this marker's base (2.5um) does not match the")
        print("    waveguide it sits on (0.8um) -- recognize_handdrawn_ports")
        print("    cannot see the waveguide, only the marker, so it returns")
        print("    a port with the WRONG width, and it is NOT edge-attached:")
        _check(problems, "P0.width_um (WRONG on purpose)", p0["width_um"], 1.95, tol=0.05)
        _check(problems, "P0.orientation_deg (WRONG on purpose)", p0["orientation"], 270.0)
        _check_bool(problems, "P0.attached (WRONG on purpose)", p0.get("attached"), False)
        print("    LESSON: always draw the triangle base EXACTLY equal to the")
        print("    waveguide width, flush against its end edge.")
    return {"cell": CELL_WRONG, "recognized": result["recognized"], "ports": ports}


# ---------------------------------------------------------------------------
# Scenario 3 -- gdsfactory's own ports (standard component)
# ---------------------------------------------------------------------------
def scenario_gf_auto(client, problems: list) -> dict:
    from klink.routing.backends.gdsfactory.gdsfactory_components import (
        place_gdsfactory_components,
    )

    print("\n[3] gdsfactory's own ports (standard mmi1x2 component)")
    client.cell_create(CELL_AUTO)
    result = place_gdsfactory_components(
        client, CELL_AUTO,
        [{
            "id": "SPL1", "component": "mmi1x2", "center_um": [0, 0], "rotation": 0,
            "params": {}, "port_nets": {"o1": "in", "o2": "out0", "o3": "out1"},
        }],
        target_layer=WG_LAYER, port_layer=PORT_LAYER,
    )
    client.show_cell(CELL_AUTO, zoom_fit=True)

    ports = {p["name"]: p for p in result["components"][0]["ports"]}
    print("    placed mmi1x2, marked %d port(s): %s"
          % (result["port_count"], sorted(ports)))
    expected = {
        "SPL1.o1": ([-10.0, 0.0], 180.0, 0.5),
        "SPL1.o2": ([15.5, 0.625], 0.0, 0.5),
        "SPL1.o3": ([15.5, -0.625], 0.0, 0.5),
    }
    for name, (center, orient, width) in expected.items():
        port = ports.get(name)
        if port is None:
            problems.append("scenario 3: missing port %s" % name)
            continue
        _check(problems, "%s.center_x_um" % name, port["center_um"][0], center[0])
        _check(problems, "%s.center_y_um" % name, port["center_um"][1], center[1])
        _check(problems, "%s.orientation_deg" % name, port["orientation"], orient)
        _check(problems, "%s.width_um" % name, port["width_um"], width)
    return {"cell": CELL_AUTO, "ports": ports}


# ---------------------------------------------------------------------------
# Scenario 4 -- synthetic blackbox cell, harvested by stub convention
# ---------------------------------------------------------------------------
def scenario_blackbox(client, problems: list) -> dict:
    from klink.domains.photonics.blackbox import harvest_instance_ports, mark_ports

    print("\n[4] synthetic blackbox cell, harvested by stub convention")
    body_li = client.layer_ensure(60, 0)["layer_index"]
    wg_li = client.layer_ensure(1, 0)["layer_index"]

    # The child cell only knows how to draw itself: an opaque body on
    # BB_BODY_LAYER plus two small stub boxes on WG_LAYER at its east/west
    # edges. That stub convention (box side == STUB_SIZE_UM, on the
    # waveguide layer) is ALL harvest_instance_ports needs -- no markers,
    # no gdsfactory Port objects, just geometry.
    client.cell_create(CELL_BB_CHILD)
    client.shape_insert_boxes(CELL_BB_CHILD, layer_index=body_li, boxes_um=[[-4, -3, 4, 3]])
    client.shape_insert_boxes(CELL_BB_CHILD, layer_index=wg_li, boxes_um=[
        [-4.25, -0.25, -3.75, 0.25],
        [3.75, -0.25, 4.25, 0.25],
    ])

    client.cell_create(CELL_BB)
    client.instance_insert(CELL_BB, CELL_BB_CHILD, position_um=[0, 0], rotation=0)

    marks = harvest_instance_ports(
        client, CELL_BB, tags={CELL_BB_CHILD: "bb"}, wg_layer=WG_LAYER,
        stub_size_um=STUB_SIZE_UM, nets={},
    )
    mark_ports(client, marks)
    client.show_cell(CELL_BB, zoom_fit=True)

    ports = {m["name"]: m for m in marks}
    print("    harvested %d port(s) from 1 blackbox instance: %s"
          % (len(marks), sorted(ports)))
    expected = {
        "bb0_0": ([-4.25, 0.0], 180.0),
        "bb0_1": ([4.25, 0.0], 0.0),
    }
    for name, (center, orient) in expected.items():
        port = ports.get(name)
        if port is None:
            problems.append("scenario 4: missing port %s" % name)
            continue
        _check(problems, "%s.center_x_um" % name, port["center_um"][0], center[0])
        _check(problems, "%s.center_y_um" % name, port["center_um"][1], center[1])
        _check(problems, "%s.orientation_deg" % name, port["orientation"], orient)
    return {"cell": CELL_BB, "ports": ports}


# ---------------------------------------------------------------------------
# Scenario 5 -- route two of them together, then drag / --reroute
# ---------------------------------------------------------------------------
def scenario_route_loop(client, problems: list) -> dict:
    from klink.port.workflow import recognize_handdrawn_ports
    from klink.routing.backends.gdsfactory.gdsfactory_components import (
        place_gdsfactory_components,
    )
    from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

    print("\n[5] route a hand-drawn device to an MMI, then drag / --reroute")
    si = client.layer_ensure(1, 0)["layer_index"]
    pl = client.layer_ensure(999, 99)["layer_index"]

    client.cell_create(CELL_LOOP)
    # The custom device sits 40um clear of where the MMI's o1 port will
    # land (at x=-10 once placed at the origin) -- enough working room that
    # a later drag-and-reroute has a real corridor to route through instead
    # of looping around itself.
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
    report = route_gdsfactory_ports(
        client, CELL_LOOP, port_layer=PORT_LAYER, route_layer=ROUTE_LAYER,
        clear=True, all_two_port_nets=True,
    )
    client.show_cell(CELL_LOOP, zoom_fit=True)

    routes = report.get("routes", [])
    print("    routed %d net(s) on %s" % (len(routes), ROUTE_LAYER))
    for route in routes:
        print("      %s -> %s : length=%.3fum points=%s"
              % (route.get("source"), route.get("target"),
                 route.get("length_um", 0.0), route.get("points_um")))
    if len(routes) != 1:
        problems.append("scenario 5: expected 1 route, got %d" % len(routes))
    else:
        route = routes[0]
        names = {route.get("source"), route.get("target")}
        if names != {"MMI.o1", "P0"}:
            problems.append("scenario 5: expected route MMI.o1<->P0, got %s" % names)
        _check(problems, "route.length_um", route.get("length_um", 0.0), 40.0)
    return {"cell": CELL_LOOP, "routes": routes}


def _reroute(client) -> None:
    from klink.routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

    print("\n[--reroute] re-routing %s from its CURRENT (possibly dragged) port"
          " positions -- no rebuild" % CELL_LOOP)
    report = route_gdsfactory_ports(
        client, CELL_LOOP, port_layer=PORT_LAYER, route_layer=ROUTE_LAYER,
        clear=True, all_two_port_nets=True,
    )
    client.show_cell(CELL_LOOP, zoom_fit=True)
    routes = report.get("routes", [])
    print("    routed %d net(s) on %s" % (len(routes), ROUTE_LAYER))
    for route in routes:
        print("      %s -> %s : length=%.3fum points=%s"
              % (route.get("source"), route.get("target"),
                 route.get("length_um", 0.0), route.get("points_um")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--reroute", action="store_true",
        help="Re-route the scenario-5 net (%s) from its CURRENT Port positions "
             "in KLayout WITHOUT rebuilding the cell -- run this after dragging "
             "the custom device or the MMI in the GUI. Run the script with no "
             "flag first to build every scenario." % CELL_LOOP)
    args = parser.parse_args()

    from klink import KLinkClient

    if args.reroute:
        with KLinkClient(port=args.port).connect() as client:
            _reroute(client)
        return 0

    problems: list[str] = []
    with KLinkClient(port=args.port).connect() as client:
        s1 = scenario_handdrawn(client, problems)
        s2 = scenario_handdrawn_wrong(client, problems)
        s3 = scenario_gf_auto(client, problems)
        s4 = scenario_blackbox(client, problems)
        s5 = scenario_route_loop(client, problems)

    print("\n==================== summary ====================")
    print("[1] %-16s recognized=%d ports=%s"
          % (s1["cell"], s1["recognized"], sorted(s1["ports"])))
    print("[2] %-16s recognized=%d ports=%s  (WRONG marker, kept for the lesson)"
          % (s2["cell"], s2["recognized"], sorted(s2["ports"])))
    print("[3] %-16s ports=%s" % (s3["cell"], sorted(s3["ports"])))
    print("[4] %-16s ports=%s" % (s4["cell"], sorted(s4["ports"])))
    print("[5] %-16s routes=%d" % (s5["cell"], len(s5["routes"])))
    if problems:
        print("\n%d check(s) FAILED:" % len(problems))
        for problem in problems:
            print("  -", problem)
    else:
        print("\nall checks passed.")

    print("\nNow drag the custom device or the MMI inside %s in the KLayout GUI,"
          % CELL_LOOP)
    print("then re-route from the new positions -- WITHOUT rebuilding -- by")
    print("re-running THIS script with --reroute:")
    print("  python %s --port %d --reroute" % (sys.argv[0], args.port))
    print("(Re-running with NO flag rebuilds every scenario from scratch and")
    print(" snaps every device back to its original spot, undoing your drag.)")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
