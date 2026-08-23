"""Stage-by-stage layout-intent tutorial capture.

Re-runs the shipped starter's flow (constants are IMPORTED from
examples_klink/public/demos/layout_intent/region_numbered_array.py so
the pictures can never drift from the demo) and screenshots each
stage: rulers drawn -> regions claimed -> numbered array applied ->
regenerated with a new numbering start.

Owns its tab lifecycle: opens a fresh tab, works only there, closes it
and restores the previous tab. See tools/tutorial_capture/README.md.
"""

import argparse
import base64
import importlib.util
import tempfile
from pathlib import Path

from klink import KLinkClient
from klink.domains.layoutintent import orchestrator
from klink.domains.layoutintent.store import LayoutIntentStore

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "layout_intent"

_spec = importlib.util.spec_from_file_location(
    "region_numbered_array",
    REPO_ROOT / "examples_klink" / "public" / "demos" / "layout_intent"
    / "region_numbered_array.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


def snap(c, out_dir: Path, name: str, cell: str) -> Path:
    c.call("view.show_cell", {"cell": cell})
    c.call("view.hier_levels", {"min": 0, "max": 12})
    c.zoom_fit()
    shot = c.screenshot(mode="base64", width_px=1400)
    data = shot["data_url"].split(",", 1)[1]
    path = out_dir / name
    path.write_bytes(base64.b64decode(data))
    print("  shot ->", path.name)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    store = LayoutIntentStore(
        Path(tempfile.mkdtemp(prefix="klink_intent_cap_")) / ".klink")

    with KLinkClient(port=args.port).connect() as c:
        prev = c.call("view.list_tabs", {}).get("current_index", -1)
        c.new_tab(cell_name=S.DEMO_TOP)
        try:
            c.cell_create(S.DEMO_SENSOR)
            li = c.layer_ensure(*S.DEVICE_LAYER)["layer_index"]
            c.shape_insert_box(S.DEMO_SENSOR, layer_index=li,
                               bbox_um=[0, 0, S.SENSOR_SIZE_UM,
                                        S.SENSOR_SIZE_UM])
            c.shape_insert_box(S.DEMO_TOP, layer_index=li,
                               bbox_um=S.OBSTACLE_BOX_UM)

            # both rulers first, so the "circle it" stage is one picture
            rid_slot = c.call("annotation.insert", {
                "points_um": [S.SLOT_BOX_UM[:2], S.SLOT_BOX_UM[2:]],
                "outline": "box", "category": "klink_demo_intent",
            })["ruler"]["id"]
            rid = c.call("annotation.insert", {
                "points_um": [[0, 0], [140, 100]],
                "outline": "box", "category": "klink_demo_intent",
            })["ruler"]["id"]
            snap(c, out_dir, "step-01-rulers.png", S.DEMO_TOP)

            slot = c.call("region.claim", {"cell": S.DEMO_SENSOR,
                                           "rulers": [{"id": rid_slot}]})
            region = c.call("region.claim", {"cell": S.DEMO_TOP,
                                             "rulers": [{"id": rid}]})
            snap(c, out_dir, "step-02-claimed-region.png", S.DEMO_TOP)
            snap(c, out_dir, "step-03-slot-in-unit-cell.png", S.DEMO_SENSOR)

            preview = orchestrator.prepare(
                c, store, region=region["name"],
                source_cell=S.DEMO_SENSOR,
                obstacle_layers=S.OBSTACLE_LAYERS,
                pitch_um=S.PITCH_UM, numbering=S.NUMBERING,
                label={"layer": S.LABEL_LAYER,
                       "slot_region": slot["name"], "margin_um": 0.2},
                instruction="tutorial capture: numbered sensor array")
            result = orchestrator.apply(
                c, store, plan_id=preview["plan_id"],
                plan_hash=preview["plan_hash"], confirm=preview["plan_id"])
            print("applied", result["container_cell"],
                  "instances", result["instances"])
            snap(c, out_dir, "step-04-applied-array.png", S.DEMO_TOP)

            preview2 = orchestrator.regenerate(
                c, store, intent_id=result["intent_id"],
                parameters_patch={"numbering": {"start": 201}})
            result2 = orchestrator.apply(
                c, store, plan_id=preview2["plan_id"],
                plan_hash=preview2["plan_hash"],
                confirm=preview2["plan_id"])
            print("regenerated ->", result2["container_cell"])
            snap(c, out_dir, "step-05-regenerated.png", S.DEMO_TOP)
        finally:
            try:
                c.call("view.close_tab", {})
                if prev is not None and int(prev) >= 0:
                    c.call("view.activate_tab", {"index": int(prev)})
            except Exception:
                pass
    print("capture complete ->", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
