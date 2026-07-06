"""Stage-by-stage EBL wraparound tutorial capture.

Mirrors klink.domains.nanodevice.devices.wraparound.build_wraparound_demo()
geometry (the same function example_template/ebl_wraparound.py calls), but
issues the RPCs incrementally (layers -> flake/keepout -> contacts+vias ->
wraparound routing to pads -> Port/Anchor intent -> writefield planning ->
patches/finish) so each stage can be screenshotted for the tutorial.

This script owns its own disposable tab lifecycle end to end: it opens a
fresh tab/cell (NANODEVICE_EBL_WRAPAROUND) via the typed `view.new_tab` RPC,
does all its drawing/screenshotting there, then closes that tab and restores
whatever tab was current beforehand (`view.activate_tab`, skipped when
`previous_current_index` is -1, i.e. there was no tab open at all) -- see
CLAUDE.md's tab-safety rule: any pre-existing tab holds the user's own
session and must never be touched.

See tools/tutorial_capture/README.md for when/why to re-run this.
"""
import argparse
import base64
import json
import os
from pathlib import Path

from klink import KLinkClient
from klink.domains.nanodevice.devices.wraparound import (
    ANCHOR_LAYER,
    CELL,
    PORT_LAYER,
    build_wraparound_demo,
)

# tools/tutorial_capture/ebl_wraparound/draw_ebl_tutorial.py -> repo root is
# 3 parents up (ebl_wraparound/ -> tutorial_capture/ -> tools/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "ebl_wraparound"

# Same process layers as example_template/ebl_wraparound.py -- example-owned,
# klink ships none of these. (Port/Anchor stay on klink's reserved 999/*
# markers; 900/0 is klink's reserved keepout layer, used here for the
# writefield stitch-wall obstacles.)
WRAP_LAYERS = {
    "flake": (30, 0), "m1": (10, 0), "m2": (11, 0), "pad": (20, 0),
    "via": (40, 0), "label": (6, 0), "patch": "113/0",
}

# Consistent framing for stages 1-6 (um), padded beyond the full device
# extent (chip bbox [-230,-170,230,170], farthest pad edges ~ +-193/+-154).
FRAME_UM = (-250.0, -195.0, 250.0, 195.0)


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


def _ensure_layers(client, items):
    seen = set()
    for item in items:
        key = (int(item["layer"]), int(item.get("datatype", 0)))
        if key not in seen:
            seen.add(key)
            client.layer_ensure(key[0], key[1], name=f"NANODEVICE_{key[0]}_{key[1]}")


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
            bundle = build_wraparound_demo(WRAP_LAYERS)
            items = bundle["shape_items"]

            # ---- Stage 1: layer plan, empty canvas ----------------------------
            # _ensure_layers scans every shape item's (layer, datatype) pair --
            # the SAME helper example_template/ebl_wraparound.py uses, so every
            # process layer (flake/m1/m2/pad/via/label/patch) plus klink's
            # reserved keepout layer (900/0, from the writefield obstacle boxes
            # already present in `items`) gets created in one pass.
            _ensure_layers(c, items)
            c.call("port.set_layer", {"layer": PORT_LAYER})
            c.call("anchor.set_layer", {"layer": ANCHOR_LAYER})
            snap(c, our_index, out_dir, "step-01-layers.png", FRAME_UM)

            # ---- Stage 2: flake + local keepout + alignment marks -------------
            # Construction order in build_wraparound_demo: 4 corner alignment
            # crosses (3 items each: box, box, label text) = 12 items, then the
            # flake polygon, then the local-keepout box + its label text.
            stage2 = items[0:15]
            c.shape_insert_many(CELL, stage2)
            snap(c, our_index, out_dir, "step-02-flake.png", FRAME_UM)

            # ---- Stage 3: contacts + vias (near-field M1 wiring) ---------------
            # The next 56 items are 8 contacts x 7 items each, in a fixed order:
            # [contact_box(M1), m1_path(M1), via_box(VIA),
            #  m2_path(M2), taper_poly(M2), pad_box(PAD), label_text(LABEL)]
            # Stage 3 draws only the first 3 of each 7-block: the short M1 stub
            # from the flake-adjacent contact point to its via.
            contact_block = items[15:71]
            stage3 = [it for i, it in enumerate(contact_block) if i % 7 in (0, 1, 2)]
            c.shape_insert_many(CELL, stage3)
            snap(c, our_index, out_dir, "step-03-contacts-vias.png", FRAME_UM)

            # ---- Stage 4: wraparound routing to pads (M2 + taper + pads) -------
            # The remaining 4 of each 7-block: the long M2 wraparound path that
            # detours around the chip perimeter to a pad far outside the flake,
            # its tapered transition into the pad neck, the pad box itself, and
            # the pad's name label.
            stage4 = [it for i, it in enumerate(contact_block) if i % 7 in (3, 4, 5, 6)]
            c.shape_insert_many(CELL, stage4)
            snap(c, our_index, out_dir, "step-04-wraparound.png", FRAME_UM)

            # ---- Stage 5: mark Port + Anchor intent (post-hoc) ------------------
            # Unlike the Hall bar tutorial (mark first, route second), this demo
            # draws explicit hand-authored routes up front -- writefield stitch
            # corridors are hard constraints, so the path shape is fixed before
            # any router runs. Ports/anchors here document intent and drive the
            # validation pass in stage 7, not an upstream route solve.
            via_anchors = bundle["anchor_marks"][0:8]
            for port in bundle["port_marks"]:
                payload = dict(port)
                payload["cell"] = CELL
                c.call("port.mark", payload)
            for anchor in via_anchors:
                payload = dict(anchor)
                payload["cell"] = CELL
                c.call("anchor.mark", payload)
            snap(c, our_index, out_dir, "step-05-ports-anchors.png", FRAME_UM)

            # ---- Stage 6: writefield planning -----------------------------------
            # klink.domains.nanodevice.ebl.writefield.plan_writefields() divided
            # the chip into a 4x4 writefield grid and emitted stitch-wall
            # obstacle boxes (klink's reserved keepout layer 900/0) everywhere
            # EXCEPT the 11 explicit crossing windows -- this is the concept that
            # does not exist in the Hall bar tutorial: an EBL writefield has hard
            # stitching error walls, and only pre-planned corridors may cross
            # them. The corridor anchors below mark those windows.
            obstacle_items = items[71 + bundle["patch_report"]["patch_count"]:]
            c.shape_insert_many(CELL, obstacle_items)
            corridor_anchors = bundle["anchor_marks"][8:]
            for anchor in corridor_anchors:
                payload = dict(anchor)
                payload["cell"] = CELL
                c.call("anchor.mark", payload)
            snap(c, our_index, out_dir, "step-06-writefield.png", FRAME_UM)

            # ---- Stage 7: patches at boundary crossings + finish -----------------
            # generate_wf_patches() (ported from Klayout-Router's Auto-patching
            # macro) placed a square stitch patch everywhere a drawn route
            # crosses a writefield boundary -- these compensate for the beam
            # stitching error at the field edge.
            patch_items = items[71:71 + bundle["patch_report"]["patch_count"]]
            c.shape_insert_many(CELL, patch_items)
            overview_path = snap(c, our_index, out_dir, "step-07-overview.png", None)  # zoom_fit

            # Detail crop: the C0 net's M2 path crossing the x=-115 writefield
            # boundary at y=18, inside the WF_XL_MID corridor window, with its
            # stitch patch.
            detail_bbox = (-145.0, -5.0, -95.0, 40.0)
            detail_path = snap(c, our_index, out_dir, "step-07-detail.png", detail_bbox)

            info = c.layout_info(verbosity="full")
            report = {
                "demo_report": bundle["report"],
                "writefield_report": bundle["writefield"]["report"],
                "patch_report": bundle["patch_report"],
                "wf_validation": {
                    "crossing_count": bundle["wf_validation"]["crossing_count"],
                    "violations": len(bundle["wf_validation"]["violations"]),
                },
                "overlap_validation": {
                    "overlaps": len(bundle["overlap_validation"]["overlaps"]),
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
