"""Passives tutorial capture: default + one parameter-expanded variant per
family, screenshotted in live KLayout.

Covers the four public passive-device geometry templates under
``examples_klink/public/demos/passives/``: ``idc_capacitor``,
``spiral_inductor``, ``saw_idt_filter``, ``baw_fbar_planview``
(docs/PASSIVE_TEMPLATES_SPEC.md).

All geometry comes from the templates' own ``build_*()`` functions -- this
script only calls them with two parameter sets (the module's own
``DEFAULT_PARAMS`` and the SAME expanded-parameter case already exercised by
``tests/unit/test_passive_templates.py``) and pushes the resulting items/
ports through plain klink RPCs. No geometry is invented here.

Both the default and variant screenshot of a family share ONE fixed
``view.zoom_box`` framed on the VARIANT's (larger) geometry bbox, instead of
each stage doing its own ``zoom_fit`` -- with independent zoom_fit, a
parameter set that draws bigger geometry still fills the same frame as the
default, so the two images look the "same size" even though the real
device grew. The shared frame makes that size difference visible.

For each family:
  1. build the DEFAULT_PARAMS bundle, push it into a fresh cell, show it and
     zoom to the family's shared bbox -> ``<family>-default.png``,
  2. also run the template's own ``write_offline()`` on the same params (a
     throwaway GDS under --out-dir) to get genuinely-recomputed self-check
     invariants for this run (not copied from any prior report),
  3. delete the cell, rebuild with the expanded-variant params, show it and
     zoom to the SAME shared bbox -> ``<family>-variant.png``, self-check
     again,
  4. delete the cell before moving to the next family.

The BAW family additionally gets one precise (exact-linear-mapping) crop of
just the pentagon, saved as ``baw-pentagon-crop.png`` together with a small
JSON describing the two edges whose directions are closest to parallel (by
construction, still not parallel) -- ``annotate_detail.py`` in this same
directory draws the Pillow highlight on top of that crop.

This script owns its own disposable tab lifecycle end to end: opens ONE
fresh tab (view.new_tab) up front, does all drawing/screenshotting there,
then closes that tab and restores whatever tab was current beforehand.  Any
pre-existing tab is the user's own session and is never touched. Never
connects to any port other than the one passed on the command line
(default: 8768, this tutorial's dedicated capture session).

See tools/tutorial_capture/README.md for when/why to re-run this.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
from pathlib import Path

# tools/tutorial_capture/passives/draw_passives_tutorial.py -> repo root is 3
# parents up (passives/ -> tutorial_capture/ -> tools/ -> repo root). Derived
# from __file__ (never a hardcoded absolute path) so `examples_klink` -- a
# repo-only package, not shipped in the wheel -- resolves no matter the
# caller's cwd (see tools/tutorial_capture/README.md).
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from klink import KLinkClient
from examples_klink.public.demos.passives import (
    baw_fbar_planview as baw,
    idc_capacitor as idc,
    saw_idt_filter as saw,
    spiral_inductor as spiral,
)

DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "passives"

# The SAME expanded-parameter case each family is exercised with in
# tests/unit/test_passive_templates.py (base+expanded harness rule) -- reused
# here verbatim so the tutorial's "variant" screenshot matches an already
# unit-tested, known-good parameter set rather than an ad hoc tweak.
FAMILIES = [
    {
        "key": "idc",
        "module": idc,
        "build_fn": idc.build_idc,
        "layers": [(idc.LAYER_METAL, "IDC_METAL")],
        "variant_overrides": {
            "finger_count": 24, "finger_width": 3.0, "gap": 2.0,
            "finger_length": 35.0, "bus_width": 6.0,
        },
    },
    {
        "key": "spiral",
        "module": spiral,
        "build_fn": spiral.build_spiral,
        "layers": [
            (spiral.LAYER_METAL_TOP, "SPIRAL_METAL_TOP"),
            (spiral.LAYER_METAL_UNDER, "SPIRAL_METAL_UNDER"),
            (spiral.LAYER_VIA, "SPIRAL_VIA"),
        ],
        "variant_overrides": {
            "turns": 6, "track_width": 3.0, "spacing": 2.0,
            "inner_size": 15.0, "underpass_width": 4.0,
        },
    },
    {
        "key": "saw",
        "module": saw,
        "build_fn": saw.build_saw_idt,
        "layers": [(saw.LAYER_METAL, "SAW_METAL")],
        "variant_overrides": {
            "pitch": 6.0, "pairs": 20, "aperture": 60.0, "bus_width": 8.0,
            "idt_gap": 45.0, "reflector_fingers": 15, "reflector_gap": 6.0,
        },
    },
    {
        "key": "baw",
        "module": baw,
        "build_fn": baw.build_baw_fbar,
        "layers": [
            (baw.LAYER_METAL_TOP, "BAW_METAL_TOP"),
            (baw.LAYER_METAL_BOT, "BAW_METAL_BOT"),
            (baw.LAYER_MEMBRANE, "BAW_MEMBRANE_RELEASE"),
        ],
        "variant_overrides": {
            "active_area_um2": 6000.0, "connect_width_um": 12.0,
            "pad_size_um": 40.0, "bottom_extension_um": 90.0,
            "overlap_margin_um": 10.0, "membrane_margin_um": 20.0,
        },
    },
]


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory to write screenshots + capture_report.json into (default: %(default)s)",
    )
    parser.add_argument(
        "--klink-port",
        type=int,
        default=8768,
        help="klink RPC port of the dedicated tutorial-capture session (default: %(default)s)",
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


def _save_shot(shot, path):
    data = shot["data_url"].split(",", 1)[1]
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    print("saved", path)


def _port_summary(bundle):
    return [
        {
            "name": p["name"],
            "center_um": p["center_um"],
            "orientation": p["orientation"],
            "net": p["net"],
            "target_layer": p["target_layer"],
        }
        for p in bundle["ports"]
    ]


def _push_bundle(client, fam, params):
    module = fam["module"]
    build_fn = fam["build_fn"]
    bundle = build_fn(params)
    cell = bundle["cell"]

    existing = {c["name"] for c in client.cell_list(limit=1000).get("cells", [])}
    if cell in existing:
        client.cell_delete(cell, recursive=True)
    client.cell_create(cell)

    for layer, name in fam["layers"]:
        client.layer_ensure(*layer, name=name)
    client.layer_ensure(*module.LAYER_PORT, name="KLINK_PORTS")

    client.shape_insert_many(cell, bundle["items"])
    client.call("port.mark_many", {
        "cell": cell,
        "layer": "%d/%d" % module.LAYER_PORT,
        "items": [
            {
                "name": p["name"], "center_um": p["center_um"],
                "orientation": p["orientation"], "width_um": p["width_um"],
                "port_type": p["port_type"], "net": p["net"],
                "target_layer": p["target_layer"],
            }
            for p in bundle["ports"]
        ],
    })
    return bundle


def _closest_to_parallel_pair(pentagon_um):
    """Recompute (independently of build_baw_fbar's own bookkeeping) which
    pair of pentagon edges has the smallest direction-angle difference mod
    180 deg -- the pair that "looks closest to parallel" even though the
    template's invariant guarantees no two edges are ever exactly parallel.
    Returns (edge_i, edge_j, diff_deg, edge_i_points_um, edge_j_points_um)."""
    n = len(pentagon_um)
    dirs = []
    for i in range(n):
        x0, y0 = pentagon_um[i]
        x1, y1 = pentagon_um[(i + 1) % n]
        dirs.append(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0)
    best = None
    for i in range(n):
        for j in range(i + 1, n):
            d = abs(dirs[i] - dirs[j])
            d = min(d, 180.0 - d)
            if best is None or d < best[0]:
                best = (d, i, j)
    diff_deg, i, j = best
    edge_i = [list(pentagon_um[i]), list(pentagon_um[(i + 1) % n])]
    edge_j = [list(pentagon_um[j]), list(pentagon_um[(j + 1) % n])]
    return i, j, diff_deg, edge_i, edge_j


def _bundle_geometry_bbox_um(bundle):
    """Overall (x0, y0, x1, y1) extent of every drawn item in `bundle`
    (boxes by their bbox_um, paths/polygons by their vertices) -- geometry
    only, no fixed margin added here (the caller pads it)."""
    xs: list[float] = []
    ys: list[float] = []
    for item in bundle["items"]:
        kind = item["kind"]
        if kind == "box":
            x0, y0, x1, y1 = item["bbox_um"]
            xs += [x0, x1]
            ys += [y0, y1]
        elif kind in ("path", "polygon"):
            for x, y in item["points_um"]:
                xs.append(x)
                ys.append(y)
        elif kind == "text":
            x, y = item["position_um"]
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _fixed_zoom_bbox_um(fam):
    """One zoom_box, shared by BOTH the default and variant screenshots of
    this family, framed on the VARIANT's (larger) geometry -- so a bigger
    parameter set actually looks bigger across the two images instead of
    each screenshot separately zoom_fit-ing to fill the frame at its own
    scale. Built from a throwaway offline `build_fn` call (pure geometry,
    no live push) so it costs nothing extra against the session."""
    variant_params = dict(fam["module"].DEFAULT_PARAMS, **fam["variant_overrides"])
    variant_bundle = fam["build_fn"](variant_params)
    x0, y0, x1, y1 = _bundle_geometry_bbox_um(variant_bundle)
    margin = max(0.08 * max(x1 - x0, y1 - y0), 5.0)
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def render_family(client, tab_index, out_dir, fam):
    key = fam["key"]
    module = fam["module"]
    report = {}
    zoom_bbox_um = _fixed_zoom_bbox_um(fam)

    for stage, params in (
        ("default", dict(module.DEFAULT_PARAMS)),
        ("variant", dict(module.DEFAULT_PARAMS, **fam["variant_overrides"])),
    ):
        bundle = _push_bundle(client, fam, params)
        cell = bundle["cell"]

        # Same fixed zoom_box for both stages of this family (framed on the
        # variant's bbox above) so the two screenshots are directly
        # comparable -- NOT each stage's own zoom_fit, which would make a
        # larger-parameter variant look the same size as the default.
        client.show_cell(cell, zoom_fit=False)
        client.zoom_box(bbox_um=list(zoom_bbox_um))
        verify_tab(client, tab_index)
        shot = client.screenshot(mode="base64", width_px=1200)
        _save_shot(shot, os.path.join(out_dir, f"{key}-{stage}.png"))

        # BAW default stage: also grab a precise, exact-linear-mapping crop
        # of just the pentagon for the apodization annotation figure, and
        # record the closest-to-parallel edge pair for annotate_detail.py.
        annotation_meta = None
        if key == "baw" and stage == "default":
            pentagon_um = bundle["pentagon_um"]
            bx0, by0, bx1, by1 = bundle["summary"]["pentagon_bbox_um"]
            margin = 10.0
            crop_bbox = (bx0 - margin, by0 - margin, bx1 + margin, by1 + margin)
            width_px = 1200
            height_px = int(round(width_px * (crop_bbox[3] - crop_bbox[1])
                                   / (crop_bbox[2] - crop_bbox[0])))
            verify_tab(client, tab_index)
            crop_shot = client.screenshot(
                mode="base64", width_px=width_px, height_px=height_px,
                bbox_um=list(crop_bbox),
            )
            crop_path = os.path.join(out_dir, "baw-pentagon-crop.png")
            _save_shot(crop_shot, crop_path)

            ei, ej, diff_deg, edge_i, edge_j = _closest_to_parallel_pair(pentagon_um)
            annotation_meta = {
                "crop_path": crop_path,
                "bbox_um": list(crop_bbox),
                "width_px": width_px,
                "height_px": height_px,
                "edge_i_index": ei,
                "edge_j_index": ej,
                "edge_i_points_um": edge_i,
                "edge_j_points_um": edge_j,
                "diff_deg": diff_deg,
            }
            with open(os.path.join(out_dir, "baw_edge_annotation.json"), "w") as f:
                json.dump(annotation_meta, f, indent=2)
            print("saved", os.path.join(out_dir, "baw_edge_annotation.json"))

        # Genuinely re-run the template's own offline self-check for this
        # exact params dict, in THIS script execution (not copied from any
        # prior report).
        gds_path = os.path.join(out_dir, f"{key}_{stage}.gds")
        offline_report = module.write_offline(params, gds_path)

        client.cell_delete(cell, recursive=True)

        stage_report = {
            "params": params,
            "ports": _port_summary(bundle),
            "offline_self_check": offline_report,
        }
        if annotation_meta is not None:
            stage_report["baw_annotation"] = annotation_meta
        report[stage] = stage_report

    return report


def main():
    args = _parse_args()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    with KLinkClient(port=args.klink_port).connect() as c:
        new_tab = c.new_tab(cell_name="PASSIVES_TUTORIAL")
        our_index = new_tab["index"]
        previous_index = new_tab["previous_current_index"]
        print("opened disposable tab:", new_tab["title"], "index", our_index, "on port", args.klink_port)

        report = {}
        try:
            for fam in FAMILIES:
                print("--- family:", fam["key"], "---")
                report[fam["key"]] = render_family(c, our_index, out_dir, fam)
        finally:
            c.call("view.close_tab", {"view_index": our_index})
            if previous_index != -1:
                c.call("view.activate_tab", {"index": previous_index})
                print("restored previous tab index", previous_index)
            else:
                print("no previous tab to restore (none was open)")

        with open(os.path.join(out_dir, "capture_report.json"), "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
