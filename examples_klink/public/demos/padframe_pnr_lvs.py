"""PUBLIC demo: PROBE-CARD-FIRST place & route, nanny-level tutorial.

Self-contained and IP-free: the process, the fitted devices, and the netlist
are all synthetic (see fit_device_pnr_lvs.py -- we reuse its public 3-layer
process + fitted-device library so this demo owns zero device geometry). Every
number below is EXAMPLE data; copy this file and edit the tables for YOUR card /
process. klink ships only the mechanisms.

The reality this demo encodes: the probe card / pad ring EXISTS FIRST (a legacy
20-pad card, positions frozen long ago), the circuit comes second and must meet
the card -- even when the card's interior is too small for the whole device
block. The traditional "route first, drop pads later" order is reversed.

What it shows, step by step:

  STEP 1  load + LINT the device netlist (catch hand-written netlist mistakes
          BEFORE any geometry exists)
  STEP 2  layer-count advisor: print what each candidate stack would cost --
          fewer layers is always better; the CHOICE is yours. add4 fits the
          public 3-layer stack comfortably, so we use it.
  STEP 3  the user's probe card: here we fabricate a stand-in card GDS, then
          HARVEST it back with pads_from_gds (in real life you skip the
          fabricate part and harvest your own card file directly)
  STEP 4  net->pad assignment table (16 of 20 pads used: all 14 primary ports
          + VDD + GND; 4 redundant pads stay unused, as on any legacy card)
  STEP 5  HALF-IN / HALF-OUT placement: the card interior only fits half the
          rows, so place_grid(forbid_y_bands=...) splits the block across the
          card's bottom pad row -- interior first, overflow below
  STEP 6  route + draw (io_pads=...), live LVS, and a POSITIVE per-pad proof
          (extraction probe: pad metal == its net's device-terminal net)

--no-card: no pads AT ALL -- every port is brought out as a BARE labelled
trace at the periphery (spread_ports wire-end stubs, no pad boxes drawn),
power stays on the auto-labelled PDN tie rails; you hand-connect your own
pads later, the labels tell you where.

Usage: python -m examples_klink.public.demos.padframe_pnr_lvs [--no-card] [--port 8766]
"""
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from klink import KLinkClient
from klink.domains.structdevice import layout_engine as eng
from klink.domains.structdevice.connectivity import ConnectivityExtractor
from klink.domains.structdevice.netlist_lint import lint_netlist
from klink.domains.structdevice.orchestrators import lvs_check
from klink.domains.structdevice.recipes import geom_terminal_provider
from klink.routing.grid.floorplan import (derive_grid, derive_row_pitch,
                                          gate_stack_height_um, layer_demand_report)
from klink.routing.grid.pad_harvest import pads_from_gds, spread_ports
from klink.routing.grid.process_profile import ProcessProfile

# The public, IP-free process + fitted synthetic device library. We reuse its
# fitter/geometry helpers so this demo owns zero device geometry of its own.
from examples_klink.public.demos import fit_device_pnr_lvs as D

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8766
NO_CARD = "--no-card" in sys.argv
CELL = "PUB_PADFRAME_ADD4_NOCARD" if NO_CARD else "PUB_PADFRAME_ADD4"

# add4 routes cleanly on the public 3-layer back-gate stack (fit_device_pnr_lvs.
# PUBLIC_PROCESS) with the same density knobs that demo proved (wire_clear=5,
# y_step=35 route 94/94 + LVS clean). grid_pitch = wire + clear = the coarse
# TRACK pitch the router snaps to.
P = replace(D.PUBLIC_PROCESS, wire_clear_um=5.0, y_step_um=35.0, col_pitch_um=100.0,
            grid_pitch_um=D.PUBLIC_PROCESS.wire_width_um + 5.0)

# A 7-layer EXAMPLE stack, defined here only so the advisor (STEP 2) can show
# what a bigger stack would cost -- add4 does NOT need it. All numbers example.
PUBLIC_MULTILAYER = ProcessProfile(
    routing_layers=("101/0", "104/0", "106/0", "109/0", "112/0", "115/0", "118/0"),
    signal_layers=("109/0", "112/0", "115/0", "118/0"),
    gate_layer="101/0", sd_layer="104/0", channel_layer="103/0",
    vias=(("101/0", "102/0", "104/0"), ("104/0", "105/0", "106/0"),
          ("106/0", "108/0", "109/0"), ("109/0", "111/0", "112/0"),
          ("112/0", "114/0", "115/0"), ("115/0", "117/0", "118/0")),
    layer_directions={"101/0": "V", "104/0": "H", "106/0": "V", "109/0": "V",
                      "112/0": "H", "115/0": "V", "118/0": "H"},
    power_rail_layer="104/0", power_strap_layer="106/0",
    wire_width_um=5.0, wire_clear_um=2.0, prl_spacing_um=10.0, prl_length_um=15.0,
    via_pad_um=5.0, litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0, margin_um=60.0)

# --------------------------------------------------------------------------- #
# Build the public synthetic device library (fit from exemplar geometry, then
# realise the fitted PCell geometry). This owns zero device data of its own.
# --------------------------------------------------------------------------- #
table = D.fit_parametric_table(verbose=False)
D.build_geom(table)
DEVICES = D.devices_library()
_raw = eng.load_device_geom(D.GEOM)
_, _dp, terms = eng._geom_tables(_raw)

# --------------------------------------------------------------------------- #
# STEP 1 -- load the device netlist and LINT it. For a HAND-WRITTEN netlist
# this is the step that saves you: every structural mistake (unknown instance,
# terminal on two nets, instance in no group, ...) is reported with its fix,
# BEFORE placement/routing ever runs. This bundled netlist is a toy 4-bit adder
# netted by an open logic synthesizer, remapped onto this demo's synthetic
# devices -- self-contained, no external path.
# --------------------------------------------------------------------------- #
nl = json.loads((Path(__file__).parent / "add4.devnet.json").read_text())
rep = lint_netlist(nl, device_terms=terms)
print(f"[lint] ok={rep['ok']} errors={len(rep['errors'])} warnings={len(rep['warnings'])} "
      f"{rep['stats']}")
for e in rep["errors"][:5]:
    print("   LINT-ERROR:", e["message"], "->", e["next_action"])
if not rep["ok"]:
    raise SystemExit("fix the netlist first (see LINT-ERROR lines above)")

# The circuit's primary ports (from the original 4-bit adder):
INPUTS = ["A[0]", "A[1]", "A[2]", "A[3]", "B[0]", "B[1]", "B[2]", "B[3]", "CIN"]
OUTPUTS = ["S[0]", "S[1]", "S[2]", "S[3]", "COUT"]

# --------------------------------------------------------------------------- #
# STEP 2 -- layer-count advisor. Candidate stacks are EXAMPLE data (what your
# fab can actually build); klink only reports the arithmetic. Rule: pick the
# FEWEST layers that fits -- every extra layer is a real deposition/litho/via
# step. add4 fits the 3-layer stack comfortably, so we use it.
# --------------------------------------------------------------------------- #
adv = layer_demand_report(nl, terms, [("public-3L", D.PUBLIC_PROCESS),
                                      ("example-7L", PUBLIC_MULTILAYER)])
print(f"[advisor] gates={adv['gates']} grid={adv['rows']}x{adv['cols']} "
      f"peak_crossing={adv['peak_crossing']}")
for c in adv["candidates"]:
    print(f"   {c['label']:12s} {c['n_routing_layers']} layers "
          f"({c['signal_v']}V+{c['signal_h']}H signal) -> core "
          f"{c['core_w_um']:.0f} x {c['core_h_um']:.0f} um = {c['core_area_mm2']} mm2")

rows, cols = derive_grid(len(nl["groups"]))
layers = list(P.routing_layers)
rp = derive_row_pitch(nl, rows, cols, terms, y_step=P.y_step_um, width_um=P.wire_width_um,
                      wire_clear_um=P.wire_clear_um, via_pad_um=P.via_pad_um,
                      n_horiz_layers=len(layers))
# Reserve 2 extra tracks per channel for the peripheral port/pad runs the
# row-pitch formula never sees.
rp = round(rp + 2 * (P.wire_width_um + P.wire_clear_um), 1)

# Block footprint BEFORE any band shifts (used to size/position the card):
_flat = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp)
_xs = [dx for (_c, dx, _dy) in _flat.values()]
BX1, BX2 = min(_xs) - 80.0, max(_xs) + 80.0          # 80 = device half-extent + slack

if not NO_CARD:
    # ----------------------------------------------------------------------- #
    # STEP 3 -- the user's probe card. We WRITE a stand-in card file first
    # (test_outputs/pub_probe_card_demo.gds: nothing but 20 pad squares on
    # 106/0 in a ring -- pretend it came out of a drawer), then HARVEST
    # it back. Your real flow starts directly at
    # pads_from_gds(YOUR_FILE, YOUR_CELL, YOUR_LAYER).
    #
    # Card geometry (EXAMPLE data): pad 100x100 um; ring interior wide enough
    # for the block but only HALF its height -> the half-in/half-out case.
    # ----------------------------------------------------------------------- #
    PS = 100.0                     # pad size, from the card's datasheet
    CLR = 160.0                    # clearance we keep between a pad and anything
    inner_rows = rows // 2         # the card interior only fits this many rows
    ring_x1 = BX1 - CLR - PS
    ring_x2 = BX2 + CLR + PS
    ring_y2 = P.y_top_um + 80.0 + CLR + PS             # above the first row
    ring_y1 = P.y_top_um - inner_rows * rp - CLR - PS  # below row inner_rows-1
    CARD = os.path.abspath("test_outputs/pub_probe_card_demo.gds")

    import klayout.db as kdb
    _ly = kdb.Layout()
    _ly.dbu = 0.001
    _top = _ly.create_cell("PROBE_CARD_20")
    _li = _ly.layer(106, 0)

    def _pad(x, y):                                    # x,y = lower-left corner
        _top.shapes(_li).insert(kdb.DBox(x, y, x + PS, y + PS))

    _side_y = [ring_y1 + PS + 60.0
               + (ring_y2 - ring_y1 - 2 * PS - 120.0 - PS) * i / 4.0 for i in range(5)]
    _row_x = [ring_x1 + (ring_x2 - ring_x1 - PS) * i / 4.0 for i in range(5)]
    for cx in _row_x:                                  # 5 top + 5 bottom pads
        _pad(cx, ring_y2 - PS)
        _pad(cx, ring_y1)
    for cy in _side_y:                                 # 5 left + 5 right pads
        _pad(ring_x1, cy)
        _pad(ring_x2 - PS, cy)
    _ly.write(CARD)
    print(f"[card] stand-in probe card written: {CARD}")

    pads = pads_from_gds(CARD, "PROBE_CARD_20", "106/0", min_size_um=50.0)
    print(f"[card] harvested {len(pads)} pads back from the GDS")

    # ----------------------------------------------------------------------- #
    # STEP 4 -- net -> pad assignment, a plain table ON TOP of the harvested
    # pads. Conventions used here (edit freely): inputs on the LEFT column +
    # top row, outputs on the RIGHT column, GND on the top row (its tie rail
    # derives ABOVE the block), VDD on the bottom-left CORNER pad (a clear
    # corridor down the left margin to its rail BELOW the block). A power pad
    # needs a clean vertical lane to its net's rail; if it does not have one,
    # route_and_draw_flexdr raises an instructive error naming the conflict.
    # ----------------------------------------------------------------------- #
    def near(x, y):                # the harvested pad closest to a card point
        return min(pads, key=lambda p: (p["box_um"][0] - x) ** 2
                   + (p["box_um"][1] - y) ** 2)

    for cy, net in zip(_side_y, ["A[0]", "A[1]", "A[2]", "A[3]", "B[0]"]):
        near(ring_x1, cy)["net"] = net                 # left column: 5 inputs
    for cy, net in zip(_side_y, ["S[0]", "S[1]", "S[2]", "S[3]", "COUT"]):
        near(ring_x2 - PS, cy)["net"] = net            # right column: 5 outputs
    for cx, net in zip(_row_x, ["B[1]", "B[2]", "GND", "B[3]", "CIN"]):
        near(cx, ring_y2 - PS)["net"] = net            # top row: inputs + GND
    near(_row_x[0], ring_y1)["net"] = "VDD"            # bottom-left corner: VDD
    n_assigned = sum(1 for p in pads if p.get("net"))
    print(f"[card] assigned {n_assigned}/{len(pads)} pads "
          f"({len(pads) - n_assigned} redundant stay unused)")
    IO = {"pad_layer": "106/0", "block_layers": None, "text_size_um": 25.0, "pads": pads}

    # ----------------------------------------------------------------------- #
    # STEP 5 -- HALF-IN / HALF-OUT placement. The card's bottom pad row crosses
    # the block: forbid that horizontal band, and place_grid pushes every row
    # that would hit it BELOW the band. Rows 0..inner_rows-1 stay inside the
    # ring, the rest continue underneath; routes thread between the bottom
    # pads. (The card is fixed -- the BLOCK is what yields.)
    # ----------------------------------------------------------------------- #
    band = (ring_y1 - CLR, ring_y1 + PS + CLR)         # bottom pad row + clearance
    placement = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp,
                               forbid_y_bands=[band])
    n_in = sum(1 for (_c, _dx, dy) in placement.values() if dy > band[1])
    n_out = sum(1 for (_c, _dx, dy) in placement.values() if dy < band[0])
    print(f"[place] half-in/half-out: {n_in} devices inside the ring, "
          f"{n_out} below it (band y {band[0]:.0f}..{band[1]:.0f})")
    # The SAME band also splits the power grid: pdn_split_bands makes one PDN
    # per region and bridges them with a spine strap through the widest
    # pad-free gap of the bottom row -- power threads the card like signals do.
    PDN_BANDS = [band]
else:
    # ----------------------------------------------------------------------- #
    # NO-CARD MODE: no pads exist, and none are drawn. Every port leaves the
    # block as a bare labelled trace: spread_ports makes WIRE-END targets
    # (draw=False -> no box; the route's own metal ends there, tagged with the
    # net-name text). Inputs on the WEST edge, outputs on the EAST edge. Power
    # needs no stub at all: the PDN tie rails are auto-labelled VDD/GND by the
    # engine -- bond/probe anywhere on the rail.
    # ----------------------------------------------------------------------- #
    PDN_BANDS = None
    placement = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp)
    ys = [dy for (_c, _dx, dy) in placement.values()]
    BB = [BX1, min(ys) - 80.0, BX2, max(ys) + 80.0]
    # Snap each stub to a ROUTING-CHANNEL centre between device rows, so a
    # wire-end target never lands inside a device-row band (which would force
    # its horizontal run to jog into a neighbour channel and overflow it).
    _stack = gate_stack_height_um(nl, terms, P.y_step_um)
    _chan = [P.y_top_um - r * rp - (_stack + rp) / 2.0 for r in range(rows)]
    IO = {"pad_layer": "106/0", "text_size_um": 15.0,
          "pads": (spread_ports(BB, INPUTS, side="W", size_um=P.wire_width_um,
                                clear_um=120.0, prefix="IN", snap=_chan)
                   + spread_ports(BB, OUTPUTS, side="E", size_um=P.wire_width_um,
                                  clear_um=120.0, prefix="OUT", snap=_chan))}

# --------------------------------------------------------------------------- #
# STEP 6 -- route + draw + live LVS + positive per-pad proof.
# --------------------------------------------------------------------------- #
cut_layer = {tuple(sorted((lo, up))): P.cut_layer(lo, up) for (lo, _c, up) in P.vias}
device_terms = {xi: {t: [round(dx + terms[c][t]["center"][0], 3),
                         round(dy + terms[c][t]["center"][1], 3)] for t in terms[c]}
                for xi, (c, dx, dy) in placement.items()}
declared = [{"net": n["net_id"], "terminals": n["terminals"]} for n in nl["nets"]]

t0 = time.time()
with KLinkClient(port=PORT).connect() as c:
    ok, info, _ = eng.route_and_draw_flexdr(
        c, CELL, nl, placement, profile=P, layers=layers, vias=P.via_rules(),
        cut_layer=cut_layer, geom_path=D.GEOM, devices=DEVICES,
        verbose=True, use_rust=True, io_pads=IO, pdn_split_bands=PDN_BANDS)
    print(f"[{CELL}] FlexDR {time.time() - t0:.1f}s ok={ok} "
          f"routed={info.get('routed')}/{info.get('nets')} markers={info.get('markers')}")
    if not ok:
        for pb in info.get("problems", []):
            print("  ", pb)
        raise SystemExit(1)
    res = lvs_check(c, CELL, declared=declared, mode="lvsdb",
                    connectivity=P.connectivity_spec(),
                    terminal_provider=geom_terminal_provider(_raw),
                    placement=placement, device_terms=device_terms)
    dev = res.get("device_lvs", {})
    print(f"[{CELL}] LVS ok={res['ok']} match={dev.get('match')} "
          f"devices={dev.get('device_count')}")
    for p in res.get("problems", [])[:6]:
        print("   problem:", p)
    dump = os.path.abspath(f"test_outputs/{CELL.lower()}_dump.gds")
    c.call("layout.save_file", {"path": dump})

# Positive proof, not just "LVS didn't explode": every assigned pad/stub's
# metal must sit on the SAME extracted net as its net's device terminals;
# every unused pad must sit on NO terminal's net.
ext = ConnectivityExtractor.from_file(dump, CELL, P.connectivity_spec())
ref2net = {t: n["net_id"] for n in nl["nets"] for t in n["terminals"]}


def probe_box(layer, box):
    """Probe the box centre, then half-pitch offsets (a wire-end stub's metal
    is one wire wide; its cell centre may sit slightly off the box centre)."""
    (x1, y1, x2, y2) = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    h = P.grid_pitch_um / 2
    for ox, oy in ((0, 0), (h, 0), (-h, 0), (0, h), (0, -h),
                   (h, h), (-h, -h), (h, -h), (-h, h)):
        d = ext.probe_um(layer, cx + ox, cy + oy)
        if d is not None:
            return d
    return None


def term_derived(net):
    ref = next(r for r, nn in ref2net.items() if nn == net)
    xi, t = ref.rsplit(".", 1)
    cell_, dx, dy = placement[xi]
    return ext.probe_um(_dp[cell_][t][0], dx + terms[cell_][t]["center"][0],
                        dy + terms[cell_][t]["center"][1])


all_term_nets = {term_derived(nn) for nn in set(ref2net.values())}
okp, n_conn, n_iso = True, 0, 0
for p in IO["pads"]:
    dnet = probe_box("106/0", p["box_um"])
    net = p.get("net")
    if net:
        same = dnet is not None and dnet == term_derived(net)
        okp &= same
        n_conn += same
        print(f"pad {p['id']:10s} {net:6s}: {'CONNECTED' if same else '*** NOT CONNECTED'}")
    else:
        iso = dnet not in all_term_nets
        okp &= iso
        n_iso += 1
        print(f"pad {p['id']:10s} (unused): {'isolated OK' if iso else '*** TOUCHES a net!'}")
final = okp and res["ok"] and dev.get("match") is True
print(f"[{CELL}] RESULT: {'PASS' if final else 'FAIL'} "
      f"({n_conn} connected, {n_iso} redundant unused)")
raise SystemExit(0 if final else 1)
