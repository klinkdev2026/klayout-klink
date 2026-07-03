"""klink routing — lazy re-exports (PEP 562).

Importing ``klink.routing`` (or any single backend such as
``klink.routing.backends.flexdr.flexdr``) no longer eagerly loads every routing
backend. The public names below remain available as package attributes (PEP 562
``__getattr__``), but accessing one imports only its owning submodule on first use.

This is the decoupling invariant in action: *runtime closure == functional
closure*. A bare ``import klink.routing.backends.flexdr.flexdr`` must not drag the
gdsfactory / damped / steiner / global_channel families. See
``docs/DEMO_DEPENDENCY_MAP.md`` and the guard test
``tests/unit/test_import_closure.py``.

To add a backend export: add its name under the owning submodule in
``_EXPORTS``. Do not re-introduce eager ``from .x import y`` at module top —
that is exactly the coupling this file removed.
"""

from importlib import import_module

# Public name -> owning submodule (new layered dotted path). The shims at the
# old flat paths were removed; accessing a name lazily imports its real module.
_EXPORTS: dict[str, tuple[str, ...]] = {
    "core.intent": (
        "anchor_applies_to_net", "anchors_for_net", "anchors_for_any_net",
        "build_route_intent", "collect_route_intent", "global_route_anchors",
    ),
    "geom.constraints": (
        "orientation_vector", "port_launch_point", "port_launch_width",
        "route_with_port_launch_stubs",
    ),
    "geom.port": ("match_ports", "sort_ports_clockwise", "port_to_gf", "ports_to_gf"),
    "geom.planner": (
        "collect_obstacle_bboxes", "plan_routes_for_cell", "plan_routes_from_intent",
    ),
    "geom.geometric": (
        "expand_obstacles_for_route", "route_many_two_port_geometric",
        "route_points_geometric", "route_segment_bboxes", "route_two_port_geometric",
    ),
    "geom.run": ("route_cell",),
    "core.validation": ("validate_route_intent",),
    "geom.writeback": ("clear_route_layer", "commit_routes"),
    "backends.geometric.tapered": (
        "build_trapezoid_polygon", "commit_tapered_routes", "compute_taper_ratio",
        "compute_tapered_widths", "polygon_hits_bboxes", "route_tapered",
        "strategy_back_load", "strategy_front_load", "strategy_uniform",
        "validate_tapered_route",
    ),
    "backends.geometric.tapered_segments": (
        "commit_tapered_hybrid", "commit_tapered_hybrid_many",
        "commit_tapered_segments", "compute_segment_widths",
        "route_tapered_hybrid_cell", "route_tapered_hybrid",
        "route_tapered_hybrid_many", "route_tapered_segments",
    ),
    "backends.geometric.steiner": ("plan_rectilinear_steiner_tree", "route_steiner_cell"),
    "backends.geometric.damped": (
        "route_damped_polygon_cell", "route_damped_segment_cell",
        "route_damped_steiner_cell",
    ),
    "backends.geometric.global_channel": (
        "assign_corridors_by_capacity", "pair_ports_with_obstacle_cost",
        "route_global_channel_cell",
    ),
    "backends.geometric.multilayer": ("route_multilayer_escape_cell",),
    "backends.gdsfactory.gdsfactory_components": (
        "gdsfactory_component_marker_to_shapes_and_ports", "place_gdsfactory_components",
    ),
    "backends.gdsfactory.gdsfactory_backend": (
        "component_polygons_to_shape_items", "route_bundle_with_gdsfactory",
    ),
    "backends.gdsfactory.gdsfactory_ports": ("route_gdsfactory_ports", "select_gdsfactory_port_groups"),
}

_NAME_TO_MOD = {name: mod for mod, names in _EXPORTS.items() for name in names}
__all__ = sorted(_NAME_TO_MOD)


def __getattr__(name: str):
    mod = _NAME_TO_MOD.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{mod}"), name)
    globals()[name] = value  # cache so __getattr__ runs at most once per name
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
