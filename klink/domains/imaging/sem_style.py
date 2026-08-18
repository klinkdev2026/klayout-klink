"""``klink_sem_style_v1`` — the declaration that makes an SEM-style view.

WHY THIS FILE HOLDS NO NUMBERS
------------------------------
An "SEM-like" image is a pile of choices: how bright a topography rim
gets, how much film grain, how fast the scanlines beat, how hard the
vignette falls off. Every one is taste, tuned by someone against their
own device and their own idea of what their microscope looks like. klink
is the mechanism — it knows how to rasterise masks, dilate a rim, seed
noise and draw a scale bar, and nothing about how much of each is right.

So this module ships the SHAPE of a style — field names, types, and
errors that say what is missing — and not one value. The values live
beside the example that uses them:

    example_template/imaging/sem_style.py

There is no default style and no fallback: a render with no style is an
error naming the file to copy, never a picture drawn with klink's taste.

Independence: this belongs to the SEM exit alone. The section, 3D-viewer
and Blender exits are separate features with separate declarations — do
not grow this into a shared "imaging style".

Shape (every block required; see the example for a filled-in one):

    background   {grey, color}
    edges        {inner_px, outer_px, inner_gain, outer_gain, ceiling}
    beam         {blur_px, corner_radius_um, corner_points}
    noise        {seed, grain, grain_floor, grain_gain, scanline_amount,
                  scanline_frequency, scanline_jitter}
    vignette     {amount, knee}
    false_color  {floor, gain}
    scale_bar    {target_fraction, color, thickness_px, font_px,
                  margin_px, label} | null
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

FORMAT_V1 = "klink_sem_style_v1"

_BACKGROUND_KEYS = ("grey", "color")
_EDGE_KEYS = ("inner_px", "outer_px", "inner_gain", "outer_gain",
              "ceiling")
_BEAM_KEYS = ("blur_px", "corner_radius_um", "corner_points")
_NOISE_KEYS = ("seed", "grain", "grain_floor", "grain_gain",
               "scanline_amount", "scanline_frequency",
               "scanline_jitter")
_VIGNETTE_KEYS = ("amount", "knee")
_FALSE_COLOR_KEYS = ("floor", "gain")
_PLATE_KEYS = ("color", "opacity", "pad_px")
_SCALE_BAR_KEYS = ("target_fraction", "color", "thickness_px",
                   "font_px", "margin_px", "label")

_BLOCKS = (("background", _BACKGROUND_KEYS), ("edges", _EDGE_KEYS),
           ("beam", _BEAM_KEYS), ("noise", _NOISE_KEYS),
           ("vignette", _VIGNETTE_KEYS),
           ("false_color", _FALSE_COLOR_KEYS))


class SemStyleError(ValueError):
    """The style is missing or malformed; the message says what to add."""


def _require(where: str, block: Any, keys) -> Dict[str, Any]:
    if not isinstance(block, Mapping):
        raise SemStyleError(
            f"{FORMAT_V1}: '{where}' must be an object with "
            f"{', '.join(keys)} — copy example_template/imaging/"
            f"sem_style.py for a filled-in one")
    missing = [k for k in keys if k not in block]
    if missing:
        raise SemStyleError(
            f"{FORMAT_V1}: '{where}' is missing {', '.join(missing)}. "
            f"klink ships no default for it. A filled-in one is "
            f"almost certainly already in your project at "
            f"example_template/imaging/sem_style.py (klink init "
            f"scaffolds it); copy it out and edit the numbers.")
    return dict(block)


def _hex(where: str, value: Any) -> str:
    body = str(value).lstrip("#")
    if len(body) != 6 or any(c not in "0123456789abcdefABCDEF"
                             for c in body):
        raise SemStyleError(
            f"{FORMAT_V1}: '{where}' must be a #RRGGBB colour, got "
            f"{value!r}")
    return "#" + body.lower()


@dataclass(frozen=True)
class SemStyle:
    """One example's SEM look. Construct it in YOUR file."""

    background: Mapping[str, Any]
    edges: Mapping[str, Any]
    beam: Mapping[str, Any]
    noise: Mapping[str, Any]
    vignette: Mapping[str, Any]
    false_color: Mapping[str, Any]
    scale_bar: Optional[Mapping[str, Any]] = None
    name: str = ""

    def __post_init__(self):
        for where, keys in _BLOCKS:
            _require(where, getattr(self, where), keys)
        _hex("background.color", self.background["color"])
        if float(self.background["grey"]) < 0:
            raise SemStyleError(
                f"{FORMAT_V1}: background.grey is a 0..1 emission level")
        if self.scale_bar is not None:
            sb = _require("scale_bar", self.scale_bar,
                          _SCALE_BAR_KEYS)
            plate = sb.get("plate")
            if plate is not None:
                pl = _require("scale_bar.plate", plate,
                              _PLATE_KEYS)
                _hex("scale_bar.plate.color", pl["color"])
                if not 0.0 <= float(pl["opacity"]) <= 1.0:
                    raise SemStyleError(
                        f"{FORMAT_V1}: scale_bar.plate.opacity "
                        f"is 0..1 (0 = invisible plate, 1 = it "
                        f"hides what is underneath)")
            _hex("scale_bar.color", sb["color"])
            frac = float(sb["target_fraction"])
            if not 0.0 < frac < 1.0:
                raise SemStyleError(
                    f"{FORMAT_V1}: scale_bar.target_fraction is the bar's "
                    f"share of the image width, so it must be between 0 "
                    f"and 1 (got {frac})")

    # ---- serialization ------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = {"format": FORMAT_V1, "name": self.name}
        for where, _keys in _BLOCKS:
            d[where] = json.loads(json.dumps(dict(getattr(self, where))))
        d["scale_bar"] = (json.loads(json.dumps(dict(self.scale_bar)))
                          if self.scale_bar is not None else None)
        return d

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1, sort_keys=True)
            fh.write("\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemStyle":
        fmt = data.get("format")
        if fmt != FORMAT_V1:
            raise SemStyleError(
                f"style format is {fmt!r}, expected {FORMAT_V1!r}")
        for where, _keys in _BLOCKS:
            if where not in data:
                raise SemStyleError(
                    f"{FORMAT_V1}: '{where}' block is required; copy "
                    f"example_template/imaging/sem_style.py")
        return cls(background=data["background"], edges=data["edges"],
                   beam=data["beam"], noise=data["noise"],
                   vignette=data["vignette"],
                   false_color=data["false_color"],
                   scale_bar=data.get("scale_bar"),
                   name=str(data.get("name", "")))

    @classmethod
    def load(cls, path: str) -> "SemStyle":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
