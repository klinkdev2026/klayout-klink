"""CHAT -> NETLIST -> P&R -> LVS, nanny-level: how a HAND-WRITTEN device
netlist (e.g. one an agent writes down from a conversation) becomes a routed,
LVS-verified layout with every observable node marked at the periphery.

The imagined conversation (this is the whole "spec"):

    user: "用拟合的器件给我搭一个三级环形振荡器,三个节点都引出来方便探针观察"
    (build a 3-stage ring oscillator from our fitted devices; bring all three
     stage nodes out so I can probe them)

Below, every sentence of that request maps to a few explicit netlist lines --
that is the entire trick: a devnet is just {instances, nets, groups}, so an
agent (or a person) can write one by hand for ANY circuit topology, including
ones no logic synthesizer would ever emit. `lint_netlist` then checks the
hand-written result against everything the engine actually assumes, with
fix-it messages, BEFORE any geometry exists.

Gate style used here (copied from how the synthesized designs wire this
NMOS-diode-load family -- inspect any INV group of a yosys devnet):
    load  device: D -> VDD, G + S -> the gate's OUTPUT node (diode)
    driver device: D -> OUTPUT node, G -> INPUT node, S -> GND
    one group per gate, instances listed [load, driver] (load drawn on top).

Everything process/device-specific is THIS demo's data (synthetic fitted
devices + the public 3-layer process from fit_device_pnr_lvs); klink ships
only mechanisms.

Run: python -m examples_klink.public.demos.digital.chat_to_netlist_pnr [--port 8766]
"""
import os
import sys
import time
from dataclasses import replace

from klink import KLinkClient
from klink.domains.structdevice import layout_engine as eng
from klink.domains.structdevice.connectivity import ConnectivityExtractor
from klink.domains.structdevice.netlist_lint import lint_netlist
from klink.domains.structdevice.orchestrators import lvs_check
from klink.domains.structdevice.recipes import geom_terminal_provider
from klink.routing.grid.floorplan import derive_grid, derive_row_pitch
from klink.routing.grid.pad_harvest import spread_ports

# The public, IP-free process + fitted synthetic device library (see that
# demo for how the devices are fitted from exemplar geometry).
from examples_klink.public.demos.digital import fit_device_pnr_lvs as D

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8766
CELL = "CHAT_RING3"

# --------------------------------------------------------------------------- #
# STEP 1 -- write the netlist BY HAND, one chat requirement at a time.
#
# "三级环形振荡器" (3-stage ring oscillator): three inverters in a loop.
# Stage k: input n<k>, output n<k+1>; the last output feeds the first input.
# Each inverter = one load (dev50_3) + one driver, wired in the diode-load
# style documented in the module docstring. Driver sizes ALTERNATE
# (dev10_8 / dev20_8): mixing fitted device sizes is free -- and a ring of
# perfectly IDENTICAL stages is fully symmetric, which KLayout's netlist
# comparer cannot anchor (it crashes on the automorphism); varying one
# device size per stage breaks the symmetry.
# --------------------------------------------------------------------------- #
STAGES = 3
DRIVER_OF = {1: "dev10_8", 2: "dev20_8", 3: "dev10_8"}
nets = {f"n{k}": [] for k in range(1, STAGES + 1)}
nets["VDD"], nets["GND"] = [], []
instances, groups = [], []
for k in range(1, STAGES + 1):
    inp, out = f"n{k}", f"n{k % STAGES + 1}"
    load, drv = f"XL{k}", f"XD{k}"
    instances += [{"instance_id": load, "device_cell": "dev50_3"},
                  {"instance_id": drv, "device_cell": DRIVER_OF[k]}]
    groups.append({"group": f"inv{k}", "gate_type": "INV", "instances": [load, drv]})
    nets["VDD"].append(f"{load}.D")                    # load drain to VDD
    nets[out] += [f"{load}.G", f"{load}.S", f"{drv}.D"]  # diode + driver drain = output
    nets[inp].append(f"{drv}.G")                       # driver gate = input
    nets["GND"].append(f"{drv}.S")                     # driver source to GND
NETLIST = {"instances": instances,
           "nets": [{"net_id": n, "terminals": t} for n, t in nets.items()],
           "groups": groups}

# --------------------------------------------------------------------------- #
# STEP 2 -- LINT the hand-written netlist against the engine's real
# assumptions. THIS is what makes hand-written netlists safe: typo a terminal
# ("XD1.Q"), forget a group, reuse a terminal on two nets -- you get a
# fix-it message here instead of a crash (or worse, a silent short) later.
# --------------------------------------------------------------------------- #
table = D.fit_parametric_table(verbose=False)
D.build_geom(table)
DEVICES = D.devices_library()
raw = eng.load_device_geom(D.GEOM)
_, _dp, terms = eng._geom_tables(raw)
rep = lint_netlist(NETLIST, device_terms=terms)
print(f"[lint] ok={rep['ok']} errors={len(rep['errors'])} warnings={len(rep['warnings'])} "
      f"{rep['stats']}")
for e in rep["errors"]:
    print("   LINT-ERROR:", e["message"], "->", e["next_action"])
if not rep["ok"]:
    raise SystemExit("fix the netlist first")

# --------------------------------------------------------------------------- #
# STEP 3 -- floorplan + placement. Density knobs are DESIGN data: these values
# are the tested-tight settings from fit_device_pnr_lvs (col_pitch/y_step were
# proven routable down to ~90/30 there; we keep a little margin).
# --------------------------------------------------------------------------- #
P = replace(D.PUBLIC_PROCESS, wire_clear_um=5.0,
            grid_pitch_um=D.PUBLIC_PROCESS.wire_width_um + 5.0,
            col_pitch_um=100.0, y_step_um=35.0)
rows, cols = derive_grid(len(NETLIST["groups"]))
layers = list(P.routing_layers)
rp = derive_row_pitch(NETLIST, rows, cols, terms, y_step=P.y_step_um,
                      width_um=P.wire_width_um, wire_clear_um=P.wire_clear_um,
                      via_pad_um=P.via_pad_um, n_horiz_layers=len(layers))
placement = eng.place_grid(NETLIST, rows, cols, profile=P, row_pitch=rp)

# --------------------------------------------------------------------------- #
# STEP 4 -- "三个节点都引出来": every stage node leaves the block as a bare
# labelled trace on the EAST edge (no pad boxes -- you probe/bond your own).
# Power needs nothing extra: the PDN tie rails are auto-labelled VDD/GND.
# --------------------------------------------------------------------------- #
xs = [dx for (_c, dx, _dy) in placement.values()]
ys = [dy for (_c, _dx, dy) in placement.values()]
BB = [min(xs) - 80.0, min(ys) - 80.0, max(xs) + 80.0, max(ys) + 80.0]
IO = {"pad_layer": "106/0", "text_size_um": 12.0,
      "pads": spread_ports(BB, [f"n{k}" for k in range(1, STAGES + 1)],
                           side="E", size_um=P.wire_width_um, clear_um=100.0,
                           prefix="TAP")}

# --------------------------------------------------------------------------- #
# STEP 5 -- route + draw + live LVS + positive per-tap proof.
# --------------------------------------------------------------------------- #
cut_layer = {tuple(sorted((lo, up))): P.cut_layer(lo, up) for (lo, _c, up) in P.vias}
device_terms = {xi: {t: [round(dx + terms[c][t]["center"][0], 3),
                         round(dy + terms[c][t]["center"][1], 3)] for t in terms[c]}
                for xi, (c, dx, dy) in placement.items()}
declared = [{"net": n["net_id"], "terminals": n["terminals"]} for n in NETLIST["nets"]]

t0 = time.time()
with KLinkClient(port=PORT).connect() as c:
    ok, info, _ = eng.route_and_draw_flexdr(
        c, CELL, NETLIST, placement, profile=P, layers=layers, vias=P.via_rules(),
        cut_layer=cut_layer, geom_path=D.GEOM, devices=DEVICES,
        verbose=True, use_rust=True, io_pads=IO)
    print(f"[{CELL}] FlexDR {time.time() - t0:.1f}s ok={ok} "
          f"routed={info.get('routed')}/{info.get('nets')} markers={info.get('markers')}")
    if not ok:
        for pb in info.get("problems", []):
            print("  ", pb)
        raise SystemExit(1)
    res = lvs_check(c, CELL, declared=declared, mode="lvsdb",
                    connectivity=P.connectivity_spec(),
                    terminal_provider=geom_terminal_provider(raw),
                    placement=placement, device_terms=device_terms)
    dev = res.get("device_lvs", {})
    print(f"[{CELL}] LVS ok={res['ok']} match={dev.get('match')} "
          f"devices={dev.get('device_count')}")
    dump = os.path.abspath(str(D._OUT / "chat_ring3_dump.gds"))
    c.call("layout.save_file", {"path": dump})

ext = ConnectivityExtractor.from_file(dump, CELL, P.connectivity_spec())
ref2net = {t: n["net_id"] for n in NETLIST["nets"] for t in n["terminals"]}
okp = True
h = P.grid_pitch_um / 2
for p in IO["pads"]:
    x1, y1, x2, y2 = p["box_um"]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    dnet = None
    for ox, oy in ((0, 0), (h, 0), (-h, 0), (0, h), (0, -h), (h, h), (-h, -h), (h, -h), (-h, h)):
        dnet = ext.probe_um("106/0", cx + ox, cy + oy)
        if dnet is not None:
            break
    ref = next(r for r, nn in ref2net.items() if nn == p["net"])
    xi, t = ref.rsplit(".", 1)
    cell_, dx, dy = placement[xi]
    tnet = ext.probe_um(_dp[cell_][t][0], dx + terms[cell_][t]["center"][0],
                        dy + terms[cell_][t]["center"][1])
    same = dnet is not None and dnet == tnet
    okp &= same
    print(f"tap {p['id']} {p['net']}: {'CONNECTED' if same else '*** NOT CONNECTED'}")
final = okp and res["ok"] and dev.get("match") is True
print("RESULT:", "PASS (hand-written netlist -> lint -> P&R -> LVS -> labelled taps)"
      if final else "FAIL")
raise SystemExit(0 if final else 1)
