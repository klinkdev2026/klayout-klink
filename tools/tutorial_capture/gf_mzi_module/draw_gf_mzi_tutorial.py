"""Stage-by-stage gdsfactory-MZI tutorial capture.

Mirrors examples_klink/public/demos/photonics/gf_mzi_module.py's main() build ->
import_gf_component(route=False) -> restyle sbend -> add all_angle + dubins
+ electrical nets -> ONE reroute() -- the exact sequence the real starter
demo runs with no flag -- but stops to screenshot after each state change,
then goes further than the shipped demo by actually PERFORMING a drag and
re-routing with the SAME `reroute()` call the demo's `--reroute` flag uses.

Why exec.python for the drag: there is no typed RPC that repositions an
ALREADY-PLACED instance's transform -- klink_plugin/python/klink_server/
methods/instance_m.py only exposes insert / insert_many / insert_pcell /
insert_pcell_many / query / delete, none of which mutate an existing
instance in place. A human dragging in the KLayout GUI calls exactly the
pya.Instance.dcplx_trans setter this script calls directly; there is no
higher-level klink RPC to prefer here, so this is a documented, justified
exec.python escape hatch for ONE mutation, not a stand-in for the rest of
the flow (which uses only import_gf_component / NetTable / reroute).

This script owns its own disposable tab lifecycle end to end: it opens a
fresh, empty tab via the typed `view.new_tab` RPC (any placeholder top cell
-- `import_gf_component` below creates GF_MZI_MODULE itself and switches the
view to show it via `view.show_cell`), does all its drawing/screenshotting
there, then closes that tab and restores whatever tab was current beforehand
(`view.activate_tab`, skipped when `previous_current_index` is -1, i.e. there
was no tab open at all) -- see CLAUDE.md's tab-safety rule: any pre-existing
tab holds the user's own session and must never be touched.

Hierarchy display depth: this device-cell-per-component layout is exactly 1
hierarchy level deep (GF_MZI_MODULE -> GFDEV_*/Port instances), and
KLayout's LayoutView defaults max_hier_levels=1. KLayout marks whatever sits
AT that boundary level with a "box + cell name" annotation (its way of
saying "hierarchy stops being expanded here") -- with this many small
device/port cells packed close together the annotations overlap into
unreadable text soup. Raising `view.hier_levels(max=...)` above the real
depth (here: 4) makes every instance an "interior" cell instead of a
boundary one, and the annotation disappears with NO geometry change --
confirmed by re-querying port.list/instance.query/reroute() report numbers
before and after, all identical. This is a pure display fix, not a data fix.

See tools/tutorial_capture/README.md for when/why to re-run this.
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

# tools/tutorial_capture/gf_mzi_module/draw_gf_mzi_tutorial.py -> repo root
# is 3 parents up (gf_mzi_module/ -> tutorial_capture/ -> tools/ -> repo
# root). Needed on sys.path so `examples_klink.public.demos.*` (a repo-only
# module, not part of the installed klink package) is importable.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from klink import KLinkClient
from klink.domains.photonics.gf_import import import_gf_component
from klink.domains.photonics.net_intent import NetTable, RouteStyle, reroute
from examples_klink.public.demos.photonics.gf_mzi_module import (
    build_user_module, CELL, OPTICAL_LAYER, METAL_LAYER,
)

DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "gf_mzi_module"

# Full-module bbox (from cell.list with_bbox=True on a first dry run) is
# (-122.213, -267.321, 400.195, 207.321) um; pad it a bit on every side and
# fix ONE viewport for stages 2/4/5/6 so a reader can flip between the
# before/after screenshots and see the SAME pixels move -- no reframing
# between shots would hide the point.
OVERVIEW_UM = (-150.0, -290.0, 420.0, 230.0)  # 570 x 520 um
OVERVIEW_PX = (1200, 1096)

# Tight crop on the tilted input GC (grating_coupler_elliptical1, center
# -80,-25, rotated 195deg -> outward-facing port at 15deg) -- the port plain
# gdsfactory could not route (all_angle only), and the clearest single
# example of "orientation harvested correctly through a live rotation".
GC_IN_DETAIL_UM = (-145.0, -55.0, -65.0, 25.0)  # 80 x 80 um, square

# Tight crop around the gc_up output grating coupler -- the component we
# drag. Same box reused before/after the move so the reader sees ONE frame
# with the device relocated and its net snapped back. (An earlier attempt
# dragged the mmi2x2 combiner +-90um in y; that FAILED reroute --
# device_hits=2 -- because the combiner's fixed east/west port orientations
# made the detour loop back through a neighboring net's obstacle box. gc_up
# has open space above it and only ONE net, so the move is honest AND the
# reroute actually succeeds -- see build_report.json for the numbers.)
GCUP_DETAIL_UM = (320.0, -20.0, 410.0, 160.0)  # 90 x 180 um


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory to write screenshots + build_report.json into (default: %(default)s)",
    )
    return parser.parse_args()


def verify_tab(client, index):
    """Screenshot iron rule: verify the CURRENT tab is the disposable one we
    created, every time, right before we touch the view. Any pre-existing
    tab is the user's own session and must never be acted on."""
    tabs = client.call("view.list_tabs", {})
    cur = tabs["tabs"][tabs["current_index"]]
    assert tabs["current_index"] == index, (
        f"current tab index is {tabs['current_index']!r}, expected our "
        f"disposable tab at index {index!r} ({cur!r}) -- refusing "
        f"to act on a tab we did not create"
    )
    return cur


def snap(client, index, out_dir, name, bbox_um=None, *, width_px=1200, height_px=None):
    """Always exact bbox_um clip (no aspect-ratio expansion) so before/after
    shots of the SAME box are pixel-comparable."""
    verify_tab(client, index)
    kwargs = {"mode": "base64", "width_px": width_px}
    if height_px is not None:
        kwargs["height_px"] = height_px
    if bbox_um is not None:
        kwargs["bbox_um"] = list(bbox_um)
    shot = client.screenshot(**kwargs)
    data = shot["data_url"].split(",", 1)[1]
    path = os.path.join(out_dir, name)
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    print("saved", path)
    return path


def snap_fit(client, index, out_dir, name, width_px=1200):
    verify_tab(client, index)
    client.zoom_fit()
    shot = client.screenshot(mode="base64", width_px=width_px)
    data = shot["data_url"].split(",", 1)[1]
    path = os.path.join(out_dir, name)
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    print("saved", path)
    return path


def _print_reroute(tag, report):
    print("[%s] reroute ok:" % tag, report["ok"],
          "| routes:", report.get("routes"),
          "| abutted:", report.get("abutted"),
          "| crossings:", report.get("crossings"),
          "| device_hits:", report.get("device_hits"))
    for problem in report.get("problems", []):
        print("  problem:", problem)


def main():
    args = _parse_args()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    report = {}
    with KLinkClient().connect() as c:
        new_tab = c.new_tab(cell_name="TOP")
        our_index = new_tab["index"]
        previous_index = new_tab["previous_current_index"]
        print("opened disposable tab:", new_tab["title"], "index", our_index)

        try:
            # Display-only fix for the hierarchy-boundary label clutter (see
            # module docstring) -- must happen before any screenshot.
            c.hier_levels(max=4)

            # ---- Stage: gdsfactory build, client-side only, nothing in KLayout
            component = build_user_module()
            print("gf component:", component.name, "insts:", len(component.insts))

            # ---- Stage 2: ONE call takes the whole module over -----------------
            result = import_gf_component(
                c, component, cell=CELL, route_layer=OPTICAL_LAYER, route=False)
            print("import ok:", result["ok"], "| nets:", len(result["nets"]),
                  "| instances:", result["instances"],
                  "| device cells:", len(result["device_cells"]))
            assert result["ok"], result
            report["import"] = {
                "ok": result["ok"], "nets": len(result["nets"]),
                "instances": result["instances"],
                "device_cells": len(result["device_cells"]),
                "ports_marked": result.get("ports_marked"),
            }
            snap(c, our_index, out_dir, "step-02-placed.png", OVERVIEW_UM, width_px=OVERVIEW_PX[0], height_px=OVERVIEW_PX[1])

            # ---- Stage 3: inspect the harvested ports (no mutation) ------------
            ports = c.call("port.list", {"cell": CELL, "layer": "999/99", "sort": "name"}).get("ports", [])
            by_name = {p["name"]: p for p in ports}
            gc_in_port = by_name["grating_coupler_elliptical1_o1"]
            print("gc_in tilted port:", gc_in_port["center_um"], gc_in_port["orientation"])
            report["gc_in_port"] = {
                "name": gc_in_port["name"], "center_um": gc_in_port["center_um"],
                "orientation": gc_in_port["orientation"],
            }
            snap(c, our_index, out_dir, "step-03-gcin-detail.png", GC_IN_DETAIL_UM, width_px=900, height_px=900)

            # ---- Stage 4: restyle + add odd-angle/electrical nets + ONE reroute
            def _xsorted(names):
                return sorted(names, key=lambda n: by_name[n]["center_um"][0])

            table = NetTable.load(CELL)
            sbent = 0
            for entry in table.entries:
                members = {entry["a"], entry["b"]}
                if any(m.startswith("mmi2x2") and m.endswith(("_o3", "_o4")) for m in members):
                    entry["style"]["router"] = "sbend"
                    sbent += 1

            aa = RouteStyle(router="all_angle", route_layer=OPTICAL_LAYER)
            table.add_pair("grating_coupler_elliptical1_o1", "mmi1x20_o1", aa)
            dub = RouteStyle(router="dubins", radius_um=40.0, route_layer=OPTICAL_LAYER)
            table.add_pair("grating_coupler_elliptical2_o1", "grating_coupler_elliptical3_o1", dub)

            heater_up = _xsorted(n for n, p in by_name.items()
                                  if n.endswith(("_l_e2", "_r_e2")) and round(p["orientation"]) == 90)
            heater_dn = _xsorted(n for n, p in by_name.items()
                                  if n.endswith(("_l_e2", "_r_e2")) and round(p["orientation"]) == 270)
            pads_top = _xsorted(n for n, p in by_name.items()
                                 if n.endswith("_e4") and n.startswith("pad") and p["center_um"][1] > 0)
            pads_bot = _xsorted(n for n, p in by_name.items()
                                 if n.endswith("_e2") and n.startswith("pad") and p["center_um"][1] < 0)
            metal = RouteStyle(router="electrical", route_layer=METAL_LAYER, separation_um=12.0)
            for heater, pad in list(zip(heater_up, pads_top)) + list(zip(heater_dn, pads_bot)):
                table.add_pair(heater, pad, metal)
            table.save()
            print("restyled sbend:", sbent, "| net table size:", len(table.entries))

            baseline = reroute(c, cell=CELL)
            _print_reroute("baseline", baseline)
            assert baseline["ok"], baseline
            report["baseline_reroute"] = {
                k: baseline.get(k) for k in ("ok", "routes", "abutted", "crossings", "device_hits")
            }
            snap(c, our_index, out_dir, "step-04-routed.png", OVERVIEW_UM, width_px=OVERVIEW_PX[0], height_px=OVERVIEW_PX[1])

            # ---- Stage 5: SIMULATE A DRAG ---------------------------------------
            # No typed RPC repositions an already-placed instance -- justified
            # exec.python escape hatch (see module docstring). Move gc_up (the
            # upper offset output grating coupler, single net) +70um in y --
            # open space above it, one net goes stale. Five instances share the
            # SAME grating_coupler_elliptical child cell, so gc_up is picked out
            # by its live position (360, 30), not by cell identity alone.
            gc_cells = c.call("cell.list", {"name_prefix": "GFDEV_grating_coupler_elliptical"}).get("cells", [])
            assert len(gc_cells) == 1, gc_cells
            gc_name = gc_cells[0]["name"]
            drag_code = (
                "top_cell = layout.cell(%r)\n"
                "child_idx = layout.cell(%r).cell_index()\n"
                "moved = []\n"
                "for inst in top_cell.each_inst():\n"
                "    if inst.cell_index != child_idx:\n"
                "        continue\n"
                "    t = inst.dcplx_trans\n"
                "    if abs(t.disp.x - 360.0) > 0.01 or abs(t.disp.y - 30.0) > 0.01:\n"
                "        continue  # not gc_up -- one of the other 4 grating couplers\n"
                "    new_t = pya.DCplxTrans(t.mag, t.angle, t.mirror,\n"
                "                            t.disp + pya.DVector(0.0, 70.0))\n"
                "    inst.dcplx_trans = new_t\n"
                "    moved.append([t.disp.x, t.disp.y, new_t.disp.x, new_t.disp.y])\n"
                "moved\n"
            ) % (CELL, gc_name)
            exec_result = c.call("exec.python", {"code": drag_code})
            print("drag exec_result:", exec_result)
            assert exec_result.get("exception") is None, exec_result
            moved = exec_result.get("return_value")
            assert moved and len(moved) == 1, moved
            report["drag"] = {"cell": gc_name, "target": "gc_up", "from_to": moved[0]}
            snap(c, our_index, out_dir, "step-05-dragged.png", OVERVIEW_UM, width_px=OVERVIEW_PX[0], height_px=OVERVIEW_PX[1])
            snap(c, our_index, out_dir, "step-05-gcup-detail.png", GCUP_DETAIL_UM, width_px=900, height_px=1800)

            # ---- Stage 6: --reroute semantics -- SAME reroute() call ------------
            fixed = reroute(c, cell=CELL)
            _print_reroute("post-drag", fixed)
            assert fixed["ok"], fixed
            report["post_drag_reroute"] = {
                k: fixed.get(k) for k in ("ok", "routes", "abutted", "crossings", "device_hits")
            }
            snap(c, our_index, out_dir, "step-06-rerouted.png", OVERVIEW_UM, width_px=OVERVIEW_PX[0], height_px=OVERVIEW_PX[1])
            snap(c, our_index, out_dir, "step-06-gcup-detail.png", GCUP_DETAIL_UM, width_px=900, height_px=1800)

            # ---- Stage 7: finish -------------------------------------------------
            snap_fit(c, our_index, out_dir, "step-07-overview.png", width_px=1200)

            info = c.layout_info(verbosity="full")
            report["layout_info"] = info
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
