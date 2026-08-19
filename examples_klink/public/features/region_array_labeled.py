"""Hero flow: circle an empty area -> numbered sensor array, regenerable.

The full Executable Layout Intent loop from docs/REGION_INTENT_DESIGN.md
against a live KLayout (klink 8765):

1. build a demo scene: a SENSOR cell, a target cell with one obstacle blob
2. drag rulers around the empty area (programmatic here; drawn by hand in
   real use) -> region.claim -> Region PCell R###
3. intent.prepare: occupancy analysis + pitch-grid plan + unique polygon
   number labels + validators -> preview (writes NOTHING)
4. intent.apply: one transaction -> KLINK_I_* container (one Ctrl+Z undoes)
5. intent.regenerate with numbering.start=201 -> apply swaps ONLY this
   intent's container

All design values below (layers, sizes, pitch, label spec) are
EXAMPLE-owned demo data — klink ships none of them.

Run:  python -m examples_klink.public.features.region_array_labeled
"""

from __future__ import annotations

from pathlib import Path
import tempfile

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


def main() -> None:
    store = LayoutIntentStore(
        Path(tempfile.mkdtemp(prefix="klink_intent_demo_")) / ".klink")
    with KLinkClient().connect() as c:
        # 1. demo scene
        for name in (DEMO_TOP, DEMO_SENSOR):
            try:
                c.cell_delete(name, recursive=True)
            except Exception:
                pass
        c.cell_create(DEMO_TOP)
        c.cell_create(DEMO_SENSOR)
        li = c.layer_ensure(*DEVICE_LAYER)["layer_index"]
        c.shape_insert_box(DEMO_SENSOR, layer_index=li,
                           bbox_um=[0, 0, SENSOR_SIZE_UM, SENSOR_SIZE_UM])
        c.shape_insert_box(DEMO_TOP, layer_index=li, bbox_um=OBSTACLE_BOX_UM)
        c.call("view.show_cell", {"cell": DEMO_TOP})

        # 2a. circle the TEXT SLOT inside the unit cell ("the number goes
        #     here") — in real use: open the SENSOR cell, drag a ruler
        rid_slot = c.call("annotation.insert", {
            "points_um": [SLOT_BOX_UM[:2], SLOT_BOX_UM[2:]],
            "outline": "box",
            "category": "klink_demo_intent",
        })["ruler"]["id"]
        slot = c.call("region.claim",
                      {"cell": DEMO_SENSOR, "rulers": [{"id": rid_slot}]})
        print("slot", slot["name"], "inside", DEMO_SENSOR)

        # 2b. circle the target area and claim it
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

        # 4. apply (one transaction, one undo step)
        result = orchestrator.apply(
            c, store, plan_id=preview["plan_id"],
            plan_hash=preview["plan_hash"], confirm=preview["plan_id"])
        print("applied -> container", result["container_cell"],
              "instances", result["instances"],
              "label polygons", result["label_shapes"])

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

        bbox = c.call("region.get", {"name": region["name"]})["bbox_um"]
        c.call("view.zoom_box", {"bbox_um": [bbox[0] - 10, bbox[1] - 10,
                                             bbox[2] + 10, bbox[3] + 10]})
        print("done — the array with unique numbers is live in KLayout; "
              "one Ctrl+Z undoes the last apply")


if __name__ == "__main__":
    main()
