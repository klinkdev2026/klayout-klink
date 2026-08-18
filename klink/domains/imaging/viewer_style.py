"""``klink_viewer_style_v1`` — the declaration behind the 3D exit.

WHY THIS FILE HOLDS NO NUMBERS
------------------------------
The 3D exit produces two things with a look: a GLB, whose materials need
a surface finish and a colour for anything the stack never declared, and
a self-contained viewer page, which is a whole small UI — page colour,
panel colour, button colours, starting exposure. Every one of those is
taste. klink builds the mesh and writes the page; it holds no opinion
about how either should look.

So this module ships the SHAPE of a style and not one value. The values
live beside the example that uses them:

    example_template/imaging/viewer_style.py

There is no default and no fallback: building with no style is an error
naming the file to copy.

Independence: this belongs to the 3D exit alone. The section, SEM and
Blender exits are separate features with separate declarations.

Shape (every block required; see the example for a filled-in one):

    material  {roughness, undeclared_color}
    viewer    {page, panel, panel_text, button, button_hover,
               button_text, exposure, shadow_intensity}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping

FORMAT_V1 = "klink_viewer_style_v1"

_MATERIAL_KEYS = ("roughness", "undeclared_color")
_VIEWER_KEYS = ("page", "panel", "panel_text", "button", "button_hover",
                "button_text", "exposure", "shadow_intensity")
_BLOCKS = (("material", _MATERIAL_KEYS), ("viewer", _VIEWER_KEYS))
_COLOR_FIELDS = (("material", "undeclared_color"),
                 ("viewer", "page"), ("viewer", "panel"),
                 ("viewer", "panel_text"), ("viewer", "button"),
                 ("viewer", "button_hover"), ("viewer", "button_text"))


class ViewerStyleError(ValueError):
    """The style is missing or malformed; the message says what to add."""


def _require(where: str, block: Any, keys) -> Dict[str, Any]:
    if not isinstance(block, Mapping):
        raise ViewerStyleError(
            f"{FORMAT_V1}: '{where}' must be an object with "
            f"{', '.join(keys)} — copy example_template/imaging/"
            f"viewer_style.py for a filled-in one")
    missing = [k for k in keys if k not in block]
    if missing:
        raise ViewerStyleError(
            f"{FORMAT_V1}: '{where}' is missing {', '.join(missing)}. "
            f"klink ships no default for it. A filled-in one is "
            f"almost certainly already in your project at "
            f"example_template/imaging/viewer_style.py (klink init "
            f"scaffolds it); copy it out and edit the numbers.")
    return dict(block)


def _hex(where: str, value: Any) -> str:
    body = str(value).lstrip("#")
    if len(body) != 6 or any(c not in "0123456789abcdefABCDEF"
                             for c in body):
        raise ViewerStyleError(
            f"{FORMAT_V1}: '{where}' must be a #RRGGBB colour, got "
            f"{value!r}. It is written straight into the viewer page's "
            f"CSS, so it has to be a plain hex colour.")
    return "#" + body.lower()


@dataclass(frozen=True)
class ViewerStyle:
    """One example's 3D look. Construct it in YOUR file."""

    material: Mapping[str, Any]
    viewer: Mapping[str, Any]
    name: str = ""

    def __post_init__(self):
        for where, keys in _BLOCKS:
            _require(where, getattr(self, where), keys)
        for where, key in _COLOR_FIELDS:
            _hex(f"{where}.{key}", getattr(self, where)[key])
        if not 0.0 <= float(self.material["roughness"]) <= 1.0:
            raise ViewerStyleError(
                f"{FORMAT_V1}: material.roughness is a 0..1 PBR value")

    @property
    def undeclared_color(self) -> str:
        return _hex("material.undeclared_color",
                    self.material["undeclared_color"])

    @property
    def roughness(self) -> float:
        return float(self.material["roughness"])

    def css(self, key: str) -> str:
        """One viewer colour, validated, ready to drop into the page."""
        return _hex(f"viewer.{key}", self.viewer[key])

    # ---- serialization ------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = {"format": FORMAT_V1, "name": self.name}
        for where, _keys in _BLOCKS:
            d[where] = json.loads(json.dumps(dict(getattr(self, where))))
        return d

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1, sort_keys=True)
            fh.write("\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ViewerStyle":
        fmt = data.get("format")
        if fmt != FORMAT_V1:
            raise ViewerStyleError(
                f"style format is {fmt!r}, expected {FORMAT_V1!r}")
        for where, _keys in _BLOCKS:
            if where not in data:
                raise ViewerStyleError(
                    f"{FORMAT_V1}: '{where}' block is required; copy "
                    f"example_template/imaging/viewer_style.py")
        return cls(material=data["material"], viewer=data["viewer"],
                   name=str(data.get("name", "")))

    @classmethod
    def load(cls, path: str) -> "ViewerStyle":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
