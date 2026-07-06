"""Stage-by-stage neural electrode harness tutorial capture.

Mirrors examples_klink/public/demos/nanodevice/neural_electrode.py's
generate_harnesspcell() geometry (the same function
example_template/neural_electrode.py --port <session-port> --elec-rows 4
calls), but issues the RPCs incrementally (layer plan -> probe body/frame ->
bond pads + routing vias -> parametric electrode array -> Port/Anchor intent
-> corridor-constrained batch routing -> finish) so each stage can be
screenshotted for the tutorial.

This script owns its own disposable tab lifecycle end to end: it opens a
fresh tab/cell (NEURAL_ELECTRODE_TUTORIAL) via the typed `view.new_tab` RPC,
does all its drawing/screenshotting there, then closes that tab and restores
whatever tab was current beforehand (`view.activate_tab`, skipped when
`previous_current_index` is -1, i.e. there was no tab open at all) -- see
CLAUDE.md's tab-safety rule: any pre-existing tab holds the user's own
session and must never be touched.

Uses the honest view.* unit semantics: view.zoom_box(bbox_um=[...]) and
view.screenshot(bbox_um=[...]) both take plain microns -- never pass
microns through the bbox_dbu= keyword.

See tools/tutorial_capture/README.md for when/why to re-run this.
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

# tools/tutorial_capture/neural_electrode/draw_neural_tutorial.py -> repo
# root is 3 parents up (neural_electrode/ -> tutorial_capture/ -> tools/ ->
# repo root). Needed on sys.path so `examples_klink.public.demos.*` (a
# repo-only module, not part of the installed klink package) is importable.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from klink import KLinkClient
from examples_klink.public.demos.nanodevice.neural_electrode import (
    HarnessPCellSpec,
    _frame_items,
    _layer_items,
    _mark_pad_ports,
    _mark_route_source_ports,
    _mark_electrode_ports,
    _mark_corridor_anchors,
    _route_harness_cell,
)

DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "neural_electrode"
CELL = "NEURAL_ELECTRODE_TUTORIAL"
SPEC = HarnessPCellSpec(cell_name=CELL, elec_rows=4)

# Consistent framing for stages 1-6 (um): the full probe chip, frame body
# spans x:[-7500, 2800], bond-pad/electrode nets span y:[-2265, 2365],
# padded a bit beyond the frame's own outer margins.
FRAME_UM = (-7650.0, -2700.0, 2950.0, 2700.0)

# Tight, square-ish crop on the right-hand electrode contact cluster (near
# the shank tip) -- the whole point of stage 4/7's detail shots is that at
# FRAME_UM scale (10600 x 5400 um) a 20x21 um electrode pad is a couple of
# pixels; this crop is where the individual pads/vias are actually visible.
ELEC_DETAIL_UM = (2370.0, -70.0, 2510.0, 70.0)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory to write screenshots + build_report.json into (default: %(default)s)",
    )
    return parser.parse_args()


def verify_tab(client, expect_title_substr):
    """CLAUDE.md tab-safety rule: verify the CURRENT tab is ours, every
    time, right before we touch the view or take a screenshot. Any
    pre-existing tab (whatever indices it happens to occupy) is the user's
    own session and must never be read as if it were our scratch layout."""
    tabs = client.call("view.list_tabs", {})
    cur = tabs["tabs"][tabs["current_index"]]
    assert expect_title_substr in cur["title"], (
        f"current tab is {cur!r}, expected title to contain "
        f"{expect_title_substr!r} -- refusing to act on a tab we did not create"
    )
    return cur


def snap(client, out_dir, name, bbox_um=None, *, exact=False, width_px=1200, height_px=None):
    """Verify tab identity, then capture a screenshot. Two framing modes:

    - bbox_um is None: zoom_fit, then screenshot at the resulting viewport
      (used for the final full-chip overview).
    - bbox_um given, exact=False: view.zoom_box(bbox_um=...) sets the
      viewport, then a plain screenshot follows -- KLayout expands one axis
      to match the widget's aspect ratio, so this is a "look about here"
      shot, not a pixel-exact crop.
    - bbox_um given, exact=True: view.screenshot(bbox_um=...) clips exactly
      to the box (no aspect-ratio expansion) -- pair width_px/height_px with
      the box's own aspect ratio for a linear um -> pixel mapping (used for
      the annotated detail crop).
    """
    verify_tab(client, CELL)
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

    with KLinkClient().connect() as c:
        new_tab = c.new_tab(cell_name=CELL)
        our_index = new_tab["index"]
        previous_index = new_tab["previous_current_index"]
        print("opened disposable tab:", new_tab["title"], "index", our_index)

        try:
            # ---- Stage 1: layer plan --------------------------------
            c.call("port.set_layer", {"layer": SPEC.port_layer})
            c.call("anchor.set_layer", {"layer": SPEC.anchor_layer})
            for layer, name in (
                (1, "M1"), (2, "VIA13"), (3, "M3"), (4, "VIA35"),
                (5, "Pads"), (6, "Frame"), (7, "Protect"),
            ):
                c.layer_ensure(layer, 0, name=name)
            c.layer_ensure(999, 99, name="KLINK_PORTS")
            c.layer_ensure(999, 98, name="KLINK_ROUTE_PORTS")
            c.layer_ensure(999, 1, name="KLINK_ANCHORS")
            snap(c, out_dir, "step-01-layers.png", FRAME_UM)

            # Pull the SAME item list the real demo writes in one shot, and
            # cross-check our stage boundaries analytically before slicing --
            # this keeps the tutorial's staging honest (no reimplemented
            # geometry, only a documented slice of the real output).
            frame_items = _frame_items(SPEC)
            items = _layer_items(SPEC)
            frame_count = len(frame_items)
            probe_count = SPEC.net_count * 11 + SPEC.pads_per_half * 2
            electrode_count = SPEC.elec_rows * 21
            assert frame_count + probe_count + electrode_count == len(items), (
                frame_count, probe_count, electrode_count, len(items),
            )

            # ---- Stage 2: probe body / frame -------------------------------
            c.shape_insert_many(CELL, items[0:frame_count])
            snap(c, out_dir, "step-02-frame.png", FRAME_UM)

            # ---- Stage 3: bond pads + intermediate routing vias ------------
            probe_items = items[frame_count:frame_count + probe_count]
            c.shape_insert_many(CELL, probe_items)
            snap(c, out_dir, "step-03-bondpads.png", FRAME_UM)

            # ---- Stage 4: parametric electrode array (--elec-rows) ---------
            electrode_items = items[frame_count + probe_count:]
            c.shape_insert_many(CELL, electrode_items)
            snap(c, out_dir, "step-04-electrodes-overview.png", FRAME_UM)
            snap(c, out_dir, "step-04-electrodes-detail.png", ELEC_DETAIL_UM,
                 exact=True, width_px=1200, height_px=1200)

            # ---- Stage 5: Port + Anchor intent ------------------------------
            dbu = float(c.layout_info().get("dbu", 0.001))
            _mark_pad_ports(c, SPEC, dbu)
            _mark_route_source_ports(c, SPEC, dbu)
            _mark_electrode_ports(c, SPEC)
            _mark_corridor_anchors(c, SPEC)
            snap(c, out_dir, "step-05-ports-anchors.png", FRAME_UM)

            # ---- Stage 6: corridor-constrained batch routing ----------------
            route_result = _route_harness_cell(c, SPEC)
            assert route_result["ok"], route_result
            snap(c, out_dir, "step-06-routed.png", FRAME_UM)

            # ---- Stage 7: finish -- overview + electrode detail -------------
            overview_path = snap(c, out_dir, "step-07-overview.png", None)
            detail_path = snap(c, out_dir, "step-07-detail.png", ELEC_DETAIL_UM,
                                exact=True, width_px=1200, height_px=1200)

            info = c.layout_info(verbosity="full")
            port_count_total = 0
            anchor_count_total = 0
            for layer_name in (SPEC.port_layer, SPEC.aux_port_layer):
                port_count_total += len(
                    c.call("port.list", {"cell": CELL, "layer": layer_name}).get("ports", [])
                )
            anchor_count_total = len(
                c.call("anchor.list", {"cell": CELL, "layer": SPEC.anchor_layer}).get("anchors", [])
            )
            report = {
                "elec_rows": SPEC.elec_rows,
                "pads_per_half": SPEC.pads_per_half,
                "net_count": SPEC.net_count,
                "shape_item_counts": {
                    "frame": frame_count,
                    "bondpads_and_vias": probe_count,
                    "electrodes": electrode_count,
                    "total": len(items),
                },
                "port_count_total": port_count_total,
                "anchor_count_total": anchor_count_total,
                "route_result": {
                    "ok": route_result["ok"],
                    "port_count": route_result["port_count"],
                    "anchor_count": route_result["anchor_count"],
                    "pair_count": route_result["pair_count"],
                    "planning_errors": route_result["planning_errors"],
                    "groups": [
                        {
                            "route_layer": g["route_layer"],
                            "ok": g["ok"],
                            "route_count": g["route_count"],
                            "sibling_overlap_count": g["sibling_overlap_count"],
                            "obstacle_hit_count": g["obstacle_hit_count"],
                            "inserted": (g["write"] or {}).get("inserted"),
                        }
                        for g in route_result["groups"]
                    ],
                },
                "layout_info": info,
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
