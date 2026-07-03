r"""
Walk the layout: list cells, look at the hierarchy tree, pull some
shapes off the top cell and print a summary.

Run KLayout with the klink plugin installed and a GDS loaded, then:
    python .\examples_klink\04_cells_and_shapes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient


def main():
    with KLinkClient() as c:
        info = c.layout_info()
        if not info.get("has_view") or not info.get("cell"):
            print("No layout open in KLayout. Load a GDS first.")
            return
        top_cell = info["cell"]
        dbu = info["dbu"]
        print(f"Active layout: top cell = {top_cell!r}, dbu = {dbu}")

        print("\n--- cell.list (first 20) ---")
        cl = c.cell_list(limit=20, with_bbox=True)
        for entry in cl["cells"]:
            bb = entry.get("bbox_dbu")
            bb_str = f"{bb}" if bb else "empty"
            print(f"  {entry['name']:30s} idx={entry['index']:>5}  bbox_dbu={bb_str}")
        print(f"total cells: {cl['total']}")

        print("\n--- cell.tree (max_depth=3) ---")
        tree = c.cell_tree(root=top_cell, max_depth=3, max_nodes=200)
        _print_tree(tree["tree"])
        if tree["truncated"]:
            print("  (tree was truncated)")

        print("\n--- shape.query (top cell, all layers, limit=20) ---")
        sq = c.shape_query(top_cell, limit=20)
        print(f"returned {sq['returned']} shapes (truncated={sq['truncated']})")
        for s in sq["shapes"][:10]:
            if s["type"] == "box":
                print(f"  box     layer_idx={s['layer_index']}  bbox_dbu={s['bbox_dbu']}")
            elif s["type"] == "polygon":
                print(f"  polygon layer_idx={s['layer_index']}  pts={len(s['points_dbu'])}")
            elif s["type"] == "path":
                print(f"  path    layer_idx={s['layer_index']}  w_dbu={s['width_dbu']}  pts={len(s['points_dbu'])}")
            elif s["type"] == "text":
                print(f"  text    layer_idx={s['layer_index']}  {s['string']!r} @ {s['position_dbu']}")


def _print_tree(node, depth: int = 0):
    pad = "  " * depth
    print(f"{pad}- {node['name']}  (instances={node['instances']})")
    for child in node.get("children", []):
        _print_tree(child, depth + 1)


if __name__ == "__main__":
    main()
