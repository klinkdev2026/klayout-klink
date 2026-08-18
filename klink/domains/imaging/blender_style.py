"""``klink_blender_style_v1`` — the declaration that shades and stages a
Blender render.

WHY THIS FILE HOLDS NO NUMBERS
------------------------------
A render's look is data, exactly like a layer number or a DRC rule: sun
energy, camera lens, film transform, how a metal is mottled, how far an
edge is bevelled. Every one of those is somebody's taste, tuned against
somebody's device. klink is the mechanism — it knows HOW to build a
Principled BSDF and stage a camera, and nothing about what looks good.

So this module ships the SHAPE of a style (field names, types, ranges,
and errors that say what is missing) and not one value. The values live
next to the example that uses them:

    example_template/imaging/blender_style.py

Copy that file out, edit the numbers, pass your own. There is no default
style and no fallback: a render with no style declared is an error that
names the file to copy, never a picture drawn with klink's taste.

Independence: this style belongs to the Blender exit alone. The section,
SEM and 3D-viewer exits are separate features with separate declarations
— do not grow this into a shared "render style" for all of imaging.

Shape (every block required; see the example for a filled-in one):

    material.metal        {roughness, mottle?}
    material.dielectric   {roughness, transmission, ior, coat_weight,
                           coat_roughness, sheen, specular_level}
    material.matte        {roughness}
    material.bevel        {radius_fraction, samples} | null
    staging.key_light     {energy, euler_deg}
    staging.fill_light    {energy, size_fraction, offset_fraction,
                           euler_deg}
    staging.world_color   "#RRGGBB"
    staging.backdrop      {color, roughness}
    staging.film          {view_transform, look, max_bounces,
                           transparent_max_bounces}
    camera                {lens_mm, sensor_mm, margin, directions{...}}
    lattice               {a_um, species{...}, atom{...}, bond{...}}
                          # figure mode only; a stack with no
                          # kind='lattice' layer never reads it
``material.metal.mottle`` (optional, null to disable) drives a noise ->
colour-ramp on Base Color: {noise_scale, noise_detail, noise_roughness,
ramp_low, ramp_high, dark_color}.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence

FORMAT_V1 = "klink_blender_style_v1"

#: Field NAMES only — naming a required key is contract, not taste.
_METAL_KEYS = ("roughness",)
_MOTTLE_KEYS = ("noise_scale", "noise_detail", "noise_roughness",
                "ramp_low", "ramp_high", "dark_color")
_DIELECTRIC_KEYS = ("roughness", "transmission", "ior", "coat_weight",
                    "coat_roughness", "sheen", "specular_level")
_MATTE_KEYS = ("roughness",)
_BEVEL_KEYS = ("radius_fraction", "samples")
_KEY_LIGHT_KEYS = ("energy", "euler_deg")
_FILL_LIGHT_KEYS = ("energy", "size_fraction", "offset_fraction",
                    "euler_deg")
_BACKDROP_KEYS = ("color", "roughness")
_FILM_KEYS = ("view_transform", "look", "max_bounces",
              "transparent_max_bounces")
_CAMERA_KEYS = ("lens_mm", "sensor_mm", "margin", "directions")

#: Camera presets the Blender exit exposes. Names, not viewpoints — the
#: direction vectors themselves are style data.
CAMERA_PRESETS = ("default", "face", "top")


class BlenderStyleError(ValueError):
    """The style is missing or malformed; the message says what to add."""


def _require(where: str, block: Any, keys: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(block, Mapping):
        raise BlenderStyleError(
            f"{FORMAT_V1}: '{where}' must be an object with "
            f"{', '.join(keys)} — see example_template/imaging/"
            f"blender_style.py for a filled-in one")
    missing = [k for k in keys if k not in block]
    if missing:
        raise BlenderStyleError(
            f"{FORMAT_V1}: '{where}' is missing {', '.join(missing)}. "
            f"klink ships no default for it. A filled-in one is "
            f"almost certainly already in your project at "
            f"example_template/imaging/blender_style.py (klink init "
            f"scaffolds it); copy it out and edit the numbers.")
    return dict(block)


def _hex(where: str, value: Any) -> str:
    s = str(value)
    body = s.lstrip("#")
    if len(body) != 6 or any(c not in "0123456789abcdefABCDEF"
                             for c in body):
        raise BlenderStyleError(
            f"{FORMAT_V1}: '{where}' must be a #RRGGBB colour, got {s!r}")
    return "#" + body.lower()


def _vec3(where: str, value: Any) -> list:
    try:
        out = [float(v) for v in value]
    except Exception:
        raise BlenderStyleError(
            f"{FORMAT_V1}: '{where}' must be three numbers")
    if len(out) != 3:
        raise BlenderStyleError(
            f"{FORMAT_V1}: '{where}' must be three numbers, got "
            f"{len(out)}")
    return out


@dataclass(frozen=True)
class BlenderStyle:
    """One example's Blender look. Construct it in YOUR file."""

    material: Mapping[str, Any]
    staging: Mapping[str, Any]
    camera: Mapping[str, Any]
    lattice: Mapping[str, Any] = field(default_factory=dict)
    name: str = ""

    # ---- validation ---------------------------------------------------
    def __post_init__(self):
        mat = _require("material", self.material,
                       ("metal", "dielectric", "matte", "bevel"))
        _require("material.metal", mat["metal"], _METAL_KEYS)
        _require("material.dielectric", mat["dielectric"],
                 _DIELECTRIC_KEYS)
        _require("material.matte", mat["matte"], _MATTE_KEYS)
        if mat["bevel"] is not None:
            b = _require("material.bevel", mat["bevel"], _BEVEL_KEYS)
            if float(b["radius_fraction"]) < 0:
                raise BlenderStyleError(
                    f"{FORMAT_V1}: material.bevel.radius_fraction must "
                    f"not be negative")
        mottle = mat["metal"].get("mottle")
        if mottle is not None:
            m = _require("material.metal.mottle", mottle, _MOTTLE_KEYS)
            _hex("material.metal.mottle.dark_color", m["dark_color"])

        st = _require("staging", self.staging,
                      ("key_light", "fill_light", "world_color",
                       "backdrop", "film"))
        k = _require("staging.key_light", st["key_light"], _KEY_LIGHT_KEYS)
        _vec3("staging.key_light.euler_deg", k["euler_deg"])
        f = _require("staging.fill_light", st["fill_light"],
                     _FILL_LIGHT_KEYS)
        _vec3("staging.fill_light.offset_fraction", f["offset_fraction"])
        _vec3("staging.fill_light.euler_deg", f["euler_deg"])
        _hex("staging.world_color", st["world_color"])
        bd = _require("staging.backdrop", st["backdrop"], _BACKDROP_KEYS)
        _hex("staging.backdrop.color", bd["color"])
        _require("staging.film", st["film"], _FILM_KEYS)

        cam = _require("camera", self.camera, _CAMERA_KEYS)
        dirs = _require("camera.directions", cam["directions"],
                        CAMERA_PRESETS)
        for preset in CAMERA_PRESETS:
            _vec3(f"camera.directions.{preset}", dirs[preset])

    # ---- lookups ------------------------------------------------------
    @property
    def metal(self) -> Dict[str, Any]:
        return dict(self.material["metal"])

    @property
    def dielectric(self) -> Dict[str, Any]:
        return dict(self.material["dielectric"])

    @property
    def matte(self) -> Dict[str, Any]:
        return dict(self.material["matte"])

    @property
    def bevel(self) -> Optional[Dict[str, Any]]:
        b = self.material["bevel"]
        return dict(b) if b is not None else None

    @property
    def mottle(self) -> Optional[Dict[str, Any]]:
        m = self.material["metal"].get("mottle")
        return dict(m) if m is not None else None

    def camera_direction(self, preset: str) -> list:
        if preset not in CAMERA_PRESETS:
            raise BlenderStyleError(
                f"unknown camera preset {preset!r}; this exit has "
                f"{', '.join(CAMERA_PRESETS)}")
        return _vec3(f"camera.directions.{preset}",
                     self.camera["directions"][preset])

    _SPECIES_KEYS = ("color", "radius_fraction")
    _ATOM_KEYS = ("metallic", "roughness")
    _BOND_KEYS = ("color", "roughness", "radius_fraction")

    def species(self, name: str) -> Dict[str, Any]:
        """Colour and drawn size for one atomic species.

        The motif library produces positions and bonds; what a Mo atom
        LOOKS like (blue-grey, 0.22 of the lattice constant) is a
        convention in the 2D-material literature, not a physical fact,
        so it is declared here rather than chosen by klink."""
        table = self.lattice.get("species")
        if not isinstance(table, Mapping) or name not in table:
            known = (", ".join(sorted(table)) if isinstance(table, Mapping)
                     else "none")
            raise BlenderStyleError(
                f"{FORMAT_V1}: this motif contains a {name!r} atom and "
                f"the style declares no lattice.species[{name!r}] "
                f"(declared: {known}). Add "
                f"{{'color': '#RRGGBB', 'radius_fraction': <of a_um>}} "
                f"to lattice.species in your blender_style.py — klink "
                f"has no palette for atoms.")
        spec = _require(f"lattice.species[{name!r}]", table[name],
                        self._SPECIES_KEYS)
        _hex(f"lattice.species[{name!r}].color", spec["color"])
        return spec

    def atom_finish(self) -> Dict[str, Any]:
        return _require("lattice.atom", self.lattice.get("atom"),
                        self._ATOM_KEYS)

    def bond_style(self) -> Dict[str, Any]:
        spec = _require("lattice.bond", self.lattice.get("bond"),
                        self._BOND_KEYS)
        _hex("lattice.bond.color", spec["color"])
        return spec

    def lattice_a_um(self) -> float:
        """Figure-scale lattice constant. A stack with no ``kind=
        'lattice'`` layer never asks for it."""
        if "a_um" not in self.lattice:
            raise BlenderStyleError(
                f"{FORMAT_V1}: this stack has a kind='lattice' layer, so "
                f"the style must declare lattice.a_um (the FIGURE-scale "
                f"lattice constant — real constants are physical facts "
                f"that belong to your process notes, not to a renderer)")
        return float(self.lattice["a_um"])

    # ---- serialization ------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": FORMAT_V1,
            "name": self.name,
            "material": json.loads(json.dumps(self.material)),
            "staging": json.loads(json.dumps(self.staging)),
            "camera": json.loads(json.dumps(self.camera)),
            "lattice": json.loads(json.dumps(dict(self.lattice))),
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1, sort_keys=True)
            fh.write("\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BlenderStyle":
        fmt = data.get("format")
        if fmt != FORMAT_V1:
            raise BlenderStyleError(
                f"style format is {fmt!r}, expected {FORMAT_V1!r}")
        for block in ("material", "staging", "camera"):
            if block not in data:
                raise BlenderStyleError(
                    f"{FORMAT_V1}: '{block}' block is required; copy "
                    f"example_template/imaging/blender_style.py")
        return cls(material=data["material"], staging=data["staging"],
                   camera=data["camera"],
                   lattice=data.get("lattice", {}),
                   name=str(data.get("name", "")))

    @classmethod
    def load(cls, path: str) -> "BlenderStyle":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
