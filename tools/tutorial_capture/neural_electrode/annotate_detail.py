"""Draw an orange highlight box + arrow on step-07-detail.png.

selection.set_box's highlight doesn't render in view.screenshot's static
output, so per CLAUDE.md we annotate with Pillow instead. The highlighted
region is the top-right electrode pad in this cluster -- elec_right_x_um[2]
= 2480, elec_pad_y_um()[3] (row 3, the top row for elec_rows=4) = 45 --
where net n40's M3 (3/0) fanout trace, routed all the way from its bond pad
~10000 um away through the shared corridor, actually terminates.

step-07-detail.png was captured with the NEW honest bbox_um clip (no more
bbox-dbu-holding-microns workaround -- the plugin's view.* RPCs were
reloaded with real bbox_um/bbox_dbu units before this tutorial was
written):
    view.screenshot(bbox_um=[2370.0, -70.0, 2510.0, 70.0], width_px=1200, height_px=1200)
Because the crop is exactly square (140 x 140 um) and width_px == height_px,
there is no letterboxing and the um -> pixel mapping is exactly linear:
8.5714 px/um, pixel_x = (um_x - 2370) * 8.5714,
pixel_y = (70 - um_y) * 8.5714.

Run with --out-dir pointing at the SAME directory draw_neural_tutorial.py
wrote into (its default matches this script's default).
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "neural_electrode"

BBOX_UM = (2370.0, -70.0, 2510.0, 70.0)
PX_PER_UM = 1200.0 / 140.0  # 1200px / 140um == 1200px / 140um (square crop)
ORANGE = (230, 126, 34, 255)

# elec_right_x_um[2]=2480, elec_pad_size_um=(20,21) -> pad bbox
PAD_UM = (2470.0, 34.5, 2490.0, 55.5)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory holding step-07-detail.png (default: %(default)s)",
    )
    return parser.parse_args()


def um_to_px(x_um, y_um):
    x0, y0, x1, y1 = BBOX_UM
    return (
        (x_um - x0) * PX_PER_UM,
        (y1 - y_um) * PX_PER_UM,  # image y grows downward, layout y grows upward
    )


def main():
    args = _parse_args()
    out_dir = Path(args.out_dir)
    src = out_dir / "step-07-detail.png"
    dst = out_dir / "step-07-detail-annotated.png"

    im = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(im)

    x0, y0 = um_to_px(PAD_UM[0], PAD_UM[1])
    x1, y1 = um_to_px(PAD_UM[2], PAD_UM[3])
    pad = 10
    box = (min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad)
    draw.rectangle(box, outline=ORANGE, width=5)

    # Short arrow from lower-left, pointing at the box's bottom-left corner.
    tip = (box[0] - 8, box[3] + 8)
    tail = (tip[0] - 170, tip[1] + 140)
    draw.line([tail, tip], fill=ORANGE, width=4)
    import math
    ang = math.atan2(tip[1] - tail[1], tip[0] - tail[0])
    for da in (0.5, -0.5):
        a = ang + math.pi - da
        head = (tip[0] + 20 * math.cos(a), tip[1] + 20 * math.sin(a))
        draw.line([tip, head], fill=ORANGE, width=4)

    im.save(dst)
    print("saved", dst)


if __name__ == "__main__":
    main()
