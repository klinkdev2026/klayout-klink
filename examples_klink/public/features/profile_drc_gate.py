# klink PUBLIC example — runnable as-is against a live KLayout session.
# Same-source DRC: the SAME ProcessProfile that drives routing and LVS also
# derives the DRC deck (profile.drc_script() / profile_drc.run_drc), so all
# three gates read one process declaration.
#
#   Run:    python -m examples_klink.public.features.profile_drc_gate --port <session-port>
#
# What it does (self-contained, ~1s):
#   1. declares a tiny EXAMPLE-owned profile (layers/dims are THIS file's
#      numbers — klink ships none),
#   2. draws a LEGAL scene with typed RPCs (two wires at legal spacing plus
#      a via stack) and shows the generated deck passes: 0 violations,
#   3. draws a DELIBERATE violation (a third wire too close) and shows the
#      deck bites: exactly the expected category fires — a negative control,
#      so you know the gate actually checks something,
#   4. optionally (--check-demo) runs the full deck over the DEMO_ADD4 cell
#      drawn by the fit_device starter, with the device-region exclusion —
#      the same call that gates that flow.
"""Profile-derived DRC gate — positive and negative control on a live session."""

from __future__ import annotations

import argparse
import sys

from klink import KLinkClient
from klink.routing.grid.process_profile import ProcessProfile
from klink.routing.grid.profile_drc import run_drc

# EXAMPLE-owned process facts (copy this file and edit for your stack).
PROFILE = ProcessProfile(
    routing_layers=("21/0", "23/0"),
    gate_layer="21/0",
    sd_layer="23/0",
    channel_layer="29/0",          # device-region marker (unused in the scene)
    vias=(("21/0", "22/0", "23/0"),),
    wire_width_um=2.0,
    wire_clear_um=2.0,
    via_pad_um=2.0,
    litho_tol_um=0.5,
    y_step_um=10.0,
    col_pitch_um=20.0,
)

CELL = "PUB_PROFILE_DRC"


def draw_scene(c: KLinkClient, with_violation: bool) -> None:
    try:
        c.cell_delete(CELL, recursive=True)
    except Exception:
        pass
    c.cell_create(CELL)
    for spec in ("21/0", "22/0", "23/0"):
        layer, datatype = (int(x) for x in spec.split("/"))
        c.layer_ensure(layer, datatype)

    w = PROFILE.wire_width_um
    clear = PROFILE.wire_clear_um
    pad = PROFILE.via_pad_um
    tol = PROFILE.litho_tol_um

    # two horizontal wires on 21/0 at exactly the legal clearance
    c.shape_insert_box(CELL, layer=21, bbox_um=[0, 0, 30, w])
    c.shape_insert_box(CELL, layer=21, bbox_um=[0, w + clear, 30, 2 * w + clear])
    # a via stack at the first wire's end: pads on both metals + inset cut
    c.shape_insert_box(CELL, layer=23, bbox_um=[30 - pad, 0, 30, pad])
    c.shape_insert_box(CELL, layer=22, bbox_um=[30 - pad + tol, tol,
                                                30 - tol, pad - tol])
    if with_violation:
        # a third wire HALF a clearance away — must trip space_21_0
        y = 2 * w + clear + clear / 2
        c.shape_insert_box(CELL, layer=21, bbox_um=[0, y, 30, y + w])
    c.show_cell(CELL)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765,
                    help="klink session port (default 8765)")
    ap.add_argument("--check-demo", action="store_true",
                    help="also run the full deck over DEMO_ADD4 (draw it "
                         "first with the fit_device starter)")
    args = ap.parse_args()

    with KLinkClient(port=args.port) as c:
        print("deck derived from the profile:")
        for line in PROFILE.drc_script().splitlines():
            print("   ", line)

        draw_scene(c, with_violation=False)
        res = run_drc(c, PROFILE)
        print(f"[positive control] legal scene: ok={res['ok']} "
              f"violations={res['total']}")
        if not res["ok"]:
            for cat in res["categories"]:
                if cat["count"]:
                    print("   ", cat["name"], cat["count"])
            print("RESULT: FAIL (legal scene must pass)")
            return 1

        draw_scene(c, with_violation=True)
        res = run_drc(c, PROFILE)
        fired = {cat["name"] for cat in res["categories"] if cat["count"]}
        print(f"[negative control] bad scene: violations={res['total']} "
              f"fired={sorted(fired)}")
        if res["ok"] or "space_21_0" not in fired:
            print("RESULT: FAIL (deliberate violation must trip space_21_0)")
            return 1

        # leave the canvas on the legal scene
        draw_scene(c, with_violation=False)

        if args.check_demo:
            from dataclasses import replace

            from examples_klink.public.demos.digital.fit_device_pnr_lvs import (
                PUBLIC_PROCESS)

            P = replace(PUBLIC_PROCESS, wire_clear_um=5.0, grid_pitch_um=10.0,
                        col_pitch_um=100.0, y_step_um=35.0)
            c.show_cell("DEMO_ADD4")
            demo = run_drc(c, P,
                           exclude_around=(P.channel_layer, 10.0))
            print(f"[demo gate] DEMO_ADD4: ok={demo['ok']} "
                  f"violations={demo['total']}")
            if not demo["ok"]:
                for cat in demo["categories"]:
                    if cat["count"]:
                        print("   ", cat["name"], cat["count"])
                print("RESULT: FAIL (fit_device layout must pass its own "
                      "profile's deck)")
                return 1

    print("RESULT: PASS (deck passes legal geometry, catches the planted "
          "violation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
