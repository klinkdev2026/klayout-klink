"""routing.* local MCP tool handlers.

Tapered hybrid/polygon, Steiner, damped (segment/polygon/steiner), global
channel, multilayer escape, and the optional gdsfactory route_bundle backend.
Handlers are functions (ctx, arguments); they route over the primary client
(ctx._client) and compact large router payloads via the shared helpers.
"""

from __future__ import annotations

from ..results import _error_result, _json_result
from . import local_tool
from ._helpers import (
    _compact_gdsfactory_route_result,
    _compact_multilayer_result,
    _compact_polygon_route_result,
    _compact_route_result,
    _compact_steiner_result,
    _gdsfactory_unavailable_message,
)


def _damped_tool_schema(*, include_corner: bool, include_root: bool) -> dict:
    properties = {
        "cell": {"type": "string", "description": "Cell name to route."},
        "port_layer": {"type": "string", "default": "999/99"},
        "anchor_layer": {"type": "string", "default": "999/1"},
        "spacing_um": {"type": "number", "default": 20.0},
        "angle_mode": {
            "type": "string",
            "enum": ["any", "manhattan", "fortyfive"],
            "default": "manhattan",
        },
        "damping_distance_um": {
            "type": "number",
            "default": 10.0,
            "description": "Extra soft-clearance distance from obstacle layers.",
        },
        "clear": {"type": "boolean", "default": True},
        "obstacle_layers": {
            "type": "array",
            "items": {"type": "string"},
        },
    }
    if include_corner:
        properties["route_layer"] = {"type": "string"}
        properties["corner_style"] = {"type": "string", "enum": ["miter", "bevel", "round"], "default": "miter"}
    if include_root:
        properties["route_layer"] = {"type": "string"}
        properties["root_ports"] = {
            "type": "object",
            "additionalProperties": {"type": "string"},
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["cell"],
        "additionalProperties": False,
    }


@local_tool(
    "routing.tapered_hybrid_cell",
    "Route one KLayout cell using klink's tapered hybrid cell router. Reads Port/Anchor PCells, plans routes, validates, and writes results. Pass obstacle_layers=[...] with YOUR design's keepout layers (no default).",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Cell name to route."},
            "spacing_um": {"type": "number", "default": 20.0},
            "angle_mode": {
                "type": "string",
                "enum": ["any", "manhattan", "fortyfive"],
                "default": "any",
                "description": "Allowed segment directions: any straight segment, Manhattan only, or Manhattan plus 45-degree.",
            },
            "clear": {"type": "boolean", "default": True},
            "port_layer": {"type": "string", "default": "999/99"},
            "anchor_layer": {"type": "string", "default": "999/1"},
            "obstacle_layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keepout layers. Pass [] to disable obstacle handling.",
            },
        },
        "required": ["cell"],
        "additionalProperties": False,
    },
)
def _tool_routing_tapered_hybrid_cell(ctx, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    try:
        from ...routing.backends.geometric.tapered_segments import route_tapered_hybrid_cell

        result = route_tapered_hybrid_cell(
            ctx._client,
            str(arguments.get("cell") or ""),
            port_layer=str(arguments.get("port_layer") or "999/99"),
            anchor_layer=str(arguments.get("anchor_layer") or "999/1"),
            spacing_um=float(arguments.get("spacing_um", 20.0)),
            angle_mode=str(arguments.get("angle_mode") or "any"),
            clear=bool(arguments.get("clear", True)),
            obstacle_layers=list(arguments.get("obstacle_layers", [])),
        )
        return _json_result(_compact_route_result(result))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "routing.tapered_polygon_cell",
    "Route one KLayout cell using klink's continuous tapered polygon backend. Supports the same Port/Anchor semantics as hybrid routing, but writes continuous taper polygons.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Cell name to route."},
            "spacing_um": {"type": "number", "default": 20.0},
            "angle_mode": {
                "type": "string",
                "enum": ["any", "manhattan", "fortyfive"],
                "default": "any",
                "description": "Allowed segment directions: any straight segment, Manhattan only, or Manhattan plus 45-degree.",
            },
            "clear": {"type": "boolean", "default": True},
            "port_layer": {"type": "string", "default": "999/99"},
            "anchor_layer": {"type": "string", "default": "999/1"},
            "route_layer": {"type": "string", "description": "Override all output routes to this layer."},
            "corner_style": {
                "type": "string",
                "enum": ["miter", "bevel", "round"],
                "default": "miter",
            },
            "obstacle_layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keepout layers. Pass [] to disable obstacle validation.",
            },
        },
        "required": ["cell"],
        "additionalProperties": False,
    },
)
def _tool_routing_tapered_polygon_cell(ctx, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    try:
        from ...routing.backends.geometric.tapered import route_tapered_polygon_cell

        result = route_tapered_polygon_cell(
            ctx._client,
            str(arguments.get("cell") or ""),
            port_layer=str(arguments.get("port_layer") or "999/99"),
            anchor_layer=str(arguments.get("anchor_layer") or "999/1"),
            route_layer=arguments.get("route_layer"),
            spacing_um=float(arguments.get("spacing_um", 20.0)),
            angle_mode=str(arguments.get("angle_mode") or "any"),
            corner_style=str(arguments.get("corner_style") or "miter"),
            clear=bool(arguments.get("clear", True)),
            obstacle_layers=list(arguments.get("obstacle_layers", [])),
        )
        return _json_result(_compact_polygon_route_result(result))
    except Exception as exc:
        return _error_result(str(exc))


#: routing.gdsfactory_ports kwargs forwarded ONLY when the caller sets them —
#: each strategy validates them against what it honors, so a foreign kwarg is
#: an instructive error, never silently ignored.
_GF_STRATEGY_KEYS = (
    "bundle_gather_um",
    "cross_section", "route_width_um", "separation_um", "radius_um",
    "sort_ports", "start_straight_um", "end_straight_um", "waypoints_um",
    "steps", "sbend_fallback", "auto_taper", "taper", "min_straight_taper_um",
    "collision_check_layers", "path_length_match", "backbone_um",
    "resolution_um", "obstacle_bboxes_um", "distance_um",
)


@local_tool(
    "routing.gdsfactory_ports",
    "Route KLayout Port markers with a named gdsfactory routing strategy. "
    "router: bundle=Manhattan river routing with separation (DEFAULT; also "
    "honors waypoints/steps, radius_um, start/end_straight_um, "
    "path_length_match, collision_check_layers); electrical=bundle with metal "
    "defaults and sharp corners; sbend=smooth S-transitions for offset "
    "facing ports; all_angle=non-Manhattan bundle (optional backbone_um "
    "spine); single=independent Manhattan route per pair; dubins=arc-based "
    "any-heading per pair; astar=EXPERIMENTAL grid A* per pair around "
    "obstacle_bboxes_um (+resolution_um/distance_um) — gf's astar is "
    "fragile, so klink verifies the result and ERRORS instead of returning "
    "a wall-crossing route; for reliable obstacle avoidance prefer klink's "
    "own routing.tapered_hybrid_cell / routing.damped_* with "
    "obstacle_layers. A parameter the chosen router cannot honor returns "
    "an error naming the routers that honor it. Requires gdsfactory in the "
    "MCP interpreter.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Cell name to route."},
            "port_layer": {"type": "string", "default": "999/99"},
            "route_layer": {"type": "string", "description": "layer to route on 'L/D' for YOUR process (required; klink ships no default)."},
            "output_mode": {
                "type": "string",
                "enum": ["batch_polygons", "klink_paths", "dry_run"],
                "default": "batch_polygons",
            },
            "clear": {"type": "boolean", "default": True},
            "allow_crossing": {"type": "boolean", "default": False},
            "gf_route_layer": {
                "type": "string",
                "description": "Temporary gdsfactory layer, remapped to route_layer on writeback.",
            },
            "router": {
                "type": "string",
                "enum": ["bundle", "electrical", "sbend", "all_angle", "single", "dubins", "astar"],
                "default": "bundle",
            },
            "cross_section": {
                "type": ["string", "null"],
                "description": "gdsfactory cross_section spec such as strip or metal_routing. Null uses explicit layer/width.",
            },
            "route_width_um": {"type": "number", "description": "Route width; default = matched port width."},
            "separation_um": {"type": "number", "description": "Spacing inside one bundle (bundle/electrical/all_angle)."},
            "bundle_gather_um": {"type": "number", "description": "bundle family: nets share one bundle only when sources AND targets are mutually within this distance (default 30um); unrelated chain stages route separately."},
            "radius_um": {"type": "number", "description": "Bend radius (bundle/single) or arc radius (dubins)."},
            "sort_ports": {"type": "boolean"},
            "start_straight_um": {"type": "number", "description": "Straight length leaving each start port (bundle/electrical/single)."},
            "end_straight_um": {"type": "number", "description": "Straight length entering each target port (bundle/electrical/single)."},
            "auto_taper": {"type": "boolean", "description": "Width transitions at ports (bundle/sbend/single)."},
            "taper": {
                "type": "string",
                "description": "Optional gdsfactory taper component spec (bundle only).",
            },
            "min_straight_taper_um": {"type": "number"},
            "sbend_fallback": {"type": "boolean", "description": "bundle only: allow S-bends where straights do not fit."},
            "collision_check_layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "bundle only: gf errors if a route collides with geometry on these 'L/D' layers of the routing component.",
            },
            "path_length_match": {
                "type": "object",
                "description": "bundle only: gf PathLengthConfig kwargs, e.g. {\"extra_length\": 40.0, \"nb_loops\": 1} to length-match the bundle.",
            },
            "backbone_um": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "description": "all_angle only: shared spine the bundle follows.",
            },
            "resolution_um": {"type": "number", "description": "astar only: routing grid step."},
            "distance_um": {"type": "number", "description": "astar only: clearance kept from obstacles."},
            "obstacle_bboxes_um": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                "description": "astar only: obstacle rectangles [x0,y0,x1,y1] um the route must go around (e.g. placed component bboxes from instance.query).",
            },
            "waypoints_um": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                },
            },
            "source": {"type": "array", "items": {"type": "string"}},
            "target": {"type": "array", "items": {"type": "string"}},
            "source_prefix": {"type": "string"},
            "target_prefix": {"type": "string"},
            "source_orientation": {"type": "number"},
            "target_orientation": {"type": "number"},
            "net": {"type": "string"},
            "all_two_port_nets": {"type": "boolean", "default": False},
            "pair_by": {"type": "string", "enum": ["net", "axis", "name"], "default": "axis"},
        },
        "required": ["cell", "route_layer"],
        "additionalProperties": False,
    },
)
def _tool_routing_gdsfactory_ports(ctx, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    if not arguments.get("route_layer"):
        return _error_result(
            "routing.gdsfactory_ports needs route_layer ('L/D', the layer to "
            "route on) for YOUR process; klink ships no default.")
    try:
        from ...routing.backends.gdsfactory.gdsfactory_ports import route_gdsfactory_ports

        selection = {
            key: arguments[key]
            for key in (
                "source",
                "target",
                "source_prefix",
                "target_prefix",
                "source_orientation",
                "target_orientation",
                "net",
                "all_two_port_nets",
                "pair_by",
            )
            if key in arguments
        }
        strategy_kwargs = {
            key: arguments[key] for key in _GF_STRATEGY_KEYS if key in arguments
        }
        result = route_gdsfactory_ports(
            ctx._client,
            str(arguments.get("cell") or ""),
            port_layer=str(arguments.get("port_layer") or "999/99"),
            route_layer=str(arguments["route_layer"]),
            gf_route_layer=arguments.get("gf_route_layer"),
            output_mode=str(arguments.get("output_mode") or "batch_polygons"),
            clear=bool(arguments.get("clear", True)),
            allow_crossing=bool(arguments.get("allow_crossing", False)),
            router=str(arguments.get("router") or "bundle"),
            **strategy_kwargs,
            **selection,
        )
        return _json_result(_compact_gdsfactory_route_result(result))
    except RuntimeError as exc:
        return _error_result(_gdsfactory_unavailable_message(str(exc)))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "routing.steiner_cell",
    "Route multi-terminal nets in one KLayout cell using klink's rectilinear Steiner/bus tree router. Use for nets with more than two ports.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Cell name to route."},
            "port_layer": {"type": "string", "default": "999/99"},
            "anchor_layer": {"type": "string", "default": "999/1"},
            "route_layer": {"type": "string", "description": "Override all output routes to this layer."},
            "root_ports": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional mapping from net name to root port name.",
            },
            "clear": {"type": "boolean", "default": True},
            "obstacle_layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keepout layers for validation. This first Steiner backend reports hits rather than rerouting around them.",
            },
        },
        "required": ["cell"],
        "additionalProperties": False,
    },
)
def _tool_routing_steiner_cell(ctx, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    try:
        from ...routing.backends.geometric.steiner import route_steiner_cell

        result = route_steiner_cell(
            ctx._client,
            str(arguments.get("cell") or ""),
            port_layer=str(arguments.get("port_layer") or "999/99"),
            anchor_layer=str(arguments.get("anchor_layer") or "999/1"),
            route_layer=arguments.get("route_layer"),
            root_ports=dict(arguments.get("root_ports") or {}),
            clear=bool(arguments.get("clear", True)),
            obstacle_layers=list(arguments.get("obstacle_layers", [])),
        )
        return _json_result(_compact_steiner_result(result))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "routing.damped_segment_cell",
    "Route one KLayout cell with the explicit damped segment backend. Uses tapered hybrid output and keeps extra distance from obstacles.",
    _damped_tool_schema(include_corner=False, include_root=False),
)
def _tool_routing_damped_segment_cell(ctx, arguments: dict) -> dict:
    return _call_damped_tool(ctx, "segment", arguments)


@local_tool(
    "routing.damped_polygon_cell",
    "Route one KLayout cell with the explicit damped polygon backend. Uses continuous taper polygons and keeps extra distance from obstacles.",
    _damped_tool_schema(include_corner=True, include_root=False),
)
def _tool_routing_damped_polygon_cell(ctx, arguments: dict) -> dict:
    return _call_damped_tool(ctx, "polygon", arguments)


@local_tool(
    "routing.damped_steiner_cell",
    "Route one KLayout cell with the explicit damped Steiner backend. Uses multi-terminal trunk/branch topology and damped obstacle clearance.",
    _damped_tool_schema(include_corner=False, include_root=True),
)
def _tool_routing_damped_steiner_cell(ctx, arguments: dict) -> dict:
    return _call_damped_tool(ctx, "steiner", arguments)


@local_tool(
    "routing.global_channel_cell",
    "Route one KLayout cell with the stronger global channel backend. It performs obstacle-aware candidate assignment and capacity-aware corridor assignment before using tapered hybrid geometry.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Cell name to route."},
            "port_layer": {"type": "string", "default": "999/99"},
            "anchor_layer": {"type": "string", "default": "999/1"},
            "spacing_um": {"type": "number", "default": 20.0},
            "angle_mode": {
                "type": "string",
                "enum": ["any", "manhattan", "fortyfive"],
                "default": "manhattan",
            },
            "safe_distance_um": {
                "type": "number",
                "default": 0.0,
                "description": "Extra obstacle clearance used during global cost estimation and final routing.",
            },
            "clear": {"type": "boolean", "default": True},
            "obstacle_layers": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["cell"],
        "additionalProperties": False,
    },
)
def _tool_routing_global_channel_cell(ctx, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    try:
        from ...routing.backends.geometric.global_channel import route_global_channel_cell

        result = route_global_channel_cell(
            ctx._client,
            str(arguments.get("cell") or ""),
            port_layer=str(arguments.get("port_layer") or "999/99"),
            anchor_layer=str(arguments.get("anchor_layer") or "999/1"),
            spacing_um=float(arguments.get("spacing_um", 20.0)),
            angle_mode=str(arguments.get("angle_mode") or "manhattan"),
            safe_distance_um=float(arguments.get("safe_distance_um", 0.0)),
            clear=bool(arguments.get("clear", True)),
            obstacle_layers=list(arguments.get("obstacle_layers", [])),
        )
        return _json_result(_compact_route_result(result))
    except Exception as exc:
        return _error_result(str(exc))


@local_tool(
    "routing.multilayer_escape_cell",
    "Route wall-blocked pairwise nets by using a primary route layer, bridge layer, and via boxes.",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "Cell name to route."},
            "port_layer": {"type": "string", "default": "999/99"},
            "route_layer": {"type": "string", "description": "primary route layer 'L/D' for YOUR process (required)."},
            "bridge_layer": {"type": "string", "description": "bridge/crossing layer 'L/D' for YOUR process (required)."},
            "via_layer": {"type": "string", "description": "via layer 'L/D' for YOUR process (required)."},
            "spacing_um": {"type": "number", "default": 8.0},
            "clear": {"type": "boolean", "default": True},
            "obstacle_layers": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["cell", "route_layer", "bridge_layer", "via_layer"],
        "additionalProperties": False,
    },
)
def _tool_routing_multilayer_escape_cell(ctx, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    if not arguments.get("route_layer") or not arguments.get("bridge_layer") \
            or not arguments.get("via_layer"):
        return _error_result(
            "routing.multilayer_escape_cell needs route_layer, bridge_layer, and "
            "via_layer ('L/D') for YOUR process; klink ships no default.")
    try:
        from ...routing.backends.geometric.multilayer import route_multilayer_escape_cell

        result = route_multilayer_escape_cell(
            ctx._client,
            str(arguments.get("cell") or ""),
            port_layer=str(arguments.get("port_layer") or "999/99"),
            route_layer=str(arguments["route_layer"]),
            bridge_layer=str(arguments["bridge_layer"]),
            via_layer=str(arguments["via_layer"]),
            spacing_um=float(arguments.get("spacing_um", 8.0)),
            clear=bool(arguments.get("clear", True)),
            obstacle_layers=list(arguments.get("obstacle_layers", [])),
        )
        return _json_result(_compact_multilayer_result(result))
    except Exception as exc:
        return _error_result(str(exc))


def _call_damped_tool(ctx, mode: str, arguments: dict) -> dict:
    if not ctx.ensure_connected() or ctx._client is None:
        return _error_result("not connected to klink: %s" % (ctx._last_error or "unknown error"))
    try:
        from ...routing.backends.geometric.damped import (
            route_damped_polygon_cell,
            route_damped_segment_cell,
            route_damped_steiner_cell,
        )

        common = {
            "port_layer": str(arguments.get("port_layer") or "999/99"),
            "anchor_layer": str(arguments.get("anchor_layer") or "999/1"),
            "angle_mode": str(arguments.get("angle_mode") or "manhattan"),
            "damping_distance_um": float(arguments.get("damping_distance_um", 10.0)),
            "clear": bool(arguments.get("clear", True)),
            "obstacle_layers": list(arguments.get("obstacle_layers", [])),
        }
        cell = str(arguments.get("cell") or "")
        if mode == "segment":
            result = route_damped_segment_cell(
                ctx._client,
                cell,
                spacing_um=float(arguments.get("spacing_um", 20.0)),
                **common,
            )
            return _json_result(_compact_route_result(result))
        if mode == "polygon":
            result = route_damped_polygon_cell(
                ctx._client,
                cell,
                route_layer=arguments.get("route_layer"),
                spacing_um=float(arguments.get("spacing_um", 20.0)),
                corner_style=str(arguments.get("corner_style") or "miter"),
                **common,
            )
            return _json_result(_compact_polygon_route_result(result))
        result = route_damped_steiner_cell(
            ctx._client,
            cell,
            route_layer=arguments.get("route_layer"),
            root_ports=dict(arguments.get("root_ports") or {}),
            **common,
        )
        return _json_result(_compact_steiner_result(result))
    except Exception as exc:
        return _error_result(str(exc))
