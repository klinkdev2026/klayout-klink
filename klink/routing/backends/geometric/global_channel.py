"""Global-decision router built on top of the tapered hybrid geometry backend.

This module owns decisions that are too global for ``tapered_hybrid_cell``:

- candidate sink assignment can use estimated routed cost with obstacles
- equivalent corridor anchors can be load-balanced by capacity

It intentionally reuses the existing tapered hybrid planner/writeback for the
actual geometry.  The stronger behavior lives in pair/corridor assignment, not
in fixture-specific route coordinates.
"""

from __future__ import annotations

import itertools
import math
from typing import Sequence

from klink.routing.geom.planner import collect_obstacle_bboxes
from klink.routing.geom.constraints import port_launch_point
from klink.routing.geom.geometry import expand_bbox, route_hits_bboxes
from klink.routing.geom.geometric import route_segment_bboxes
from klink.routing.backends.geometric.tapered import TaperStrategy
from klink.routing.backends.geometric.tapered_segments import (
    _anchor_applies,
    _corridor_path,
    _infer_pair_route_layer,
    _obstacle_aware_inner_points,
    _required_points_from_non_corridor_anchors,
    _is_candidate_sink,
    _lane_offsets,
    _net_sort_key,
    _net_tokens,
    _pair_ports_by_net_tokens,
    _route_name,
    _sibling_envelope_overlaps,
    commit_tapered_hybrid_many,
    route_tapered_hybrid,
    route_tapered_hybrid_many,
)


def _point(port_or_point: dict | Sequence[float]) -> tuple[float, float]:
    if isinstance(port_or_point, dict):
        value = port_or_point.get("center_um") or [0.0, 0.0]
    else:
        value = port_or_point
    return (float(value[0]), float(value[1]))


def _distance(a: dict | Sequence[float], b: dict | Sequence[float]) -> float:
    ax, ay = _point(a)
    bx, by = _point(b)
    return math.hypot(ax - bx, ay - by)


def _path_length(points: Sequence[Sequence[float]]) -> float:
    return sum(_distance(a, b) for a, b in zip(points, points[1:]))


def _route_cost(
    pair: dict,
    *,
    anchors: Sequence[dict],
    obstacle_bboxes: Sequence[Sequence[float]],
    spacing_um: float,
    angle_mode: str,
    safe_distance_um: float,
) -> float:
    try:
        planned = route_tapered_hybrid_many(
            [pair],
            anchors=anchors,
            spacing_um=spacing_um,
            angle_mode=angle_mode,
            safe_distance_um=safe_distance_um,
            obstacle_bboxes=obstacle_bboxes,
            validate_sibling_overlap=False,
        )
    except Exception:
        return float("inf")
    if not planned.get("ok") or not planned.get("routes"):
        return float("inf")
    route = planned["routes"][0]
    return _path_length(route.get("points_um", []))


def _assignment_min_cost(
    demands: Sequence[tuple[str, dict]],
    candidates: Sequence[dict],
    cost_matrix: Sequence[Sequence[float]],
) -> list[tuple[str, dict, dict, float]]:
    if not demands or not candidates:
        return []
    demand_count = len(demands)
    candidate_count = len(candidates)
    if candidate_count < demand_count:
        demands = list(demands)[:candidate_count]
        demand_count = candidate_count

    # The expected layout use case is small user-marked candidate sets.  Keep a
    # deterministic exhaustive solver for those, then fall back to greedy for
    # very large selections rather than hiding exponential behavior.
    if demand_count <= 8 and candidate_count <= 12:
        best_cost = float("inf")
        best: list[tuple[str, dict, dict, float]] = []
        for candidate_indices in itertools.permutations(range(candidate_count), demand_count):
            total = 0.0
            assignment: list[tuple[str, dict, dict, float]] = []
            feasible = True
            for demand_idx, candidate_idx in enumerate(candidate_indices):
                cost = float(cost_matrix[demand_idx][candidate_idx])
                if math.isinf(cost):
                    feasible = False
                    break
                net, demand = demands[demand_idx]
                assignment.append((net, demand, candidates[candidate_idx], cost))
                total += cost
            if feasible and total < best_cost:
                best_cost = total
                best = assignment
        return best

    unused = set(range(candidate_count))
    assigned = []
    for demand_idx, (net, demand) in enumerate(demands):
        best_idx = min(unused, key=lambda idx: cost_matrix[demand_idx][idx], default=None)
        if best_idx is None or math.isinf(float(cost_matrix[demand_idx][best_idx])):
            continue
        unused.remove(best_idx)
        assigned.append((net, demand, candidates[best_idx], float(cost_matrix[demand_idx][best_idx])))
    return assigned


def pair_ports_with_obstacle_cost(
    ports: Sequence[dict],
    *,
    anchors: Sequence[dict] | None = None,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
    spacing_um: float = 20.0,
    angle_mode: str = "manhattan",
    safe_distance_um: float = 0.0,
) -> dict:
    """Pair ordinary two-port nets and netless candidate sinks.

    Exactly-two-port nets stay deterministic.  One-port nets may bind to
    candidate sinks using routed cost, so obstacles can change the assignment.
    """
    anchors = list(anchors or [])
    obstacle_bboxes = [list(map(float, bbox)) for bbox in (obstacle_bboxes or [])]
    normal_ports = [dict(p) for p in ports if not _is_candidate_sink(p)]
    candidate_sinks = [dict(p) for p in ports if _is_candidate_sink(p)]

    by_net: dict[str, list[dict]] = {}
    for port in normal_ports:
        for net in _net_tokens(port.get("net")):
            by_net.setdefault(net, []).append(port)

    pairs: list[dict] = []
    demands: list[tuple[str, dict]] = []
    unsupported = []
    for net in sorted(by_net, key=_net_sort_key):
        members = sorted(
            by_net[net],
            key=lambda p: (
                float((p.get("center_um") or [0.0, 0.0])[0]),
                float((p.get("center_um") or [0.0, 0.0])[1]),
                str(p.get("name") or ""),
            ),
        )
        if len(members) == 1:
            demands.append((net, members[0]))
            continue
        if len(members) == 2:
            pairs.append({
                "net": net,
                "source": members[0],
                "target": members[1],
                "route_layer": _infer_pair_route_layer(net, members[0], members[1]),
            })
            continue
        unsupported.append({
            "type": "unsupported_multi_port_net",
            "net": net,
            "port_count": len(members),
            "message": f"net {net} has {len(members)} ordinary ports; use routing.steiner_cell/global tree backend",
        })

    assignment_report = []
    if demands and candidate_sinks:
        cost_matrix = []
        for net, demand in demands:
            row = []
            for candidate in candidate_sinks:
                pair = {
                    "net": net,
                    "source": demand,
                    "target": candidate,
                    "route_layer": _infer_pair_route_layer(net, demand, candidate),
                }
                row.append(_route_cost(
                    pair,
                    anchors=[a for a in anchors if _anchor_applies(a, net) and a.get("kind") != "corridor"],
                    obstacle_bboxes=obstacle_bboxes,
                    spacing_um=spacing_um,
                    angle_mode=angle_mode,
                    safe_distance_um=safe_distance_um,
                ))
            cost_matrix.append(row)
        for net, demand, candidate, cost in _assignment_min_cost(demands, candidate_sinks, cost_matrix):
            pairs.append({
                "net": net,
                "source": demand,
                "target": candidate,
                "route_layer": _infer_pair_route_layer(net, demand, candidate),
                "assignment": "candidate_sink_obstacle_cost",
                "assignment_cost_um": cost,
            })
            assignment_report.append({
                "net": net,
                "source": str(demand.get("name") or ""),
                "target": str(candidate.get("name") or ""),
                "cost_um": cost,
            })
    elif demands:
        unsupported.extend({
            "type": "unmatched_one_port_net",
            "net": net,
            "message": f"net {net} has one ordinary port and no candidate sink",
        } for net, _demand in demands)

    return {
        "pairs": pairs,
        "unsupported": unsupported,
        "candidate_assignment": assignment_report,
    }


def _corridor_capacity(corridor: dict, max_width: float, spacing_um: float) -> int:
    width = float(corridor.get("width_um", 0.0) or 0.0)
    if width <= 0:
        return 0
    pitch = max(float(max_width), 0.0) + float(spacing_um)
    count = 0
    for candidate_count in range(1, 10000):
        offsets = _lane_offsets(candidate_count, pitch)
        allowed = (width - max_width) / 2.0
        if offsets and max(abs(v) for v in offsets) > allowed + 1e-9:
            break
        count = candidate_count
    return count


def _corridor_choice_group(corridor: dict) -> str:
    for key in ("choice_group", "channel_group", "corridor_group"):
        value = str(corridor.get(key) or "").strip()
        if value:
            return value
    label = str(corridor.get("label") or "")
    for token in label.replace(";", " ").replace(",", " ").split():
        for prefix in ("choice_group=", "channel_group=", "corridor_group="):
            if token.startswith(prefix):
                return token[len(prefix):].strip()
    return ""


def _corridor_cost(pair: dict, corridor: dict) -> float:
    path = _corridor_path(corridor)
    if not path:
        return 0.0
    source = pair["source"]
    target = pair["target"]
    first = path[0]
    last = path[-1]
    return min(
        _distance(source, first) + _distance(target, last),
        _distance(source, last) + _distance(target, first),
    )


def assign_corridors_by_capacity(
    pairs: Sequence[dict],
    anchors: Sequence[dict],
    *,
    spacing_um: float = 20.0,
) -> dict:
    """Restrict explicit choice corridors so the hybrid planner can route.

    A plain corridor remains a required path.  Only corridors marked with the
    same ``choice_group``/``channel_group`` are treated as alternative channels
    where the global router may choose exactly one for each matching net.
    """
    corridors = [dict(a) for a in anchors if a.get("kind") == "corridor"]
    other_anchors = [dict(a) for a in anchors if a.get("kind") != "corridor"]
    hard_corridors: list[dict] = []
    choice_groups: dict[str, list[dict]] = {}
    for corridor in corridors:
        group_id = _corridor_choice_group(corridor)
        if group_id:
            choice_groups.setdefault(group_id, []).append(corridor)
        else:
            hard_corridors.append(corridor)
    if not choice_groups:
        return {"anchors": list(anchors), "assignments": [], "errors": []}

    max_width = 1.0
    for pair in pairs:
        max_width = max(
            max_width,
            float(pair["source"].get("width_um", 1.0) or 1.0),
            float(pair["target"].get("width_um", 1.0) or 1.0),
        )
    rewritten = [*other_anchors, *hard_corridors]
    reports = []
    errors = []

    for group_id, group_corridors in sorted(choice_groups.items()):
        remaining = {
            str(c.get("id") or idx): _corridor_capacity(c, max_width, spacing_um)
            for idx, c in enumerate(group_corridors)
        }
        by_id = {str(c.get("id") or idx): c for idx, c in enumerate(group_corridors)}
        assignments: dict[str, list[str]] = {cid: [] for cid in by_id}

        sorted_pairs = sorted(
            pairs,
            key=lambda p: (
                sum(1 for c in group_corridors if _anchor_applies(c, str(p.get("net") or ""))),
                _net_sort_key(str(p.get("net") or "")),
            ),
        )
        for pair in sorted_pairs:
            net = str(pair.get("net") or "")
            options = [
                (str(c.get("id") or idx), c)
                for idx, c in enumerate(group_corridors)
                if _anchor_applies(c, net)
            ]
            if not options:
                continue
            available = [(cid, c) for cid, c in options if remaining.get(cid, 0) > 0]
            if not available:
                errors.append({
                    "type": "corridor_choice_capacity",
                    "choice_group": group_id,
                    "net": net,
                    "message": f"choice group {group_id} has no remaining corridor capacity for net {net}",
                })
                continue
            cid, corridor = min(available, key=lambda item: (_corridor_cost(pair, item[1]), item[0]))
            remaining[cid] -= 1
            assignments[cid].append(net)
            reports.append({
                "net": net,
                "choice_group": group_id,
                "corridor_id": cid,
                "remaining_capacity": remaining[cid],
            })

        for cid, corridor in by_id.items():
            nets = assignments[cid]
            if nets:
                rewritten.append({**corridor, "net": ",".join(sorted(set(nets), key=_net_sort_key))})
    return {"anchors": rewritten, "assignments": reports, "errors": errors}


def _matching_corridors(pairs: Sequence[dict], anchors: Sequence[dict]) -> list[dict]:
    return [
        a for a in anchors
        if a.get("kind") == "corridor"
        and any(_anchor_applies(a, str(pair.get("net") or "")) for pair in pairs)
    ]


def _route_tapered_hybrid_many_frozen_in_order(
    pairs: Sequence[dict],
    *,
    anchors: Sequence[dict] | None = None,
    spacing_um: float = 20.0,
    strategy: str | TaperStrategy = "uniform",
    angle_mode: str = "manhattan",
    safe_distance_um: float = 0.0,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
) -> dict:
    anchors = list(anchors or [])
    base_obstacles = [list(map(float, bbox)) for bbox in (obstacle_bboxes or [])]
    frozen: list[list[float]] = []
    routes = []
    errors = []
    for idx, pair in enumerate(pairs):
        source = dict(pair.get("source") or {})
        target = dict(pair.get("target") or {})
        net = str(pair.get("net") or source.get("net") or target.get("net") or "")
        matching_anchors = [a for a in anchors if _anchor_applies(a, net)]
        inner_points = _required_points_from_non_corridor_anchors(source, target, matching_anchors)
        source_launch = port_launch_point(source)
        target_launch = port_launch_point(target)
        current_pin_escape_frozen = [
            bbox for bbox in frozen
            if not (
                float(bbox[0]) <= float(source_launch[0]) <= float(bbox[2])
                and float(bbox[1]) <= float(source_launch[1]) <= float(bbox[3])
            )
            and not (
                float(bbox[0]) <= float(target_launch[0]) <= float(bbox[2])
                and float(bbox[1]) <= float(target_launch[1]) <= float(bbox[3])
            )
        ]
        all_obstacles = [*base_obstacles, *current_pin_escape_frozen]
        try:
            routed_inner = _obstacle_aware_inner_points(
                source,
                target,
                inner_points,
                all_obstacles,
                angle_mode=angle_mode,
                safe_distance_um=safe_distance_um,
            )
            route = route_tapered_hybrid(source, target, routed_inner, strategy=strategy)
        except Exception as exc:
            errors.append({
                "type": "route_failed",
                "net": net,
                "message": str(exc),
            })
            continue
        route.update({
            "route_id": pair.get("route_id") or f"route_{net or idx}",
            "net": net,
            "source": _route_name(source),
            "target": _route_name(target),
            "route_layer": pair.get("route_layer"),
            "anchors": [a.get("id") for a in matching_anchors if a.get("id")],
            "lane_offset_um": 0.0,
            "corridor_id": None,
        })
        routes.append(route)
        max_width = max(
            float(route.get("source_width_um", source.get("width_um", 1.0)) or 1.0),
            float(route.get("target_width_um", target.get("width_um", 1.0)) or 1.0),
        )
        freeze_margin = max_width / 2.0 + float(spacing_um) + float(safe_distance_um)
        frozen.extend(expand_bbox(bbox, freeze_margin) for bbox in route_segment_bboxes(route["points_um"]))

    overlaps = _sibling_envelope_overlaps(routes)
    obstacle_hits = []
    for route in routes:
        width = max(float(route.get("source_width_um", 1.0) or 1.0), float(route.get("target_width_um", 1.0) or 1.0))
        for hit in route_hits_bboxes(route.get("points_um", []), base_obstacles, width):
            obstacle_hits.append({**hit, "route_id": route.get("route_id"), "net": route.get("net")})
    error_messages = []
    if errors:
        error_messages.append("route failed")
    if overlaps:
        error_messages.append("same-layer sibling route overlap")
    if obstacle_hits:
        error_messages.append("route hits obstacle")
    return {
        "ok": not errors and not overlaps and not obstacle_hits,
        "backend": "global_channel_frozen_paths",
        "routes": routes,
        "route_count": len(routes),
        "angle_mode": angle_mode,
        "safe_distance_um": float(safe_distance_um),
        "lane_reports": [],
        "sibling_overlaps": overlaps,
        "obstacle_hits": obstacle_hits,
        "planning_errors": errors,
        "errors": error_messages,
        "frozen_obstacle_count": len(frozen),
    }


def _route_order_candidates(pairs: Sequence[dict]) -> list[list[dict]]:
    indexed = list(enumerate(pairs))
    orders: list[list[int]] = []

    def add(order: Sequence[int]) -> None:
        value = list(order)
        if value not in orders:
            orders.append(value)

    add(range(len(indexed)))
    add(reversed(range(len(indexed))))
    add(idx for idx, _pair in sorted(
        indexed,
        key=lambda item: (
            -float(item[1].get("assignment_cost_um", 0.0) or 0.0),
            _net_sort_key(str(item[1].get("net") or "")),
        ),
    ))
    add(idx for idx, _pair in sorted(
        indexed,
        key=lambda item: (
            float(item[1].get("assignment_cost_um", 0.0) or 0.0),
            _net_sort_key(str(item[1].get("net") or "")),
        ),
    ))
    add(idx for idx, _pair in sorted(
        indexed,
        key=lambda item: (
            float((item[1].get("source", {}).get("center_um") or [0.0, 0.0])[1]),
            float((item[1].get("target", {}).get("center_um") or [0.0, 0.0])[1]),
        ),
    ))
    add(idx for idx, _pair in sorted(
        indexed,
        key=lambda item: (
            -float((item[1].get("source", {}).get("center_um") or [0.0, 0.0])[1]),
            -float((item[1].get("target", {}).get("center_um") or [0.0, 0.0])[1]),
        ),
    ))
    if len(indexed) <= 6:
        for order in itertools.permutations(range(len(indexed))):
            add(order)
    return [[dict(pairs[idx]) for idx in order] for order in orders]


def _frozen_route_score(result: dict) -> tuple[int, int, int, int, float, float]:
    route_count = int(result.get("route_count") or 0)
    route_length = sum(_path_length(route.get("points_um", [])) for route in result.get("routes", []))
    return (
        0 if result.get("ok") else 1,
        len(result.get("planning_errors") or []),
        len(result.get("sibling_overlaps") or []),
        len(result.get("obstacle_hits") or []),
        -float(route_count),
        float(route_length),
    )


def route_tapered_hybrid_many_with_frozen_paths(
    pairs: Sequence[dict],
    *,
    anchors: Sequence[dict] | None = None,
    spacing_um: float = 20.0,
    strategy: str | TaperStrategy = "uniform",
    angle_mode: str = "manhattan",
    safe_distance_um: float = 0.0,
    obstacle_bboxes: Sequence[Sequence[float]] | None = None,
) -> dict:
    """Route pairs with completed-route freezing and route-order search.

    Completed paths become inflated obstacles for later paths.  For small
    bundles, routing order can decide whether a valid plan exists, so this
    tries deterministic candidate orders and accepts only a validated plan.
    """
    pairs = [dict(pair) for pair in pairs]
    if not pairs:
        return _route_tapered_hybrid_many_frozen_in_order(
            [],
            anchors=anchors,
            spacing_um=spacing_um,
            strategy=strategy,
            angle_mode=angle_mode,
            safe_distance_um=safe_distance_um,
            obstacle_bboxes=obstacle_bboxes,
        )

    best = None
    best_order: list[str] = []
    attempts = 0
    for ordered_pairs in _route_order_candidates(pairs):
        attempts += 1
        result = _route_tapered_hybrid_many_frozen_in_order(
            ordered_pairs,
            anchors=anchors,
            spacing_um=spacing_um,
            strategy=strategy,
            angle_mode=angle_mode,
            safe_distance_um=safe_distance_um,
            obstacle_bboxes=obstacle_bboxes,
        )
        order = [str(pair.get("net") or "") for pair in ordered_pairs]
        if best is None or _frozen_route_score(result) < _frozen_route_score(best):
            best = result
            best_order = order
        if result.get("ok"):
            result["route_order"] = order
            result["route_order_attempts"] = attempts
            return result

    assert best is not None
    best["route_order"] = best_order
    best["route_order_attempts"] = attempts
    return best


def route_global_channel_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    spacing_um: float = 20.0,
    strategy: str | TaperStrategy = "uniform",
    angle_mode: str = "manhattan",
    safe_distance_um: float = 0.0,
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route a cell with explicit global assignment before hybrid geometry."""
    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    anchors = client.call("anchor.list", {"cell": cell, "layer": anchor_layer, "sort": "id"}).get("anchors", [])
    obstacle_layers = list(obstacle_layers or [])
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)

    paired = pair_ports_with_obstacle_cost(
        ports,
        anchors=anchors,
        obstacle_bboxes=obstacle_bboxes,
        spacing_um=spacing_um,
        angle_mode=angle_mode,
        safe_distance_um=safe_distance_um,
    )
    pairs = paired["pairs"]
    # If there are no candidate sinks, preserve the existing pairing behavior
    # exactly; it already handles ordered-loop candidate fixtures.
    if not any(_is_candidate_sink(p) for p in ports):
        pairs = _pair_ports_by_net_tokens(ports)

    corridor_plan = assign_corridors_by_capacity(pairs, anchors, spacing_um=spacing_um)
    planned_anchors = corridor_plan["anchors"]

    by_layer: dict[str, list[dict]] = {}
    for pair in pairs:
        by_layer.setdefault(str(pair.get("route_layer") or "10/0"), []).append(pair)

    groups = []
    ok = not paired["unsupported"] and not corridor_plan["errors"]
    for route_layer in sorted(by_layer):
        layer_pairs = by_layer[route_layer]
        if _matching_corridors(layer_pairs, planned_anchors):
            planned = route_tapered_hybrid_many(
                layer_pairs,
                anchors=planned_anchors,
                spacing_um=spacing_um,
                strategy=strategy,
                angle_mode=angle_mode,
                safe_distance_um=safe_distance_um,
                obstacle_bboxes=obstacle_bboxes,
            )
        else:
            planned = route_tapered_hybrid_many_with_frozen_paths(
                layer_pairs,
                anchors=planned_anchors,
                spacing_um=spacing_um,
                strategy=strategy,
                angle_mode=angle_mode,
                safe_distance_um=safe_distance_um,
                obstacle_bboxes=obstacle_bboxes,
            )
        write = None
        if planned["ok"] and not corridor_plan["errors"]:
            write = commit_tapered_hybrid_many(client, cell, planned, route_layer=route_layer, clear=clear)
        else:
            ok = False
        groups.append({
            "route_layer": route_layer,
            "ok": planned["ok"],
            "route_count": planned["route_count"],
            "lane_reports": planned["lane_reports"],
            "sibling_overlaps": planned["sibling_overlaps"],
            "obstacle_hits": planned.get("obstacle_hits", []),
            "planning_errors": planned.get("planning_errors", []),
            "errors": planned["errors"],
            "route_order": planned.get("route_order"),
            "route_order_attempts": planned.get("route_order_attempts"),
            "write": write,
        })

    errors = [e["message"] for e in paired["unsupported"]]
    errors.extend(e["message"] for e in corridor_plan["errors"])
    for group in groups:
        errors.extend(group.get("errors") or [])

    return {
        "ok": ok and all(g["ok"] for g in groups),
        "backend": "global_channel_cell",
        "cell": cell,
        "port_count": len(ports),
        "anchor_count": len(anchors),
        "pair_count": len(pairs),
        "angle_mode": angle_mode,
        "safe_distance_um": float(safe_distance_um),
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "candidate_assignment": paired["candidate_assignment"],
        "corridor_assignment": corridor_plan["assignments"],
        "planning_errors": [*paired["unsupported"], *corridor_plan["errors"]],
        "errors": errors,
        "groups": groups,
    }
