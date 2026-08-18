"""VisualStack — the ONE declaration every imaging exit consumes.

Serialized format string: ``klink_visual_stack_v1`` (klink-branded, like
``klink_fitted_device_pcell_v2``).  A VisualStack maps layout layers to
how they LOOK and BUILD across the imaging exits: solid extrusion, 3D
z-range, colors/alpha, SEM grey response, atomic-lattice motif, and the
``.pyxs`` recipe variable it corresponds to.

Field doctrine (owner + review rulings):
- z fields are unit-suffixed: ``z0_um`` / ``z1_um`` — never bare z0/z1;
- ``material.name`` is the DISPLAY name (prose); ``recipe_symbol`` is
  the .pyxs variable name — two different things, never conflated;
- ``from_stackspec()`` follows the ``stack25d.stack_displays`` doctrine:
  the caller passes ``z_um`` and material/style tables; klink REFUSES to
  guess process facts (StackSpec carries no heights/materials/colors).

klink ships the class; INSTANCES are example/project-owned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

FORMAT_V1 = "klink_visual_stack_v1"

_KINDS = ("solid", "lattice", "dielectric")


class VisualStackError(ValueError):
    """Bad stack input; the message says what to fix."""


def _ld(value: Any, what: str) -> str:
    """Normalise a layer reference to canonical 'L/D'."""
    if isinstance(value, str) and "/" in value:
        layer_s, dt_s = value.split("/", 1)
        return f"{int(layer_s)}/{int(dt_s)}"
    raise VisualStackError(
        f"{what}: layer must be an 'L/D' string, got {value!r}")


@dataclass(frozen=True)
class VisualLayer:
    layer: str                       # canonical 'L/D'
    z0_um: float
    z1_um: float
    name: str                        # display/material name (prose)
    kind: str = "solid"              # solid | lattice | dielectric
    role: str = ""                   # user vocabulary (gate, channel, ...)
    color: str = ""                  # #RRGGBB; REQUIRED unless lattice
    alpha: float = 1.0               # dielectric exits render translucent
    metallic: float = 0.0            # 3D/PBR hint
    sem_grey: float = 0.5            # SEM emission level 0..1
    edge_glow: float = 0.0           # SEM topography rim 0..1
    motif: str = ""                  # lattice motif key (kind=lattice)
    recipe_symbol: str = ""          # .pyxs variable this layer matches

    def __post_init__(self):
        self.validate(None)

    def validate(self, i: Optional[int] = None) -> None:
        what = (f"layer {self.name or self.layer}" if i is None
                else f"layers[{i}] ({self.name or self.layer})")
        _ld(self.layer, what)
        if not self.z1_um > self.z0_um:
            raise VisualStackError(
                f"{what}: z1_um ({self.z1_um}) must be above z0_um "
                f"({self.z0_um})")
        if self.kind not in _KINDS:
            raise VisualStackError(
                f"{what}: kind must be one of {_KINDS}, got {self.kind!r}")
        if self.kind == "lattice" and not self.motif:
            raise VisualStackError(
                f"{what}: kind='lattice' needs a motif key "
                f"(e.g. 'graphene', 'mos2')")
        # A lattice layer is drawn as ATOMS, so it needs no fill
        # colour. Everything else does, and klink has none to offer:
        # picking one here would be klink deciding what your process
        # looks like.
        if self.kind != "lattice" and not self.color:
            raise VisualStackError(
                f"{what}: color is required (#RRGGBB). klink ships no "
                f"default colour — what YOUR materials look like is "
                f"yours to declare, not klink's to guess.")
        if not 0.0 <= self.alpha <= 1.0:
            raise VisualStackError(f"{what}: alpha must be in [0, 1]")


@dataclass(frozen=True)
class VisualStack:
    """Ordered visual declaration (paint/build order = list order,
    bottom-up).

    ``recipe_styles`` styles process-engine materials that have NO mask
    layer of their own (bulk, blanket depositions): ``{recipe_symbol:
    {"name", "color", "alpha", "metallic"}}`` — optional, additive."""

    layers: Tuple[VisualLayer, ...]
    name: str = ""
    recipe_styles: Tuple[Tuple[str, Tuple[Tuple[str, Any], ...]], ...] = ()

    def __post_init__(self):
        if not self.layers:
            raise VisualStackError("visual stack has no layers")
        for i, layer in enumerate(self.layers):
            layer.validate(i)
        if isinstance(self.recipe_styles, Mapping):   # ergonomic input
            object.__setattr__(self, "recipe_styles",
                               self._freeze_styles(self.recipe_styles))
        for sym, kv in self.recipe_styles:
            d = dict(kv)
            if "name" not in d:
                raise VisualStackError(
                    f"recipe_styles[{sym!r}] needs at least a 'name'")

    @staticmethod
    def _freeze_styles(styles: Mapping[str, Mapping[str, Any]]
                       ) -> Tuple[Tuple[str, Tuple[Tuple[str, Any], ...]],
                                  ...]:
        return tuple(sorted(
            (sym, tuple(sorted(dict(d).items())))
            for sym, d in styles.items()))

    def recipe_style(self, symbol: str) -> Optional[Dict[str, Any]]:
        for sym, kv in self.recipe_styles:
            if sym == symbol:
                return dict(kv)
        return None

    # ---- lookups -----------------------------------------------------
    def by_layer(self, layer: str) -> Optional[VisualLayer]:
        key = _ld(layer, "by_layer")
        for vl in self.layers:
            if vl.layer == key:
                return vl
        return None

    def by_recipe_symbol(self, symbol: str) -> Optional[VisualLayer]:
        for vl in self.layers:
            if vl.recipe_symbol == symbol:
                return vl
        return None

    # ---- serialization ----------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": FORMAT_V1,
            "name": self.name,
            "recipe_styles": {sym: dict(kv)
                              for sym, kv in self.recipe_styles},
            "layers": [
                {
                    "layer": vl.layer, "z0_um": vl.z0_um,
                    "z1_um": vl.z1_um, "name": vl.name, "kind": vl.kind,
                    "role": vl.role, "color": vl.color,
                    "alpha": vl.alpha, "metallic": vl.metallic,
                    "sem_grey": vl.sem_grey, "edge_glow": vl.edge_glow,
                    "motif": vl.motif,
                    "recipe_symbol": vl.recipe_symbol,
                }
                for vl in self.layers
            ],
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1, sort_keys=True)
            fh.write("\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualStack":
        fmt = data.get("format")
        if fmt != FORMAT_V1:
            raise VisualStackError(
                f"visual stack format is {fmt!r}, expected {FORMAT_V1!r}")
        raw = data.get("layers")
        if not raw:
            raise VisualStackError("visual stack has no layers")
        layers = []
        for i, d in enumerate(raw):
            missing = [k for k in ("layer", "z0_um", "z1_um", "name")
                       if k not in d]
            if missing:
                raise VisualStackError(
                    f"layers[{i}] is missing required key(s) {missing}")
            layers.append(VisualLayer(
                layer=_ld(d["layer"], f"layers[{i}]"),
                z0_um=float(d["z0_um"]), z1_um=float(d["z1_um"]),
                name=str(d["name"]), kind=str(d.get("kind", "solid")),
                role=str(d.get("role", "")),
                color=str(d.get("color", "")),
                alpha=float(d.get("alpha", 1.0)),
                metallic=float(d.get("metallic", 0.0)),
                sem_grey=float(d.get("sem_grey", 0.5)),
                edge_glow=float(d.get("edge_glow", 0.0)),
                motif=str(d.get("motif", "")),
                recipe_symbol=str(d.get("recipe_symbol", "")),
            ))
        return cls(layers=tuple(layers), name=str(data.get("name", "")),
                   recipe_styles=cls._freeze_styles(
                       data.get("recipe_styles") or {}))

    @classmethod
    def load(cls, path: str) -> "VisualStack":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    # ---- derivation (refuses to guess) -------------------------------
    @classmethod
    def from_stackspec(
        cls,
        stack: Any,                      # klink.process_stack.StackSpec
        z_um: Mapping[str, Any],
        materials: Mapping[str, Mapping[str, Any]],
        *,
        name: str = "",
        extra_layers: Sequence[Mapping[str, Any]] = (),
    ) -> "VisualStack":
        """Derive a VisualStack from an electrical StackSpec.

        Doctrine (same as ``stack25d.stack_displays``): a StackSpec has
        NO heights, materials or colors — those are process facts YOU
        own.  ``z_um``: layer 'L/D' -> (z0_um, z1_um), REQUIRED for
        every conductor (and via layer).  ``materials``: layer 'L/D' ->
        dict of VisualLayer fields (at least ``name``); klink refuses to
        invent a missing entry.  ``extra_layers``: full VisualLayer
        dicts appended as-is (substrate outlines, flake layers, ...)."""
        wanted: List[Tuple[str, str]] = []
        for c in stack.conductors:
            wanted.append((c.layer, c.role))
        for v in stack.vias:
            wanted.append((v.via_layer, "via"))
        layers: List[VisualLayer] = []
        for layer, role in wanted:
            if layer not in z_um:
                raise VisualStackError(
                    f"z_um[{layer!r}] missing: pass (z0_um, z1_um) for "
                    f"every stack layer — klink does not guess process "
                    f"heights")
            if layer not in materials:
                raise VisualStackError(
                    f"materials[{layer!r}] missing: pass at least a "
                    f"'name' for every stack layer — klink does not "
                    f"guess materials")
            z0, z1 = (float(z_um[layer][0]), float(z_um[layer][1]))
            m = dict(materials[layer])
            if "name" not in m:
                raise VisualStackError(
                    f"materials[{layer!r}] has no 'name'")
            layers.append(VisualLayer(
                layer=_ld(layer, "from_stackspec"), z0_um=z0, z1_um=z1,
                role=role or str(m.get("role", "")), **{
                    k: m[k] for k in
                    ("name", "kind", "color", "alpha", "metallic",
                     "sem_grey", "edge_glow", "motif", "recipe_symbol")
                    if k in m}))
        for i, d in enumerate(extra_layers):
            sub = dict(d)
            missing = [k for k in ("layer", "z0_um", "z1_um", "name")
                       if k not in sub]
            if missing:
                raise VisualStackError(
                    f"extra_layers[{i}] is missing {missing}")
            layers.append(VisualLayer(
                layer=_ld(sub.pop("layer"), f"extra_layers[{i}]"),
                z0_um=float(sub.pop("z0_um")),
                z1_um=float(sub.pop("z1_um")), **sub))
        layers.sort(key=lambda vl: (vl.z0_um, vl.z1_um, vl.layer))
        return cls(layers=tuple(layers), name=name)
