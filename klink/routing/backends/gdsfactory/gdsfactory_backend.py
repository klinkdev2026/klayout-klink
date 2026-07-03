"""Optional gdsfactory routing backend.

klink Port dicts -> ``gf.Port`` -> one gdsfactory routing strategy -> klink
route dicts. Each strategy is its own function with ONLY its own parameters;
``route_bundle_with_gdsfactory`` is the dispatching entry point and validates
the given kwargs against the chosen strategy BEFORE calling gdsfactory, so a
parameter the strategy cannot honor is an instructive error, never silently
ignored.

Strategy map (mirrors ``gf.routing``):

===========  ==================================================  ============
router       gdsfactory function                                 pairing
===========  ==================================================  ============
bundle       route_bundle (Manhattan river routing)              group
electrical   route_bundle with electrical defaults               group
sbend        route_bundle_sbend (smooth S-transitions)           group
all_angle    route_bundle_all_angle (non-Manhattan)              group
single       route_single (one Manhattan route per pair)         per pair
dubins       route_dubins (arc-based, any heading)               per pair
astar        route_astar (grid A*, avoids obstacle geometry)     per pair
===========  ==================================================  ============

`astar` is the only strategy that ROUTES AROUND obstacles: pass
``obstacle_bboxes_um`` and they are drawn on the reserved scratch layer
1000/0 inside the routing component (1000/0 never reaches KLayout — the
polygon writeback excludes it). kfactory's ``bboxes`` on route_bundle are NOT
avoidance (escape-length heuristics only; proven to wrap collinear chains
into loops), so klink does not expose them.
"""

from __future__ import annotations

from typing import Any, Sequence

from klink.routing.geom.port import port_to_gf

#: Scratch layer for obstacle polygons inside the routing component. Reserved
#: by klink's gf bridge (like 999/99 port markers); excluded from writeback.
OBSTACLE_LAYER = (1000, 0)


def _parse_layer(layer: str | tuple[int, int]) -> tuple[int, int]:
    if isinstance(layer, tuple):
        return (int(layer[0]), int(layer[1]))
    parts = str(layer).split("/")
    if len(parts) == 1:
        return (int(parts[0]), 0)
    return (int(parts[0]), int(parts[1]))


def _load_gdsfactory():
    try:
        import gdsfactory as gf
    except ImportError as exc:
        import sys

        # Name the EXACT interpreter so it lands in the one running klink/klink-mcp
        # (venvs / multiple Pythons make a bare "pip install" easy to misdirect).
        raise RuntimeError(
            "gdsfactory is not installed in this Python environment. "
            f'Install it into THIS interpreter: "{sys.executable}" -m pip '
            "install gdsfactory"
        ) from exc

    try:
        gf.get_active_pdk()
    except Exception:
        gf.gpdk.PDK.activate()
    return gf


def _gf_port(port):
    if hasattr(port, "orientation") and hasattr(port, "center"):
        return port
    gf_port = port_to_gf(port)
    if gf_port is None:
        raise RuntimeError("failed to convert klink port to gf.Port")
    return gf_port


def _route_points_um(route, dbu: float) -> list[list[float]]:
    """Extract the route backbone/centerline in um from any gf route type.

    Manhattan routes carry integer dbu Points; all-angle/dubins routes carry
    float um DPoints — the coordinate type decides the scaling.
    """
    backbone = getattr(route, "backbone", None)
    if backbone:
        scale = dbu if isinstance(backbone[0].x, int) else 1.0
        return [[float(p.x) * scale, float(p.y) * scale] for p in backbone]
    points = getattr(route, "points", None)
    if points is not None:
        out = []
        for p in points:
            if hasattr(p, "x"):
                out.append([float(p.x), float(p.y)])
            else:
                out.append([float(p[0]), float(p[1])])
        return out
    return []


def component_polygons_to_shape_items(
    component,
    *,
    dbu: float | None = None,
    layer_map: dict[str | tuple[int, int], str | tuple[int, int]] | None = None,
    include_layers: set[str | tuple[int, int]] | None = None,
    exclude_layers: set[str | tuple[int, int]] | None = None,
) -> list[dict]:
    """Flatten a gdsfactory component into batch ``shape.insert_many`` items.

    This preserves gdsfactory-generated curved bends as polygons while avoiding
    an intermediate KLayout tab / recorder pass.
    """

    gf = _load_gdsfactory()
    if getattr(component, "vinsts", None):
        component.insert_vinsts()
    scale = float(dbu) if dbu is not None else float(gf.kcl.dbu)
    items: list[dict] = []
    polygons_by_layer = component.get_polygons(by="tuple", merge=False)
    remap = layer_map or {}
    for layer_key, polygons in polygons_by_layer.items():
        layer_name = "%d/%d" % _parse_layer(layer_key)
        if include_layers is not None and layer_key not in include_layers and layer_name not in include_layers:
            continue
        if exclude_layers is not None and (layer_key in exclude_layers or layer_name in exclude_layers):
            continue
        out_layer = remap.get(layer_key, remap.get(layer_name, layer_key))
        layer, datatype = _parse_layer(out_layer)
        for polygon in polygons:
            points = [
                [float(point.x) * scale, float(point.y) * scale]
                for point in polygon.each_point_hull()
            ]
            if len(points) >= 3:
                items.append(
                    {
                        "kind": "polygon",
                        "layer": layer,
                        "datatype": datatype,
                        "points_um": points,
                    }
                )
    return items


def _width_um(port_dicts: Sequence[dict], fallback: float = 0.5) -> float:
    widths = [float(p.get("width_um", 0.0)) for p in port_dicts if isinstance(p, dict) and p.get("width_um")]
    return min(widths) if widths else fallback


# --------------------------------------------------------------------------- #
# shared context for the strategy functions
# --------------------------------------------------------------------------- #

class _RouteCtx:
    """Everything a strategy function needs, prepared once."""

    def __init__(self, ports1, ports2, layer, component_name, route_width_um):
        self.gf = _load_gdsfactory()
        self.raw1 = list(ports1)
        self.raw2 = list(ports2)
        self.ports1 = [_gf_port(p) for p in self.raw1]
        self.ports2 = [_gf_port(p) for p in self.raw2]
        if not self.ports1 or not self.ports2:
            raise ValueError("ports1 and ports2 must both contain at least one port")
        if len(self.ports1) != len(self.ports2):
            raise ValueError(
                "ports1 and ports2 differ in length: %d vs %d"
                % (len(self.ports1), len(self.ports2)))
        self.layer = _parse_layer(layer)
        self.width = (float(route_width_um) if route_width_um is not None
                      else _width_um(self.raw1 + self.raw2))
        self.component = self.gf.Component(component_name)
        self.dbu = float(self.gf.kcl.dbu)

    def waypoints(self, waypoints_um):
        if not waypoints_um:
            return None
        return [self.gf.kdb.DPoint(float(p[0]), float(p[1])) for p in waypoints_um]

    def draw_obstacles(self, obstacle_bboxes_um) -> None:
        """Place obstacle rectangles as INSTANCES on scratch layer 1000/0.

        route_astar (called without avoid_layers) treats the bbox of every
        instance in the component as an obstacle — including the route
        instances added for earlier pairs. Plain polygons would be ignored,
        so each obstacle is a one-rectangle sub-component reference.
        """
        if not obstacle_bboxes_um:
            return
        for index, bbox in enumerate(obstacle_bboxes_um):
            x0, y0, x1, y1 = (float(v) for v in bbox)
            block = self.gf.Component(
                "%s_obst_%d" % (self.component.name, index))
            block.add_polygon(
                [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer=OBSTACLE_LAYER)
            self.component.add_ref(block)

    def report(self, gf_routes, router_name: str) -> dict:
        routes = []
        for index, route in enumerate(gf_routes):
            source = self.ports1[index] if index < len(self.ports1) else self.ports1[0]
            target = self.ports2[index] if index < len(self.ports2) else self.ports2[0]
            points = _route_points_um(route, self.dbu)
            if not points:
                # S-bend routes carry no backbone; the straight chord between
                # the two ports is the honest stand-in for crossing checks.
                points = [
                    [float(source.center[0]), float(source.center[1])],
                    [float(target.center[0]), float(target.center[1])],
                ]
            # Route objects disagree on the unit (dbu vs um) and presence of
            # `length`; the backbone polyline is the one honest measure.
            length = sum(
                ((points[i + 1][0] - points[i][0]) ** 2
                 + (points[i + 1][1] - points[i][1]) ** 2) ** 0.5
                for i in range(len(points) - 1)
            )
            routes.append(
                {
                    "route_id": "gf_route_%d" % index,
                    "backend": "gdsfactory.%s" % router_name,
                    "source": getattr(source, "name", ""),
                    "target": getattr(target, "name", ""),
                    "layer": "%d/%d" % self.layer,
                    "width_um": self.width,
                    "points_um": points,
                    "length_um": length,
                }
            )
        return {
            "ok": True,
            "backend": "gdsfactory.%s" % router_name,
            "routes": routes,
            "gf_component": self.component,
        }


def _mismatch_kwargs() -> dict:
    return {
        "allow_width_mismatch": True,
        "allow_layer_mismatch": True,
        "allow_type_mismatch": True,
    }


# --------------------------------------------------------------------------- #
# strategy functions — each accepts ONLY what its gf router honors
# --------------------------------------------------------------------------- #

def route_gf_bundle(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    cross_section: str | object | None = None,
    route_width_um: float | None = None,
    separation_um: float = 3.0,
    radius_um: float | None = None,
    sort_ports: bool = False,
    start_straight_um: float | None = None,
    end_straight_um: float | None = None,
    waypoints_um: Sequence[Sequence[float]] | None = None,
    steps: Sequence[dict] | None = None,
    sbend_fallback: bool = False,
    auto_taper: bool = False,
    auto_taper_taper=None,
    taper=None,
    min_straight_taper_um: float = 100.0,
    collision_check_layers: Sequence[str] | None = None,
    path_length_match: dict | None = None,
    port_type: str | None = None,
    component_name: str = "klink_gf_route",
) -> dict:
    """Manhattan river routing (`gf.routing.route_bundle`).

    The bundle keeps `separation_um` between its member routes; all starts
    must share one heading and all targets one heading (the caller partitions
    by angle). `path_length_match` maps to gf's PathLengthConfig, e.g.
    ``{"extra_length": 40.0, "nb_loops": 1}``.
    """
    ctx = _RouteCtx(ports1, ports2, layer, component_name, route_width_um)
    gf = ctx.gf
    kwargs: dict[str, Any] = {
        "cross_section": cross_section,
        "separation": float(separation_um),
        "sort_ports": sort_ports,
        "auto_taper": bool(auto_taper),
        "sbend": bool(sbend_fallback),
        **_mismatch_kwargs(),
    }
    if cross_section is None:
        kwargs["layer"] = ctx.layer
        kwargs["route_width"] = ctx.width
    if radius_um is not None:
        kwargs["radius"] = float(radius_um)
    if start_straight_um is not None:
        kwargs["start_straight_length"] = float(start_straight_um)
    if end_straight_um is not None:
        kwargs["end_straight_length"] = float(end_straight_um)
    if auto_taper_taper is not None:
        kwargs["auto_taper_taper"] = auto_taper_taper
    if taper is not None:
        kwargs["taper"] = taper
        kwargs["min_straight_taper"] = float(min_straight_taper_um)
    if collision_check_layers:
        kwargs["collision_check_layers"] = [
            _parse_layer(l) for l in collision_check_layers]
        kwargs["on_collision"] = "error"
    if path_length_match:
        from gdsfactory.routing import PathLengthConfig
        kwargs["path_length_matching_config"] = PathLengthConfig(**path_length_match)
    if port_type is not None:
        kwargs["port_type"] = port_type
    gf_routes = gf.routing.route_bundle(
        component=ctx.component,
        ports1=ctx.ports1,
        ports2=ctx.ports2,
        waypoints=ctx.waypoints(waypoints_um),
        steps=steps,
        **kwargs,
    )
    return ctx.report(gf_routes, "bundle")


def route_gf_electrical(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    cross_section: str | object | None = None,
    route_width_um: float | None = None,
    separation_um: float = 10.0,
    sort_ports: bool = False,
    start_straight_um: float | None = None,
    end_straight_um: float | None = None,
    waypoints_um: Sequence[Sequence[float]] | None = None,
    steps: Sequence[dict] | None = None,
    component_name: str = "klink_gf_route",
) -> dict:
    """Electrical Manhattan bundle (sharp corners, metal defaults).

    Same engine as `bundle` but with electrical port typing; when no
    cross_section is given the route is drawn `route_width_um` wide on
    `layer` with 90-degree wire corners.
    """
    ctx = _RouteCtx(ports1, ports2, layer, component_name, route_width_um)
    gf = ctx.gf
    kwargs: dict[str, Any] = {
        "separation": float(separation_um),
        "sort_ports": sort_ports,
        "port_type": "electrical",
        **_mismatch_kwargs(),
    }
    if cross_section is not None:
        kwargs["cross_section"] = cross_section
    else:
        # klink-native path: draw `route_width` wide on `layer` with sharp
        # 90-degree wire corners (route_bundle rejects layer+cross_section
        # together, so no cross-section here).
        kwargs["cross_section"] = None
        kwargs["route_width"] = ctx.width
        kwargs["layer"] = ctx.layer
        kwargs["bend"] = "wire_corner"
    if start_straight_um is not None:
        kwargs["start_straight_length"] = float(start_straight_um)
    if end_straight_um is not None:
        kwargs["end_straight_length"] = float(end_straight_um)
    gf_routes = gf.routing.route_bundle(
        component=ctx.component,
        ports1=ctx.ports1,
        ports2=ctx.ports2,
        waypoints=ctx.waypoints(waypoints_um),
        steps=steps,
        **kwargs,
    )
    return ctx.report(gf_routes, "electrical")


def route_gf_single(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    cross_section: str | object | None = None,
    route_width_um: float | None = None,
    radius_um: float | None = None,
    start_straight_um: float | None = None,
    end_straight_um: float | None = None,
    waypoints_um: Sequence[Sequence[float]] | None = None,
    steps: Sequence[dict] | None = None,
    auto_taper: bool = False,
    component_name: str = "klink_gf_route",
) -> dict:
    """One independent Manhattan route per pair (`gf.routing.route_single`).

    No bundle spacing between the routes; waypoints/steps apply to EVERY
    pair, so give explicit waypoints only with a single pair.
    """
    ctx = _RouteCtx(ports1, ports2, layer, component_name, route_width_um)
    gf = ctx.gf
    if waypoints_um and len(ctx.ports1) > 1:
        raise ValueError(
            "waypoints_um with router='single' needs exactly one pair; route "
            "pairs one call at a time or use router='bundle' with waypoints")
    gf_routes = []
    for p1, p2 in zip(ctx.ports1, ctx.ports2):
        kwargs: dict[str, Any] = {
            "cross_section": cross_section,
            "auto_taper": bool(auto_taper),
            "allow_width_mismatch": True,
            # route_single's on_error="error" ALSO calls component.show()
            # (streams to klive on port 8082) before raising -- proven live,
            # that silently swaps the user's active KLayout tab out from
            # under a later call in the SAME run, which then fails with an
            # unrelated "no such cell" error. Leave on_error at its silent
            # default (None) and detect the failure ourselves below instead.
        }
        if cross_section is None:
            kwargs["layer"] = ctx.layer
            kwargs["route_width"] = ctx.width
        if radius_um is not None:
            kwargs["radius"] = float(radius_um)
        if start_straight_um is not None:
            kwargs["start_straight_length"] = float(start_straight_um)
        if end_straight_um is not None:
            kwargs["end_straight_length"] = float(end_straight_um)
        instances_before = len(ctx.component.insts)
        gf_routes.append(gf.routing.route_single(
            ctx.component, p1, p2,
            waypoints=ctx.waypoints(waypoints_um),
            steps=steps,
            **kwargs,
        ))
        # route_single's DEFAULT failure handling (on_error=None) swallows a
        # place_manhattan failure silently: it inserts a raw path directly on
        # CONF.layer_error_path (no bend/straight sub-instances at all) and
        # returns as if nothing happened. batch_polygons extraction excludes
        # that layer, so the caller would see "ok" with zero actual
        # geometry. A real success always places at least one bend/straight
        # instance for a route with 2+ waypoints; treat none as a failure.
        if (waypoints_um or steps) and len(ctx.component.insts) == instances_before:
            raise ValueError(
                "gdsfactory router 'single' could not place a valid Manhattan "
                "route through the given waypoints/steps (place_manhattan "
                "failed internally and fell back to an unusable error path). "
                "Fix the arrangement instead of shipping it: give the ports "
                "more clearance/bend room, adjust radius_um, or re-derive the "
                "waypoints.")
    return ctx.report(gf_routes, "single")


def route_gf_sbend(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    cross_section: str | object | None = None,
    route_width_um: float | None = None,
    sort_ports: bool = False,
    auto_taper: bool = False,
    component_name: str = "klink_gf_route",
) -> dict:
    """Smooth S-bend transitions (`gf.routing.route_bundle_sbend`).

    For laterally offset ports facing each other; no intermediate bends,
    no waypoint control.
    """
    ctx = _RouteCtx(ports1, ports2, layer, component_name, route_width_um)
    gf = ctx.gf
    gf_routes = gf.routing.route_bundle_sbend(
        ctx.component,
        ctx.ports1,
        ctx.ports2,
        sort_ports=sort_ports,
        enforce_port_ordering=False,
        auto_taper=bool(auto_taper),
        cross_section=cross_section or "strip",
        **_mismatch_kwargs(),
    )
    return ctx.report(gf_routes, "sbend")


def route_gf_all_angle(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    cross_section: str | object | None = None,
    route_width_um: float | None = None,
    separation_um: float = 3.0,
    backbone_um: Sequence[Sequence[float]] | None = None,
    component_name: str = "klink_gf_route",
) -> dict:
    """Non-Manhattan bundle (`gf.routing.route_bundle_all_angle`).

    Ports may face any direction; `backbone_um` optionally pins the shared
    spine the bundle follows.
    """
    ctx = _RouteCtx(ports1, ports2, layer, component_name, route_width_um)
    gf = ctx.gf
    kwargs: dict[str, Any] = {
        "separation": float(separation_um),
        "cross_section": cross_section or "strip",
    }
    if backbone_um:
        kwargs["backbone"] = [
            ctx.gf.kdb.DPoint(float(p[0]), float(p[1])) for p in backbone_um]
    gf_routes = gf.routing.route_bundle_all_angle(
        ctx.component, ctx.ports1, ctx.ports2, **kwargs)
    for route in gf_routes:
        for inst in getattr(route, "instances", []):
            ref = ctx.component.add_ref_off_grid(inst.cell)
            ref.trans = inst.trans
    ctx.component.insert_vinsts()
    return ctx.report(gf_routes, "all_angle")


def route_gf_dubins(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    cross_section: str | object | None = None,
    route_width_um: float | None = None,
    radius_um: float = 100.0,
    component_name: str = "klink_gf_route",
) -> dict:
    """Arc-based shortest path per pair (`gf.routing.route_dubins`).

    Connects arbitrary headings with circular arcs of `radius_um`; ideal
    when neither Manhattan nor S-bend geometry fits.
    """
    ctx = _RouteCtx(ports1, ports2, layer, component_name, route_width_um)
    gf = ctx.gf
    xs = cross_section or gf.cross_section.strip(
        width=ctx.width, radius=float(radius_um))
    gf_routes = []
    for p1, p2 in zip(ctx.ports1, ctx.ports2):
        gf_routes.append(gf.routing.route_dubins(
            ctx.component, port1=p1, port2=p2, cross_section=xs))
    return ctx.report(gf_routes, "dubins")


def route_gf_astar(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    cross_section: str | object | None = None,
    route_width_um: float | None = None,
    resolution_um: float = 1.0,
    obstacle_bboxes_um: Sequence[Sequence[float]] | None = None,
    distance_um: float = 1.0,
    component_name: str = "klink_gf_route",
) -> dict:
    """Grid A* per pair that ROUTES AROUND obstacles (`gf.routing.route_astar`).

    The only strategy with real obstacle avoidance. `obstacle_bboxes_um`
    become instances on klink's reserved scratch layer 1000/0 inside the
    routing component (excluded from writeback); the A* grid blocks the bbox
    of EVERY instance with `distance_um` clearance on a `resolution_um` grid,
    so later pairs also avoid the route instances of earlier pairs.
    """
    ctx = _RouteCtx(ports1, ports2, layer, component_name, route_width_um)
    gf = ctx.gf
    ctx.draw_obstacles(obstacle_bboxes_um)
    # route_astar spans its grid over the COMPONENT bbox only; ports outside
    # the obstacle geometry would fall off the grid and the "route" comes
    # back as a blind straight line. Two 0.1um corner-post instances stretch
    # the grid over ports + obstacles + margin.
    xs: list[float] = []
    ys: list[float] = []
    for port in ctx.ports1 + ctx.ports2:
        xs.append(float(port.center[0]))
        ys.append(float(port.center[1]))
    for bbox in (obstacle_bboxes_um or []):
        xs.extend((float(bbox[0]), float(bbox[2])))
        ys.extend((float(bbox[1]), float(bbox[3])))
    margin = max(10.0 * float(resolution_um), 20.0)
    for corner_i, (cx, cy) in enumerate(
            ((min(xs) - margin, min(ys) - margin),
             (max(xs) + margin, max(ys) + margin))):
        post = gf.Component("%s_post_%d" % (ctx.component.name, corner_i))
        post.add_polygon([(cx, cy), (cx + 0.1, cy), (cx + 0.1, cy + 0.1),
                          (cx, cy + 0.1)], layer=OBSTACLE_LAYER)
        ctx.component.add_ref(post)
    if cross_section is None:
        cross_section = gf.cross_section.strip(width=ctx.width)
    gf_routes = []
    for p1, p2 in zip(ctx.ports1, ctx.ports2):
        gf_routes.append(gf.routing.route_astar(
            component=ctx.component,
            port1=p1,
            port2=p2,
            resolution=float(resolution_um),
            distance=float(distance_um),
            cross_section=cross_section,
        ))
    report = ctx.report(gf_routes, "astar")

    # gf's route_astar is FRAGILE: when its internal waypoint rebuild fails
    # it silently returns a straight line through the wall (error candidates
    # win its fewest-bends contest). klink refuses to pretend: verify every
    # backbone against the very obstacles the caller declared.
    if obstacle_bboxes_um:
        from klink.routing.geom.geometry import route_hits_bboxes

        offenders = []
        for route in report["routes"]:
            hits = route_hits_bboxes(route.get("points_um", []),
                                     obstacle_bboxes_um,
                                     float(route.get("width_um", 0.5)))
            if hits:
                offenders.append("%s->%s" % (route.get("source"), route.get("target")))
        if offenders:
            raise ValueError(
                "gf route_astar failed to avoid the declared obstacles for "
                "%s (a known gdsfactory fragility: failed rebuilds return a "
                "blind straight). Options: enlarge the gap relative to the "
                "bend radius, re-place the components, or use klink's own "
                "obstacle-aware backends (routing.tapered_hybrid_cell / "
                "routing.damped_* with obstacle_layers)." % ", ".join(offenders))
    return report


# --------------------------------------------------------------------------- #
# registry + dispatching entry point
# --------------------------------------------------------------------------- #

#: router name -> (strategy function, pairing mode). Pairing mode drives the
#: caller's grouping: "angle_group" strategies need all starts to share one
#: heading and all targets one heading per call; "free" strategies take any
#: heading mix in a single call.
GF_ROUTERS: dict[str, dict] = {
    "bundle": {"fn": route_gf_bundle, "pairing": "angle_group"},
    "electrical": {"fn": route_gf_electrical, "pairing": "angle_group"},
    "sbend": {"fn": route_gf_sbend, "pairing": "angle_group"},
    "all_angle": {"fn": route_gf_all_angle, "pairing": "free"},
    "single": {"fn": route_gf_single, "pairing": "free"},
    "dubins": {"fn": route_gf_dubins, "pairing": "free"},
    "astar": {"fn": route_gf_astar, "pairing": "free"},
}

#: kwargs every strategy accepts (handled by _RouteCtx / dispatcher).
_COMMON_PARAMS = {"layer", "cross_section", "route_width_um", "component_name"}


def router_params(router: str) -> set[str]:
    """Parameter names the strategy function accepts (beyond common ones)."""
    import inspect

    spec = GF_ROUTERS.get(_normalize_router(router))
    if spec is None:
        return set()
    return {
        name
        for name in inspect.signature(spec["fn"]).parameters
        if name not in ("ports1", "ports2")
    }


def _normalize_router(router: str | None) -> str:
    name = str(router or "bundle").replace("-", "_").lower()
    return {"allangle": "all_angle"}.get(name, name)


def route_bundle_with_gdsfactory(
    ports1: Sequence[dict],
    ports2: Sequence[dict],
    *,
    layer: str | tuple[int, int] = "1/0",
    router: str = "bundle",
    component_name: str = "klink_gdsfactory_route",
    **kwargs,
) -> dict:
    """Route two port groups with one named gdsfactory strategy.

    Validates `kwargs` against the strategy's real parameter set first: a
    parameter the strategy cannot honor raises with the list of parameters
    it does honor (and the strategies that would honor the rejected one).

    gdsfactory's error handling is neutralized for the duration of the call:
    its defaults (CONF.on_collision/on_placer_error = "show_error") push a
    debug OASIS into the user's LIVE KLayout via klive on every failure and
    then silently substitute a straight "error path" on CONF.layer_error_path
    — klink turns both into ONE loud, instructive exception instead.
    """
    import warnings as _warnings

    name = _normalize_router(router)
    spec = GF_ROUTERS.get(name)
    if spec is None:
        raise ValueError(
            "unknown gdsfactory router %r; available: %s"
            % (router, ", ".join(sorted(GF_ROUTERS))))

    allowed = router_params(name)
    rejected = sorted(k for k in kwargs if k not in allowed)
    if rejected:
        hints = []
        for key in rejected:
            takers = sorted(r for r in GF_ROUTERS if key in router_params(r))
            hints.append("%s (honored by: %s)" % (key, ", ".join(takers) or "no router"))
        raise ValueError(
            "router %r does not honor parameter(s): %s. It honors: %s"
            % (name, "; ".join(hints),
               ", ".join(sorted(allowed - _COMMON_PARAMS)) or "(none beyond common)"))

    gf = _load_gdsfactory()
    saved = (gf.CONF.on_collision, gf.CONF.on_placer_error)
    gf.CONF.on_collision = "error"
    gf.CONF.on_placer_error = "error"
    try:
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            report = spec["fn"](
                ports1, ports2,
                layer=layer,
                component_name=component_name,
                **kwargs,
            )
    finally:
        gf.CONF.on_collision, gf.CONF.on_placer_error = saved

    failures = [str(w.message) for w in caught
                if "routing failed" in str(w.message).lower()
                or "collision" in str(w.message).lower()]
    if failures:
        raise ValueError(
            "gdsfactory router %r could not build valid geometry and fell "
            "back to an error path (%s). Fix the arrangement instead of "
            "shipping it: give the ports more clearance/bend room, adjust "
            "separation_um/radius_um, pin the path with waypoints_um/steps, "
            "or pick another router." % (name, "; ".join(failures[:3])))
    return report
