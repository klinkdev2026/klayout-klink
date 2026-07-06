"""PUBLIC demo: multilayer place & route at scale, nanny-level tutorial.

What it shows, step by step (every number is EXAMPLE data -- copy this file
and edit the tables for YOUR process/circuit; klink ships only mechanisms):

  STEP 1  load the BUNDLED synthetic netlist (766 fitted-device instances --
          a toy 4-bit ALU netted by an open logic synthesizer, remapped onto
          this demo's synthetic fitted devices) and LINT it -- catch
          structural mistakes BEFORE any geometry exists
  STEP 2  layer-count advisor: compare the public 3-layer process (from
          fit_device_pnr_lvs.py) against a 7-layer example stack defined
          right here, so you can see WHY a design this size needs more
          routing layers -- fewer layers is always better when it fits
  STEP 3  fit the synthetic devices from exemplar geometry (same fitter as
          fit_device_pnr_lvs.py; produces the PCell + fit table this run
          places)
  STEP 4  floorplan: derive the row/column grid and row pitch, with a small
          port-budget allowance (2 extra tracks per channel) for the
          peripheral port runs the row-pitch formula does not see
  STEP 5  FULL primary-port marking: every non-internal, non-power net
          leaves the block as a bare labelled trace -- inputs on the WEST
          edge, outputs on the EAST edge, snapped to routing-channel centres
          so a stub never overflows into a neighbour channel
  STEP 6  route with the multilayer routing engine, live LVS, and a
          POSITIVE per-port extraction probe (every marked port's metal must
          land on the SAME extracted net as its net's device terminals)

Usage: python -m examples_klink.public.demos.digital.multilayer_pnr_lvs [--port 8766]
"""
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

# LVS is the judge here, not the DRC spacing oracle: TG_DRAW_ANYWAY=1 lets the
# engine draw despite residual DRC spacing/PRL markers (those are NOT
# electrical shorts) so we get the real, live LVS verdict on the routed
# geometry. Set before importing the engine so it reads the flag at run time.
os.environ.setdefault("TG_DRAW_ANYWAY", "1")

from klink import KLinkClient
from klink.domains.structdevice import layout_engine as eng
from klink.domains.structdevice.connectivity import ConnectivityExtractor
from klink.domains.structdevice.netlist_lint import lint_netlist
from klink.domains.structdevice.orchestrators import lvs_check
from klink.domains.structdevice.recipes import geom_terminal_provider
from klink.routing.grid.floorplan import (derive_grid, derive_row_pitch,
                                          gate_stack_height_um,
                                          layer_demand_report)
from klink.routing.grid.pad_harvest import spread_ports
from klink.routing.grid.process_profile import ProcessProfile

# The public, IP-free process + fitted synthetic device library (see that
# demo for how the devices are fitted from exemplar geometry). We reuse its
# fitter/geometry helpers so this demo owns zero device geometry of its own.
from examples_klink.public.demos.digital import fit_device_pnr_lvs as D

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8766
CELL = "PUB_ML_CPU4"

# --------------------------------------------------------------------------- #
# The multilayer example process: EXTENDS the public 3-layer process
# (fit_device_pnr_lvs.PUBLIC_PROCESS) to a 7-layer example stack. All numbers
# below are EXAMPLE data -- copy-edit for your own process. The layer numbers
# 101/104/106/109/112/115/118 (routing) and 102/105/108/111/114/117 (via cuts)
# are an arbitrary but internally consistent alternating conductor/cut
# numbering; signal_layers are the 4 clean layers ABOVE the device terminals
# (2 vertical + 2 horizontal) so parallel runs can spread across two layers
# of the same preferred direction instead of piling onto one.
# --------------------------------------------------------------------------- #
PUBLIC_MULTILAYER = ProcessProfile(
    routing_layers=("101/0", "104/0", "106/0", "109/0", "112/0", "115/0", "118/0"),
    signal_layers=("109/0", "112/0", "115/0", "118/0"),   # 2V (109,115) + 2H (112,118)
    gate_layer="101/0",
    sd_layer="104/0",
    channel_layer="103/0",
    vias=(("101/0", "102/0", "104/0"), ("104/0", "105/0", "106/0"),
          ("106/0", "108/0", "109/0"), ("109/0", "111/0", "112/0"),
          ("112/0", "114/0", "115/0"), ("115/0", "117/0", "118/0")),
    layer_directions={"101/0": "V", "104/0": "H", "106/0": "V",
                      "109/0": "V", "112/0": "H", "115/0": "V", "118/0": "H"},
    # PDN stays on the lower stack (rail=104 followpins, strap=106) so it
    # never shares a signal layer with the routed nets.
    power_rail_layer="104/0",
    power_strap_layer="106/0",
    # Same dims as the public 3-layer process (fit_device_pnr_lvs.PUBLIC_PROCESS):
    wire_width_um=5.0,
    wire_clear_um=2.0,
    prl_spacing_um=10.0,
    prl_length_um=15.0,
    via_pad_um=5.0,
    litho_tol_um=1.0,
    y_step_um=30.0,
    col_pitch_um=100.0,
    margin_um=60.0,
)

# --------------------------------------------------------------------------- #
# STEP 1 -- load the BUNDLED synthetic netlist and LINT it. 766 device
# instances / 268 gates: a toy 4-bit ALU (our own synthetic circuit) netted
# by an open logic synthesizer, with device_cell names remapped onto this
# demo's synthetic fitted devices (dev10_8 / dev20_8 / dev50_3) -- see
# fit_device_pnr_lvs.py for how those are fitted. Self-contained: no external
# path, no confidential data.
# --------------------------------------------------------------------------- #
table = D.fit_parametric_table(verbose=False)
D.build_geom(table)
DEVICES = D.devices_library()
raw = eng.load_device_geom(D.GEOM)
_, _dp, terms = eng._geom_tables(raw)

nl = json.loads((Path(__file__).parent / "cpu4.devnet.json").read_text())
rep = lint_netlist(nl, device_terms=terms)
print(f"[lint] ok={rep['ok']} errors={len(rep['errors'])} warnings={len(rep['warnings'])} "
      f"{rep['stats']}")
for e in rep["errors"][:5]:
    print("   LINT-ERROR:", e["message"], "->", e["next_action"])
if not rep["ok"]:
    raise SystemExit("fix the netlist first (see LINT-ERROR lines above)")

# --------------------------------------------------------------------------- #
# STEP 2 -- layer-count advisor: compare the public 3-layer process against
# this file's 7-layer multilayer stack. Pick the FEWEST layers that fits --
# every extra layer is a real deposition/litho/via step in the lab. A design
# this size (766 devices / 268 gates) wants the multilayer stack; a small
# circuit (see fit_device_pnr_lvs.py, chat_to_netlist_pnr.py) is comfortable
# on 3.
# --------------------------------------------------------------------------- #
adv = layer_demand_report(nl, terms, [("3-layer (public)", D.PUBLIC_PROCESS),
                                      ("7-layer (multilayer)", PUBLIC_MULTILAYER)])
print(f"[advisor] gates={adv['gates']} grid={adv['rows']}x{adv['cols']} "
      f"peak_crossing={adv['peak_crossing']}")
for c in adv["candidates"]:
    print(f"   {c['label']:20s} {c['n_routing_layers']} layers "
          f"({c['signal_v']}V+{c['signal_h']}H signal) -> core "
          f"{c['core_w_um']:.0f} x {c['core_h_um']:.0f} um = {c['core_area_mm2']} mm2")

# --------------------------------------------------------------------------- #
# STEP 3/4 -- floorplan on the 7-layer stack: derive the row/column grid and
# row pitch from the netlist's peak track crossing, then reserve 2 extra
# tracks per channel for the peripheral port runs marked in STEP 5 (those
# consume horizontal tracks the row-pitch formula never sees).
# --------------------------------------------------------------------------- #
P = replace(PUBLIC_MULTILAYER, grid_pitch_um=PUBLIC_MULTILAYER.wire_width_um
            + PUBLIC_MULTILAYER.wire_clear_um)
rows, cols = derive_grid(len(nl["groups"]))
layers = list(P.routing_layers)
rp = derive_row_pitch(nl, rows, cols, terms, y_step=P.y_step_um, width_um=P.wire_width_um,
                      wire_clear_um=P.wire_clear_um, via_pad_um=P.via_pad_um,
                      n_horiz_layers=len(layers))
rp = round(rp + 2 * (P.wire_width_um + P.wire_clear_um), 1)   # port-budget allowance
placement = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp)
cut_layer = {tuple(sorted((lo, up))): P.cut_layer(lo, up) for (lo, _c, up) in P.vias}
device_terms = {xi: {t: [round(dx + terms[c][t]["center"][0], 3),
                         round(dy + terms[c][t]["center"][1], 3)] for t in terms[c]}
                for xi, (c, dx, dy) in placement.items()}
declared = [{"net": n["net_id"], "terminals": n["terminals"]} for n in nl["nets"]]

# --------------------------------------------------------------------------- #
# STEP 5 -- FULL primary-port marking: every non-internal ($-prefixed),
# non-power net leaves the block as a bare labelled trace -- inputs on the
# WEST edge, outputs on the EAST edge. Direction classification (which
# prefixes are "inputs") is EXAMPLE data for this toy ALU's port names.
# Port stubs are snapped to routing-channel centres BETWEEN device rows so a
# stub never overflows into a neighbour channel.
# --------------------------------------------------------------------------- #
_IN_PREFIX = ("a[", "A[", "b[", "B[", "cin", "CIN", "opcode", "alu_ctrl")
ports = sorted(n["net_id"] for n in nl["nets"]
              if not n["net_id"].startswith("$") and n["net_id"] not in ("VDD", "GND"))
ins = [p for p in ports if p.startswith(_IN_PREFIX)]
outs = [p for p in ports if p not in ins]
_xs = [dx for (_c2, dx, _dy) in placement.values()]
_ys = [dy for (_c2, _dx, dy) in placement.values()]
_bb = [min(_xs) - 80.0, min(_ys) - 80.0, max(_xs) + 80.0, max(_ys) + 80.0]
_stack = gate_stack_height_um(nl, terms, P.y_step_um)
_chan = [P.y_top_um - r * rp - (_stack + rp) / 2.0 for r in range(rows)]
IO = {"pad_layer": "106/0", "text_size_um": 12.0,
      "pads": (spread_ports(_bb, ins, side="W", size_um=P.wire_width_um,
                            clear_um=120.0, prefix="IN", snap=_chan)
               + spread_ports(_bb, outs, side="E", size_um=P.wire_width_um,
                              clear_um=120.0, prefix="OUT", snap=_chan))}
print(f"[{CELL}] marking {len(ins)} inputs W + {len(outs)} outputs E "
      f"(all {len(ports)} primary ports)", flush=True)

# --------------------------------------------------------------------------- #
# STEP 6 -- route with the multilayer routing engine, live LVS, and a
# positive per-port extraction probe.
# --------------------------------------------------------------------------- #
from examples_klink.public.demos.digital import _multilayer_engine as engine

t0 = time.time()
with KLinkClient(port=PORT).connect() as c:
    ok, info, _ = eng.route_and_draw_flexdr(
        c, CELL, nl, placement, profile=P, layers=layers, vias=P.via_rules(),
        cut_layer=cut_layer, geom_path=D.GEOM, devices=DEVICES,
        verbose=True, engine=engine, io_pads=IO)
    print(f"[{CELL}] route+draw {time.time() - t0:.1f}s ok={ok} "
          f"routed={info.get('routed')}/{info.get('nets')} markers={info.get('markers')}",
          flush=True)
    if not ok:
        for pb in info.get("problems", []):
            print("   problem:", pb, flush=True)
        raise SystemExit(1)
    res = lvs_check(c, CELL, declared=declared, mode="lvsdb",
                    connectivity=P.connectivity_spec(),
                    terminal_provider=geom_terminal_provider(raw),
                    placement=placement, device_terms=device_terms)
    dev = res.get("device_lvs", {})
    print(f"[{CELL}] LVS ok={res['ok']} match={dev.get('match')} "
          f"devices={dev.get('device_count')}", flush=True)
    for p in res.get("problems", [])[:10]:
        print("   problem:", p, flush=True)
    dump = os.path.abspath(str(D._OUT / "pub_ml_cpu4_dump.gds"))
    c.call("layout.save_file", {"path": dump})

# Positive proof, not just "LVS didn't explode": every marked port's metal
# must sit on the SAME extracted net as its net's device terminals.
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


okp, n_conn = True, 0
for p in IO["pads"]:
    dnet = probe_box("106/0", p["box_um"])
    net = p.get("net")
    same = dnet is not None and dnet == term_derived(net)
    okp &= same
    n_conn += same
    status = "CONNECTED" if same else "*** NOT CONNECTED"
    print(f"port {p['id']:10s} {net:20s}: {status}")
final = okp and res["ok"] and dev.get("match") is True
print(f"[{CELL}] RESULT: {'PASS' if final else 'FAIL'} "
      f"({n_conn}/{len(IO['pads'])} ports connected)")
raise SystemExit(0 if final else 1)
