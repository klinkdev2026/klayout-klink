"""Pure conflict-resource accounting for negotiated routing.

This module deliberately has no dependency on the router backends.  It turns
already-planned net dictionaries into resource claims and prices those claims
with the PathFinder present/history split.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

Point = tuple[float, float]
ResourceKey = tuple[Any, ...]


@dataclass(frozen=True)
class SegmentEnvelopeResource:
    bbox_nm: tuple[int, int, int, int]
    capacity: int = 1

    @property
    def key(self) -> tuple[int, int, int, int]:
        return self.bbox_nm


@dataclass(frozen=True)
class FlankResource:
    instance: str
    terminal: str
    side: str
    capacity: int = 1

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.instance, self.terminal, self.side)


@dataclass(frozen=True)
class CorridorGateResource:
    corridor_id: str
    gate: str
    capacity: int = 1

    @property
    def key(self) -> tuple[str, str]:
        return (self.corridor_id, self.gate)


@dataclass(frozen=True)
class LaunchZoneResource:
    net: str
    port_name: str
    capacity: int = 1

    @property
    def key(self) -> tuple[str, str]:
        return (self.net, self.port_name)


Resource = SegmentEnvelopeResource | FlankResource | CorridorGateResource | LaunchZoneResource


class NetPlanError(ValueError):
    """Raised when a plain net_plan dict cannot produce reliable claims."""


def flank_claims(net_plan: Mapping[str, Any], *, allowed_sides_by_port: Mapping[str, str]) -> list[FlankResource]:
    ports = _ports(net_plan)
    claims: list[FlankResource] = []
    for port in ports:
        name = _required_str(port, "name", context="port")
        side = allowed_sides_by_port.get(name)
        if side is None:
            raise NetPlanError(f"missing allowed side for port {name!r}")
        side = _valid_side(side)
        instance = str(port.get("instance") or port.get("instance_name") or "")
        terminal = str(port.get("terminal") or port.get("terminal_name") or name)
        if not instance:
            raise NetPlanError(f"port {name!r} missing instance for flank claim")
        claims.append(FlankResource(instance=instance, terminal=terminal, side=side))
    return claims


def launch_zone_claims(net_plan: Mapping[str, Any]) -> list[LaunchZoneResource]:
    net = _required_str(net_plan, "net", context="net_plan")
    claims = [LaunchZoneResource(net=net, port_name=_required_str(port, "name", context="port")) for port in _ports(net_plan)]
    for claim in net_plan.get("claimed_launch_zones") or []:
        if not isinstance(claim, Mapping):
            raise NetPlanError("claimed_launch_zones entries must be mappings")
        claims.append(
            LaunchZoneResource(
                net=_required_str(claim, "net", context="claimed_launch_zones"),
                port_name=_required_str(claim, "port_name", context="claimed_launch_zones"),
                capacity=_capacity(claim),
            )
        )
    return claims


def corridor_gate_claims(net_plan: Mapping[str, Any]) -> list[CorridorGateResource]:
    corridor = net_plan.get("corridor")
    if corridor is None:
        return []
    if not isinstance(corridor, Mapping):
        raise NetPlanError("corridor must be a mapping or None")
    corridor_id = _required_str(corridor, "id", context="corridor")
    capacity = _capacity(corridor)
    return [
        CorridorGateResource(corridor_id=corridor_id, gate="entry", capacity=capacity),
        CorridorGateResource(corridor_id=corridor_id, gate="exit", capacity=capacity),
    ]


def segment_envelope_claims(net_plan: Mapping[str, Any], *, spacing_um: float) -> list[SegmentEnvelopeResource]:
    spacing = _finite_number(spacing_um, "spacing_um")
    if spacing < 0:
        raise NetPlanError("spacing_um must be >= 0")
    claims: list[SegmentEnvelopeResource] = []
    for index, segment in enumerate(net_plan.get("segments") or []):
        if not isinstance(segment, Mapping):
            raise NetPlanError(f"segment {index} must be a mapping")
        a = _point(segment.get("a"), f"segment {index}.a")
        b = _point(segment.get("b"), f"segment {index}.b")
        width = _finite_number(segment.get("width_um"), f"segment {index}.width_um")
        if width <= 0:
            raise NetPlanError(f"segment {index}.width_um must be > 0")
        margin = width / 2.0 + spacing
        bbox = (
            min(a[0], b[0]) - margin,
            min(a[1], b[1]) - margin,
            max(a[0], b[0]) + margin,
            max(a[1], b[1]) + margin,
        )
        claims.append(SegmentEnvelopeResource(bbox_nm=_quantized_bbox_nm(bbox), capacity=_capacity(segment)))
    return claims


def all_claims(
    net_plan: Mapping[str, Any],
    *,
    spacing_um: float,
    allowed_sides_by_port: Mapping[str, str],
) -> list[Resource]:
    _required_str(net_plan, "net", context="net_plan")
    return [
        *flank_claims(net_plan, allowed_sides_by_port=allowed_sides_by_port),
        *launch_zone_claims(net_plan),
        *corridor_gate_claims(net_plan),
        *segment_envelope_claims(net_plan, spacing_um=spacing_um),
    ]


class ResourceCostTable:
    def __init__(self) -> None:
        self._claims_by_key: dict[ResourceKey, set[str]] = {}
        self._keys_by_net: dict[str, list[ResourceKey]] = {}
        self._key_sets_by_net: dict[str, set[ResourceKey]] = {}
        self._capacity_by_key: dict[ResourceKey, int] = {}
        self._history_by_key: dict[ResourceKey, float] = {}

    def add_claim(self, net: str, resource: Resource) -> None:
        net_name = _nonempty_string(net, "net")
        key = tuple(resource.key)
        capacity = _positive_int(resource.capacity, "resource.capacity")
        previous = self._capacity_by_key.get(key)
        if previous is not None and previous != capacity:
            raise NetPlanError(f"resource {key!r} claimed with conflicting capacities {previous} and {capacity}")
        self._capacity_by_key[key] = capacity
        self._claims_by_key.setdefault(key, set()).add(net_name)
        key_set = self._key_sets_by_net.setdefault(net_name, set())
        if key not in key_set:
            self._keys_by_net.setdefault(net_name, []).append(key)
            key_set.add(key)

    def occupancy(self, key: ResourceKey) -> int:
        return len(self._claims_by_key.get(tuple(key), set()))

    def is_overused(self, key: ResourceKey) -> bool:
        key = tuple(key)
        return self.occupancy(key) > self._capacity_by_key.get(key, 1)

    def present_cost(self, key: ResourceKey, pres_fac: float) -> float:
        pres = _finite_number(pres_fac, "pres_fac")
        key = tuple(key)
        occ = self.occupancy(key)
        capacity = self._capacity_by_key.get(key, 1)
        return pres * max(0, occ - capacity + 1)

    def history_cost(self, key: ResourceKey) -> float:
        return self._history_by_key.get(tuple(key), 0.0)

    def bump_history(self, hist_fac: float) -> None:
        hist = _finite_number(hist_fac, "hist_fac")
        for key in self.overused_resources():
            self._history_by_key[key] = self._history_by_key.get(key, 0.0) + hist

    def net_cost(self, net: str, pres_fac: float) -> float:
        net_name = _nonempty_string(net, "net")
        total = 0.0
        for key in self._keys_by_net.get(net_name, []):
            total += (1.0 + self.history_cost(key)) * (1.0 + self.present_cost(key, pres_fac))
        return total

    def overused_resources(self) -> list[ResourceKey]:
        return sorted(
            (key for key in self._claims_by_key if self.is_overused(key)),
            key=_stable_key_sort,
        )

    def clear_occupancy(self) -> None:
        self._claims_by_key.clear()
        self._keys_by_net.clear()
        self._key_sets_by_net.clear()
        self._capacity_by_key.clear()


def _ports(net_plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ports = net_plan.get("ports")
    if not isinstance(ports, list) or not ports:
        raise NetPlanError("net_plan must include a non-empty ports list")
    normalized: list[Mapping[str, Any]] = []
    for index, port in enumerate(ports):
        if not isinstance(port, Mapping):
            raise NetPlanError(f"port {index} must be a mapping")
        _required_str(port, "name", context=f"port {index}")
        _point(port.get("center_um"), f"port {index}.center_um")
        _finite_number(port.get("orientation_deg"), f"port {index}.orientation_deg")
        width = _finite_number(port.get("width_um"), f"port {index}.width_um")
        if width <= 0:
            raise NetPlanError(f"port {index}.width_um must be > 0")
        normalized.append(port)
    return normalized


def _required_str(mapping: Mapping[str, Any], field: str, *, context: str) -> str:
    return _nonempty_string(mapping.get(field), f"{context}.{field}")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NetPlanError(f"{label} must be a non-empty string")
    return value


def _valid_side(value: str) -> str:
    side = _nonempty_string(value, "side")
    if side not in {"left", "right", "up", "down"}:
        raise NetPlanError(f"side must be one of left/right/up/down, got {side!r}")
    return side


def _capacity(mapping: Mapping[str, Any]) -> int:
    return _positive_int(mapping.get("capacity", 1), "capacity")


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise NetPlanError(f"{label} must be a positive integer")
    return value


def _finite_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise NetPlanError(f"{label} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise NetPlanError(f"{label} must be a finite number")
    return result


def _point(value: Any, label: str) -> Point:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise NetPlanError(f"{label} must be a 2-item coordinate")
    return (_finite_number(value[0], f"{label}[0]"), _finite_number(value[1], f"{label}[1]"))


def _quantized_bbox_nm(bbox_um: Iterable[float]) -> tuple[int, int, int, int]:
    values = tuple(int(round(_finite_number(v, "bbox_um") * 1000.0)) for v in bbox_um)
    if len(values) != 4:
        raise NetPlanError("bbox must have four values")
    if values[0] > values[2] or values[1] > values[3]:
        raise NetPlanError(f"invalid bbox order after quantization: {values!r}")
    return values


def _stable_key_sort(key: ResourceKey) -> tuple[str, str]:
    return (str(type(key).__name__), repr(key))
