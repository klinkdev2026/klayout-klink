"""Simple multi-layer escape router for wall-blocked two-port nets."""

from __future__ import annotations

from typing import Sequence

from klink.routing.geom.constraints import port_launch_point
from klink.routing.geom.geometry import parse_layer, route_hits_bboxes
from klink.routing.geom.planner import collect_obstacle_bboxes
from klink.routing.backends.geometric.tapered_segments import _net_sort_key, _pair_ports_by_net_tokens, _route_name
from klink.routing.geom.writeback import clear_route_layer


def _route_width(pair: dict) -> float:
    return min(
        float(pair["source"].get("width_um", 1.0) or 1.0),
        float(pair["target"].get("width_um", 1.0) or 1.0),
    )


def _bbox_union(bboxes: Sequence[Sequence[float]]) -> list[float] | None:
    if not bboxes:
        return None
    return [
        min(float(b[0]) for b in bboxes),
        min(float(b[1]) for b in bboxes),
        max(float(b[2]) for b in bboxes),
        max(float(b[3]) for b in bboxes),
    ]


def _center(port: dict) -> list[float]:
    center = port.get("center_um") or [0.0, 0.0]
    return [float(center[0]), float(center[1])]


def _ordered_pair(pair: dict) -> tuple[dict, dict]:
    source = dict(pair["source"])
    target = dict(pair["target"])
    if _center(source)[0] <= _center(target)[0]:
        return source, target
    return target, source


def _plan_escape_route(
    pair: dict,
    *,
    obstacle_bboxes: Sequence[Sequence[float]],
    spacing_um: float,
    route_layer: str,
    bridge_layer: str,
) -> dict:
    left, right = _ordered_pair(pair)
    width = _route_width(pair)
    union = _bbox_union(obstacle_bboxes)
    left_center = _center(left)
    right_center = _center(right)
    left_launch = port_launch_point(left)
    right_launch = port_launch_point(right)
    y = (float(left_center[1]) + float(right_center[1])) / 2.0
    if union is None:
        primary = [[left_center, left_launch, right_launch, right_center]]
        bridge = []
        vias = []
    else:
        x_before = float(union[0]) - float(spacing_um) - width / 2.0
        x_after = float(union[2]) + float(spacing_um) + width / 2.0
        via_left = [x_before, y]
        via_right = [x_after, y]
        primary = [
            [left_center, left_launch, via_left],
            [via_right, right_launch, right_center],
        ]
        bridge = [[via_left, via_right]]
        vias = [via_left, via_right]
    route_id = pair.get("route_id") or f"ml_{pair.get('net') or _route_name(left)}"
    return {
        "route_id": route_id,
        "net": str(pair.get("net") or ""),
        "source": _route_name(left),
        "target": _route_name(right),
        "width_um": width,
        "route_layer": route_layer,
        "bridge_layer": bridge_layer,
        "primary_paths": primary,
        "bridge_paths": bridge,
        "vias": vias,
    }


def _write_multilayer_routes(
    client,
    cell: str,
    routes: Sequence[dict],
    *,
    route_layer: str,
    bridge_layer: str,
    via_layer: str,
    clear: bool,
) -> dict:
    route_l, route_d = parse_layer(route_layer)
    bridge_l, bridge_d = parse_layer(bridge_layer)
    via_l, via_d = parse_layer(via_layer)
    client.layer_ensure(route_l, route_d, name="KLINK_ROUTES")
    client.layer_ensure(bridge_l, bridge_d, name="KLINK_BRIDGE_ROUTES")
    client.layer_ensure(via_l, via_d, name="KLINK_ROUTE_VIAS")
    deleted = 0
    if clear:
        deleted += int(clear_route_layer(client, cell, route_layer=route_layer).get("deleted", 0))
        deleted += int(clear_route_layer(client, cell, route_layer=bridge_layer).get("deleted", 0))
        client.shape_delete(cell, layers=[via_layer], kinds=["boxes"], limit=10000)
    primary_paths = 0
    bridge_paths = 0
    vias = 0
    for route in routes:
        width = float(route.get("width_um", 1.0) or 1.0)
        for path in route.get("primary_paths", []):
            if len(path) < 2:
                continue
            client.shape_insert_path(
                cell,
                layer=route_l,
                datatype=route_d,
                points_um=path,
                width_um=width,
                begin_ext_um=width / 2.0,
                end_ext_um=width / 2.0,
                round_ends=False,
            )
            primary_paths += 1
        for path in route.get("bridge_paths", []):
            if len(path) < 2:
                continue
            client.shape_insert_path(
                cell,
                layer=bridge_l,
                datatype=bridge_d,
                points_um=path,
                width_um=width,
                begin_ext_um=width / 2.0,
                end_ext_um=width / 2.0,
                round_ends=False,
            )
            bridge_paths += 1
        via_size = max(width, 1.0)
        for x, y in route.get("vias", []):
            client.shape_insert_box(
                cell,
                layer=via_l,
                datatype=via_d,
                bbox_um=[x - via_size / 2.0, y - via_size / 2.0, x + via_size / 2.0, y + via_size / 2.0],
            )
            vias += 1
    return {
        "cell": cell,
        "route_layer": route_layer,
        "bridge_layer": bridge_layer,
        "via_layer": via_layer,
        "deleted": deleted,
        "primary_paths": primary_paths,
        "bridge_paths": bridge_paths,
        "vias": vias,
    }


def route_multilayer_escape_cell(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    route_layer: str = "12/0",
    bridge_layer: str = "13/0",
    via_layer: str = "14/0",
    spacing_um: float = 8.0,
    clear: bool = True,
    obstacle_layers: Sequence[str] | None = (),
) -> dict:
    """Route pairwise nets by escaping around primary-layer walls on a bridge layer."""
    ports = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"}).get("ports", [])
    pairs = _pair_ports_by_net_tokens(ports)
    obstacle_layers = list(obstacle_layers or [])
    obstacle_bboxes = collect_obstacle_bboxes(client, cell, obstacle_layers)
    routes = [
        _plan_escape_route(
            pair,
            obstacle_bboxes=obstacle_bboxes,
            spacing_um=spacing_um,
            route_layer=route_layer,
            bridge_layer=bridge_layer,
        )
        for pair in sorted(pairs, key=lambda p: _net_sort_key(str(p.get("net") or "")))
    ]
    obstacle_hits = []
    for route in routes:
        width = float(route.get("width_um", 1.0) or 1.0)
        for path in route.get("primary_paths", []):
            for hit in route_hits_bboxes(path, obstacle_bboxes, width):
                obstacle_hits.append({**hit, "route_id": route.get("route_id"), "net": route.get("net")})
    ok = not obstacle_hits
    write = None
    if ok:
        write = _write_multilayer_routes(
            client,
            cell,
            routes,
            route_layer=route_layer,
            bridge_layer=bridge_layer,
            via_layer=via_layer,
            clear=clear,
        )
    return {
        "ok": ok,
        "backend": "multilayer_escape_cell",
        "cell": cell,
        "port_count": len(ports),
        "pair_count": len(pairs),
        "route_count": len(routes),
        "route_layer": route_layer,
        "bridge_layer": bridge_layer,
        "via_layer": via_layer,
        "obstacle_layers": obstacle_layers,
        "obstacle_bboxes": obstacle_bboxes,
        "obstacle_hits": obstacle_hits,
        "errors": ["primary route hits obstacle"] if obstacle_hits else [],
        "routes": routes,
        "write": write,
    }
