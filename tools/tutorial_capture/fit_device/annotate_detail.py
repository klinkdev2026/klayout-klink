"""Annotate step-02-exemplar-detail.png with the fitted-edge story.

Overlays on the W=10/L=4 exemplar crop:
  * a blue double-headed arrow across the channel width (x -5..5 at y 0)
    labelled w_um -- the edges the fitter classified as driven by w_um;
  * a blue double-headed arrow across the source/drain gap (y -2..2)
    labelled l_um -- the edges driven by l_um;
  * an orange band on the source's OUTER edge (y = -11, x -7.5..7.5) --
    one of the 2 edges the fitter classified as CONSTANT (coef 0): the
    fixed S/D outer edge of the confirmed device model.

step-02-exemplar-detail.png is an exact square clip
(view.screenshot(bbox_um=[-29, -24, 19, 24], width_px=1200, height_px=1200)),
so the um -> pixel mapping is exactly linear: 25 px/um,
pixel_x = (um_x + 29) * 25, pixel_y = (24 - um_y) * 25.

Run with --out-dir pointing at the SAME directory
draw_fit_device_tutorial.py wrote into (defaults match).
"""
import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "test_outputs" / "tutorial_capture" / "fit_device"

BBOX_UM = (-29.0, -24.0, 19.0, 24.0)
PX_PER_UM = 1200.0 / 48.0
BLUE = (30, 80, 220)
ORANGE = (230, 126, 34)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="Directory holding step-02-exemplar-detail.png (default: %(default)s)",
    )
    return parser.parse_args()


def um_to_px(x_um, y_um):
    x0, _y0, _x1, y1 = BBOX_UM
    return ((x_um - x0) * PX_PER_UM,
            (y1 - y_um) * PX_PER_UM)  # image y grows downward


def _arrowhead(draw, tip, ang, color, size=18, width=5):
    for da in (0.45, -0.45):
        a = ang + math.pi - da
        head = (tip[0] + size * math.cos(a), tip[1] + size * math.sin(a))
        draw.line([tip, head], fill=color, width=width)


def double_arrow(draw, p, q, color, width=5):
    draw.line([p, q], fill=color, width=width)
    ang = math.atan2(q[1] - p[1], q[0] - p[0])
    _arrowhead(draw, q, ang, color, width=width)
    _arrowhead(draw, p, ang + math.pi, color, width=width)


def _font(size=34):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def main():
    args = _parse_args()
    out_dir = Path(args.out_dir)
    src = out_dir / "step-02-exemplar-detail.png"
    dst = out_dir / "step-02-exemplar-detail-annotated.png"

    im = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(im)
    font = _font()

    # w_um: channel width, x in [-5, 5] at y = 0 (channel centre line)
    double_arrow(draw, um_to_px(-5.0, 0.0), um_to_px(5.0, 0.0), BLUE)
    draw.text((um_to_px(0.0, 0.0)[0] - 46, um_to_px(0.0, 0.0)[1] + 14),
              "w_um", fill=BLUE, font=font)

    # l_um: source/drain gap, y in [-2, 2], drawn at x = 10 (right of the
    # pads) with thin guide lines out from the pad inner edges (x = 7.5)
    gx = 10.0
    for gy in (-2.0, 2.0):
        draw.line([um_to_px(7.5, gy), um_to_px(gx + 2.0, gy)],
                  fill=BLUE, width=2)
    double_arrow(draw, um_to_px(gx, -2.0), um_to_px(gx, 2.0), BLUE)
    draw.text((um_to_px(gx, 0.0)[0] + 14, um_to_px(gx, 0.0)[1] - 20),
              "l_um", fill=BLUE, font=font)

    # constant edge: the source's fixed outer edge y = -11, x in [-7.5, 7.5]
    x0, y0 = um_to_px(-7.5, -11.0)
    x1, _ = um_to_px(7.5, -11.0)
    draw.rectangle((x0 - 6, y0 - 10, x1 + 6, y0 + 10), outline=ORANGE, width=5)
    draw.text((x1 + 20, y0 - 20), "coef=0", fill=ORANGE, font=font)

    im.save(dst)
    print("saved", dst)


if __name__ == "__main__":
    main()
