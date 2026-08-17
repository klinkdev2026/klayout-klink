"""Draw a real device into a running L-Edit — the copy-and-adapt starter.

    python draw_device_demo.py                 # into the design already open
    python draw_device_demo.py --new my_lib    # bootstrap a scratch design
    python draw_device_demo.py --cell NMOS_4F --fingers 4

Draws a static two-finger NMOS (active / n+ implant / poly / contact /
metal1) so there is something concrete to change: edit LAYERS and the
geometry function, and it is your device.

Requires: `pip install klayout-klink` (imports as `klink`) and L-Edit
running with ledit_bridge.cpp loaded (Tools > Macro > Load Macro...).
Check with `python -m klink.doctor`, which reports the bridge.

Four things this file demonstrates that are easy to get wrong:

1. TARGETING — commands act on whatever design is VISIBLE in L-Edit. Every
   write here carries `expect_file`, so a design switch makes the write
   FAIL instead of silently landing in the user's own layout.
2. IDEMPOTENCE — `draw` only appends, so re-running would stack shapes on
   top of each other. Clear the cell first.
3. BATCHING — a request costs about one poll interval no matter how big it
   is, so the whole device goes over in ONE `batch` rather than a dozen
   round trips.
4. VERIFICATION — read the cell back and assert, rather than trusting that
   the writes landed.
"""
from __future__ import annotations

import argparse
import sys

from klink.bridges.ledit import LEditBridgeClient, LEditBridgeError

# name -> (gds_layer, gds_datatype, fill rgb). GDS numbers are tape-out
# critical: a layer left at -1 will not export correctly, so stamp them.
LAYERS = [
    ("ACTIVE",  65, 20, [55, 170, 95]),
    ("NPLUS",   93, 44, [245, 205, 70]),
    ("POLY",    66, 20, [220, 70, 70]),
    ("CONTACT", 67, 44, [35, 35, 35]),
    ("MET1",    68, 20, [70, 135, 225]),
]

# device dimensions in microns — the knobs worth turning first
GATE_W_UM = 0.20          # poly finger width (drawn gate length)
GATE_PITCH_UM = 1.30      # finger to finger
DIFF_H_UM = 1.20          # active height (device width)
DIFF_MARGIN_UM = 0.30     # active beyond the outer fingers
IMPLANT_ENC_UM = 0.55     # n+ enclosure of active
POLY_OVERHANG_UM = 0.35   # poly beyond active, top and bottom
CONTACT_UM = 0.24
CONTACT_ENC_UM = 0.10     # metal enclosure of contact


def box(layer, x0, y0, x1, y1):
    return {"kind": "box", "layer": layer, "bbox_um": [x0, y0, x1, y1]}


def two_finger_mosfet(fingers=2):
    """Geometry only — no bridge calls, so it is unit-testable on its own."""
    if fingers < 1:
        raise ValueError("fingers must be >= 1")

    # source/drain columns sit between and outside the gate fingers
    diff_w = (fingers - 1) * GATE_PITCH_UM + GATE_W_UM + 2 * DIFF_MARGIN_UM
    items = [
        box("NPLUS", -IMPLANT_ENC_UM, -IMPLANT_ENC_UM,
            diff_w + IMPLANT_ENC_UM, DIFF_H_UM + IMPLANT_ENC_UM),
        box("ACTIVE", 0.0, 0.0, diff_w, DIFF_H_UM),
    ]

    for f in range(fingers):
        gx = DIFF_MARGIN_UM + f * GATE_PITCH_UM
        items.append(box("POLY", gx, -POLY_OVERHANG_UM,
                         gx + GATE_W_UM, DIFF_H_UM + POLY_OVERHANG_UM))

    # one contacted strap per source/drain column (fingers + 1 of them)
    strap_w = GATE_PITCH_UM - GATE_W_UM - 2 * CONTACT_ENC_UM
    for c in range(fingers + 1):
        cx = c * GATE_PITCH_UM + DIFF_MARGIN_UM / 2.0
        items.append(box("MET1", cx, CONTACT_ENC_UM,
                         cx + strap_w, DIFF_H_UM - CONTACT_ENC_UM))
        # two contacts up the column, inside the metal enclosure
        for row in (0.30, 0.70):
            cy = DIFF_H_UM * row - CONTACT_UM / 2.0
            items.append(box("CONTACT",
                             cx + CONTACT_ENC_UM, cy,
                             cx + CONTACT_ENC_UM + CONTACT_UM, cy + CONTACT_UM))

    # gate bus tying the fingers together above the device
    bus_y = DIFF_H_UM + POLY_OVERHANG_UM
    items.append(box("MET1", -DIFF_MARGIN_UM, bus_y,
                     diff_w + DIFF_MARGIN_UM, bus_y + 0.30))
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cell", default="NMOS_2F_DEMO")
    ap.add_argument("--fingers", type=int, default=2)
    ap.add_argument("--new", metavar="NAME", default=None,
                    help="create a scratch design instead of using the open one")
    ap.add_argument("--namespace", default="default")
    args = ap.parse_args(argv)

    bridge = LEditBridgeClient(namespace=args.namespace)

    # status() never raises: it reports what is wrong AND the next action.
    st = bridge.status()
    if not st.get("macro_alive"):
        print("bridge not usable: %s" % st.get("error", "no heartbeat"),
              file=sys.stderr)
        print("-> %s" % st.get("next_action", ""), file=sys.stderr)
        return 1

    if args.new:
        target = bridge.new_design(args.new)["file"]
        print("created scratch design %r" % target)
    elif st.get("design_ready"):
        target = st["ping"]["file"]
        print("drawing into the design already open: %r" % target)
    else:
        print("no design open in L-Edit. Re-run with --new my_lib to create "
              "one, or open a .tdb first.", file=sys.stderr)
        return 1

    # Guard EVERY write: if the active design changes under us (someone
    # clicks another window), the write fails rather than landing there.
    guard = {"expect_file": target}

    ops = []
    for name, gl, gd, rgb in LAYERS:
        ops.append(("ensure_layer", dict(guard, name=name,
                                         gds_layer=gl, gds_datatype=gd)))
        ops.append(("set_layer_style", dict(guard, layer=name, fill_rgb=rgb)))
    ops.append(("create_cell", dict(guard, name=args.cell)))
    # draw is append-only: clear first so re-running is idempotent
    ops.append(("clear_cell", dict(guard, cell=args.cell)))

    items = two_finger_mosfet(args.fingers)
    ops.append(("draw", dict(guard, cell=args.cell, items=items)))

    try:
        bridge.batch(ops)                     # one request, ordered
    except LEditBridgeError as exc:
        print("draw failed: %s" % exc, file=sys.stderr)
        print("-> %s" % exc.next_action, file=sys.stderr)
        return 1

    # Verify by reading the cell back, not by trusting the write.
    got = bridge.get_cell(args.cell)
    shapes = [o for o in got.get("objects", []) if o.get("kind") != "instance"]
    per_layer = {}
    for o in shapes:
        per_layer[o.get("layer", "?")] = per_layer.get(o.get("layer", "?"), 0) + 1
    ok = len(shapes) == len(items)
    print("%s: %d shapes in %r (expected %d) — %s"
          % ("OK" if ok else "MISMATCH", len(shapes), args.cell, len(items),
             ", ".join("%s=%d" % kv for kv in sorted(per_layer.items()))))
    if not ok:
        return 1
    print("open cell %r in L-Edit to inspect it; re-run to regenerate."
          % args.cell)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
