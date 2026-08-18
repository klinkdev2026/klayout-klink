"""``klink_section_style_v1`` — the declaration that draws a section PNG.

WHY THIS FILE HOLDS NO NUMBERS
------------------------------
Page colour, ruler colour, how dark a material's edge is drawn, how much
top-to-bottom gradient a film gets, how a label bar is proportioned, what
colour an undeclared material falls back to: every one is taste. klink is
the mechanism — it rasterises polygons, solves ruler ticks and rounds a
scale bar — and holds no opinion about how any of it should look.

So this module ships the SHAPE of a style and not one value. The values
live beside the example that uses them:

    example_template/imaging/section_style.py

There is no default and no fallback: rendering with no style is an error
naming the file to copy.

Independence: this belongs to the cross-section exit alone. The SEM,
3D-viewer and Blender exits are separate features with separate
declarations — do not merge them.

Note the exit is only partly visual: ``imaging.xsection_run`` writes
section GDS with no style at all, and only needs one when ``render=True``
asks for pictures.

Shape (every block required; see the example for a filled-in one):

    page        {background, geometry_shade, edge_darken, supersample,
                 min_height_px}
    axis        {gutter_px, gutter_background, rule_color, tick_color,
                 tick_font_px, unit_text}
    scale_bar   {target_fraction, color, thickness_px, font_px,
                 margin_px} | null
    label_bar   {height_px, background, text_color, font_px,
                 unit_text_color, unit_font_px} | null
    auto_color  {saturation, value}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

FORMAT_V1 = "klink_section_style_v1"

_PAGE_KEYS = ("background", "geometry_shade", "edge_darken",
              "supersample", "min_height_px")
_AXIS_KEYS = ("gutter_px", "gutter_background", "rule_color",
              "tick_color", "tick_font_px", "unit_text")
_PLATE_KEYS = ("color", "opacity", "pad_px")
_SCALE_BAR_KEYS = ("target_fraction", "color", "thickness_px",
                   "font_px", "margin_px")
_LABEL_BAR_KEYS = ("height_px", "background", "text_color", "font_px",
                   "unit_text_color", "unit_font_px")
_AUTO_COLOR_KEYS = ("saturation", "value")

_BLOCKS = (("page", _PAGE_KEYS), ("axis", _AXIS_KEYS),
           ("auto_color", _AUTO_COLOR_KEYS))


class SectionStyleError(ValueError):
    """The style is missing or malformed; the message says what to add."""


def _require(where: str, block: Any, keys) -> Dict[str, Any]:
    if not isinstance(block, Mapping):
        raise SectionStyleError(
            f"{FORMAT_V1}: '{where}' must be an object with "
            f"{', '.join(keys)} — copy example_template/imaging/"
            f"section_style.py for a filled-in one")
    missing = [k for k in keys if k not in block]
    if missing:
        raise SectionStyleError(
            f"{FORMAT_V1}: '{where}' is missing {', '.join(missing)}. "
            f"klink ships no default for it. A filled-in one is "
            f"almost certainly already in your project at "
            f"example_template/imaging/section_style.py (klink init "
            f"scaffolds it); copy it out and edit the numbers.")
    return dict(block)


def _hex(where: str, value: Any) -> str:
    body = str(value).lstrip("#")
    if len(body) != 6 or any(c not in "0123456789abcdefABCDEF"
                             for c in body):
        raise SectionStyleError(
            f"{FORMAT_V1}: '{where}' must be a #RRGGBB colour, got "
            f"{value!r}")
    return "#" + body.lower()


@dataclass(frozen=True)
class SectionStyle:
    """One example's section look. Construct it in YOUR file."""

    page: Mapping[str, Any]
    axis: Mapping[str, Any]
    auto_color: Mapping[str, Any]
    scale_bar: Optional[Mapping[str, Any]] = None
    label_bar: Optional[Mapping[str, Any]] = None
    name: str = ""

    def __post_init__(self):
        for where, keys in _BLOCKS:
            _require(where, getattr(self, where), keys)
        _hex("page.background", self.page["background"])
        for key in ("gutter_background", "rule_color", "tick_color"):
            _hex(f"axis.{key}", self.axis[key])
        if not 0.0 <= float(self.page["edge_darken"]) <= 1.0:
            raise SectionStyleError(
                f"{FORMAT_V1}: page.edge_darken multiplies a material's "
                f"own colour to draw its outline, so it belongs in 0..1")
        if self.scale_bar is not None:
            sb = _require("scale_bar", self.scale_bar,
                          _SCALE_BAR_KEYS)
            plate = sb.get("plate")
            if plate is not None:
                pl = _require("scale_bar.plate", plate,
                              _PLATE_KEYS)
                _hex("scale_bar.plate.color", pl["color"])
                if not 0.0 <= float(pl["opacity"]) <= 1.0:
                    raise SectionStyleError(
                        f"{FORMAT_V1}: scale_bar.plate.opacity "
                        f"is 0..1 (0 = invisible plate, 1 = it "
                        f"hides what is underneath)")
            _hex("scale_bar.color", sb["color"])
            frac = float(sb["target_fraction"])
            if not 0.0 < frac < 1.0:
                raise SectionStyleError(
                    f"{FORMAT_V1}: scale_bar.target_fraction is the "
                    f"bar's share of the image width, so it must be "
                    f"between 0 and 1 (got {frac})")
        if self.label_bar is not None:
            lb = _require("label_bar", self.label_bar, _LABEL_BAR_KEYS)
            for key in ("background", "text_color", "unit_text_color"):
                _hex(f"label_bar.{key}", lb[key])

    # ---- serialization ------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = {"format": FORMAT_V1, "name": self.name}
        for where, _keys in _BLOCKS:
            d[where] = json.loads(json.dumps(dict(getattr(self, where))))
        for opt in ("scale_bar", "label_bar"):
            block = getattr(self, opt)
            d[opt] = (json.loads(json.dumps(dict(block)))
                      if block is not None else None)
        return d

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1, sort_keys=True)
            fh.write("\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SectionStyle":
        fmt = data.get("format")
        if fmt != FORMAT_V1:
            raise SectionStyleError(
                f"style format is {fmt!r}, expected {FORMAT_V1!r}")
        for where, _keys in _BLOCKS:
            if where not in data:
                raise SectionStyleError(
                    f"{FORMAT_V1}: '{where}' block is required; copy "
                    f"example_template/imaging/section_style.py")
        return cls(page=data["page"], axis=data["axis"],
                   auto_color=data["auto_color"],
                   scale_bar=data.get("scale_bar"),
                   label_bar=data.get("label_bar"),
                   name=str(data.get("name", "")))

    @classmethod
    def load(cls, path: str) -> "SectionStyle":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
