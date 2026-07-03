"""Negotiated routing v2 control loop (cost-ordered iterative reroute).

Design: docs/NEGOTIATED_ROUTING_V2_DESIGN.md.  Sits on top of the pure
resource layer (negotiated_resources.py) and the existing hard-blocking
per-net router.  ORDER is the lever, HISTORY the cross-iteration memory
that breaks the cyclic displacement the promote-once heuristic cannot.

The pure decision functions here (ordering, launch-overlap detection)
have no router or KLayout dependency and are unit-tested directly; the
orchestrator (connect_nets) supplies the routing callback.
"""

from __future__ import annotations

from typing import Any, Callable, List, Mapping, Sequence, Tuple

from klink.routing.grid.capacity_grid import CapacityGrid, NetInput, RouteResult, _terminal_cellsets
from klink.routing.backends.negotiated.negotiated_resources import (
    LaunchZoneResource,
    ResourceCostTable,
    all_claims,
)
from klink.routing.grid.pathfinder import route_negotiated as _python_route_negotiated

Bbox = Tuple[float, float, float, float]


def launch_zone_bbox_um(
    port: Mapping[str, Any], *, stub_factor: float = 2.0
) -> Bbox:
    """Launch-zone footprint of a port: a square around its center
    sized by width * stub_factor.  Geometry the pure resource layer
    deliberately does not carry (Phase-1 #3 gap, Update 46)."""
    cx, cy = port["center_um"]
    half = float(port["width_um"]) * float(stub_factor) / 2.0
    return (cx - half, cy - half, cx + half, cy + half)


def _bbox_overlap(a: Bbox, b: Bbox) -> bool:
    return (min(a[2], b[2]) > max(a[0], b[0])
            and min(a[3], b[3]) > max(a[1], b[1]))


def _segment_bbox_um(seg: Mapping[str, Any]) -> Bbox:
    a, b = seg["a"], seg["b"]
    half = float(seg.get("width_um", 0.0)) / 2.0
    return (min(a[0], b[0]) - half, min(a[1], b[1]) - half,
            max(a[0], b[0]) + half, max(a[1], b[1]) + half)


def launch_overlap_claims(
    plans: Sequence[Mapping[str, Any]], *, stub_factor: float = 2.0
) -> List[Tuple[str, LaunchZoneResource]]:
    """Detect segment-vs-launch-zone blocking ACROSS nets and emit the
    cross-claims (intruder_net, victim's LaunchZoneResource).  This is
    the geometry the control loop owns so the resource layer stays
    pure.  Deterministic order."""
    out: List[Tuple[str, LaunchZoneResource]] = []
    zones: List[Tuple[str, str, Bbox]] = []
    for plan in plans:
        for port in plan.get("ports", []):
            zones.append((plan["net"], port["name"],
                          launch_zone_bbox_um(port, stub_factor=stub_factor)))
    for plan in plans:
        intruder = plan["net"]
        seg_bboxes = [_segment_bbox_um(s) for s in plan.get("segments", [])]
        if not seg_bboxes:
            continue
        for victim_net, port_name, zone in zones:
            if victim_net == intruder:
                continue
            if any(_bbox_overlap(sb, zone) for sb in seg_bboxes):
                out.append((intruder,
                            LaunchZoneResource(net=victim_net,
                                               port_name=port_name)))
    out.sort(key=lambda pair: (pair[0], pair[1].key))
    return out


def repopulate_occupancy(
    table: ResourceCostTable,
    plans: Sequence[Mapping[str, Any]],
    *,
    spacing_um: float,
    allowed_sides_by_port: Mapping[str, Mapping[str, str]],
    stub_factor: float = 2.0,
) -> None:
    """Re-account one iteration's claims into ``table``.

    Uses the public clear_occupancy (which Phase-1 guarantees PRESERVES
    history) so accumulated memory survives across iterations - no
    reaching into table internals.  ``allowed_sides_by_port`` is
    per-net."""
    table.clear_occupancy()
    for plan in plans:
        net = plan["net"]
        try:
            claims = all_claims(
                plan, spacing_um=spacing_um,
                allowed_sides_by_port=dict(
                    allowed_sides_by_port.get(net, {})))
        except Exception:
            # a plan too thin for full claims (e.g. unrouted) still
            # must not crash the loop
            claims = []
        for c in claims:
            table.add_claim(net, c)
    for intruder, zone in launch_overlap_claims(plans,
                                                stub_factor=stub_factor):
        table.add_claim(intruder, zone)


def negotiation_order(
    plans: Sequence[Mapping[str, Any]],
    table: ResourceCostTable,
    *,
    pres_fac: float,
    fallback_key: Callable[[Mapping[str, Any]], Any],
) -> List[Mapping[str, Any]]:
    """Hardest-contended net first.  Ties (and a cold table) fall back
    to the caller's heuristic so the first iteration matches today's
    deterministic order."""
    return sorted(
        plans,
        key=lambda p: (-table.net_cost(p["net"], pres_fac),
                       fallback_key(p)),
    )


def route_negotiated(
    g: CapacityGrid,
    nets: Sequence[NetInput],
    *,
    width_um: float = 0.0,
    wire_clear_um: float = 0.0,
    via_clear_um: float = 0.0,
    max_iters: int = 80,
    pres0: float = 0.5,
    growth: float = 1.8,
    hist_fac: float = 1.0,
) -> RouteResult:
    """Use the Rust pathfinder kernel when installed, else Python."""

    try:
        import klink_pathfinder_rs
    except ImportError:
        return _python_route_negotiated(
            g,
            nets,
            width_um=width_um,
            wire_clear_um=wire_clear_um,
            via_clear_um=via_clear_um,
            max_iters=max_iters,
            pres0=pres0,
            growth=growth,
            hist_fac=hist_fac,
        )
    raw = klink_pathfinder_rs.route_negotiated(
        _pathfinder_payload(g, nets, width_um, wire_clear_um, via_clear_um, max_iters, pres0, growth, hist_fac)
    )
    return _pathfinder_result(raw)


def rust_available() -> bool:
    try:
        import klink_pathfinder_rs  # noqa: F401
    except ImportError:
        return False
    return True


def _pathfinder_payload(
    g: CapacityGrid,
    nets: Sequence[NetInput],
    width_um: float,
    wire_clear_um: float,
    via_clear_um: float,
    max_iters: int,
    pres0: float,
    growth: float,
    hist_fac: float,
) -> dict[str, Any]:
    layer_index = {layer: i for i, layer in enumerate(g.layers)}
    net_items = []
    for net in nets:
        terminals = []
        for term in _terminal_cellsets(g, net):
            terminals.append(sorted(tuple(cell) for cell in term))
        net_items.append({"net": net.net, "terminals": terminals})
    return {
        "nx": g.nx,
        "ny": g.ny,
        "pitch_nm": g.pitch_nm,
        "layer_count": len(g.layers),
        "wire_blocked_all": [
            (layer, ix, iy) for layer, cells in sorted(g.wire_blocked_all.items()) for ix, iy in sorted(cells)
        ],
        "pad_cells": [
            (layer, owner, ix, iy)
            for layer, owners in sorted(g.pad_cells.items())
            for owner, cells in sorted(owners.items())
            for ix, iy in sorted(cells)
        ],
        "via_blocked": sorted(g.via_blocked),
        "via_rules": [
            {
                "a": layer_index[rule.a],
                "b": layer_index[rule.b],
                "cost": rule.cost,
                "fp_w": rule.footprint_um[0],
                "fp_h": rule.footprint_um[1],
            }
            for rule in g.via_rules
            if rule.a in layer_index and rule.b in layer_index
        ],
        "nets": net_items,
        "params": {
            "width_um": width_um,
            "wire_clear_um": wire_clear_um,
            "via_clear_um": via_clear_um,
            "max_iters": max_iters,
            "pres0": pres0,
            "growth": growth,
            "hist_fac": hist_fac,
        },
    }


def _pathfinder_result(raw: dict[str, Any]) -> RouteResult:
    return RouteResult(
        bool(raw["ok"]),
        {str(net): [tuple(cell) for cell in cells] for net, cells in raw["routes"].items()},
        int(raw["iterations"]),
        tuple(dict(problem) for problem in raw["problems"]),
        {
            str(net): [(tuple(edge[0]), tuple(edge[1])) for edge in edges]
            for net, edges in raw["edges"].items()
        },
    )
