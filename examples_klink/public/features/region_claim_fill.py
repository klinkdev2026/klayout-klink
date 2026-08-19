"""Intent Region walking skeleton: rulers -> region.claim -> fill + zoom.

Demonstrates the I1a loop from docs/REGION_INTENT_DESIGN.md §13.3 end to end
against a live KLayout (klink 8765):

1. build a small demo scene (a sensor-ish cell and some obstacle geometry)
2. draw box/ellipse RULERS around an empty area (programmatically here; in
   real use the user drags them with the ruler tool)
3. region.claim converts the rulers into ONE klink_Region PCell on the
   reserved marker layer and consumes the rulers
4. region.get returns the composed polygon; cell.fill_region tiles the demo
   cell inside it; view.zoom_box navigates to it

All design content in this file (layer numbers, sizes, pitches) is
EXAMPLE-owned demo data — klink itself ships none of it.

Run:  python -m examples_klink.public.features.region_claim_fill
"""

from __future__ import annotations

from klink import KLinkClient

# --- demo design data (example-owned) --------------------------------------
DEMO_TOP = "REGION_DEMO"
DEMO_FILL_CELL = "REGION_DEMO_SENSOR"
DEVICE_LAYER = (10, 0)      # demo "device" geometry
OBSTACLE_BOX_UM = [70.0, 40.0, 130.0, 90.0]
SENSOR_SIZE_UM = 8.0
REGION_LAYER = "999/10"     # klink reserved Region marker layer (default)


def main() -> None:
    with KLinkClient().connect() as c:
        # 1. demo scene: top cell + a fill cell + one obstacle blob
        for name in (DEMO_TOP, DEMO_FILL_CELL):
            try:
                c.cell_delete(name, recursive=True)
            except Exception:
                pass
        c.cell_create(DEMO_TOP)
        c.cell_create(DEMO_FILL_CELL)
        li = c.layer_ensure(*DEVICE_LAYER)["layer_index"]
        c.shape_insert_box(DEMO_FILL_CELL, layer_index=li,
                           bbox_um=[0, 0, SENSOR_SIZE_UM, SENSOR_SIZE_UM])
        c.shape_insert_box(DEMO_TOP, layer_index=li, bbox_um=OBSTACLE_BOX_UM)
        c.call("view.show_cell", {"cell": DEMO_TOP})

        # 2. "the user circles an area": one big box with a bite clipped off
        #    by an ellipse — drawn here as rulers, exactly what the ruler
        #    tool produces
        include = c.call("annotation.insert", {
            "points_um": [[0, 0], [200, 140]],
            "outline": "box",
            "category": "klink_demo_region",
        })["ruler"]["id"]
        exclude = c.call("annotation.insert", {
            "points_um": [[140, 80], [220, 160]],
            "outline": "ellipse",
            "category": "klink_demo_region",
        })["ruler"]["id"]

        # 3. claim: rulers -> Region PCell (rulers are consumed)
        claimed = c.call("region.claim", {
            "cell": DEMO_TOP,
            "layer": REGION_LAYER,
            "rulers": [
                {"id": include, "role": "include"},
                {"id": exclude, "role": "exclude"},
            ],
        })
        print("claimed:", claimed["name"], claimed["klink_id"],
              "area_um2=%.1f" % claimed["area_um2"])

        # 4a. fill the region with the demo sensor cell
        got = c.call("region.get", {"name": claimed["name"]})
        filled = c.call("cell.fill_region", {
            "cell": DEMO_TOP,
            "fill_cell": DEMO_FILL_CELL,
            "polygons_um": [got["hull_um"]],
            "exclude_layers": ["%d/%d" % DEVICE_LAYER],
        })
        print("fill result:", {k: filled.get(k) for k in
                               ("placed", "count", "remaining_area_um2")
                               if k in filled})

        # 4b. navigate to the region
        l, b, r, t = got["bbox_um"]
        margin = 10.0
        c.call("view.zoom_box", {
            "bbox_um": [l - margin, b - margin, r + margin, t + margin]})
        print("zoomed to region", got["name"], "bbox_um", got["bbox_um"])
        print("next: click the Region outline in KLayout and press SEND — "
              "the agent can resolve it via interaction.selection.latest")


if __name__ == "__main__":
    main()
