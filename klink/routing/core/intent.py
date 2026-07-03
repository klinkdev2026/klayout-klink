"""Build router-facing intent from Port and Anchor RPC snapshots.

This module is deliberately small.  It fixes the first routing boundary:
the router should consume normalized route requests, not raw RPC output.
"""

from __future__ import annotations

from typing import Any


def _name_key(item: dict) -> str:
    return str(item.get("name") or item.get("id") or "")


def _anchor_key(anchor: dict) -> tuple:
    return (
        -int(anchor.get("priority", 0)),
        str(anchor.get("id", "")),
    )


def _is_candidate_sink(port: dict) -> bool:
    return str(port.get("port_type", "")).lower() == "candidate_sink"


def _net(port_or_anchor: dict) -> str:
    return str(port_or_anchor.get("net", "") or "")


def _net_tokens(value: str) -> set[str]:
    text = str(value or "").replace(";", ",").replace(" ", ",")
    return {token.strip() for token in text.split(",") if token.strip()}


def anchor_applies_to_net(anchor: dict, net: str) -> bool:
    """Return whether an anchor is explicitly usable by ``net``.

    Anchor ``net`` accepts a single net or a comma/semicolon/space separated
    set such as ``sig0,sig1``.  Empty anchor net means global/default and is
    handled separately by ``global_route_anchors``.
    """
    tokens = _net_tokens(_net(anchor))
    return bool(net and tokens and net in tokens)


def _normalize_anchor(anchor: dict) -> dict:
    normalized = dict(anchor)
    if normalized.get("kind") == "corridor":
        # A corridor is a directional channel around its stored centerline.
        # It is not a waypoint region that can be satisfied by crossing it.
        normalized.setdefault("corridor_policy", "follow_centerline")
        normalized.setdefault("allows_crossing", False)
    return normalized


def anchors_for_net(anchors: list[dict], net: str) -> list[dict]:
    """Return anchors explicitly bound to ``net``, ordered by priority then id."""
    return [_normalize_anchor(a) for a in sorted([a for a in anchors if anchor_applies_to_net(a, net)], key=_anchor_key)]


def anchors_for_any_net(anchors: list[dict], nets: set[str]) -> list[dict]:
    """Return anchors bound to at least one net in ``nets`` plus global anchors."""
    applicable = [
        anchor for anchor in anchors
        if not _net(anchor) or any(anchor_applies_to_net(anchor, net) for net in nets)
    ]
    return [_normalize_anchor(a) for a in sorted(applicable, key=_anchor_key)]


def global_route_anchors(anchors: list[dict]) -> list[dict]:
    """Return netless anchors that apply at assignment/planning scope."""
    return [_normalize_anchor(a) for a in sorted([a for a in anchors if not _net(a)], key=_anchor_key)]


def build_route_intent(
    ports: list[dict],
    anchors: list[dict] | None = None,
    *,
    cell: str = "",
) -> dict[str, Any]:
    """Convert Port/Anchor lists into router-facing requests.

    Nets with at least two non-candidate ports become ordinary route requests.
    Single demand ports plus candidate_sink ports become one assignment request.
    Anchor ``net`` can be a comma/semicolon/space separated allow-list, for
    example ``sig0,sig1`` on a CorridorAnchor.  Netless anchors are not guessed
    onto ordinary route nets; for assignment requests they remain global/default
    anchors.
    Corridor anchors are directional channels: the router may enter/exit the
    corridor, but satisfying the anchor means following the stored path
    direction within the corridor width, not crossing the path as a waypoint.
    """
    anchors = list(anchors or [])
    normal_ports = sorted([p for p in ports if not _is_candidate_sink(p)], key=_name_key)
    candidate_sinks = sorted([p for p in ports if _is_candidate_sink(p)], key=_name_key)

    by_net: dict[str, list[dict]] = {}
    for port in normal_ports:
        net = _net(port)
        if net:
            by_net.setdefault(net, []).append(port)

    route_requests = []
    assignment_demands = []
    for net in sorted(by_net):
        net_ports = by_net[net]
        if len(net_ports) >= 2:
            request = {
                "route_id": "route_%s" % net,
                "net": net,
                "ports": net_ports,
                "anchors": anchors_for_net(anchors, net),
            }
            if len(net_ports) == 2:
                request["source"] = net_ports[0]
                request["target"] = net_ports[1]
            route_requests.append(request)
        elif len(net_ports) == 1:
            assignment_demands.append(net_ports[0])

    assignment_requests = []
    if assignment_demands and candidate_sinks:
        demand_nets = {_net(port) for port in assignment_demands if _net(port)}
        assignment_requests.append(
            {
                "assignment_id": "assign_%s" % (cell or "cell"),
                "mode": "fanout_to_candidate_sinks",
                "demands": assignment_demands,
                "candidate_sinks": candidate_sinks,
                "anchors": anchors_for_any_net(anchors, demand_nets),
                "anchors_by_demand": {
                    str(port.get("name", "")): anchors_for_net(anchors, _net(port))
                    for port in assignment_demands
                },
            }
        )

    return {
        "cell": cell,
        "route_requests": route_requests,
        "assignment_requests": assignment_requests,
        "global_anchors": global_route_anchors(anchors),
        "unmatched_ports": [
            p for p in normal_ports
            if not _net(p) or (len(by_net.get(_net(p), [])) == 1 and not candidate_sinks)
        ],
    }


def collect_route_intent(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
) -> dict[str, Any]:
    """Read ``port.list`` / ``anchor.list`` and build route intent."""
    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": anchor_layer, "sort": "id"}).get("anchors", [])
    return build_route_intent(ports, anchors, cell=cell)
