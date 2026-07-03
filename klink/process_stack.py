"""Process stack — the single source of truth for layer relations.

Per docs/FEATURE_GRID_ROUTER_DESIGN.md F0 and the stack-router review:
ONE deterministic, byte-stable parser interprets which layers are
routing planes and which via cell bridges which pair.  BOTH routing
(feature-grid planes + via-edges) and LVS (ConnectivitySpec) read their
layer views from THIS module — never each parsing their own — so the
worst bug class ("routes fine but LVS does not recognize it") cannot
arise.

Leaf module: zero klink imports (stdlib only), so both klink.routing
and klink.domains can depend on it without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class StackError(ValueError):
    """Bad process-stack declaration.  Messages instruct."""


def _ld(value: Any, what: str) -> str:
    """Normalize a layer to the canonical 'L/D' string (the one form
    used everywhere downstream)."""
    if isinstance(value, str):
        try:
            layer_s, dt_s = value.split("/")
            return f"{int(layer_s)}/{int(dt_s)}"
        except (ValueError, AttributeError):
            raise StackError(
                f"{what}: layer must be 'L/D', got {value!r}") from None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{int(value[0])}/{int(value[1])}"
    if isinstance(value, Mapping) and "layer" in value:
        inner = value["layer"]
        if isinstance(inner, str) and "/" in inner:
            return _ld(inner, what)
        return f"{int(inner)}/{int(value.get('datatype', 0))}"
    raise StackError(f"{what}: cannot read a layer from {value!r}")


@dataclass(frozen=True)
class Conductor:
    layer: str            # canonical 'L/D'
    role: str = ""        # user vocabulary (gate_metal, sd_metal, ...)
    prefer: str = ""      # routing preference hint (signal, crossunder)


@dataclass(frozen=True)
class Via:
    a: str                # canonical 'L/D' conductor
    via_layer: str        # canonical 'L/D'
    b: str                # canonical 'L/D' conductor
    via_cell: str         # KLayout cell name placed at the transition


@dataclass(frozen=True)
class StackSpec:
    """Parsed, validated process stack.  Construct via from_dict; this
    is the ONLY parser of layer relations."""

    conductors: Tuple[Conductor, ...]
    vias: Tuple[Via, ...] = ()
    order: Tuple[str, ...] = ()    # vertical adjacency, top -> bottom

    # ---- the single parser entry point ----
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StackSpec":
        conductors_raw = data.get("conductors")
        if not conductors_raw:
            raise StackError("stack.conductors is empty")
        conductors = []
        seen = set()
        for i, c in enumerate(conductors_raw):
            layer = _ld(c, f"conductors[{i}]")
            if layer in seen:
                raise StackError(f"duplicate conductor layer {layer}")
            seen.add(layer)
            conductors.append(Conductor(
                layer=layer,
                role=str((c.get("role") if isinstance(c, Mapping) else "")
                         or ""),
                prefer=str((c.get("prefer") if isinstance(c, Mapping) else "")
                           or "")))
        cond_layers = {c.layer for c in conductors}

        vias = []
        for i, v in enumerate(data.get("vias") or []):
            if not isinstance(v, Mapping):
                raise StackError(f"vias[{i}] must be a mapping")
            a = _ld(v.get("from"), f"vias[{i}].from")
            b = _ld(v.get("to"), f"vias[{i}].to")
            vlayer = _ld(v.get("via_layer"), f"vias[{i}].via_layer")
            cell = v.get("via_cell")
            if not cell or not isinstance(cell, str):
                raise StackError(
                    f"vias[{i}] needs a via_cell name bridging {a}<->{b}")
            for side in (a, b):
                if side not in cond_layers:
                    raise StackError(
                        f"vias[{i}] references {side!r} which is not a "
                        "declared conductor; declare it first.")
            vias.append(Via(a=a, via_layer=vlayer, b=b, via_cell=cell))

        order = tuple(_ld(o, f"order[{i}]")
                      for i, o in enumerate(data.get("order") or []))
        return cls(conductors=tuple(conductors), vias=tuple(vias),
                   order=order)

    # ---- deterministic views (both consumers read these) ----
    def conductor_layers(self) -> List[str]:
        """Routing planes / LVS conductors, declaration order preserved."""
        return [c.layer for c in self.conductors]

    def via_triples(self) -> List[Tuple[str, str, str]]:
        """(conductor_a, via_layer, conductor_b) for ConnectivitySpec —
        declaration order, deterministic."""
        return [(v.a, v.via_layer, v.b) for v in self.vias]

    def via_cell_for(self, a: str, b: str) -> Optional[str]:
        a, b = _ld(a, "a"), _ld(b, "b")
        for v in self.vias:
            if {v.a, v.b} == {a, b}:
                return v.via_cell
        return None

    def role_of(self, layer: str) -> str:
        layer = _ld(layer, "layer")
        for c in self.conductors:
            if c.layer == layer:
                return c.role
        return ""

    def prefer_of(self, layer: str) -> str:
        layer = _ld(layer, "layer")
        for c in self.conductors:
            if c.layer == layer:
                return c.prefer
        return ""

    def to_dict(self) -> Dict[str, Any]:
        """Byte-stable serialization (sorted keys at the JSON layer;
        list order is the meaningful declaration order)."""
        return {
            "conductors": [
                {"layer": c.layer, "role": c.role, "prefer": c.prefer}
                for c in self.conductors],
            "vias": [
                {"from": v.a, "via_layer": v.via_layer, "to": v.b,
                 "via_cell": v.via_cell} for v in self.vias],
            "order": list(self.order),
        }


# Process-stack INSTANCES are process data and live in your pdk.py, not here --
# this module ships only the StackSpec mechanism.
