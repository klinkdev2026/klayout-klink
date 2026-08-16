"""Raster exits — PNG/GIF renders of sections and SEM-style top views.

All rendering is deterministic (seeded noise, no timestamps) and driven
by the VisualStack declaration:

- section PNGs color materials by ``recipe_symbol`` match →
  ``recipe_styles`` → a deterministic auto-color (colors here are
  cosmetic; auto-colored materials are reported, never silently mixed
  with declared ones);
- SEM top views use each layer's ``sem_grey`` (secondary-electron
  emission level) and ``edge_glow`` (topography rim brightness), plus
  beam blur, film grain, scanlines and vignette;
- ``film_strip`` assembles per-step frames into a labeled contact sheet
  PNG and an animated GIF.

Optional deps: numpy, scipy, pillow (instructive error names the pip
command).  KLayout's Region does the merging/corner rounding.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .visual_stack import VisualStack


class RasterError(ValueError):
    """Bad input; the message says what to fix."""


def _font(ImageFont, size):
    """A readable TrueType face if the OS has one, else PIL's bitmap."""
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _nice_step(span: float) -> float:
    """A 1/2/5-decade tick step giving ~4-10 ticks across ``span``."""
    if span <= 0:
        return 1.0
    raw = span / 6.0
    decade = 10.0 ** math.floor(math.log10(raw))
    for mult in (1.0, 2.0, 5.0, 10.0):
        if raw <= mult * decade:
            return mult * decade
    return 10.0 * decade



def _deps():
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from scipy.ndimage import (binary_dilation, binary_erosion,
                                   gaussian_filter)
    except ImportError as exc:
        raise RasterError(
            f"raster rendering needs numpy, scipy and pillow in THIS "
            f"interpreter ({exc.name} is missing). Install with: "
            f"pip install numpy scipy pillow") from exc
    return (np, Image, ImageDraw, ImageFont, binary_dilation,
            binary_erosion, gaussian_filter)


def _auto_color(name: str) -> Tuple[int, int, int]:
    """Deterministic cosmetic color for an undeclared material."""
    import colorsys
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    r, g, b = colorsys.hsv_to_rgb(h, 0.45, 0.75)
    return int(r * 255), int(g * 255), int(b * 255)


def _hex_rgb(color: str) -> Tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _style_for(stack: Optional[VisualStack], symbol: str
               ) -> Tuple[str, Tuple[int, int, int], bool]:
    """-> (display name, rgb, declared?)."""
    if stack is not None:
        vl = stack.by_recipe_symbol(symbol)
        if vl is not None:
            return vl.name, _hex_rgb(vl.color), True
        style = stack.recipe_style(symbol)
        if style is not None:
            return (str(style["name"]),
                    _hex_rgb(str(style.get("color", "#8a8f96"))), True)
    return symbol, _auto_color(symbol), False


def _shaded(Image, np, size, rgb, shade: float):
    """A fill image with a subtle top-to-bottom gradient (cheap depth
    cue; ``shade=0`` gives the flat color)."""
    if shade <= 0:
        return Image.new("RGB", size, rgb)
    h = size[1]
    ramp = np.linspace(1.0 + shade, 1.0 - shade, h)[:, None]
    band = np.clip(np.array(rgb, float)[None, None, :] * ramp[..., None],
                   0, 255).astype("uint8")
    return Image.fromarray(np.repeat(band, size[0], axis=1), "RGB")


def render_section_png(
    section_gds: str,
    materials: Mapping[str, str],
    out_png: str,
    *,
    stack: Optional[VisualStack] = None,
    width_px: int = 1400,
    label: str = "",
    z_window_um: Optional[Sequence[float]] = None,
    axis: bool = False,
    supersample: bool = True,
    shade: float = 0.13,
) -> Dict[str, Any]:
    """Material-colored raster of one section GDS (paint order = layer
    number order, which is the engine's material definition order).

    ``z_window_um=(z_bottom, z_top)`` frames the vertical axis to that
    window instead of the whole section bbox. The engine's substrate
    runs microns deep while the interesting films are a few hundred
    nanometres thick, so the default framing spends most of the image on
    bulk; pick a window and the layers fill the frame. Geometry outside
    the window is clipped, not dropped.

    ``axis=True`` adds a z ruler down the left edge (µm ticks on the
    same mapping as the geometry) plus a lateral scale bar: a section
    without a scale is a picture, with one it is a measurement.

    Geometry is drawn 2x and downscaled (``supersample``) because the
    interesting edges here are process profiles — tapers, bird's beaks,
    rounded implant fronts — which alias badly when drawn flat. Each
    material gets a slight top-to-bottom gradient (``shade``, 0 = flat
    fill) so stacked films of similar color still read apart."""
    (np, Image, ImageDraw, ImageFont, *_rest) = _deps()
    import klayout.db as kdb

    ly = kdb.Layout(); ly.read(section_gds)
    top = ly.top_cell()
    bb = top.dbbox()
    if bb.empty():
        raise RasterError(f"{section_gds} has no geometry")
    scale = width_px / bb.width()
    w = width_px
    if z_window_um is not None:
        z_lo, z_hi = (float(v) for v in z_window_um)
        if not z_hi > z_lo:
            raise RasterError(
                f"z_window_um must be (z_bottom, z_top) with top above "
                f"bottom, got {tuple(z_window_um)!r}")
        top_y, height_um = z_hi, z_hi - z_lo
    else:
        top_y, height_um = bb.top, bb.height()
    h = max(int(height_um * scale), 40)
    pad_top = 36 if label else 0
    gutter = 74 if axis else 0
    # geometry is drawn SUPERSAMPLED and downscaled: polygon edges here
    # are process profiles (tapers, bird's beaks, rounded implants) and
    # they look like a staircase at 1x.
    ss = 2 if supersample else 1
    geo = Image.new("RGB", (w * ss, h * ss), (250, 249, 246))
    gdr = ImageDraw.Draw(geo)
    auto_colored: List[str] = []
    # NUMERIC layer order = the engine's material definition order
    # (string sort would paint "10/0" before "2/0")
    keys = sorted(
        (("%d/%d" % (ly.get_info(li).layer, ly.get_info(li).datatype)),
         li)
        for li in ly.layer_indexes())
    keys.sort(key=lambda kv: (int(kv[0].split("/")[0]),
                              int(kv[0].split("/")[1])))
    for key, li in keys:
        symbol = materials.get(key, key)
        _name, rgb, declared = _style_for(stack, symbol)
        if not declared and symbol not in auto_colored:
            auto_colored.append(symbol)
        for sh in top.shapes(li).each():
            if not (sh.is_box() or sh.is_polygon() or sh.is_path()):
                continue
            # dpolygon points are ALREADY microns (no dbu scaling).
            # Holes are punched via a mask so lower materials show
            # through voids instead of being over-painted.
            dp = sh.dpolygon
            def ring_pts(ring_iter):
                return [(((p.x - bb.left) * scale) * ss,
                         ((top_y - p.y) * scale) * ss)
                        for p in ring_iter]
            hull = ring_pts(dp.each_point_hull())
            holes = [ring_pts(dp.each_point_hole(hi))
                     for hi in range(dp.holes())]
            # one paint path for both cases: mask -> shaded fill ->
            # outline, so holes stay real and shading stays uniform
            mimg = Image.new("1", geo.size, 0)
            mdr = ImageDraw.Draw(mimg)
            mdr.polygon(hull, fill=1)
            for ring in holes:
                mdr.polygon(ring, fill=0)
            geo.paste(_shaded(Image, np, geo.size, rgb, shade), (0, 0),
                      mimg)
            edge = tuple(int(c * 0.55) for c in rgb)
            gdr.line(hull + [hull[0]], fill=edge, width=ss)
            for ring in holes:
                gdr.line(ring + [ring[0]], fill=edge, width=ss)
    if ss > 1:
        geo = geo.resize((w, h), Image.LANCZOS)
    img = Image.new("RGB", (w + gutter, h + pad_top), (250, 249, 246))
    img.paste(geo, (gutter, pad_top))
    dr = ImageDraw.Draw(img)
    if axis:
        small = _font(ImageFont, 17)
        z_lo_px = top_y - height_um
        step = _nice_step(height_um)
        dr.rectangle([0, pad_top, gutter - 1, pad_top + h],
                     fill=(244, 243, 239))
        dr.line([gutter - 1, pad_top, gutter - 1, pad_top + h],
                fill=(120, 122, 126))
        k = math.ceil(z_lo_px / step)
        while k * step <= top_y + 1e-9:
            z = k * step
            y = pad_top + (top_y - z) * scale
            dr.line([gutter - 8, y, gutter - 1, y], fill=(120, 122, 126))
            digits = max(0, -int(math.floor(math.log10(step))))
            txt = f"{z:.{digits}f}"
            if float(txt) == 0:            # no '-0.00' / '+0'
                txt = "0"
            dr.text((8, y - 9), txt, fill=(70, 72, 76), font=small)
            k += 1
        if not pad_top:          # no header bar to carry the unit
            dr.text((8, pad_top + h - 20), "z / µm",
                    fill=(70, 72, 76), font=small)
        # lateral scale bar, bottom right, on the same µm scale
        bar_um = _nice_step(bb.width() / 4) * 2
        bar_px = bar_um * scale
        if bar_px < w * 0.9:
            x1 = gutter + w - 24
            y0 = pad_top + h - 26
            dr.line([x1 - bar_px, y0, x1, y0], fill=(40, 42, 46), width=3)
            for x in (x1 - bar_px, x1):
                dr.line([x, y0 - 6, x, y0 + 6], fill=(40, 42, 46), width=3)
            cap = (f"{bar_um:g} µm")
            dr.text((x1 - bar_px, y0 + 6), cap, fill=(40, 42, 46),
                    font=small)
    if label:
        font = _font(ImageFont, 22)
        dr.rectangle([0, 0, w + gutter, 32], fill=(24, 26, 30))
        dr.text((gutter + 10, 5), label, fill=(230, 235, 240), font=font)
        if axis:                 # unit rides in the header, over the ruler
            dr.text((8, 8), "z / µm", fill=(150, 154, 160),
                    font=_font(ImageFont, 15))
    img.save(out_png)
    return {"path": out_png.replace(os.sep, "/"),
            "size": [img.width, img.height],
            "auto_colored": auto_colored}


def render_sem_png(
    gds_path: str,
    stack: VisualStack,
    out_grey: str,
    *,
    out_color: Optional[str] = None,
    cell: Optional[str] = None,
    layers: Optional[Sequence[str]] = None,
    width_px: int = 1600,
    corner_radius_um: float = 0.05,
    background_grey: float = 0.18,
    background_color: str = "#5a5f6e",
    edge_px: int = 3,
    grain: float = 0.10,
    scanline: float = 0.035,
    blur_px: float = 0.9,
    seed: int = 7,
) -> Dict[str, Any]:
    """SEM-style top view: per-layer grey from ``sem_grey``, bright rims
    from ``edge_glow``; paint order = stack order (upper occludes)."""
    (np, Image, ImageDraw, _ImageFont, binary_dilation, binary_erosion,
     gaussian_filter) = _deps()
    import klayout.db as kdb

    from ._util import top_cell_of

    ly = kdb.Layout(); ly.read(gds_path)
    top = top_cell_of(ly, cell, RasterError, gds_path)
    bb = top.dbbox()
    if bb.empty():
        raise RasterError(
            f"cell {top.name!r} in {gds_path} has no geometry — an SEM "
            f"render of nothing would be pure noise")
    scale = width_px / bb.width()
    w, h = width_px, max(int(bb.height() * scale), 20)
    dbu = ly.dbu

    grey = np.full((h, w), float(background_grey))
    tint = np.zeros((h, w, 3))
    tint[:] = np.array(_hex_rgb(background_color)) / 255.0
    used: List[str] = []
    wanted = set(layers) if layers is not None else None
    for vl in stack.layers:
        if wanted is not None and vl.layer not in wanted:
            continue
        l, d = (int(v) for v in vl.layer.split("/"))
        li = ly.find_layer(kdb.LayerInfo(l, d))
        if li is None:
            continue
        region = kdb.Region(top.begin_shapes_rec(li))
        region.merge()
        if corner_radius_um > 0:
            r = int(corner_radius_um / dbu)
            region.round_corners(r, r, 32)
        mimg = Image.new("1", (w, h), 0)
        mdr = ImageDraw.Draw(mimg)
        for poly in region.each():
            rings = [[(p.x * dbu, p.y * dbu)
                      for p in poly.each_point_hull()]]
            for hi in range(poly.holes()):
                rings.append([(p.x * dbu, p.y * dbu)
                              for p in poly.each_point_hole(hi)])
            for k, ring in enumerate(rings):
                pts = [((x - bb.left) * scale, (bb.top - y) * scale)
                       for x, y in ring]
                mdr.polygon(pts, fill=0 if k else 1)
        mask = np.array(mimg, bool)
        if not mask.any():
            continue
        used.append(vl.layer)
        grey[mask] = vl.sem_grey
        tint[mask] = np.array(_hex_rgb(vl.color)) / 255.0
        if vl.edge_glow > 0:
            rim = mask & ~binary_erosion(mask, iterations=edge_px)
            outer = binary_dilation(mask, iterations=1) & ~mask
            grey[rim] = np.clip(grey[rim] + 0.55 * vl.edge_glow, 0, 1.6)
            grey[outer] = np.clip(grey[outer] + 0.25 * vl.edge_glow,
                                  0, 1.6)

    grey = gaussian_filter(grey, blur_px)
    rng = np.random.default_rng(seed)
    grey += rng.normal(0, grain, grey.shape) * (0.35 + 0.65 * grey)
    grey *= 1 + scanline * np.sin(
        np.arange(h)[:, None] * 2.2 + rng.normal(0, 0.4, (h, 1)))
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    grey *= 1 - 0.18 * np.clip(r - 0.55, 0, 1) ** 2
    grey = np.clip(grey, 0, 1)
    Image.fromarray((grey * 255).astype(np.uint8), "L").save(out_grey)
    out = {"grey": out_grey.replace(os.sep, "/"),
           "layers_rendered": used, "size": [w, h]}
    if out_color:
        tint_blur = gaussian_filter(tint, (blur_px, blur_px, 0))
        col = np.clip(tint_blur * (0.25 + 1.35 * grey[..., None]), 0, 1)
        Image.fromarray((col * 255).astype(np.uint8), "RGB").save(
            out_color)
        out["color"] = out_color.replace(os.sep, "/")
    return out


def film_strip(
    frame_pngs: Sequence[str],
    out_png: str,
    out_gif: str,
    *,
    duration_ms: int = 900,
) -> Dict[str, Any]:
    """Vertical contact sheet + animated GIF from per-step frames."""
    (_np, Image, *_rest) = _deps()
    if not frame_pngs:
        raise RasterError("film_strip needs at least one frame")
    frames = [Image.open(p).convert("RGB") for p in frame_pngs]
    w = max(f.width for f in frames)
    sheet = Image.new("RGB", (w, sum(f.height for f in frames)),
                      (255, 255, 255))
    y = 0
    for f in frames:
        sheet.paste(f, (0, y)); y += f.height
    sheet.save(out_png)
    frames[0].save(out_gif, save_all=True, append_images=frames[1:],
                   duration=duration_ms, loop=0)
    return {"strip": out_png.replace(os.sep, "/"),
            "gif": out_gif.replace(os.sep, "/"),
            "frames": len(frames)}
