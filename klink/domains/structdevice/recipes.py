"""Terminal-recipe TOOLKIT + the recipe-free harvested-geometry provider.

Structure-as-Device M2 (docs/STRUCTURE_AS_DEVICE_IR.md).  A *terminal recipe* is
a callable ``recipe(client, device_cell) -> {name: DerivedTerminal}`` that turns
a device cell's geometry into Port-IR terminals.  klink ships only the MECHANISM:

  * ``DerivedTerminal`` -- the Port-IR terminal data class;
  * ``RecipeError`` -- the instructive error type;
  * geometry primitives (box overlap / touch / orientation / extent) that a
    recipe author composes;
  * ``geom_terminal_provider`` -- a RECIPE-FREE provider that reads terminals
    straight from a harvested ``device_geom.json`` (terms/pads are DATA), used
    by the BUILD path so verifying a built cell needs no device-specific recipe.

DEVICE-SPECIFIC recipes (e.g. the back-gate transistor rule set that infers
G/S/D from gate/channel/sd boxes) are PROCESS data and live with the example /
PDK (``your recipes``), not here -- klink holds zero device
geometry rules.  An orchestrator that derives terminals from live hand-drawn
geometry takes the recipe as an injected argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]  # (x1, y1, x2, y2), normalized


class RecipeError(ValueError):
    """Recipe rule violation.  Messages are instructions, not just facts."""


@dataclass(frozen=True)
class DerivedTerminal:
    """One derived terminal, ready to materialize as a Port marker.

    ``length_um`` is the pad extent ALONG the orientation; the routing
    attach point is ``center + length/2`` in the orientation direction
    (the pad's outer edge).  Agents never compute this themselves --
    the recipe knows the pad geometry (foolproofing ruling, STATUS
    Update 24)."""

    name: str
    center_um: Tuple[float, float]
    orientation_deg: float
    width_um: float
    layer: str  # "layer/datatype"
    length_um: float = 0.0
    forbidden_attach_deg: Optional[float] = None
    port_type: str = "electrical"
    source: str = "derived"

    def to_port_dict(self) -> Dict[str, Any]:
        """Port-IR dict shape consumed by port.mark / routing backends."""
        return {
            "name": self.name,
            "center_um": list(self.center_um),
            "orientation_deg": self.orientation_deg,
            "width_um": self.width_um,
            "layer": self.layer,
            "port_type": self.port_type,
            "source": self.source,
        }


# --------------------------------------------------------------------------- #
# geometry primitives (PUBLIC toolkit for building terminal recipes)
# --------------------------------------------------------------------------- #
def norm_box(box: Sequence[float], what: str) -> Box:
    if len(box) != 4:
        raise RecipeError(f"{what}: a box needs 4 numbers, got {len(box)}")
    x1, y1, x2, y2 = (float(v) for v in box)
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    if x1 == x2 or y1 == y2:
        raise RecipeError(f"{what}: degenerate box {list(box)}")
    return (x1, y1, x2, y2)


def center(box: Box) -> Tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def overlap_area(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if (w > 0 and h > 0) else 0.0


def touches(a: Box, b: Box) -> bool:
    """True if boxes overlap or share an edge (electrical continuity)."""
    return (
        min(a[2], b[2]) >= max(a[0], b[0])
        and min(a[3], b[3]) >= max(a[1], b[1])
    )


def snap_orientation(vec: Tuple[float, float], what: str) -> float:
    vx, vy = vec
    if vx == 0.0 and vy == 0.0:
        raise RecipeError(
            f"{what}: terminal center coincides with the channel center; "
            "the recipe cannot infer a launch direction. Mark this "
            "terminal with an explicit Port override instead."
        )
    if abs(vx) >= abs(vy):
        return 0.0 if vx > 0 else 180.0
    return 90.0 if vy > 0 else 270.0


def perpendicular_extent(box: Box, orientation_deg: float) -> float:
    if orientation_deg in (0.0, 180.0):
        return box[3] - box[1]
    return box[2] - box[0]


def parallel_extent(box: Box, orientation_deg: float) -> float:
    if orientation_deg in (0.0, 180.0):
        return box[2] - box[0]
    return box[3] - box[1]


# --------------------------------------------------------------------------- #
# recipe-free provider: terminals straight from harvested device geometry
# --------------------------------------------------------------------------- #
def geom_terminal_provider(raw_geom: Mapping[str, Any]):
    """Build a terminal provider from a harvested device-geometry table
    (``device_geom.json``: per device cell ``{terms, pads, ...}``).

    Terminals are DATA -- each carries ``{center, orientation, length, layer}``,
    and the pad box gives the width perpendicular to the launch orientation. No
    device-specific geometric rule is applied, so this is the BUILD path's
    terminal source: a built cell is LVS-verified with zero device recipe.

    Returns ``provider(client, device_cell) -> {name: DerivedTerminal}`` (the
    ``client`` argument is ignored -- the geometry is already harvested -- so the
    provider is interchangeable with a live-geometry recipe)."""

    def provider(_client: Any, device_cell: str) -> Dict[str, DerivedTerminal]:
        g = raw_geom.get(device_cell)
        if g is None:
            raise RecipeError(
                f"no harvested geometry for device cell {device_cell!r}; "
                "harvest it into the device_geom table first.")
        pads = g.get("pads", {})
        out: Dict[str, DerivedTerminal] = {}
        for name, t in g["terms"].items():
            ori = float(t["orientation"])
            pad = pads.get(name)
            if pad:
                width = (abs(pad[3] - pad[1]) if ori in (0.0, 180.0)
                         else abs(pad[2] - pad[0]))
            else:
                width = 0.0
            out[name] = DerivedTerminal(
                name=name,
                center_um=(float(t["center"][0]), float(t["center"][1])),
                orientation_deg=ori,
                width_um=float(width),
                layer=str(t["layer"]),
                length_um=float(t.get("length", 0.0)),
                source="derived:device_geom",
            )
        return out

    return provider
