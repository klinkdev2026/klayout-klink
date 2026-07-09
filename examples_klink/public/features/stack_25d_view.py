# klink PUBLIC example — runnable as-is against a live KLayout session
# (needs a KLayout build with OpenGL; the 2.5d viewer is native KLayout,
# official D25View API since 0.28).
#
#   Run:    python -m examples_klink.public.features.stack_25d_view --port <session-port>
#
# What it does (~1s): draws a small two-metal scene with a via stack via
# typed RPCs, declares the SAME StackSpec that routing/LVS would read, adds
# the EXAMPLE-owned z table (thickness/elevation are process facts this file
# owns — klink ships none), and opens KLayout's native 2.5d window with one
# call. Drag with the mouse to orbit the extruded stack.
"""Same-source 2.5d view: StackSpec + z table -> KLayout's native 3D stack."""

from __future__ import annotations

import argparse
import sys

from klink import KLinkClient
from klink.errors import KLinkServerError
from klink.process_stack import StackSpec
from klink.stack25d import stack_displays

CELL = "PUB_STACK_25D"

# EXAMPLE-owned process facts (copy this file and edit for your stack).
STACK = StackSpec.from_dict({
    "conductors": [
        {"layer": "31/0", "role": "metalA"},
        {"layer": "33/0", "role": "metalB"},
    ],
    "vias": [{"from": "31/0", "via_layer": "32/0", "to": "33/0",
              "via_cell": "VIA_A"}],
})
Z_UM = {                       # zstart, zstop (microns)
    "31/0": (0.00, 0.50),
    "32/0": (0.50, 1.00),
    "33/0": (1.00, 1.50),
}
COLORS = {"31/0": 0x2B6CB0, "32/0": 0x718096, "33/0": 0xD69E2E}


def draw_scene(c: KLinkClient) -> None:
    try:
        c.cell_delete(CELL, recursive=True)
    except Exception:
        pass
    c.cell_create(CELL)
    for layer in (31, 32, 33):
        c.layer_ensure(layer, 0)
    # two crossing wires bridged by a via stack
    c.shape_insert_box(CELL, layer=31, bbox_um=[0, 4, 30, 6])       # metalA E-W
    c.shape_insert_box(CELL, layer=33, bbox_um=[14, 0, 16, 10])     # metalB N-S
    c.shape_insert_box(CELL, layer=32, bbox_um=[14.3, 4.3, 15.7, 5.7])  # cut
    c.show_cell(CELL)


# --- the full-block mode: the fit_device starter's add4 layout in 3D -------
# EXAMPLE-owned z table for the synthetic BACKGATE device stack: the gate
# plate is the BOTTOM conductor, the semiconductor channel floats above it
# across a thin dielectric, source/drain metal lands on the channel, and two
# via families climb to the top routing metal. All numbers are this file's
# process facts.
DEMO_STACK = StackSpec.from_dict({
    "conductors": [
        {"layer": "101/0", "role": "gate metal (bottom)"},
        {"layer": "104/0", "role": "source/drain metal"},
        {"layer": "106/0", "role": "top routing metal"},
    ],
    "vias": [
        {"from": "101/0", "via_layer": "102/0", "to": "104/0",
         "via_cell": "VIA_G_SD"},
        {"from": "104/0", "via_layer": "105/0", "to": "106/0",
         "via_cell": "VIA_SD_TOP"},
    ],
})
DEMO_Z_UM = {
    "101/0": (0.00, 0.10),     # backgate plate
    "102/0": (0.10, 0.16),     # gate <-> SD cut
    "103/0": (0.12, 0.16),     # semiconductor channel (device layer)
    "104/0": (0.16, 0.28),     # source/drain metal
    "105/0": (0.28, 0.40),     # SD <-> top cut
    "106/0": (0.40, 0.55),     # top routing metal
}
DEMO_COLORS = {"101/0": 0x2B6CB0, "102/0": 0x718096, "103/0": 0x805AD5,
               "104/0": 0x2F855A, "105/0": 0xA0AEC0, "106/0": 0xD69E2E}
DEMO_CELL = "DEMO_ADD4"


def show_demo_block(c: KLinkClient) -> int:
    """3D view of the full 4-bit adder drawn by the fit_device starter."""
    displays = stack_displays(
        DEMO_STACK, DEMO_Z_UM, colors=DEMO_COLORS,
        names={"103/0": "channel"}, extra_layers=["103/0"])
    print("demo block display list:")
    for d in displays:
        print(f"   {d['layer']:6s} {d['name']:22s} "
              f"z {d['zstart_um']:.2f} -> {d['zstop_um']:.2f} um")
    try:
        res = c.show_25d(displays, cell=DEMO_CELL,
                         generator="klink add4 stack")
    except KLinkServerError as exc:
        if exc.code == "ERR_NOT_FOUND":
            print(f"RESULT: SKIP ({DEMO_CELL} not drawn — run the "
                  "fit_device starter first: python -m examples_klink."
                  "public.demos.digital.fit_device_pnr_lvs --port <port>)")
            return 1
        raise
    print(f"2.5d window: ok={res['ok']} cell={res['cell']} "
          f"displays={res['displays']} empty_layers={res['empty_layers']}")
    if not (res["ok"] and not res["empty_layers"]):
        print("RESULT: FAIL (all six materials must display)")
        return 1
    print("RESULT: PASS (full add4 block in 3D — 173 devices, PDN, "
          "both via families; orbit with the mouse)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765,
                    help="klink session port (default 8765)")
    ap.add_argument("--demo-add4", action="store_true",
                    help="show the fit_device starter's DEMO_ADD4 block "
                         "(6 materials incl. the device channel) instead "
                         "of the minimal scene")
    args = ap.parse_args()

    if args.demo_add4:
        with KLinkClient(port=args.port) as c:
            return show_demo_block(c)

    displays = stack_displays(STACK, Z_UM, colors=COLORS)
    print("display list derived from the stack:")
    for d in displays:
        print(f"   {d['layer']:6s} {d['name']:16s} "
              f"z {d['zstart_um']:.2f} -> {d['zstop_um']:.2f} um")

    with KLinkClient(port=args.port) as c:
        draw_scene(c)
        try:
            res = c.show_25d(displays, cell=CELL, generator="klink stack demo")
        except KLinkServerError as exc:
            print(f"RESULT: SKIP ({exc.code}: {exc})")
            print("The 2.5d viewer needs an OpenGL-enabled KLayout build.")
            return 1
        print(f"2.5d window: ok={res['ok']} cell={res['cell']} "
              f"displays={res['displays']} empty_layers={res['empty_layers']}")
        if not (res["ok"] and res["displays"] == len(displays)
                and not res["empty_layers"]):
            print("RESULT: FAIL (all three materials must display)")
            return 1

    print("RESULT: PASS (native 2.5d stack shown — orbit it with the mouse)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
