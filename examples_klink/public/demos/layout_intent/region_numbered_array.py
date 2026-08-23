"""Hero flow: circle an empty area -> numbered sensor array, regenerable.

The full Executable Layout Intent loop (docs/REGION_INTENT_DESIGN.md
Section 9) against a live KLayout: build a small demo scene, circle the
target area, ``intent.prepare`` a pitch-grid placement plan with unique
number labels (a pure preview, nothing written), ``intent.apply`` it as
one undoable container, then ``intent.regenerate`` with a changed
numbering start so a second ``apply`` swaps the container for a new one.
All design values below (layers, sizes, pitch, label spec) are
EXAMPLE-owned demo data -- klink ships none of them.

Run:  python example_template/layout_intent/region_numbered_array.py
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from klink import KLinkClient
from klink.domains.layoutintent import orchestrator
from klink.domains.layoutintent.store import LayoutIntentStore

# --- demo design data (example-owned) --------------------------------------
DEMO_TOP = "INTENT_DEMO"
DEMO_SENSOR = "INTENT_DEMO_SENSOR"
DEVICE_LAYER = (10, 0)
LABEL_LAYER = "20/0"
SENSOR_SIZE_UM = 8.0
OBSTACLE_BOX_UM = [55.0, 45.0, 85.0, 75.0]
PITCH_UM = [20.0, 20.0]
NUMBERING = {"prefix": "S", "width": 3, "start": 1, "order": "top_down"}
# label SLOT: circled INSIDE the sensor cell (lower band); every array copy
# gets its own number auto-fitted into its own slot
SLOT_BOX_UM = [0.5, 0.5, 7.5, 2.5]
OBSTACLE_LAYERS = ["%d/%d" % DEVICE_LAYER]


def _fail(problems: list, message: str) -> None:
    print("      MISMATCH: %s" % message)
    problems.append(message)


def _check_container(c, container_cell: str, expect_instances: int,
                      expect_labels: int, problems: list) -> None:
    """Query the applied container cell and confirm counts match the plan
    the orchestrator reported -- structured evidence, not vibes."""
    q_inst = c.instance_query(container_cell)
    q_label = c.shape_query(container_cell, layers=[LABEL_LAYER],
                            kinds=["polygons"], limit=5000)
    inst_count = q_inst["returned"]
    label_count = q_label["returned"]
    print("      container %s: instances=%d (expected %d) "
          "label_polygons=%d (expected %d)"
          % (container_cell, inst_count, expect_instances,
             label_count, expect_labels))
    if inst_count != expect_instances:
        _fail(problems, "%s: instance count %d != expected %d"
              % (container_cell, inst_count, expect_instances))
    if label_count != expect_labels:
        _fail(problems, "%s: label polygon count %d != expected %d"
              % (container_cell, label_count, expect_labels))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765,
                    help="klink RPC port of the target KLayout (default 8765)")
    args = ap.parse_args()

    problems: list = []
    store = LayoutIntentStore(
        Path(tempfile.mkdtemp(prefix="klink_intent_demo_")) / ".klink")

    with KLinkClient(port=args.port).connect() as c:
        # 1. demo scene, in its own new tab so this never touches whatever
        #    the user already has open
        c.new_tab(cell_name=DEMO_TOP)
        c.cell_create(DEMO_SENSOR)
        li = c.layer_ensure(*DEVICE_LAYER)["layer_index"]
        c.shape_insert_box(DEMO_SENSOR, layer_index=li,
                           bbox_um=[0, 0, SENSOR_SIZE_UM, SENSOR_SIZE_UM])
        c.shape_insert_box(DEMO_TOP, layer_index=li, bbox_um=OBSTACLE_BOX_UM)
        c.call("view.show_cell", {"cell": DEMO_TOP})

        # 2a. circle the TEXT SLOT inside the unit cell ("the number goes
        #     here") -- in real use: open the SENSOR cell, drag a ruler by
        #     hand; here the ruler is drawn programmatically for repeatability
        rid_slot = c.call("annotation.insert", {
            "points_um": [SLOT_BOX_UM[:2], SLOT_BOX_UM[2:]],
            "outline": "box",
            "category": "klink_demo_intent",
        })["ruler"]["id"]
        slot = c.call("region.claim",
                      {"cell": DEMO_SENSOR, "rulers": [{"id": rid_slot}]})
        print("slot", slot["name"], "inside", DEMO_SENSOR)

        # 2b. circle the target area and claim it -- again drawn by hand
        #     in real use
        rid = c.call("annotation.insert", {
            "points_um": [[0, 0], [140, 100]],
            "outline": "box",
            "category": "klink_demo_intent",
        })["ruler"]["id"]
        region = c.call("region.claim",
                        {"cell": DEMO_TOP, "rulers": [{"id": rid}]})
        print("claimed", region["name"], "area %.0f um2" % region["area_um2"])

        # 3. prepare (pure analysis + plan; nothing written)
        preview = orchestrator.prepare(
            c, store,
            region=region["name"],
            source_cell=DEMO_SENSOR,
            obstacle_layers=OBSTACLE_LAYERS,
            pitch_um=PITCH_UM,
            numbering=NUMBERING,
            label={"layer": LABEL_LAYER, "slot_region": slot["name"],
                   "margin_um": 0.2},
            instruction="在这里铺 SENSOR 阵列，编号写进每个单元的文字槽",
        )
        print("preview: placed=%d rejected=%s labels %s..%s "
              "font %.2fum (auto-fit)" % (
                  preview["placed"], preview["rejected_reasons"],
                  preview["label_range"][0], preview["label_range"][1],
                  preview["label_height_um"]))
        if preview["problems"]:
            _fail(problems, "prepare returned blocking problems: %s"
                  % preview["problems"])
        if preview["placed"] == 0:
            _fail(problems, "prepare placed 0 instances")

        # 4. apply (one transaction, one undo step)
        result = orchestrator.apply(
            c, store, plan_id=preview["plan_id"],
            plan_hash=preview["plan_hash"], confirm=preview["plan_id"])
        print("applied -> container", result["container_cell"],
              "instances", result["instances"],
              "label polygons", result["label_shapes"])
        _check_container(c, result["container_cell"],
                         result["instances"], result["label_shapes"], problems)

        # 5. regenerate with a new numbering start; apply swaps the container
        preview2 = orchestrator.regenerate(
            c, store, intent_id=result["intent_id"],
            parameters_patch={"numbering": {"start": 201}})
        result2 = orchestrator.apply(
            c, store, plan_id=preview2["plan_id"],
            plan_hash=preview2["plan_hash"], confirm=preview2["plan_id"])
        print("regenerated -> %s replaced %s; labels now %s..%s" % (
            result2["container_cell"], result2["replaced_container"],
            preview2["label_range"][0], preview2["label_range"][1]))

        # the new numbering start must actually show up in the id range
        if preview2["label_range"][0] == preview["label_range"][0]:
            _fail(problems, "regenerate did not change the label range "
                  "(numbering.start patch had no effect)")

        _check_container(c, result2["container_cell"],
                         result2["instances"], result2["label_shapes"], problems)

        # the OLD container must be gone (apply_managed_plan replace mode
        # deletes it, it does not just detach it) -- confirm the swap really
        # happened instead of trusting the return value alone
        old_container = result2["replaced_container"]
        if old_container != result["container_cell"]:
            _fail(problems, "replaced_container %r != first container %r"
                  % (old_container, result["container_cell"]))
        else:
            still_present = c.cell_list(name_prefix=old_container)["cells"]
            still_present = [e for e in still_present if e["name"] == old_container]
            if still_present:
                _fail(problems, "old container %r still exists after "
                      "regenerate+apply swap" % old_container)
            else:
                print("      confirmed: old container %r was deleted by the "
                      "swap" % old_container)

        bbox = c.call("region.get", {"name": region["name"]})["bbox_um"]
        c.call("view.zoom_box", {"bbox_um": [bbox[0] - 10, bbox[1] - 10,
                                             bbox[2] + 10, bbox[3] + 10]})

        if problems:
            print("\nFAILED (%d problem(s)):" % len(problems))
            for p in problems:
                print("  -", p)
            return 1
        print("\ndone — the array with unique numbers is live in KLayout; "
              "one Ctrl+Z undoes the last apply")
        return 0


if __name__ == "__main__":
    sys.exit(main())
