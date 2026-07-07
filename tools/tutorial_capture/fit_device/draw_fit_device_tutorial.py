"""Stage-by-stage fit-device tutorial capture (repo-only demo).

Mirrors examples_klink/public/demos/digital/fit_device_pnr_lvs.py -- the same
functions (fit_parametric_table / build_geom / devices_library /
route_and_draw_flexdr / lvs_check) the demo calls in one shot -- but issues
them incrementally (exemplar geometry -> fit -> registered PCell variants ->
placement -> detailed routing -> live LVS) so each stage can be
screenshotted for the tutorial. No geometry is reimplemented here: stages 1-2
draw the demo's own _device_boxes() output, stages 3-6 call the demo's own
helpers, so the tutorial's staging is a documented slice of the real flow.

This script owns its own disposable tab lifecycle end to end: it opens a
fresh tab (FIT_DEVICE_TUTORIAL) via the typed `view.new_tab` RPC, creates its
stage cells there (FIT_EXEMPLARS / FIT_VARIANTS / FIT_ADD4), screenshots,
then closes that tab and restores whatever tab was current beforehand
(`view.activate_tab`, skipped when `previous_current_index` is -1). Any
pre-existing tab is the user's own session and is never touched.

Uses the honest view.* unit semantics: view.zoom_box(bbox_um=[...]) and
view.screenshot(bbox_um=[...]) both take plain microns.

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

# tools/tutorial_capture/fit_device/draw_fit_device_tutorial.py -> repo root
# is 3 parents up. Needed on sys.path so `examples_klink.public.demos.*` (a
# repo-only module, not part of the installed klink package) is importable.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from klink import KLinkClient
from klink.domains.structdevice import layout_engine as eng
from klink.domains.structdevice.orchestrators import lvs_check
from klink.domains.structdevice.recipes import geom_terminal_provider
from klink.routing.grid.floorplan import derive_grid, derive_row_pitch
from examples_klink.public.demos.digital.fit_device_pnr_lvs import (
    BUILD_DEVICES,
    EXEMPLAR_SIZES,
    GEOM,
    PUBLIC_PROCESS,
    _device_boxes,
    build_geom,
    devices_library,
    fit_parametric_table,
)

DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "fit_device"

TAB_CELL = "FIT_DEVICE_TUTORIAL"          # cell view.new_tab opens with
CELL_EXEMPLARS = "FIT_EXEMPLARS"          # stage 1-2
CELL_VARIANTS = "FIT_VARIANTS"            # stage 3
CELL_BUILD = "FIT_ADD4"                   # stage 4-6

# Stage 1: the 4 exemplars in one row, x offsets chosen so the widest
# exemplar (W=50: gate plate x in [-35, 33]) never touches its neighbour.
EXEMPLAR_DX = (0.0, 110.0, 220.0, 330.0)
FRAME_EXEMPLARS = (-50.0, -36.0, 385.0, 26.0)

# Stage 2: exact square crop on exemplar #1 (W=10, L=4; device extents
# x[-15, 13], y[-11, 11]) for the annotated fit figure. Square crop + equal
# width/height px -> exactly linear um -> pixel mapping for annotate_detail.
EXEMPLAR_DETAIL_UM = (-29.0, -24.0, 19.0, 24.0)

# Stage 3: the 3 registered-PCell variants at i*180 (same spacing the demo's
# DEMO_DEVICES viewer cell uses); widest variant dev50_3 body x in [-35, 33].
FRAME_VARIANTS = (-60.0, -40.0, 460.0, 30.0)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory to write screenshots + build_report.json into (default: %(default)s)",
    )
    parser.add_argument(
        "--klink-port",
        type=int,
        default=8766,
        help="klink RPC port of the live KLayout session (default: %(default)s)",
    )
    return parser.parse_args()


def verify_tab(client, index):
    """Screenshot iron rule: verify the CURRENT tab is the disposable one we
    created, every time, right before we touch the view. Any pre-existing
    tab is the user's own session and must never be acted on."""
    tabs = client.call("view.list_tabs", {})
    cur = tabs["tabs"][tabs["current_index"]]
    assert tabs["current_index"] == index, (
        f"current tab is {cur!r} (current_index={tabs['current_index']}), "
        f"expected our disposable tab at index {index} -- refusing to act "
        "on a tab we did not create"
    )
    return cur


def snap(client, index, out_dir, name, bbox_um=None, *, exact=False,
         width_px=1200, height_px=None):
    """Verify tab identity, then capture. bbox_um=None -> zoom_fit;
    exact=False -> zoom_box then screenshot (aspect-expanded framing);
    exact=True -> view.screenshot(bbox_um=...) clips exactly to the box
    (pair width/height px with the box aspect for a linear um->px map)."""
    verify_tab(client, index)
    kwargs = {"mode": "base64", "width_px": width_px}
    if height_px is not None:
        kwargs["height_px"] = height_px
    if bbox_um is not None and exact:
        kwargs["bbox_um"] = list(bbox_um)
    elif bbox_um is not None:
        client.zoom_box(bbox_um=list(bbox_um))
    else:
        client.zoom_fit()
    shot = client.screenshot(**kwargs)
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

    # ---- off-KLayout part of the demo flow (identical to the demo) --------
    table = fit_parametric_table(verbose=True)
    n_lin = sum(1 for role in table["styles"]["default"]["roles"].values()
                for e in role["edges"].values()
                if any(c != 0 for c in e["coef"].values()))
    n_con = sum(1 for role in table["styles"]["default"]["roles"].values()
                for e in role["edges"].values()
                if all(c == 0 for c in e["coef"].values()))
    build_geom(table)
    DEVICES = devices_library()
    nl = json.loads((REPO_ROOT / "examples_klink" / "public" / "demos"
                     / "add4.devnet.json").read_text())

    with KLinkClient(port=args.klink_port).connect() as c:
        new_tab = c.new_tab(cell_name=TAB_CELL)
        our_index = new_tab["index"]
        previous_index = new_tab["previous_current_index"]
        print("opened disposable tab:", new_tab["title"], "index", our_index)
        # A fresh tab displays 0 hierarchy levels; the fitted-device PCell
        # instances live one level below the stage cells, so raise the
        # displayed depth or stages 3-5 show only instance outline frames.
        c.hier_levels(max=4)

        try:
            # ---- Stage 1: exemplar geometry (raw boxes, pre-fit) ---------
            c.cell_create(CELL_EXEMPLARS)
            for L in (101, 103, 104, 6):
                c.layer_ensure(L, 0)
            label_li = c.layer_ensure(6, 0)["layer_index"]
            items = []
            for (W, Lp), dx in zip(EXEMPLAR_SIZES, EXEMPLAR_DX):
                for _role, (ly, (x1, y1, x2, y2)) in _device_boxes(W, Lp).items():
                    l, d = (int(v) for v in ly.split("/"))
                    items.append({"kind": "box", "layer": l, "datatype": d,
                                  "bbox_um": [dx + x1, y1, dx + x2, y2]})
                items.append({"kind": "text", "layer": 6, "datatype": 0,
                              "text": f"W={W} L={Lp}",
                              "position_um": [dx - 12.0, -30.0], "size_um": 5.0})
            c.shape_insert_many(CELL_EXEMPLARS, items)
            c.call("view.show_cell", {"cell": CELL_EXEMPLARS})
            snap(c, our_index, out_dir, "step-01-exemplars.png", FRAME_EXEMPLARS)

            # ---- Stage 2: exact detail crop for the fit annotation -------
            snap(c, our_index, out_dir, "step-02-exemplar-detail.png",
                 EXEMPLAR_DETAIL_UM, exact=True, width_px=1200, height_px=1200)

            # ---- Stage 3: register fitted PCell, draw 3 NEW variants ------
            # (dev20_8 and dev50_3 are NOT exemplar sizes -> proves the PCell
            # interpolates/extrapolates from the fitted edge model.)
            eng.ensure_pcell(c, DEVICES)
            c.cell_create(CELL_VARIANTS)
            pitems = [eng._pcell_item(DEVICES, k, i * 180.0, 0.0)
                      for i, k in enumerate(BUILD_DEVICES)]
            c.instance_insert_pcell_many(CELL_VARIANTS, pitems)
            c.shape_insert_many(CELL_VARIANTS, [
                {"kind": "text", "layer": 6, "datatype": 0,
                 "text": f"{k}  W={W} L={Lp}",
                 "position_um": [i * 180.0 - 35.0, -34.0], "size_um": 5.0}
                for i, (k, (W, Lp)) in enumerate(BUILD_DEVICES.items())])
            c.call("view.show_cell", {"cell": CELL_VARIANTS})
            snap(c, our_index, out_dir, "step-03-variants.png", FRAME_VARIANTS)

            # ---- Stage 4: floorplan + placement (devices only) ------------
            # Same derived floorplan the demo uses (density knobs are
            # EXAMPLE-owned).
            P = replace(PUBLIC_PROCESS, wire_clear_um=5.0,
                        grid_pitch_um=PUBLIC_PROCESS.wire_width_um + 5.0,
                        col_pitch_um=100.0, y_step_um=35.0)
            raw = eng.load_device_geom(GEOM)
            _, _, terms = eng._geom_tables(raw)
            rows, cols = derive_grid(len(nl["groups"]))
            layers = list(P.routing_layers)
            rp = derive_row_pitch(nl, rows, cols, terms, y_step=P.y_step_um,
                                  width_um=P.wire_width_um,
                                  wire_clear_um=P.wire_clear_um,
                                  via_pad_um=P.via_pad_um,
                                  n_horiz_layers=len(layers))
            placement = eng.place_grid(nl, rows, cols, profile=P, row_pitch=rp)
            c.cell_create(CELL_BUILD)
            for lyr in (P.gate_layer, P.sd_layer, P.channel_layer):
                l, d = (int(v) for v in lyr.split("/"))
                c.layer_ensure(l, d)
            c.instance_insert_pcell_many(CELL_BUILD, [
                eng._pcell_item(DEVICES, cellkey, dx, dy)
                for _xi, (cellkey, dx, dy) in placement.items()])
            xs = [dx for (_c2, dx, _dy) in placement.values()]
            ys = [dy for (_c2, _dx, dy) in placement.values()]
            frame_build = (min(xs) - 80.0, min(ys) - 80.0,
                           max(xs) + 80.0, max(ys) + 80.0)
            c.call("view.show_cell", {"cell": CELL_BUILD})
            snap(c, our_index, out_dir, "step-04-placement.png", frame_build)

            # ---- Stage 5: declared nets -> detailed routing (FlexDR) ------
            declared = [{"net": n["net_id"], "terminals": n["terminals"]}
                        for n in nl["nets"]]
            cut_layer = {tuple(sorted((lo, up))): P.cut_layer(lo, up)
                         for (lo, _c2, up) in P.vias}
            t0 = time.time()
            ok, info, _plan = eng.route_and_draw_flexdr(
                c, CELL_BUILD, nl, placement, profile=P, layers=layers,
                vias=P.via_rules(), cut_layer=cut_layer, geom_path=GEOM,
                devices=DEVICES, use_rust=True)
            route_secs = round(time.time() - t0, 1)
            print(f"FlexDR {route_secs}s ok={ok} "
                  f"routed={info.get('routed')}/{info.get('nets')} "
                  f"markers={info.get('markers')}")
            assert ok, info
            # route_and_draw_flexdr deletes + recreates the cell: re-show it.
            c.call("view.show_cell", {"cell": CELL_BUILD})
            snap(c, our_index, out_dir, "step-05-routed.png", frame_build)
            # detail: the first gate column pair (placement puts group 0 at
            # x=0 with its two devices at y=0 / y=-35) + the routing around it
            snap(c, our_index, out_dir, "step-05-detail.png",
                 (-60.0, -110.0, 180.0, 10.0), exact=True,
                 width_px=1200, height_px=600)

            # ---- Stage 6: live LVS (declared vs extracted) -----------------
            device_terms = {
                xi: {t: [round(dx + terms[cellkey][t]["center"][0], 3),
                         round(dy + terms[cellkey][t]["center"][1], 3)]
                     for t in terms[cellkey]}
                for xi, (cellkey, dx, dy) in placement.items()}
            res = lvs_check(c, CELL_BUILD, declared=declared, mode="lvsdb",
                            connectivity=P.connectivity_spec(),
                            terminal_provider=geom_terminal_provider(raw),
                            placement=placement, device_terms=device_terms)
            dev = res.get("device_lvs", {})
            print(f"LVS ok={res['ok']} match={dev.get('match')} "
                  f"devices={dev.get('device_count')}")
            assert res["ok"] and dev.get("match") is True, res
            overview = snap(c, our_index, out_dir, "step-06-overview.png", None)

            report = {
                "exemplars": [{"w_um": W, "l_um": Lp} for W, Lp in EXEMPLAR_SIZES],
                "fit": {"format": table["format"],
                        "param_order": table["param_order"],
                        "roles": sorted(table["styles"]["default"]["roles"]),
                        "edges_parametric": n_lin, "edges_constant": n_con},
                "variants": {k: {"w_um": W, "l_um": Lp}
                             for k, (W, Lp) in BUILD_DEVICES.items()},
                "floorplan": {"gates": len(nl["groups"]), "rows": rows,
                              "cols": cols, "row_pitch_um": rp,
                              "instances": len(placement)},
                "route": {"ok": info["ok"], "routed": info["routed"],
                          "nets": info["nets"], "markers": info["markers"],
                          "sig_vias": info.get("sig_vias"),
                          "pdn_vias": info.get("pdn_vias"),
                          "seconds": route_secs},
                "lvs": {"ok": res["ok"], "match": dev.get("match"),
                        "device_count": dev.get("device_count"),
                        "mode": "lvsdb"},
            }
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
