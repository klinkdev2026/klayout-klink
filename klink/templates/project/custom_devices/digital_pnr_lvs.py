"""Digital P&R -> live LVS starter (Verilog -> devices -> route -> match=True).

Copy/adapt this for your design. The 'Bring your own' recipe: the transistor
layout is YOURS. You supply (in pdk.py / by path, never committed):
  - PROCESS + DEVICES + LIBRARY + SIZING       (pdk.py)
  - a harvested device-geometry JSON           (out/device_geom.json, by path)
  - a Verilog source + its top module name

Run it:                  python custom_devices/digital_pnr_lvs.py
On the proposal, re-run:  python custom_devices/digital_pnr_lvs.py --confirm <token>

Weak-agent rules (docs AGENT_TOOL_DESIGN): build_from_netlist is ONE call that
derives the floorplan, routes, draws, and device-LVS-verifies. DO NOT place /
route / draw yourself. Read each result's `next_action` and follow it; relay
`problems` to the user verbatim — never improvise a fix.
"""

from __future__ import annotations

import sys

# --- dependency checks: report the exact fix, never crash cryptically --------
# (errors are instructions: an agent can relay these to the user verbatim).

# yosys synthesizes Verilog -> logic. It is an EXTERNAL tool; klink discovers it
# and raises an instructive error (the exact `pip install` line) if it is
# missing. Surface that BEFORE doing any work.
try:
    from klink.domains.structdevice.yosys_flow import (
        discover_yosys, verilog_to_device_netlist,
    )
    _yosys = discover_yosys()
    print(f"[setup] yosys: {_yosys}")
except Exception as exc:   # YosysFlowError already says: pip install yowasp-yosys
    sys.exit(f"[setup] {exc}")

# klayout.db runs the offline LVS extraction; it must be in THIS interpreter
# (the same one that runs this script and the MCP server).
try:
    import klayout.db  # noqa: F401
except Exception:
    sys.exit("[setup] klayout.db is not importable in this interpreter.\n"
             f"        fix: {sys.executable} -m pip install klayout")

from klink import KLinkClient
from klink.domains.structdevice.netlist_build import build_from_netlist
from klink.domains.structdevice.sizing import apply_sizing

# your process + device library (the ONLY home for process data)
from pdk import PROCESS, DEVICES, LIBRARY

# SIZING is a MANDATORY design choice, NOT an optional default. Without it every
# driver is unit-width, which is electrically wrong for any series (NAND/AND)
# stack -- the layout would build and even pass TOPOLOGY LVS while being WRONG
# (this is exactly how a "looks fine, sizes are off" bug happens). Force it.
try:
    from pdk import SIZING
except ImportError:
    sys.exit("[pdk] no SIZING defined in pdk.py. Device sizing is a MANDATORY "
             "design choice -- klink ships no default because there is no safe "
             "one: 'all drivers unit-width' is electrically wrong for any series "
             "(NAND/AND) stack, yet still builds and passes topology LVS. Define "
             "SIZING (AutoRatioSizing or ExplicitSizing) in pdk.py for YOUR "
             "circuit family before building.")

# --- YOUR design inputs (TODO: edit these) -----------------------------------
VERILOG = "rtl/my_design.v"           # TODO your Verilog source
TOP = "my_top"                        # TODO top module name
GEOM_PATH = "out/device_geom.json"    # TODO your harvested device geometry (by path)
GATE_SET = ("INV", "NAND2", "NOR2")   # the cells your LIBRARY defines
CELL = "PNR_MY_DESIGN"                # output cell (clearly named; not the user's cell)
PORT = 8765                           # your KLayout RPC port (8765 = default)


def main() -> int:
    if not DEVICES or not LIBRARY.get("gates"):
        sys.exit("[pdk] DEVICES / LIBRARY are empty -- fill them in pdk.py first "
                 "(uncomment + edit the skeleton there).")

    # Verilog -> device netlist (yosys runs here; a missing yosys would already
    # have stopped us above with the install instruction).
    netlist = verilog_to_device_netlist(VERILOG, TOP, LIBRARY, gate_set=GATE_SET)
    netlist = apply_sizing(netlist, LIBRARY, SIZING)   # MANDATORY (see import guard)

    confirm = None
    if "--confirm" in sys.argv:
        confirm = sys.argv[sys.argv.index("--confirm") + 1]

    with KLinkClient(port=PORT).connect() as c:
        res = build_from_netlist(c, CELL, netlist, profile=PROCESS,
                                 devices=DEVICES, geom_path=GEOM_PATH,
                                 confirm=confirm)
        # First call returns a proposal + a confirm token (in next_action).
        # Relay it to the user; on approval re-run with --confirm <token>.
        if res.get("needs_confirmation"):
            print(res["next_action"])
            return 0
        print("LVS match:", res.get("device_match"),
              "| devices:", res.get("devices"))
        print("next:", res.get("next_action"))
        for p in res.get("problems", []):
            print("  problem:", p)        # relay verbatim; do not improvise
        return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
