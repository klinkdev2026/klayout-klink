"""High-level routing workflow entry points."""

from __future__ import annotations

from typing import Any

from klink.routing.core.intent import collect_route_intent
from klink.routing.geom.planner import collect_obstacle_bboxes, plan_routes_from_intent
from klink.routing.core.validation import validate_route_intent
from klink.routing.geom.writeback import commit_routes


def route_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    anchor_layer: str = "999/1",
    obstacle_layers: list[str] | None = None,
    route_layer: str = "10/0",
    dry_run: bool = True,
    clear: bool = True,
    require_obstacle_layers: bool = False,
    router_backend: str = "semantic",
    safe_distance_um: float = 0.0,
    angle_mode: str = "manhattan",
) -> dict[str, Any]:
    """Plan and optionally write routes for one cell.

    This is the public client-side workflow:

    1. read Port/Anchor PCells,
    2. build route intent,
    3. validate semantic completeness,
    4. plan route skeletons,
    5. run basic geometry checks,
    6. optionally write paths to ``route_layer``.

    The current planner is a deterministic semantic skeleton router.  It is
    not the future grid/damping backend; it preserves Port/Anchor rules while
    giving us a stable workflow and report schema.
    """
    obstacle_layers = list(obstacle_layers or [])
    intent = collect_route_intent(
        client,
        cell,
        port_layer=port_layer,
        anchor_layer=anchor_layer,
    )
    validation = validate_route_intent(
        intent,
        obstacle_layers=obstacle_layers,
        require_obstacle_layers=require_obstacle_layers,
    )
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)
    plan = plan_routes_from_intent(
        intent,
        obstacle_bboxes=obstacle_bboxes,
        obstacle_layers=obstacle_layers,
        router_backend=router_backend,
        safe_distance_um=safe_distance_um,
        angle_mode=angle_mode,
    )

    committed = False
    writeback = None
    if not dry_run and plan.get("ok"):
        writeback = commit_routes(
            client,
            cell,
            plan.get("routes", []),
            route_layer=route_layer,
            clear=clear,
        )
        committed = True

    route_count = len(plan.get("routes", []))
    return {
        "cell": cell,
        "dry_run": bool(dry_run),
        "committed": committed,
        "route_layer": route_layer,
        "port_layer": port_layer,
        "anchor_layer": anchor_layer,
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "algorithm": "geometric_visibility_dijkstra" if router_backend == "geometric" else "deterministic_semantic_skeleton",
        "backend": validation.get("recommended_backend") if router_backend == "semantic" else router_backend,
        "router_backend": router_backend,
        "recommended_backend": validation.get("recommended_backend"),
        "safe_distance_um": float(safe_distance_um),
        "angle_mode": angle_mode,
        "routable": bool(validation.get("routable")) and bool(plan.get("ok")),
        "route_count": route_count,
        "intent": intent,
        "validation": validation,
        "routes": plan.get("routes", []),
        "crossings": plan.get("crossings", []),
        "self_crossings": plan.get("self_crossings", []),
        "obstacle_hits": plan.get("obstacle_hits", []),
        "errors": list(validation.get("errors", [])) + list(plan.get("errors", [])),
        "warnings": validation.get("warnings", []),
        "writeback": writeback,
    }
