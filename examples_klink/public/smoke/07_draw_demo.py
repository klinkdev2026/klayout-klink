"""
M3 Round 1-3 smoke test: draw a small chip into the active layout.

Prerequisite
------------
Open KLayout and either load an existing GDS, or create a new empty
layout (File -> New Layout, DBU 0.001). Make sure klink_server is
running (it auto-starts via the klink_plugin macro, or you can run
`klink_plugin/macros/start_klink.lym`).

What it does
------------
1. Creates a cell named "DEMO" (or appends $N if the name is taken).
2. Ensures GDS layers 101/0 (metal), 102/0 (poly), 66/0 (label) exist,
   getting back their layer_index handles.
3. Draws a metal frame, two poly boxes, a polygon, a diagonal path,
   and a text label, all in microns.
4. Prints what it inserted.

Everything is wrapped in transactions, so Ctrl+Z / Ctrl+Shift+Z in
KLayout will undo/redo each individual insert.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient


def main():
    with KLinkClient() as c:
        # -- 1. create cell ------------------------------------------------
        info = c.layout_info()
        if not info.get("has_view") or info.get("cell") is None:
            print("No layout is open in KLayout.")
            print("Please do File -> New Layout (DBU 0.001 is fine) and re-run.")
            print(f"(layout.info said: {info})")
            return
        original_top = info["cell"]  # the cell currently shown in KLayout
        print(f"layout dbu = {info['dbu']} um, "
              f"current top cells = {info.get('top_cells') or []}")
        print(f"original displayed cell: {original_top!r}")

        created = c.cell_create(name="DEMO")
        cell = created["name"]
        print(f"created cell -> {cell} (idx={created['cell_index']}, "
              f"renamed={created['renamed']})")

        # -- 2. ensure layers ----------------------------------------------
        metal = c.layer_ensure(layer=101, datatype=0, name="METAL")
        poly  = c.layer_ensure(layer=102, datatype=0, name="POLY")
        lbl   = c.layer_ensure(layer=66,  datatype=0, name="LABEL")
        for r in (metal, poly, lbl):
            tag = "new" if r["created"] else "exists"
            print(f"  layer {r['layer']}/{r['datatype']:<2} "
                  f"-> index={r['layer_index']} ({tag})")

        # -- 3. draw a little layout (microns) -----------------------------
        # metal frame (big rectangle)
        c.shape_insert_box(cell, layer_index=metal["layer_index"],
                           bbox_um=[0, 0, 50, 30])

        # two poly rectangles inside
        c.shape_insert_box(cell, layer_index=poly["layer_index"],
                           bbox_um=[5, 5, 20, 10])
        c.shape_insert_box(cell, layer_index=poly["layer_index"],
                           bbox_um=[30, 5, 45, 10])

        # a triangle polygon on metal
        c.shape_insert_polygon(cell, layer_index=metal["layer_index"],
                               points_um=[[5, 15], [20, 15], [12.5, 25]])

        # a diagonal path on poly, 0.5 um wide
        c.shape_insert_path(cell, layer_index=poly["layer_index"],
                            points_um=[[5, 20], [25, 28], [45, 20]],
                            width_um=0.5)

        # a label
        c.shape_insert_text(cell, "hello klink", layer_index=lbl["layer_index"],
                            position_um=[10, 2])

        # -- 4. verify -----------------------------------------------------
        q = c.shape_query(cell=cell, limit=100)
        print(f"{cell} now has {q['returned']} shape(s):")
        for s in q["shapes"]:
            extra = ""
            if s["type"] == "text":
                extra = f" str={s['string']!r}"
            elif s["type"] == "path":
                extra = f" width_dbu={s['width_dbu']}"
            elif s["type"] == "polygon":
                extra = f" pts={len(s['points_dbu'])}"
            print(f"  layer_idx={s['layer_index']:<3} {s['type']}"
                  f" bbox={s.get('bbox_dbu')}{extra}")

        # -- 5. hierarchy / array test -----------------------------------
        # Create a small SUBCELL with a cross shape, then array it into
        # DEMO as a 2-row x 3-col grid. This exercises both cell.create
        # (again) and instance.insert with array parameters.
        sub = c.cell_create(name="SUBCELL")
        sub_name = sub["name"]
        print(f"created child cell -> {sub_name}")

        c.shape_insert_box(sub_name, layer_index=metal["layer_index"],
                           bbox_um=[-3, -0.3, 3, 0.3])  # horizontal bar
        c.shape_insert_box(sub_name, layer_index=metal["layer_index"],
                           bbox_um=[-0.3, -3, 0.3, 3])  # vertical bar

        inst = c.instance_insert(cell, sub_name,
                                 position_um=[10, 45],
                                 array={"rows": 2, "cols": 3,
                                        "pitch_x_um": 12,
                                        "pitch_y_um": 10})
        arr = inst.get("array") or {}
        print(f"  inserted SUBCELL array {arr.get('cols')}x{arr.get('rows')} "
              f"into DEMO at trans={inst['trans']}")

        # Also insert one rotated single instance of SUBCELL (no array).
        c.instance_insert(cell, sub_name, position_um=[40, 50], rotation=45)

        # -- 6. PCell test -----------------------------------------------
        # Basic.CIRCLE parameter names per the KLayout forum / docs:
        #   layer   (LayerInfo)   - target layer
        #   radius  (double, um)  - circle radius
        #   npoints (int)         - polygon approximation
        # (An older hypothesis said 'l', 'r', 'n'; those are wrong for
        # the Basic library - confirmed by klayout.de/forum examples.)
        try:
            pcell_info = c.pcell_info("CIRCLE", library="Basic")
            print("Basic.CIRCLE params (per pcell.info):")
            for p in pcell_info["params"]:
                print(f"    {p.get('name'):<16} type={p.get('type'):<8} "
                      f"default={p.get('default')}")
        except Exception as e:
            print(f"  (pcell.info failed, continuing with hardcoded names: {e})")

        try:
            res = c.instance_insert_pcell(
                cell, "CIRCLE", library="Basic",
                params={
                    "layer":   {"layer": 101, "datatype": 0},
                    "radius":  4.0,
                    "npoints": 64,
                },
                position_um=[70, 15])
            print(f"  inserted PCell -> variant cell {res['variant_cell']!r} "
                  f"(bbox_dbu={res.get('variant_bbox_dbu')})")
        except Exception as e:
            print(f"  PCell insert failed: {e}")

        # -- 7. Also place DEMO as an instance inside the original top ----
        # KLayout displays exactly one cell per cellview. cell.create
        # made DEMO as a new top cell, but your current view is still
        # pointing at `original_top`, which has no link to DEMO. To
        # make DEMO visible *inside* the existing top, we instantiate
        # it there too. This is the idiomatic GDS workflow: a "tile"
        # is a cell you draw once and drop into your chip as instances.
        if original_top and original_top != cell:
            try:
                c.instance_insert(original_top, cell, position_um=[0, 0])
                print(f"  instantiated {cell!r} inside {original_top!r} at (0,0)")
            except Exception as e:
                print(f"  could not instance DEMO into {original_top!r}: {e}")

        # -- 8. Switch viewer to DEMO ------------------------------------
        # view.show_cell hops the active cellview to DEMO and zoom-fits.
        # If you'd rather stay on the original top, call c.show_cell(
        # original_top) instead - you'll see DEMO as a sub-instance
        # because step 7 linked them.
        res = c.show_cell(cell)
        print(f"done. KLayout active cellview -> {res['cell']!r} "
              f"(cv_idx={res.get('active_cellview')}).")
        print("Inside DEMO you should see: metal frame, 2 poly boxes,")
        print("a triangle, a diagonal path, a text label, a 3x2 grid of")
        print("SUBCELL crosses, a rotated SUBCELL and a Basic.CIRCLE.")
        print(f"Double-click {original_top!r} in the Cells panel to switch")
        print("back; you'll see DEMO as an instance at origin there too.")


if __name__ == "__main__":
    main()
