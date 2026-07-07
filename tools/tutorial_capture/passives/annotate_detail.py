"""Draw an orange highlight on baw-pentagon-crop.png marking the pentagon's
closest-to-parallel edge pair (still NOT parallel -- that's the point).

Reads ``baw_edge_annotation.json`` written by ``draw_passives_tutorial.py``
in the SAME --out-dir: it records the exact crop bbox_um/width_px/height_px
used for ``baw-pentagon-crop.png`` (an exact-linear-mapping crop -- square
aspect was NOT assumed, so the um->pixel scale can differ per axis, computed
below from the recorded bbox) and the two edges (each a pair of um points,
taken directly from the template's own ``build_baw_fbar()`` pentagon
vertices -- no geometry is invented here) whose directions are numerically
closest to parallel among all 5 edges.

Run with --out-dir pointing at the SAME directory draw_passives_tutorial.py
wrote into (its default matches this script's default).
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "passives"

ORANGE = (230, 126, 34, 255)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory holding baw-pentagon-crop.png + baw_edge_annotation.json (default: %(default)s)",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    out_dir = Path(args.out_dir)
    meta_path = out_dir / "baw_edge_annotation.json"
    with open(meta_path) as f:
        meta = json.load(f)

    src = Path(meta["crop_path"])
    dst = out_dir / "baw-apodization-annotated.png"

    bbox_um = meta["bbox_um"]
    width_px = meta["width_px"]
    height_px = meta["height_px"]
    x0, y0, x1, y1 = bbox_um
    px_per_um_x = width_px / (x1 - x0)
    px_per_um_y = height_px / (y1 - y0)

    def um_to_px(pt):
        x_um, y_um = pt
        return (
            (x_um - x0) * px_per_um_x,
            (y1 - y_um) * px_per_um_y,  # image y grows downward, layout y grows upward
        )

    im = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(im)

    for edge_pts in (meta["edge_i_points_um"], meta["edge_j_points_um"]):
        p0 = um_to_px(edge_pts[0])
        p1 = um_to_px(edge_pts[1])
        draw.line([p0, p1], fill=ORANGE, width=6)
        for p in (p0, p1):
            r = 7
            draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], outline=ORANGE, width=3)

    label = f"edges {meta['edge_i_index']} & {meta['edge_j_index']}: {meta['diff_deg']:.1f}° apart, not parallel"
    font_size = 30
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    text_box = draw.textbbox((0, 0), label, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    pad = 10
    box = [8, height_px - text_h - 2 * pad - 8, 8 + text_w + 2 * pad, height_px - 8]
    draw.rectangle(box, fill=(255, 255, 255), outline=ORANGE, width=2)
    draw.text((box[0] + pad, box[1] + pad), label, fill=ORANGE, font=font)

    im.save(dst)
    print("saved", dst)


if __name__ == "__main__":
    main()
