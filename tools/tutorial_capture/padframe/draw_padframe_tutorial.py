"""Stage-by-stage probe-card padframe tutorial capture.

Mirrors examples_klink/public/demos/digital/padframe_pnr_lvs.py (BOTH modes: the
20-pad stand-in card AND --no-card bare labelled stubs), but issues the build
incrementally so each stage can be screenshotted for the tutorial:

  stage 1  lint + layer advisor + the flat-placed synthetic device block
  stage 2  stand-in probe card GDS -> pads_from_gds harvest -> draw the ring
  stage 3  net -> pad assignment table (labels on the assigned pads)
  stage 4  half-in/half-out placement (place_grid forbid_y_bands)
  stage 5  route + draw (io_pads + pdn_split_bands) -> live LVS + per-pad proof
  stage 6  --no-card variant in its own cell -> live LVS + per-stub proof

This script owns its own disposable tab lifecycle end to end: it opens a
fresh tab via the typed `view.new_tab` RPC, draws/screenshots there, then
closes that tab and restores whatever tab was current beforehand
(`view.activate_tab`, skipped when `previous_current_index` is -1) -- see
CLAUDE.md's tab-safety rule: any pre-existing tab holds the user's own
session and must never be touched.

See tools/tutorial_capture/README.md for when/why to re-run this.
"""
import argparse
import base64
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

# tools/tutorial_capture/padframe/draw_padframe_tutorial.py -> repo root is
# 3 parents up (padframe/ -> tutorial_capture/ -> tools/ -> repo root).
# Derived from __file__ (never a hardcoded absolute path) so `examples_klink`
# -- a repo-only package, not shipped in the wheel -- resolves no matter the
# caller's cwd.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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

# The public, IP-free process + fitted synthetic device library (same import
# the demo itself uses; this capture owns zero device geometry).
from examples_klink.public.demos import fit_device_pnr_lvs as D

REPO_ROOT = _REPO_ROOT
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "padframe"

CELL = "PUB_PADFRAME_ADD4"
CELL_NOCARD = "PUB_PADFRAME_ADD4_NOCARD"

INPUTS = ["A[0]", "A[1]", "A[2]", "A[3]", "B[0]", "B[1]", "B[2]", "B[3]", "CIN"]
OUTPUTS = ["S[0]", "S[1]", "S[2]", "S[3]", "COUT"]


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUT),
        help="Directory for screenshots + build_report.json (default: %(default)s)")
    parser.add_argument(
        "--klink-port", type=int, default=8767,
        help="klink RPC port of the live KLayout session (default: %(default)s)")
    return parser.parse_args()


def verify_tab(client, index):
    """Screenshot iron rule: verify the CURRENT tab is the disposable one we
    created, every time, right before we touch the view."""
    tabs = client.call("view.list_tabs", {})
    cur = tabs["tabs"][tabs["current_index"]]
    assert tabs["current_index"] == index, (
        f"current tab is {cur!r} (current_index={tabs['current_index']}), "
        f"expected our disposable tab at index {index} -- refusing to act "
        "on a tab we did not create")
    return cur


def snap(client, index, out_dir, name, bbox_um=None):
    verify_tab(client, index)
    if bbox_um is not None:
        client.zoom_box(bbox_um=list(bbox_um))
    else:
        client.zoom_fit()
    shot = client.screenshot(mode="base64", width_px=1400)
    data = shot["data_url"].split(",", 1)[1]
    path = os.path.join(out_dir, name)
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    print("saved", path)
    return path


def main():
    args = _parse_args()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    report = {}

    # ---------------- offline prep (identical numbers to the demo) ----------
    table = D.fit_parametric_table(verbose=False)
    D.build_geom(table)
    DEVICES = D.devices_library()
    raw = eng.load_device_geom(D.GEOM)
    _, dp, terms = eng._geom_tables(raw)

    P = replace(D.PUBLIC_PROCESS, wire_clear_um=5.0, y_step_um=35.0,
                col_pitch_um=100.0,
                grid_pitch_um=D.PUBLIC_PROCESS.wire_width_um + 5.0)

    nl = json.loads((REPO_ROOT / "examples_klink" / "public" / "demos"
                     / "add4.devnet.json").read_text())
    lint = lint_netlist(nl, device_terms=terms)
    print(f"[lint] ok={lint['ok']} errors={len(lint['errors'])} "
          f"warnings={len(lint['warnings'])} {lint['stats']}")
    assert lint["ok"], lint["errors"]
    report["lint"] = {"ok": lint["ok"], "stats": lint["stats"]}

    # 7-layer EXAMPLE stack for the advisor comparison (same as the demo).
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
        via_pad_um=5.0, litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0,
        margin_um=60.0)
    adv = layer_demand_report(nl, terms, [("public-3L", D.PUBLIC_PROCESS),
                                          ("example-7L", PUBLIC_MULTILAYER)])
    report["advisor"] = adv
    print(f"[advisor] gates={adv['gates']} grid={adv['rows']}x{adv['cols']} "
          f"peak_crossing={adv['peak_crossing']}")
    for c in adv["candidates"]:
        print(f"   {c['label']:12s} {c['n_routing_layers']} layers "
              f"({c['signal_v']}V+{c['signal_h']}H signal) -> core "
              f"{c['core_w_um']:.0f} x {c['core_h_um']:.0f} um = {c['core_area_mm2']} mm2")

    rows, cols = derive_grid(len(nl["groups"]))
    layers = list(P.routing_layers)
    rp = derive_row_pitch(nl, rows, cols, terms, y_step=P.y_step_um,
                          width_um=P.wire_width_um, wire_clear_um=P.wire_clear_um,
                          via_pad_um=P.via_pad_um, n_horiz_layers=len(layers))
    rp = round(rp + 2 * (P.wire_width_um + P.wire_clear_um), 1)
    report["floorplan"] = {"rows": rows, "cols": cols, "row_pitch_um": rp}

    flat = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp)
    xs = [dx for (_c, dx, _dy) in flat.values()]
    BX1, BX2 = min(xs) - 80.0, max(xs) + 80.0

    # ---------------- stand-in probe card (same numbers as the demo) --------
    PS = 100.0
    CLR = 160.0
    inner_rows = rows // 2
    ring_x1 = BX1 - CLR - PS
    ring_x2 = BX2 + CLR + PS
    ring_y2 = P.y_top_um + 80.0 + CLR + PS
    ring_y1 = P.y_top_um - inner_rows * rp - CLR - PS
    CARD = os.path.join(out_dir, "pub_probe_card_demo.gds")

    import klayout.db as kdb
    _ly = kdb.Layout()
    _ly.dbu = 0.001
    _top = _ly.create_cell("PROBE_CARD_20")
    _li = _ly.layer(106, 0)

    def _pad(x, y):
        _top.shapes(_li).insert(kdb.DBox(x, y, x + PS, y + PS))

    side_y = [ring_y1 + PS + 60.0
              + (ring_y2 - ring_y1 - 2 * PS - 120.0 - PS) * i / 4.0 for i in range(5)]
    row_x = [ring_x1 + (ring_x2 - ring_x1 - PS) * i / 4.0 for i in range(5)]
    for cx in row_x:
        _pad(cx, ring_y2 - PS)
        _pad(cx, ring_y1)
    for cy in side_y:
        _pad(ring_x1, cy)
        _pad(ring_x2 - PS, cy)
    _ly.write(CARD)
    print(f"[card] stand-in probe card written: {CARD}")

    pads = pads_from_gds(CARD, "PROBE_CARD_20", "106/0", min_size_um=50.0)
    print(f"[card] harvested {len(pads)} pads back from the GDS")
    report["card"] = {"pads_harvested": len(pads),
                      "ring_um": [ring_x1, ring_y1, ring_x2, ring_y2]}

    def near(x, y):
        return min(pads, key=lambda p: (p["box_um"][0] - x) ** 2
                   + (p["box_um"][1] - y) ** 2)

    for cy, net in zip(side_y, ["A[0]", "A[1]", "A[2]", "A[3]", "B[0]"]):
        near(ring_x1, cy)["net"] = net
    for cy, net in zip(side_y, ["S[0]", "S[1]", "S[2]", "S[3]", "COUT"]):
        near(ring_x2 - PS, cy)["net"] = net
    for cx, net in zip(row_x, ["B[1]", "B[2]", "GND", "B[3]", "CIN"]):
        near(cx, ring_y2 - PS)["net"] = net
    near(row_x[0], ring_y1)["net"] = "VDD"
    n_assigned = sum(1 for p in pads if p.get("net"))
    print(f"[card] assigned {n_assigned}/{len(pads)} pads")
    report["card"]["assigned"] = n_assigned
    IO = {"pad_layer": "106/0", "block_layers": None, "text_size_um": 25.0,
          "pads": pads}

    band = (ring_y1 - CLR, ring_y1 + PS + CLR)
    placement = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp,
                               forbid_y_bands=[band])
    n_in = sum(1 for (_c, _dx, dy) in placement.values() if dy > band[1])
    n_out = sum(1 for (_c, _dx, dy) in placement.values() if dy < band[0])
    print(f"[place] half-in/half-out: {n_in} in, {n_out} out "
          f"(band y {band[0]:.0f}..{band[1]:.0f})")
    report["placement"] = {"in_ring": n_in, "below_ring": n_out,
                           "band_um": list(band)}

    # Consistent frame: ring + the DEEPEST placement (band-split rows go lower
    # than the flat block), padded.
    ys_band = [dy for (_c, _dx, dy) in placement.values()]
    frame = (ring_x1 - 60.0, min(ys_band) - 140.0, ring_x2 + 60.0, ring_y2 + 60.0)

    cut_layer = {tuple(sorted((lo, up))): P.cut_layer(lo, up)
                 for (lo, _c, up) in P.vias}

    def device_terms_of(pl):
        return {xi: {t: [round(dx + terms[c][t]["center"][0], 3),
                         round(dy + terms[c][t]["center"][1], 3)] for t in terms[c]}
                for xi, (c, dx, dy) in pl.items()}

    declared = [{"net": n["net_id"], "terminals": n["terminals"]} for n in nl["nets"]]

    def probe_pads(dump, cell, pl, io_entries):
        """The demo's positive per-pad proof: every assigned pad/stub's metal
        sits on the SAME extracted net as its net's device terminals; every
        unused pad sits on NO terminal's net."""
        ext = ConnectivityExtractor.from_file(dump, cell, P.connectivity_spec())
        ref2net = {t: n["net_id"] for n in nl["nets"] for t in n["terminals"]}

        def probe_box(layer, box):
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
            cell_, dx, dy = pl[xi]
            return ext.probe_um(dp[cell_][t][0], dx + terms[cell_][t]["center"][0],
                                dy + terms[cell_][t]["center"][1])

        all_term_nets = {term_derived(nn) for nn in set(ref2net.values())}
        okp, n_conn, n_iso = True, 0, 0
        for p in io_entries:
            dnet = probe_box("106/0", p["box_um"])
            net = p.get("net")
            if net:
                same = dnet is not None and dnet == term_derived(net)
                okp &= same
                n_conn += same
            else:
                iso = dnet not in all_term_nets
                okp &= iso
                n_iso += 1
        return okp, n_conn, n_iso

    # ---------------- live stages ------------------------------------------
    with KLinkClient(port=args.klink_port).connect() as c:
        new_tab = c.new_tab(cell_name=CELL)
        our_index = new_tab["index"]
        previous_index = new_tab["previous_current_index"]
        print("opened disposable tab:", new_tab["title"], "index", our_index)
        try:
            # ---- Stage 1: lint + advisor + flat-placed device block --------
            eng.ensure_pcell(c, DEVICES)
            for L in (101, 103, 104):
                c.layer_ensure(L, 0)
            c.layer_ensure(106, 0)
            c.instance_insert_pcell_many(CELL, [
                eng._pcell_item(DEVICES, cc, dx, dy)
                for (cc, dx, dy) in flat.values()])
            snap(c, our_index, out_dir, "step-01-block.png", frame)

            # ---- Stage 2: harvested probe card ring -------------------------
            li106 = c.layer_ensure(106, 0)["layer_index"]
            c.shape_insert_boxes(CELL, layer_index=li106,
                                 boxes_um=[p["box_um"] for p in pads])
            snap(c, our_index, out_dir, "step-02-card.png", frame)

            # ---- Stage 3: net -> pad assignment labels ----------------------
            for p in pads:
                x1, y1, x2, y2 = p["box_um"]
                c.shape_insert_text(CELL, p.get("net") or p["id"],
                                    layer_index=li106,
                                    position_um=[x1, y2 + 6.0], size_um=25.0)
            snap(c, our_index, out_dir, "step-03-assign.png", frame)

            # ---- Stage 4: half-in/half-out placement ------------------------
            c.instance_delete(CELL, all=True)
            c.instance_insert_pcell_many(CELL, [
                eng._pcell_item(DEVICES, cc, dx, dy)
                for (cc, dx, dy) in placement.values()])
            snap(c, our_index, out_dir, "step-04-halfinout.png", frame)

            # ---- Stage 5: route + draw + LVS (card mode) --------------------
            # route_and_draw_flexdr deletes + recreates the cell, so the
            # staging geometry above is replaced by the real build.
            t0 = time.time()
            ok, info, _ = eng.route_and_draw_flexdr(
                c, CELL, nl, placement, profile=P, layers=layers,
                vias=P.via_rules(), cut_layer=cut_layer, geom_path=D.GEOM,
                devices=DEVICES, verbose=True, use_rust=True, io_pads=IO,
                pdn_split_bands=[band])
            dt = time.time() - t0
            print(f"[{CELL}] FlexDR {dt:.1f}s ok={ok} "
                  f"routed={info.get('routed')}/{info.get('nets')} "
                  f"markers={info.get('markers')}")
            assert ok, info.get("problems")
            report["card_route"] = {"seconds": round(dt, 1), "ok": ok,
                                    "routed": info.get("routed"),
                                    "nets": info.get("nets"),
                                    "markers": info.get("markers")}
            c.show_cell(CELL, zoom_fit=True)
            snap(c, our_index, out_dir, "step-05-routed.png", frame)
            # detail: VDD corner pad + its in-band strap down to the rail
            vp = next(p for p in pads if p.get("net") == "VDD")["box_um"]
            snap(c, our_index, out_dir, "step-05-detail-vdd.png",
                 (vp[0] - 90.0, vp[1] - 260.0, vp[2] + 260.0, vp[3] + 90.0))
            # detail: routes + PDN spine threading the bottom pad row (band)
            snap(c, our_index, out_dir, "step-05-detail-thread.png",
                 (row_x[1] - 120.0, band[0] - 120.0, row_x[3] + PS + 120.0,
                  band[1] + 120.0))

            res = lvs_check(c, CELL, declared=declared, mode="lvsdb",
                            connectivity=P.connectivity_spec(),
                            terminal_provider=geom_terminal_provider(raw),
                            placement=placement,
                            device_terms=device_terms_of(placement))
            dev = res.get("device_lvs", {})
            print(f"[{CELL}] LVS ok={res['ok']} match={dev.get('match')} "
                  f"devices={dev.get('device_count')}")
            dump = os.path.join(out_dir, f"{CELL.lower()}_dump.gds")
            c.call("layout.save_file", {"path": os.path.abspath(dump)})
            okp, n_conn, n_iso = probe_pads(dump, CELL, placement, IO["pads"])
            print(f"[{CELL}] pad proof ok={okp} connected={n_conn} isolated={n_iso}")
            report["card_lvs"] = {"ok": res["ok"], "match": dev.get("match"),
                                  "devices": dev.get("device_count"),
                                  "pad_proof_ok": okp, "pads_connected": n_conn,
                                  "pads_isolated": n_iso}
            assert res["ok"] and dev.get("match") is True and okp

            # ---- Stage 6: --no-card mode ------------------------------------
            pl2 = flat
            ys2 = [dy for (_c2, _dx, dy) in pl2.values()]
            BB = [BX1, min(ys2) - 80.0, BX2, max(ys2) + 80.0]
            stack = gate_stack_height_um(nl, terms, P.y_step_um)
            chan = [P.y_top_um - r * rp - (stack + rp) / 2.0 for r in range(rows)]
            IO2 = {"pad_layer": "106/0", "text_size_um": 15.0,
                   "pads": (spread_ports(BB, INPUTS, side="W",
                                         size_um=P.wire_width_um,
                                         clear_um=120.0, prefix="IN", snap=chan)
                            + spread_ports(BB, OUTPUTS, side="E",
                                           size_um=P.wire_width_um,
                                           clear_um=120.0, prefix="OUT",
                                           snap=chan))}
            t0 = time.time()
            ok2, info2, _ = eng.route_and_draw_flexdr(
                c, CELL_NOCARD, nl, pl2, profile=P, layers=layers,
                vias=P.via_rules(), cut_layer=cut_layer, geom_path=D.GEOM,
                devices=DEVICES, verbose=True, use_rust=True, io_pads=IO2,
                pdn_split_bands=None)
            dt2 = time.time() - t0
            print(f"[{CELL_NOCARD}] FlexDR {dt2:.1f}s ok={ok2} "
                  f"routed={info2.get('routed')}/{info2.get('nets')} "
                  f"markers={info2.get('markers')}")
            assert ok2, info2.get("problems")
            report["nocard_route"] = {"seconds": round(dt2, 1), "ok": ok2,
                                      "routed": info2.get("routed"),
                                      "nets": info2.get("nets"),
                                      "markers": info2.get("markers")}
            c.show_cell(CELL_NOCARD, zoom_fit=True)
            frame2 = (BB[0] - 220.0, BB[1] - 120.0, BB[2] + 220.0, BB[3] + 120.0)
            snap(c, our_index, out_dir, "step-06-nocard.png", frame2)
            # detail: west-edge bare labelled stubs (wire ends, no pad boxes)
            wst = [p["box_um"] for p in IO2["pads"] if p["id"].startswith("IN_W")]
            wy = sorted((b[1] + b[3]) / 2 for b in wst)
            snap(c, our_index, out_dir, "step-06-detail-stub.png",
                 (BB[0] - 200.0, wy[0] - 60.0, BB[0] + 180.0, wy[2] + 60.0))

            res2 = lvs_check(c, CELL_NOCARD, declared=declared, mode="lvsdb",
                             connectivity=P.connectivity_spec(),
                             terminal_provider=geom_terminal_provider(raw),
                             placement=pl2, device_terms=device_terms_of(pl2))
            dev2 = res2.get("device_lvs", {})
            print(f"[{CELL_NOCARD}] LVS ok={res2['ok']} match={dev2.get('match')} "
                  f"devices={dev2.get('device_count')}")
            dump2 = os.path.join(out_dir, f"{CELL_NOCARD.lower()}_dump.gds")
            c.call("layout.save_file", {"path": os.path.abspath(dump2)})
            okp2, n_conn2, n_iso2 = probe_pads(dump2, CELL_NOCARD, pl2, IO2["pads"])
            print(f"[{CELL_NOCARD}] stub proof ok={okp2} connected={n_conn2} "
                  f"isolated={n_iso2}")
            report["nocard_lvs"] = {"ok": res2["ok"], "match": dev2.get("match"),
                                    "devices": dev2.get("device_count"),
                                    "stub_proof_ok": okp2,
                                    "stubs_connected": n_conn2,
                                    "stubs_isolated": n_iso2}
            assert res2["ok"] and dev2.get("match") is True and okp2

            with open(os.path.join(out_dir, "build_report.json"), "w") as f:
                json.dump(report, f, indent=2, default=str)
            print(json.dumps(report, indent=2, default=str))
        finally:
            c.call("view.close_tab", {"view_index": our_index})
            if previous_index != -1:
                c.call("view.activate_tab", {"index": previous_index})
                print("restored previous tab index", previous_index)
            else:
                print("no previous tab to restore (none was open)")


if __name__ == "__main__":
    main()
