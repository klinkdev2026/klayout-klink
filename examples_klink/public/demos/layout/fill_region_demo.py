# klink PUBLIC example — runnable as-is, self-contained: imports only `klink`
# (no PDK, no NDA, no extra GDS). Carries its own layers.
#
#   Run:    python <this file> --port <your-klayout-rpc-port>
#   In a `klink init` project these live in example_template/ — copy one into
#   your project and adapt.
#
"""Fill / tiling showcase for ``cell.fill_region`` (KLayout Fill Utility).

Needs a running KLayout with the klink plugin loaded. Builds one demo cell
(``FILL_DEMO``) that tiles a tiny 2x1 um "device" fill cell over five kinds
of regions:

  1. a hand-drawn polygon taken from a scratch layer (``region_layers``)
  2. a full circle (``circles_um``)
  3. a 90-degree pie sector (``circles_um`` with start/end angles)
  4. a rectangle with gaps between tiles (``row_step_um``/``column_step_um``)
  5. the same circle as (2) but with 1 um spacing, for a density contrast

Spacing rule: gap = step - footprint, per direction. Only tiles that fit
ENTIRELY inside a region are placed, so curved boundaries keep an unfilled
rim; every call reports it honestly as ``remaining_area_um2`` (check the
printed table: placed x footprint area + remaining == region area).

Every tile is an instance of the fill cell, so the hierarchy stays clean:
swap the fill cell's geometry once and every tile follows. Each fill call
is one undo step in KLayout. The demo leaves ``FILL_DEMO`` open so you can
inspect it; re-running the script rebuilds it from scratch.
"""

from __future__ import annotations

import argparse
import math

from klink import KLinkClient

FC = "FILL_DEMO_FC"
TOP = "FILL_DEMO"


def arc_points(cx, cy, r, a0, a1, n=96):
    return [[cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))]
            for i in range(n + 1)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765,
                    help="klink RPC port of the target KLayout (default 8765)")
    args = ap.parse_args()

    with KLinkClient(port=args.port).connect() as c:
        for name in (TOP, FC):
            try:
                c.cell_delete(name, recursive=True)
            except Exception:
                pass

        li_dev = c.layer_ensure(1, 0)["layer_index"]
        li_ct = c.layer_ensure(2, 0)["layer_index"]
        li_blob = c.layer_ensure(66, 0)["layer_index"]
        li_guide = c.layer_ensure(67, 0)["layer_index"]

        # The fill cell: a 2x1 um device body with a contact dot. Every
        # placed tile is an instance of this cell (clean hierarchy).
        c.cell_create(FC)
        c.shape_insert_box(FC, layer_index=li_dev, bbox_um=[0, 0, 2.0, 1.0])
        c.shape_insert_box(FC, layer_index=li_ct, bbox_um=[0.7, 0.25, 1.3, 0.75])

        c.cell_create(TOP)

        # 1) hand-drawn region: draw any polygon on a scratch layer, then
        #    point region_layers at it -- no coordinate shuttling.
        c.shape_insert_polygon(
            TOP, layer_index=li_blob,
            points_um=[[0, 0], [30, 0], [30, 12], [15, 24], [0, 12]])
        r_blob = c.cell_fill_region(TOP, FC, region_layers=["66/0"])

        # thin outline guides so the circle/sector boundaries stay visible
        c.call("shape.insert_path", {
            "cell": TOP, "layer_index": li_guide,
            "points_um": arc_points(60, 12, 12, 0, 360), "width_um": 0.15})
        c.call("shape.insert_path", {
            "cell": TOP, "layer_index": li_guide,
            "points_um": [[95, 0]] + arc_points(95, 0, 20, 0, 90) + [[95, 0]],
            "width_um": 0.15})
        c.call("shape.insert_path", {
            "cell": TOP, "layer_index": li_guide,
            "points_um": arc_points(60, -20, 12, 0, 360), "width_um": 0.15})

        # 2) full circle  3) 90-degree sector
        r_circle = c.cell_fill_region(
            TOP, FC, circles_um=[{"center": [60, 12], "radius": 12}])
        r_sector = c.cell_fill_region(
            TOP, FC, circles_um=[{"center": [95, 0], "radius": 20,
                                  "start_angle_deg": 0, "end_angle_deg": 90}])

        # 4) rectangle with gaps: footprint 2x1, steps 4x2.5 -> 2 um / 1.5 um gaps
        r_gapped = c.cell_fill_region(
            TOP, FC, boxes_um=[[125, 0, 150, 24]],
            row_step_um=[4.0, 0.0], column_step_um=[0.0, 2.5])

        # 5) spacing contrast: same circle as (2), 1 um gap both directions
        r_spaced = c.cell_fill_region(
            TOP, FC, circles_um=[{"center": [60, -20], "radius": 12}],
            row_step_um=[3.0, 0.0], column_step_um=[0.0, 2.0])

        print(f"{'region':8s} {'placed':>6s} {'region_um2':>11s} {'remaining_um2':>13s}")
        for tag, r in [("blob", r_blob), ("circle", r_circle),
                       ("sector", r_sector), ("gapped", r_gapped),
                       ("spaced", r_spaced)]:
            print(f"{tag:8s} {r['placed']:6d} {r['region_area_um2']:11.1f} "
                  f"{r['remaining_area_um2']:13.1f}")

        c.call("view.show_cell", {"cell": TOP})


if __name__ == "__main__":
    main()
