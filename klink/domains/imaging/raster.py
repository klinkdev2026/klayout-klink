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

from ._util import kdb as _kdb

class RasterError(ValueError):
    """Bad input; the message says what to fix."""


# Chrome (tick numbers, "z / µm", scale bar) is ASCII, so the Latin
# faces serve it.  USER text is not: a step marker reads
# ``# klink-step: 生长栅氧`` as readily as ``# klink-step: gate ox``, and
# NONE of arial/DejaVu/Liberation carries a Han glyph — PIL's bitmap
# default carries even less.  Without the CJK list below such a label
# renders as tofu boxes into a sha256'd PNG/GIF: wrong output, green run.
_LATIN_FONTS = ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")
_CJK_FONTS = (
    "msyh.ttc", "simhei.ttf", "simsun.ttc",              # Windows
    "NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.otf",
    "NotoSansSC-Regular.otf", "wqy-zenhei.ttc",
    "DroidSansFallbackFull.ttf",                         # Linux
    "PingFang.ttc", "Hiragino Sans GB.ttc",
    "STHeiti Light.ttc", "Arial Unicode.ttf",            # macOS
)
# a plane-15 private-use codepoint: no real face defines it, so its mask
# IS that face's .notdef rendering
_NOTDEF_PROBE = "\U000f0000"
_FONT_CACHE: Dict[Tuple[str, int], Any] = {}


def _try_font(ImageFont, name: str, size: int):
    key = (name, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(name, size)
        except OSError:
            _FONT_CACHE[key] = None
    return _FONT_CACHE[key]


def _font(ImageFont, size):
    """A readable TrueType face if the OS has one, else PIL's bitmap.
    For ASCII chrome only — user text goes through ``_text_font``."""
    for name in _LATIN_FONTS:
        font = _try_font(ImageFont, name, size)
        if font is not None:
            return font
    return ImageFont.load_default()


def _missing_glyphs(font, text: str) -> List[str]:
    """The characters ``font`` would draw as .notdef (tofu), in order.

    A missing character maps to glyph 0, so its rendered mask is
    byte-identical to the probe's.  This ASKS the loaded face instead of
    guessing coverage from the font's name (SimHei, for one, has Han but
    no 'µ').  ``bytes(mask)``, not ``mask.tobytes()``: getmask returns a
    raw ImagingCore."""
    try:
        blank = bytes(font.getmask(_NOTDEF_PROBE))
    except Exception:            # a face that refuses the probe tells
        return [c for c in dict.fromkeys(text)   # us nothing: be strict
                if not c.isspace() and ord(c) > 0x7F]
    out: List[str] = []
    for ch in dict.fromkeys(text):
        if ch.isspace():
            continue
        try:
            if bytes(font.getmask(ch)) == blank:
                out.append(ch)
        except Exception:
            out.append(ch)
    return out


def _text_font(ImageFont, size: int, text: str):
    """``(font, missing)`` for USER-supplied text.

    Picks the first available face that actually has every glyph.
    ``missing`` is what no installed face could draw — REPORTED to the
    caller, never quietly painted as boxes."""
    if not text:
        return _font(ImageFont, size), []
    needs_cjk = any(ord(c) > 0x2E7F for c in text)
    order = ((_CJK_FONTS + _LATIN_FONTS) if needs_cjk
             else (_LATIN_FONTS + _CJK_FONTS))
    fallback = fallback_missing = None
    for name in order:
        font = _try_font(ImageFont, name, size)
        if font is None:
            continue
        missing = _missing_glyphs(font, text)
        if not missing:
            return font, []
        if fallback is None:
            fallback, fallback_missing = font, missing
    if fallback is not None:
        return fallback, fallback_missing
    default = ImageFont.load_default()
    return default, _missing_glyphs(default, text)


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


def _auto_color(name: str, spec: Mapping[str, Any]
                ) -> Tuple[int, int, int]:
    """Deterministic fallback colour for a material the stack never
    declared. The HUE is derived from the name (so it is stable across
    runs and frames); how saturated and how bright that hue is drawn is
    the caller's declaration, not klink's taste."""
    import colorsys
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    r, g, b = colorsys.hsv_to_rgb(h, float(spec["saturation"]),
                                  float(spec["value"]))
    return int(r * 255), int(g * 255), int(b * 255)


def _hex_rgb(color: str) -> Tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _style_for(stack: Optional[VisualStack], symbol: str,
               auto_spec: Mapping[str, Any]
               ) -> Tuple[str, Tuple[int, int, int], bool]:
    """-> (display name, rgb, declared?).

    A recipe_style that names a material but gives it no colour falls
    back to the same name-derived auto colour rather than to some fixed
    grey — klink has no house colour to offer."""
    if stack is not None:
        vl = stack.by_recipe_symbol(symbol)
        if vl is not None:
            return vl.name, _hex_rgb(vl.color), True
        style = stack.recipe_style(symbol)
        if style is not None:
            declared = style.get("color")
            rgb = (_hex_rgb(str(declared)) if declared
                   else _auto_color(symbol, auto_spec))
            return str(style["name"]), rgb, bool(declared)
    return symbol, _auto_color(symbol, auto_spec), False


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
    style,
    *,
    stack: Optional[VisualStack] = None,
    width_px: int = 1400,
    label: str = "",
    z_window_um: Optional[Sequence[float]] = None,
    axis: bool = False,
) -> Dict[str, Any]:
    """Material-coloured raster of one section GDS (paint order = layer
    number order, which is the engine's material definition order).

    ``style`` is a
    :class:`~klink.domains.imaging.section_style.SectionStyle`. klink
    ships none: page colour, gradient, outline darkening, ruler
    colours, label bar, scale bar and the fallback colour for
    undeclared materials all come from it.

    ``z_window_um=(z_bottom, z_top)`` frames the vertical axis to that
    window instead of the whole section bbox. The engine's substrate
    runs microns deep while the interesting films are a few hundred
    nanometres thick, so the default framing spends most of the image on
    bulk; pick a window and the layers fill the frame. Geometry outside
    the window is clipped, not dropped.

    ``axis=True`` adds a z ruler down the left edge (um ticks on the
    same mapping as the geometry) plus a lateral scale bar: a section
    without a scale is a picture, with one it is a measurement."""
    (np, Image, ImageDraw, ImageFont, *_rest) = _deps()
    kdb = _kdb(RasterError)
    page, ax = style.page, style.axis
    shade = float(page["geometry_shade"])
    edge_darken = float(page["edge_darken"])
    page_rgb = _hex_rgb(str(page["background"]))

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
    h = max(int(height_um * scale), int(page["min_height_px"]))
    lb = style.label_bar
    pad_top = int(lb["height_px"]) if (label and lb is not None) else 0
    gutter = int(ax["gutter_px"]) if axis else 0
    # geometry is drawn SUPERSAMPLED and downscaled: polygon edges here
    # are process profiles (tapers, bird's beaks, rounded implants) and
    # they look like a staircase at 1x.
    ss = max(1, int(page["supersample"]))
    geo = Image.new("RGB", (w * ss, h * ss), page_rgb)
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
        _name, rgb, declared = _style_for(stack, symbol, style.auto_color)
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
            edge = tuple(int(c * edge_darken) for c in rgb)
            gdr.line(hull + [hull[0]], fill=edge, width=ss)
            for ring in holes:
                gdr.line(ring + [ring[0]], fill=edge, width=ss)
    if ss > 1:
        geo = geo.resize((w, h), Image.LANCZOS)
    img = Image.new("RGB", (w + gutter, h + pad_top), page_rgb)
    img.paste(geo, (gutter, pad_top))
    dr = ImageDraw.Draw(img)
    unit_text = str(ax["unit_text"])
    if axis:
        small = _font(ImageFont, int(ax["tick_font_px"]))
        rule_rgb = _hex_rgb(str(ax["rule_color"]))
        tick_rgb = _hex_rgb(str(ax["tick_color"]))
        z_lo_px = top_y - height_um
        step = _nice_step(height_um)
        dr.rectangle([0, pad_top, gutter - 1, pad_top + h],
                     fill=_hex_rgb(str(ax["gutter_background"])))
        dr.line([gutter - 1, pad_top, gutter - 1, pad_top + h],
                fill=rule_rgb)
        k = math.ceil(z_lo_px / step)
        while k * step <= top_y + 1e-9:
            z = k * step
            y = pad_top + (top_y - z) * scale
            dr.line([gutter - 8, y, gutter - 1, y], fill=rule_rgb)
            digits = max(0, -int(math.floor(math.log10(step))))
            txt = f"{z:.{digits}f}"
            if float(txt) == 0:            # no '-0.00' / '+0'
                txt = "0"
            dr.text((8, y - int(ax["tick_font_px"]) // 2), txt,
                    fill=tick_rgb, font=small)
            k += 1
        if not pad_top:          # no header bar to carry the unit
            dr.text((8, pad_top + h - 20), unit_text, fill=tick_rgb,
                    font=small)

    sb = style.scale_bar
    scale_report = None
    if axis and sb is not None:
        # lateral scale bar, bottom right, on the same um scale
        bar_um = _nice_bar(bb.width() * float(sb["target_fraction"]))
        bar_px = bar_um * scale
        margin = int(sb["margin_px"])
        bar_rgb = _hex_rgb(str(sb["color"]))
        thick = int(sb["thickness_px"])
        bar_font = _font(ImageFont, int(sb["font_px"]))
        if bar_px < w - margin:
            x1 = gutter + w - margin
            y0 = pad_top + h - margin - 2
            cap_px = int(sb["font_px"])
            _plate(Image, img,
                   (x1 - bar_px, y0 - 2 * thick, x1,
                    y0 + 2 * thick + cap_px + 4), sb.get("plate"))
            dr = ImageDraw.Draw(img)
            dr.line([x1 - bar_px, y0, x1, y0], fill=bar_rgb, width=thick)
            for x in (x1 - bar_px, x1):
                dr.line([x, y0 - 2 * thick, x, y0 + 2 * thick],
                        fill=bar_rgb, width=thick)
            cap = (("%g um" % bar_um) if bar_um >= 1
                   else ("%g nm" % (bar_um * 1000)))
            dr.text((x1 - bar_px, y0 + 2 * thick), cap, fill=bar_rgb,
                    font=bar_font)
            scale_report = {"drawn": True, "bar_um": bar_um, "text": cap}
        else:
            scale_report = {"drawn": False, "reason":
                            "scale_bar.target_fraction does not fit the "
                            "frame at this width_px"}

    missing_glyphs: List[str] = []
    if label and lb is not None:
        font, missing_glyphs = _text_font(ImageFont, int(lb["font_px"]),
                                          label)
        dr.rectangle([0, 0, w + gutter, pad_top],
                     fill=_hex_rgb(str(lb["background"])))
        dr.text((gutter + 10, 5), label,
                fill=_hex_rgb(str(lb["text_color"])), font=font)
        if axis:                 # unit rides in the header, over the ruler
            dr.text((8, 8), unit_text,
                    fill=_hex_rgb(str(lb["unit_text_color"])),
                    font=_font(ImageFont, int(lb["unit_font_px"])))
    img.save(out_png)
    out = {"path": out_png.replace(os.sep, "/"),
           "size": [img.width, img.height],
           "um_per_px": bb.width() / float(w),
           "auto_colored": auto_colored,
           "missing_glyphs": missing_glyphs}
    if scale_report is not None:
        out["scale_bar"] = scale_report
    return out


def _nice_bar(value: float) -> float:
    """The largest 1/2/5 x 10^n at or below ``value`` — so a scale bar
    reads "2 um", never "1.87 um"."""
    if value <= 0:
        return 1.0
    decade = 10.0 ** math.floor(math.log10(value))
    for mult in (5.0, 2.0, 1.0):
        if mult * decade <= value:
            return mult * decade
    return decade


def _plate(Image, img, box, spec):
    """Blend a contrast plate under a caption.

    A bar drawn straight onto bright geometry is invisible, which makes
    the picture unmeasurable — the one thing a scale bar exists to
    prevent. Blending (rather than pasting) keeps the layout faintly
    visible underneath, and works in both "L" and "RGB" without
    changing the image mode."""
    if spec is None:
        return
    pad = int(spec["pad_px"])
    x0, y0, x1, y1 = box
    x0 = max(0, int(x0) - pad); y0 = max(0, int(y0) - pad)
    x1 = min(img.width, int(x1) + pad); y1 = min(img.height, int(y1) + pad)
    if x1 <= x0 or y1 <= y0:
        return
    rgb = _hex_rgb(str(spec["color"]))
    fill = (int(round(0.2126 * rgb[0] + 0.7152 * rgb[1]
                      + 0.0722 * rgb[2])) if img.mode == "L" else rgb)
    region = img.crop((x0, y0, x1, y1))
    img.paste(Image.blend(region,
                          Image.new(img.mode, region.size, fill),
                          float(spec["opacity"])), (x0, y0))


def _draw_scale_bar(Image, ImageDraw, ImageFont, img, um_per_px, spec):
    """Burn a scale bar into a finished frame; returns what it drew.

    A micrograph without a scale is a picture, not a measurement — but
    HOW that bar looks (length, colour, weight, placement) is the
    caller's taste, so every number here comes from ``spec``. Only the
    1/2/5 rounding is klink's, because "1.87 um" is not a scale bar."""
    bar_um = _nice_bar(img.width * um_per_px
                       * float(spec["target_fraction"]))
    bar_px = bar_um / um_per_px
    margin = int(spec["margin_px"])
    thick = int(spec["thickness_px"])
    font_px = int(spec["font_px"])
    rgb = _hex_rgb(str(spec["color"]))
    fill = (int(round(0.2126 * rgb[0] + 0.7152 * rgb[1]
                      + 0.0722 * rgb[2])) if img.mode == "L" else rgb)
    x1 = img.width - margin
    x0 = x1 - bar_px
    y = img.height - margin - thick
    if x0 < margin or y < font_px:
        return None                     # a bar that does not fit is not
    caption = (("%g um" % bar_um) if bar_um >= 1
               else ("%g nm" % (bar_um * 1000)))
    text = (str(spec.get("label") or "") + " " + caption).strip()
    font, missing = _text_font(ImageFont, font_px, text)
    _plate(Image, img, (x0, y - font_px - 6, x1, y + thick),
           spec.get("plate"))
    dr = ImageDraw.Draw(img)            # a scale bar; say so instead
    dr.rectangle([x0, y, x1, y + thick], fill=fill)
    dr.text((x0, y - font_px - 4), text, fill=fill, font=font)
    return {"bar_um": bar_um, "text": text, "missing_glyphs": missing}


def render_sem_png(
    gds_path: str,
    stack: VisualStack,
    out_grey: str,
    style,
    *,
    out_color: Optional[str] = None,
    cell: Optional[str] = None,
    layers: Optional[Sequence[str]] = None,
    width_px: int = 1600,
    window_um: Optional[Sequence[float]] = None,
    corner_radius_um: Optional[float] = None,
) -> Dict[str, Any]:
    """SEM-style top view: per-layer grey from ``sem_grey``, bright rims
    from ``edge_glow``; paint order = stack order (upper occludes).

    ``style`` is a :class:`~klink.domains.imaging.sem_style.SemStyle`.
    klink ships none: every appearance number — background, rim gains,
    beam blur, grain, scanlines, vignette, false-colour mix, scale bar —
    comes from it. ``corner_radius_um`` overrides the style's for a
    one-off run.

    ``window_um=(x0, y0, x1, y1)`` frames a REGION instead of the whole
    cell — a higher-magnification view. Note the difference from
    ``width_px``, which only adds pixels over the same field: raising
    width_px sharpens, a window magnifies, and only a window changes
    what the scale bar says."""
    (np, Image, ImageDraw, ImageFont, binary_dilation, binary_erosion,
     gaussian_filter) = _deps()
    kdb = _kdb(RasterError)
    from ._util import top_cell_of

    bg, edges = style.background, style.edges
    beam, noise = style.beam, style.noise
    vig, fc = style.vignette, style.false_color
    blur_px = float(beam["blur_px"])
    radius_um = (float(corner_radius_um) if corner_radius_um is not None
                 else float(beam["corner_radius_um"]))

    ly = kdb.Layout(); ly.read(gds_path)
    top = top_cell_of(ly, cell, RasterError, gds_path)
    bb = top.dbbox()
    if bb.empty():
        raise RasterError(
            f"cell {top.name!r} in {gds_path} has no geometry — an SEM "
            f"render of nothing would be pure noise")
    if window_um is not None:
        x0, y0, x1, y1 = (float(v) for v in window_um)
        if not (x1 > x0 and y1 > y0):
            raise RasterError(
                f"window_um must be (x0, y0, x1, y1) with x1>x0 and "
                f"y1>y0, got {tuple(window_um)!r}")
        win = kdb.DBox(x0, y0, x1, y1)
        if not win.overlaps(bb):
            raise RasterError(
                f"window_um {tuple(window_um)!r} does not overlap the "
                f"geometry of {top.name!r} ({bb.left:.3f}, "
                f"{bb.bottom:.3f}, {bb.right:.3f}, {bb.top:.3f}) — the "
                f"render would be an empty field")
        bb = win
    scale = width_px / bb.width()
    w, h = width_px, max(int(bb.height() * scale), 20)
    dbu = ly.dbu

    grey = np.full((h, w), float(bg["grey"]))
    tint = np.zeros((h, w, 3))
    tint[:] = np.array(_hex_rgb(str(bg["color"]))) / 255.0
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
        if radius_um > 0:
            r = int(radius_um / dbu)
            region.round_corners(r, r, int(beam["corner_points"]))
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
            ceiling = float(edges["ceiling"])
            rim = mask & ~binary_erosion(
                mask, iterations=int(edges["inner_px"]))
            outer = binary_dilation(
                mask, iterations=int(edges["outer_px"])) & ~mask
            grey[rim] = np.clip(
                grey[rim] + float(edges["inner_gain"]) * vl.edge_glow,
                0, ceiling)
            grey[outer] = np.clip(
                grey[outer] + float(edges["outer_gain"]) * vl.edge_glow,
                0, ceiling)

    grey = gaussian_filter(grey, blur_px)
    rng = np.random.default_rng(int(noise["seed"]))
    grey += (rng.normal(0, float(noise["grain"]), grey.shape)
             * (float(noise["grain_floor"])
                + float(noise["grain_gain"]) * grey))
    grey *= 1 + float(noise["scanline_amount"]) * np.sin(
        np.arange(h)[:, None] * float(noise["scanline_frequency"])
        + rng.normal(0, float(noise["scanline_jitter"]), (h, 1)))
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    grey *= 1 - float(vig["amount"]) * np.clip(
        r - float(vig["knee"]), 0, 1) ** 2
    grey = np.clip(grey, 0, 1)

    um_per_px = bb.width() / float(w)
    grey_img = Image.fromarray((grey * 255).astype(np.uint8), "L")
    out = {"grey": out_grey.replace(os.sep, "/"),
           "layers_rendered": used, "size": [w, h],
           "um_per_px": um_per_px}
    if style.scale_bar is not None:
        drawn = _draw_scale_bar(Image, ImageDraw, ImageFont, grey_img,
                                um_per_px, style.scale_bar)
        out["scale_bar"] = (
            dict(drawn, drawn=True) if drawn is not None
            else {"drawn": False,
                  "reason": "scale_bar.target_fraction does not fit the "
                            "frame at this width_px"})
    grey_img.save(out_grey)
    if out_color:
        tint_blur = gaussian_filter(tint, (blur_px, blur_px, 0))
        col = np.clip(tint_blur * (float(fc["floor"])
                                   + float(fc["gain"])
                                   * grey[..., None]), 0, 1)
        col_img = Image.fromarray((col * 255).astype(np.uint8), "RGB")
        if style.scale_bar is not None:
            _draw_scale_bar(Image, ImageDraw, ImageFont, col_img,
                            um_per_px, style.scale_bar)
        col_img.save(out_color)
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
