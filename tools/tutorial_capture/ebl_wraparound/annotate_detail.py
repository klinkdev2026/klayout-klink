"""Draw an orange highlight box + arrow on step-07-detail.png.

selection.set_box's highlight doesn't render in view.screenshot's static
output, so per CLAUDE.md we annotate with Pillow instead. The highlighted
region is the stitch patch (113/0) at um bbox [-118.5, 14.5, -111.5, 21.5]
-- the exact spot where the C0 net's M2 wraparound path crosses the x=-115
writefield boundary through the WF_XL_MID corridor window.

step-07-detail.png was captured via draw_ebl_tutorial.py's snap() as
    view.zoom_box(bbox_um=[-145, -5, -95, 40])
    view.screenshot(width_px=1200)
(no separate height_px, so KLayout expands one axis to the widget's own
aspect ratio -- see the "look about here" note in the other tutorials'
snap() docstrings). The 24 px/um mapping below assumes the resulting image
still matches this 50 x 45 um box's own aspect ratio (1200 x 1080); if the
capture widget's aspect ratio differs, re-derive PX_PER_UM/BBOX_UM from the
actual saved image dimensions before trusting this overlay.

Run with --out-dir pointing at the SAME directory draw_ebl_tutorial.py wrote
into (its default matches this script's default).
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "ebl_wraparound"

BBOX_UM = (-145.0, -5.0, -95.0, 40.0)
PX_PER_UM = 24.0  # 1200px / 50um == 1080px / 45um
ORANGE = (230, 126, 34, 255)

PATCH_UM = (-118.5, 14.5, -111.5, 21.5)


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

    x0, y0 = um_to_px(PATCH_UM[0], PATCH_UM[1])
    x1, y1 = um_to_px(PATCH_UM[2], PATCH_UM[3])
    pad = 8
    box = (min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad)
    draw.rectangle(box, outline=ORANGE, width=4)

    # Short arrow from upper-right, pointing at the box's top-right corner.
    tip = (box[2] + 6, box[1] - 6)
    tail = (tip[0] + 160, tip[1] - 140)
    draw.line([tail, tip], fill=ORANGE, width=3)
    # simple arrowhead
    import math
    ang = math.atan2(tip[1] - tail[1], tip[0] - tail[0])
    for da in (0.5, -0.5):
        a = ang + math.pi - da
        head = (tip[0] + 18 * math.cos(a), tip[1] + 18 * math.sin(a))
        draw.line([tip, head], fill=ORANGE, width=3)

    im.save(dst)
    print("saved", dst)


if __name__ == "__main__":
    main()
