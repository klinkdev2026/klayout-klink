"""
Zoo of all Basic-library PCells via klink.

Places one instance of each Basic PCell KLayout ships with:

    CIRCLE, ARC, DONUT, ELLIPSE,
    ROUND_PATH, ROUND_POLYGON,
    STROKED_BOX, STROKED_POLYGON,
    TEXT

Each is laid out in a 3x3 grid of 30x30 um tiles inside a fresh cell
named PCELL_ZOO, so you can flip through them in KLayout.

If a PCell parameter name is wrong for your KLayout build (KLayout
has tweaked Basic PCell params over versions), the call will fail for
that one PCell only; the rest of the zoo will still render. On
failure the script also queries pcell.info to print the real
parameter names that KLayout accepts, so you can retry with the
correct names via c.instance_insert_pcell(..., params={...}).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from klink import KLinkClient


TILE = 30.0   # um, tile size
PAD  = 4.0    # um, margin inside tile


def tile_xy(col, row):
    """Lower-left corner of a tile in the 3x3 layout grid (um)."""
    return (col * TILE, row * TILE)


def tile_center(col, row):
    return (col * TILE + TILE / 2, row * TILE + TILE / 2)


def main():
    with KLinkClient() as c:
        info = c.layout_info()
        if not info.get("has_view") or info.get("cell") is None:
            print("No layout is open in KLayout. "
                  "File -> New Layout (DBU 0.001), then re-run.")
            return
        original_top = info["cell"]

        # Dedicated cell for the zoo.
        zoo = c.cell_create(name="PCELL_ZOO")["name"]
        print(f"zoo cell: {zoo}")

        # Layers. We only need a main geometry layer and a text layer;
        # ensure them up front so they appear in KLayout's layer panel.
        c.layer_ensure(layer=101, datatype=0, name="MAIN")
        l_text = c.layer_ensure(layer=66, datatype=0, name="LABEL")["layer_index"]

        # Put a small text label in each tile corner so it's easy to
        # tell which PCell is which at a glance. These labels go on
        # layer 66/0 (LABEL).
        labels_and_positions = [
            ("CIRCLE",          (0, 2)),
            ("ARC",             (1, 2)),
            ("DONUT",           (2, 2)),
            ("ELLIPSE",         (0, 1)),
            ("ROUND_PATH",      (1, 1)),
            ("ROUND_POLYGON",   (2, 1)),
            ("STROKED_BOX",     (0, 0)),
            ("STROKED_POLYGON", (1, 0)),
            ("TEXT",            (2, 0)),
        ]
        for name, (col, row) in labels_and_positions:
            x, y = tile_xy(col, row)
            c.shape_insert_text(zoo, name, layer_index=l_text,
                                position_um=[x + 0.5, y + TILE - 2.0])

        # Place each PCell at the center of its tile.
        results = []
        r = TILE / 2 - PAD  # shared "big radius"

        def safe_call(label, pcell_name, fn):
            """Run fn(); report the variant's bounding box and (on
            failure) the server-side diagnostic: how the parameter dict
            was coerced into pya objects, and how many shapes the PCell
            actually produced. A non-empty adapted_params plus
            variant_shape_count=0 means the PCell's produce_impl ran
            but emitted nothing (usually a hidden actual_* param or an
            extra handle-type param is still needed)."""
            try:
                res = fn()
                bb = res.get("variant_bbox_dbu") if res else None
                n_shapes = res.get("variant_shape_count", 0) if res else 0
                by_layer = res.get("variant_shapes_by_layer") if res else None
                adapted = res.get("adapted_params") if res else None
                if bb:
                    w = (bb[2] - bb[0]) / 1000.0
                    h = (bb[3] - bb[1]) / 1000.0
                    status = (f"ok  bbox={bb}  ({w:.2f}x{h:.2f} um) "
                              f"{n_shapes} shape(s) -> {by_layer}")
                    if max(w, h) < 1.0:
                        status += "  <-- TINY! shape param likely ignored"
                else:
                    status = (f"ok  (no bbox?)  {n_shapes} shape(s) -> "
                              f"{by_layer}")
                results.append((label, status))
                # Print adapted params on ANY anomaly (empty bbox or no
                # shapes produced) so the user/agent can see what type
                # each param arrived at on the server.
                if (not bb) or n_shapes == 0:
                    print(f"  [{label}] adapted_params:")
                    for k, v in (adapted or {}).items():
                        print(f"      {k} -> {v}")
                return res
            except Exception as e:
                results.append((label, f"ERR: {e}"))
                print(f"  {label}: {e}")
                try:
                    info = c.pcell_info(pcell_name, library="Basic")
                    names = [pd.get("name") for pd in info.get("params", [])]
                    print(f"    Basic.{pcell_name} actually accepts: {names}")
                except Exception as e2:
                    print(f"    (could not fetch schema: {e2})")
                return None

        # CIRCLE
        safe_call("CIRCLE", "CIRCLE", lambda: c.basic_circle(
            zoo, layer=(101, 0), radius=r - 2, npoints=64,
            position_um=list(tile_center(0, 2))))

        # ARC - half ring from 0 to 180 deg
        safe_call("ARC", "ARC", lambda: c.basic_arc(
            zoo, layer=(101, 0),
            radius1=r - 6, radius2=r - 2,
            start_angle=0, end_angle=180, npoints=64,
            position_um=list(tile_center(1, 2))))

        # DONUT
        safe_call("DONUT", "DONUT", lambda: c.basic_donut(
            zoo, layer=(101, 0), radius1=r - 7, radius2=r - 2, npoints=64,
            position_um=list(tile_center(2, 2))))

        # ELLIPSE
        safe_call("ELLIPSE", "ELLIPSE", lambda: c.basic_ellipse(
            zoo, layer=(101, 0), radius_x=r - 1, radius_y=r - 5, npoints=64,
            position_um=list(tile_center(0, 1))))

        # ROUND_PATH - simple L-shape, 2um wide line with 2um-radius
        # rounded corner. With the zoo tile at 30um, the L has legs of
        # ~16um which makes the rounding clearly visible.
        cx, cy = tile_center(1, 1)
        pts = [[cx - 8, cy - 8], [cx + 8, cy - 8], [cx + 8, cy + 8]]
        safe_call("ROUND_PATH", "ROUND_PATH", lambda: c.basic_round_path(
            zoo, layer=(101, 0),
            points_um=pts, width_um=2.0,
            radius=2.0, npoints=32))

        # ROUND_POLYGON - blunted triangle with 2um corner radius.
        cx, cy = tile_center(2, 1)
        tri = [[cx - 9, cy - 8], [cx + 9, cy - 8], [cx, cy + 9]]
        safe_call("ROUND_POLYGON", "ROUND_POLYGON",
                  lambda: c.basic_round_polygon(
            zoo, layer=(101, 0),
            points_um=tri, radius=2.0, npoints=32))

        # STROKED_BOX - rim of a rectangle, 1.5um wide line
        x, y = tile_xy(0, 0)
        safe_call("STROKED_BOX", "STROKED_BOX",
                  lambda: c.basic_stroked_box(
            zoo, layer=(101, 0),
            bbox_um=[x + PAD, y + PAD, x + TILE - PAD, y + TILE - PAD],
            width_um=1.5))

        # STROKED_POLYGON - outline of a pentagon, 1.5um wide line
        cx, cy = tile_center(1, 0)
        from math import cos, sin, radians
        pent = [[cx + (r - 2) * cos(radians(90 + 72 * i)),
                 cy + (r - 2) * sin(radians(90 + 72 * i))]
                for i in range(5)]
        safe_call("STROKED_POLYGON", "STROKED_POLYGON",
                  lambda: c.basic_stroked_polygon(
            zoo, layer=(101, 0),
            points_um=pent, width_um=1.5))

        # TEXT
        cx, cy = tile_xy(2, 0)
        safe_call("TEXT", "TEXT", lambda: c.basic_text(
            zoo, layer=(66, 0),
            text="HELLO",
            mag=2.5,
            position_um=[cx + PAD, cy + PAD * 2]))

        # Summary
        print("\nZoo results:")
        for label, status in results:
            print(f"  {label:<16} {status}")

        # Plant PCELL_ZOO into the original top so users can see it
        # without having to switch cellviews.
        if original_top and original_top != zoo:
            try:
                c.instance_insert(original_top, zoo, position_um=[100, 0])
                print(f"\ninstantiated {zoo!r} inside {original_top!r} "
                      f"at (100, 0)")
            except Exception as e:
                print(f"could not instance zoo into {original_top!r}: {e}")

        c.show_cell(zoo)
        print(f"\ndone. viewing cell {zoo!r}. "
              f"the 3x3 tiles (30x30 um each) cover:")
        print("  row 2: CIRCLE | ARC | DONUT")
        print("  row 1: ELLIPSE | ROUND_PATH | ROUND_POLYGON")
        print("  row 0: STROKED_BOX | STROKED_POLYGON | TEXT")


if __name__ == "__main__":
    main()
