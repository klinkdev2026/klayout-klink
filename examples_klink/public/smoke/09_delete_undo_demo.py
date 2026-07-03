"""
09_delete_undo_demo.py - Round 5 demo: shape.delete / instance.delete
                                       / edit.undo / edit.redo.

Walkthrough:
  1. Build a fresh cell DEL_DEMO containing:
       * 6 boxes on layer MAIN (101/0)    (two "keep" + four "kill")
       * 1 path on ROUTE (102/0)
       * 1 text on LABEL (66/0)
       * 3 VIA instances at known positions
  2. Query to show the initial population.
  3. shape.delete with bbox filter -> remove the four "kill" boxes.
  4. shape.delete with kinds=['texts'] -> remove the label.
  5. instance.delete with child='VIA' + bbox filter -> remove 2 of 3.
  6. edit.undo x3 -> everything comes back.
  7. edit.redo x3 -> everything gone again.
  8. shape.delete(all_layers=True, dry_run=True) -> show grand-total
     without actually nuking.

You need a layout open in KLayout. After the script finishes the
window will be viewing DEL_DEMO, zoom-fitted.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient


def counts(c, cell):
    """Snapshot of shape + instance counts in `cell`. We piggy-back on
    instance.delete(dry_run=True) as a cheap 'count instances' primitive
    and read the `matched` field (not `deleted`, which is 0 during a
    dry run by design)."""
    q = c.shape_query(cell=cell, limit=5000)
    by_layer: dict = {}
    for s in q["shapes"]:
        by_layer[s["layer_index"]] = by_layer.get(s["layer_index"], 0) + 1
    n_inst = c.instance_delete(cell, all=True, dry_run=True)["matched"]
    return {"shapes": len(q["shapes"]), "by_layer": by_layer, "instances": n_inst}


def main():
    with KLinkClient() as c:
        info = c.layout_info()
        if not info.get("has_view") or info.get("cell") is None:
            print("No layout open in KLayout. File -> New Layout first.")
            return
        original_top = info["cell"]

        # Fresh cells.
        demo = c.cell_create(name="DEL_DEMO")["name"]
        via  = c.cell_create(name="VIA")["name"]
        print(f"cells: demo={demo}, via={via}")

        # Layers.
        l_main_idx  = c.layer_ensure(layer=101, datatype=0, name="MAIN")["layer_index"]
        l_route_idx = c.layer_ensure(layer=102, datatype=0, name="ROUTE")["layer_index"]
        l_label_idx = c.layer_ensure(layer=66,  datatype=0, name="LABEL")["layer_index"]

        # Populate the VIA cell with a small square.
        c.shape_insert_box(via, layer_index=l_main_idx, bbox_um=[-1, -1, 1, 1])

        # ---- 1. populate DEL_DEMO -------------------------------------
        keep1 = c.shape_insert_box(demo, layer_index=l_main_idx, bbox_um=[ 0,  0,  4,  4])
        keep2 = c.shape_insert_box(demo, layer_index=l_main_idx, bbox_um=[50, 50, 54, 54])
        for (x1, y1, x2, y2) in [
            (10, 10, 20, 20),
            (22, 10, 32, 20),
            (10, 22, 20, 32),
            (22, 22, 32, 32),
        ]:
            c.shape_insert_box(demo, layer_index=l_main_idx, bbox_um=[x1, y1, x2, y2])

        c.shape_insert_path(demo, layer_index=l_route_idx,
                            points_um=[[0, 40], [60, 40]], width_um=1.0)
        c.shape_insert_text(demo, "DEL_DEMO", layer_index=l_label_idx,
                            position_um=[0, 60], size_um=2.0)

        for (x, y) in [(5, 70), (25, 70), (45, 70)]:
            c.instance_insert(demo, via, position_um=[x, y])

        before = counts(c, demo)
        print("\n-- initial counts --")
        print(f"  shapes={before['shapes']}  by_layer_idx={before['by_layer']}  instances={before['instances']}")

        # ---- 2. delete the four "kill" boxes via bbox filter ---------
        r = c.shape_delete(
            demo, layer_index=l_main_idx,
            bbox_um=[9, 9, 33, 33],
            kinds=["boxes"],
        )
        print(f"\n[shape.delete bbox]  deleted={r['deleted']}  per_layer={r['per_layer']}")

        # ---- 3. delete the text -------------------------------------
        r = c.shape_delete(demo, layer_index=l_label_idx, kinds=["texts"])
        print(f"[shape.delete text]  deleted={r['deleted']}  per_layer={r['per_layer']}")

        # ---- 4. delete 2 of 3 VIA instances (x in [0..30]) ----------
        r = c.instance_delete(demo, child="VIA", bbox_um=[0, 60, 30, 80])
        print(f"[instance.delete]   deleted={r['deleted']}  per_child={r['per_child']}")

        mid = counts(c, demo)
        print(f"\n-- after 3 deletes --\n  shapes={mid['shapes']}  by_layer_idx={mid['by_layer']}  instances={mid['instances']}")

        # ---- 5. edit.undo x3 (each delete is one transaction) --------
        print("\n-- edit.undo x3 --")
        for i in range(3):
            u = c.edit_undo()
            print(f"  undo #{i+1}: path={u['path']}  before={u['before']}  after={u['after']}")

        after_undo = counts(c, demo)
        print(f"\n-- after 3 undos --\n  shapes={after_undo['shapes']}  instances={after_undo['instances']}")
        if after_undo["shapes"] != before["shapes"] or after_undo["instances"] != before["instances"]:
            print("  WARN: undo did not fully restore the initial population!")

        # ---- 6. edit.redo x3 ---------------------------------------
        print("\n-- edit.redo x3 --")
        for i in range(3):
            r_ = c.edit_redo()
            print(f"  redo #{i+1}: path={r_['path']}")

        after_redo = counts(c, demo)
        print(f"\n-- after 3 redos --\n  shapes={after_redo['shapes']}  instances={after_redo['instances']}")
        if after_redo != mid:
            print("  WARN: redo landed on a different state than the pre-undo one!")

        # ---- 7. dry-run total nuke --------------------------------
        dry = c.shape_delete(demo, all_layers=True, dry_run=True)
        print(f"\n[dry_run all_layers]  would_delete={dry['matched']}  per_layer={dry['per_layer']}  truncated={dry['truncated']}")

        # Park the view on DEL_DEMO for the user to inspect.
        if original_top and original_top != demo:
            try:
                c.instance_insert(original_top, demo, position_um=[200, 0])
            except Exception:
                pass

        c.show_cell(demo)
        st = c.edit_status()
        print(f"\nedit.status: {st}")
        print(f"done. viewing {demo!r}.")


if __name__ == "__main__":
    main()
