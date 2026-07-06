"""Draw an orange highlight box + arrow on step-06-gcup-detail.png.

selection.set_box's highlight doesn't render in view.screenshot's static
output, so per CLAUDE.md we annotate with Pillow instead. The highlighted
point is grating_coupler_elliptical4_o1 (gc_up's fiber port) -- queried
live via port.list AFTER the post-drag reroute() call, at its NEW harvested
position (360.0, 100.0), orientation 180 -- proof the net table re-harvested
from the live (dragged) instance position rather than the script's original
placement.

step-06-gcup-detail.png was captured with an exact bbox_um clip:
    view.screenshot(bbox_um=[320.0, -20.0, 410.0, 160.0], width_px=900, height_px=1800)
The crop is 90 x 180 um mapped to 900 x 1800 px -- exactly 10 px/um on both
axes, so the um -> pixel mapping is linear with no letterboxing:
    pixel_x = (um_x - 320) * 10
    pixel_y = (160 - um_y) * 10

Run with --out-dir pointing at the SAME directory draw_gf_mzi_tutorial.py
wrote into (its default matches this script's default).
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "gf_mzi_module"

BBOX_UM = (320.0, -20.0, 410.0, 160.0)
PX_PER_UM = 10.0
ORANGE = (230, 126, 34, 255)

# grating_coupler_elliptical4_o1 (gc_up's fiber port), live-harvested AFTER
# the post-drag reroute -- from port.list, NOT the script's original (360,30).
PORT_UM = (360.0, 100.0)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory holding step-06-gcup-detail.png (default: %(default)s)",
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
    src = out_dir / "step-06-gcup-detail.png"
    dst = out_dir / "step-06-gcup-detail-annotated.png"

    im = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(im)

    cx, cy = um_to_px(*PORT_UM)
    pad = 45
    box = (cx - pad, cy - pad, cx + pad, cy + pad)
    draw.rectangle(box, outline=ORANGE, width=5)

    # Short arrow from lower-left, pointing at the box's bottom-left corner.
    tip = (box[0] - 8, box[3] + 8)
    tail = (tip[0] - 160, tip[1] + 160)
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
