"""Stage-by-stage Hall bar tutorial capture.

Mirrors klink.domains.nanodevice.devices.hallbar.build_hallbar() /
klink.domains.nanodevice.pipeline.route_hallbar_offline() geometry, but issues
the RPCs incrementally (mesa -> contacts -> pads -> ports/anchors -> route ->
labels) so each stage can be screenshotted for the tutorial.

This script owns its own disposable tab lifecycle end to end: it opens a
fresh tab/cell (HALLBAR_TUTORIAL) via the typed `view.new_tab` RPC, does all
its drawing/screenshotting there, then closes that tab and restores whatever
tab was current beforehand (`view.activate_tab`, skipped when
`previous_current_index` is -1, i.e. there was no tab open at all). Any
pre-existing tab is the user's own session and is never touched.

See tools/tutorial_capture/README.md for when/why to re-run this.
"""
import argparse
import base64
import json
import os
from pathlib import Path

from klink import KLinkClient
from klink.domains.nanodevice.devices.hallbar import HallBarSpec, build_hallbar
from klink.domains.nanodevice.pipeline import route_hallbar_offline
from klink.routing.backends.geometric.tapered_segments import commit_tapered_hybrid_many

# tools/tutorial_capture/hallbar/draw_hallbar_tutorial.py -> repo root is 3
# parents up (hallbar/ -> tutorial_capture/ -> tools/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "hallbar"

CELL = "HALLBAR_TUTORIAL"

SPEC = HallBarSpec(
    name="NDHB",
    device_layer="1/0",
    metal_layer="10/0",
    label_layer="6/0",
    route_layer="12/0",
)

# Consistent framing used for stages 1-6 (um), padded beyond the full device
# extent (mesa x:[-72,72], pads y up to +-58).
FRAME_UM = (-92.0, -70.0, 92.0, 70.0)


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
        f"current tab is {cur!r} (current_index={tabs['current_index']}), "
        f"expected our disposable tab at index {index} -- refusing to act "
        "on a tab we did not create"
    )
    return cur


def snap(client, index, out_dir, name, bbox_um=None):
    verify_tab(client, index)
    if bbox_um is not None:
        client.zoom_box(bbox_um=list(bbox_um))
    else:
        client.zoom_fit()
    shot = client.screenshot(mode="base64", width_px=1200)
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
            bundle = build_hallbar(SPEC)

            # ---- Stage 1: layer plan, empty canvas -----------------------
            device_li = c.layer_ensure(1, 0, name="NANODEVICE_DEVICE")["layer_index"]
            metal_li = c.layer_ensure(10, 0, name="NANODEVICE_METAL")["layer_index"]
            label_li = c.layer_ensure(6, 0, name="NANODEVICE_LABEL")["layer_index"]
            route_li = c.layer_ensure(12, 0, name="KLINK_ROUTES")["layer_index"]
            # klink reserved marker layers (Port/Anchor); ensure so the layer
            # panel shows them even before any marker exists.
            c.layer_ensure(999, 99, name="KLINK_PORT")
            c.layer_ensure(999, 1, name="KLINK_ANCHOR")
            snap(c, our_index, out_dir, "step-01-layers.png", FRAME_UM)

            # ---- Stage 2: mesa / channel -----------------------------------
            mesa_item = next(i for i in bundle["shape_items"] if i["layer"] == 1)
            c.shape_insert_boxes(CELL, layer_index=device_li, boxes_um=[mesa_item["bbox_um"]])
            snap(c, our_index, out_dir, "step-02-mesa.png", FRAME_UM)

            # ---- Stage 3: ohmic contacts -------------------------------------
            contact_boxes = []
            pad_boxes = []
            # shape_items order from build_hallbar: [mesa, label, contact, pad, contact, pad, ...]
            for item in bundle["shape_items"]:
                if item["layer"] != 10:
                    continue
                # contacts are the taller/narrower boxes (contact_length x contact_width),
                # pads are square (pad_size x pad_size) -- classify by width.
                x0, y0, x1, y1 = item["bbox_um"]
                w = x1 - x0
                if abs(w - SPEC.contact_width_um) < 1e-6:
                    contact_boxes.append(item["bbox_um"])
                else:
                    pad_boxes.append(item["bbox_um"])
            c.shape_insert_boxes(CELL, layer_index=metal_li, boxes_um=contact_boxes)
            snap(c, our_index, out_dir, "step-03-contacts.png", FRAME_UM)

            # ---- Stage 4: probe pads -----------------------------------------
            c.shape_insert_boxes(CELL, layer_index=metal_li, boxes_um=pad_boxes)
            snap(c, our_index, out_dir, "step-04-pads.png", FRAME_UM)

            # ---- Stage 5: Port + Anchor intent --------------------------------
            for port in bundle["port_marks"]:
                payload = dict(port)
                payload["cell"] = CELL
                c.call("port.mark", payload)
            for anchor in bundle["anchor_marks"]:
                payload = dict(anchor)
                payload["cell"] = CELL
                c.call("anchor.mark", payload)
            snap(c, our_index, out_dir, "step-05-ports-anchors.png", FRAME_UM)

            # ---- Stage 6: route fanout (geometry-first: mark -> route) -------
            route_result = route_hallbar_offline(bundle)
            assert route_result["ok"], route_result.get("errors")
            write = commit_tapered_hybrid_many(c, CELL, route_result, route_layer=SPEC.route_layer, clear=True)
            snap(c, our_index, out_dir, "step-06-routed.png", FRAME_UM)

            # ---- Stage 7: label + finish --------------------------------------
            label_item = next(i for i in bundle["shape_items"] if i["layer"] == 6)
            c.shape_insert_text(
                CELL,
                label_item["text"],
                layer_index=label_li,
                position_um=label_item["position_um"],
                size_um=label_item["size_um"],
            )
            overview_path = snap(c, our_index, out_dir, "step-07-overview.png", None)  # zoom_fit

            # Detail crop: T0 contact/mesa/pad column (x=-36) from mesa edge to pad top.
            detail_bbox = (-46.0, -2.0, -26.0, 62.0)
            detail_path = snap(c, our_index, out_dir, "step-07-detail.png", detail_bbox)

            info = c.layout_info(verbosity="full")
            report = {
                "route_result_ok": route_result["ok"],
                "route_count": route_result["route_count"],
                "obstacle_hits": len(route_result.get("obstacle_hits") or []),
                "sibling_overlaps": len(route_result.get("sibling_overlaps") or []),
                "write": write,
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
