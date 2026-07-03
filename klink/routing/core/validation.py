"""Validate route intent before invoking a router backend."""

from __future__ import annotations

from typing import Any


def _issue(code: str, message: str, *, severity: str = "error", **data) -> dict[str, Any]:
    out = {"severity": severity, "code": code, "message": message}
    out.update(data)
    return out


def _net_tokens(value: str) -> set[str]:
    text = str(value or "").replace(";", ",").replace(" ", ",")
    return {token.strip() for token in text.split(",") if token.strip()}


def _port_name(port: dict) -> str:
    return str(port.get("name", ""))


def _anchor_id(anchor: dict) -> str:
    return str(anchor.get("id") or anchor.get("name") or "")


def _has_center(obj: dict) -> bool:
    center = obj.get("center_um")
    return isinstance(center, list) and len(center) >= 2


def _validate_port(port: dict) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    name = _port_name(port)
    if not name:
        issues.append(_issue("PORT_MISSING_NAME", "port is missing a unique name"))
    if not _has_center(port):
        issues.append(_issue("PORT_MISSING_CENTER", "port is missing center_um", port=name))
    try:
        width = float(port.get("width_um", 0.0))
    except Exception:
        width = 0.0
    if width <= 0:
        issues.append(_issue("PORT_BAD_WIDTH", "port width_um must be positive", port=name))
    if "orientation" not in port:
        issues.append(_issue("PORT_MISSING_ORIENTATION", "port is missing orientation", port=name))
    if not str(port.get("target_layer", "")):
        issues.append(_issue("PORT_MISSING_TARGET_LAYER", "port is missing target_layer", port=name))
    access_mode = str(port.get("access_mode", "point"))
    if access_mode == "edge":
        if not bool(port.get("slide_allowed", False)):
            issues.append(
                _issue(
                    "EDGE_PORT_NOT_SLIDEABLE",
                    "edge access port should set slide_allowed=true",
                    port=name,
                    severity="warning",
                )
            )
        if not str(port.get("slide_edge", "")):
            issues.append(_issue("EDGE_PORT_MISSING_SLIDE_EDGE", "edge access port is missing slide_edge", port=name))
    return issues


def _validate_corridor(anchor: dict) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    anchor_id = _anchor_id(anchor)
    if not str(anchor.get("path_points", "")):
        issues.append(_issue("CORRIDOR_MISSING_PATH", "corridor anchor is missing path_points", anchor=anchor_id))
    try:
        width = float(anchor.get("width_um", 0.0))
    except Exception:
        width = 0.0
    if width <= 0:
        issues.append(_issue("CORRIDOR_BAD_WIDTH", "corridor width_um must be positive", anchor=anchor_id))
    if not _net_tokens(str(anchor.get("net", ""))):
        issues.append(
            _issue(
                "CORRIDOR_NETLESS",
                "corridor has no net allow-list; deterministic routing needs net or net-group binding",
                anchor=anchor_id,
                severity="warning",
            )
        )
    return issues


def _validate_anchor(anchor: dict) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    anchor_id = _anchor_id(anchor)
    kind = str(anchor.get("kind", ""))
    if not anchor_id:
        issues.append(_issue("ANCHOR_MISSING_ID", "anchor is missing id"))
    if not _has_center(anchor):
        issues.append(_issue("ANCHOR_MISSING_CENTER", "anchor is missing center_um", anchor=anchor_id))
    if kind == "corridor":
        issues.extend(_validate_corridor(anchor))
    elif kind == "waypoint_region":
        if not _net_tokens(str(anchor.get("net", ""))):
            issues.append(
                _issue(
                    "WAYPOINT_NETLESS",
                    "waypoint has no net binding; it cannot be assigned deterministically",
                    anchor=anchor_id,
                    severity="warning",
                )
            )
    elif kind == "bend_region":
        try:
            radius = float(anchor.get("radius_um", 0.0))
        except Exception:
            radius = 0.0
        if radius <= 0:
            issues.append(_issue("BEND_BAD_RADIUS", "bend anchor radius_um must be positive", anchor=anchor_id))
    else:
        issues.append(_issue("ANCHOR_UNKNOWN_KIND", "anchor kind is not supported", anchor=anchor_id, kind=kind))
    return issues


def _recommend_backend(intent: dict, obstacle_layers: list[str]) -> str:
    route_requests = intent.get("route_requests", [])
    assignment_requests = intent.get("assignment_requests", [])
    has_corridor = any(
        a.get("kind") == "corridor"
        for req in route_requests + assignment_requests
        for a in req.get("anchors", [])
    )
    has_obstacles = bool(obstacle_layers)
    if assignment_requests and has_corridor:
        return "corridor_lane_router"
    if assignment_requests:
        return "assignment_router"
    if has_obstacles:
        return "obstacle_aware_router"
    if has_corridor:
        return "corridor_router"
    if route_requests:
        return "simple_route_router"
    return "none"


def validate_route_intent(
    intent: dict,
    *,
    obstacle_layers: list[str] | None = None,
    require_obstacle_layers: bool = False,
) -> dict[str, Any]:
    """Validate route intent and recommend a router backend.

    ``obstacle_layers`` is intentionally explicit.  A layout layer is not an
    obstacle by itself; the routing profile/request decides which layers are
    treated as keepout for this run.
    """
    obstacle_layers = list(obstacle_layers or [])
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def add(issue: dict[str, Any]) -> None:
        if issue.get("severity") == "warning":
            warnings.append(issue)
        else:
            errors.append(issue)

    seen_ports: dict[str, dict] = {}
    for req in intent.get("route_requests", []):
        ports = list(req.get("ports", []))
        if len(ports) < 2:
            add(_issue("ROUTE_TOO_FEW_PORTS", "route request needs at least two ports", route_id=req.get("route_id")))
        for port in ports:
            for issue in _validate_port(port):
                add(issue)
            name = _port_name(port)
            if name:
                seen_ports[name] = port
        for anchor in req.get("anchors", []):
            for issue in _validate_anchor(anchor):
                add(issue)

    for req in intent.get("assignment_requests", []):
        demands = list(req.get("demands", []))
        candidates = list(req.get("candidate_sinks", []))
        if not demands:
            add(_issue("ASSIGNMENT_NO_DEMANDS", "assignment request has no demand ports", assignment_id=req.get("assignment_id")))
        if not candidates:
            add(_issue("ASSIGNMENT_NO_CANDIDATES", "assignment request has no candidate sinks", assignment_id=req.get("assignment_id")))
        for port in demands + candidates:
            for issue in _validate_port(port):
                add(issue)
            name = _port_name(port)
            if name:
                seen_ports[name] = port
        for anchor in req.get("anchors", []):
            for issue in _validate_anchor(anchor):
                add(issue)
        anchors_by_demand = req.get("anchors_by_demand", {})
        for demand in demands:
            name = _port_name(demand)
            bound = anchors_by_demand.get(name, [])
            if any(a.get("kind") == "corridor" for a in req.get("anchors", [])) and not bound:
                add(
                    _issue(
                        "DEMAND_MISSING_CORRIDOR",
                        "demand has no corridor anchor bound by net allow-list",
                        assignment_id=req.get("assignment_id"),
                        port=name,
                    )
                )

    if intent.get("unmatched_ports"):
        for port in intent.get("unmatched_ports", []):
            add(
                _issue(
                    "UNMATCHED_PORT",
                    "port is not part of any route or assignment request",
                    port=_port_name(port),
                    severity="warning",
                )
            )

    if require_obstacle_layers and not obstacle_layers:
        add(_issue("OBSTACLE_LAYERS_REQUIRED", "this routing profile requires explicit obstacle_layers"))

    backend = _recommend_backend(intent, obstacle_layers)
    return {
        "routable": not errors and backend != "none",
        "recommended_backend": backend,
        "obstacle_layers": obstacle_layers,
        "errors": errors,
        "warnings": warnings,
    }
