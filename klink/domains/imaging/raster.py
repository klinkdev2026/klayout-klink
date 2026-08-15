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
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .visual_stack import VisualStack


class RasterError(ValueError):
    """Bad input; the message says what to fix."""


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


def render_section_png(
    section_gds: str,
    materials: Mapping[str, str],
    out_png: str,
    *,
    stack: Optional[VisualStack] = None,
    width_px: int = 1400,
    label: str = "",
) -> Dict[str, Any]:
    """Material-colored raster of one section GDS (paint order = layer
    number order, which is the engine's material definition order)."""
    (np, Image, ImageDraw, ImageFont, *_rest) = _deps()
    import klayout.db as kdb

    ly = kdb.Layout(); ly.read(section_gds)
    top = ly.top_cell()
    bb = top.dbbox()
    if bb.empty():
        raise RasterError(f"{section_gds} has no geometry")
    scale = width_px / bb.width()
    w = width_px
    h = max(int(bb.height() * scale), 40)
    pad_top = 36 if label else 0
    img = Image.new("RGB", (w, h + pad_top), (250, 249, 246))
    dr = ImageDraw.Draw(img)
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
                return [((p.x - bb.left) * scale,
                         pad_top + (bb.top - p.y) * scale)
                        for p in ring_iter]
            hull = ring_pts(dp.each_point_hull())
            if dp.holes():
                mimg = Image.new("1", img.size, 0)
                mdr = ImageDraw.Draw(mimg)
                mdr.polygon(hull, fill=1)
                for hi in range(dp.holes()):
                    mdr.polygon(ring_pts(dp.each_point_hole(hi)),
                                fill=0)
                img.paste(Image.new("RGB", img.size, rgb), (0, 0),
                          mimg)
                dr.polygon(hull, outline=(60, 60, 60))
                for hi in range(dp.holes()):
                    dr.polygon(ring_pts(dp.each_point_hole(hi)),
                               outline=(60, 60, 60))
            else:
                dr.polygon(hull, fill=rgb, outline=(60, 60, 60))
    if label:
        try:
            font = ImageFont.truetype("arial.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
        dr.rectangle([0, 0, w, 32], fill=(24, 26, 30))
        dr.text((10, 5), label, fill=(230, 235, 240), font=font)
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
