"""Import a user-written gdsfactory Component into klink's interactive loop.

The user writes ORDINARY gdsfactory code (place components, connect/route
them, maybe already `show()`-ed to KLayout). One call —
``import_gf_component(client, component)`` — takes that Component over into
klink's drag -> harvest -> reroute mechanism:

* device instances become REAL KLayout cells + instances (draggable units,
  batch RPC: one ``shape.insert_many`` per unique device cell + one
  ``instance.insert_many`` for all placements);
* routing instances (bends/straights/tapers made by ``gf.routing.*``) are
  NOT imported — their connectivity is collapsed into device-level nets and
  handed to klink's own routing, so the layout stays re-routable;
* each device cell's gf port table is persisted as a template in the net
  spec (state on disk, not in agent memory); ports are re-harvested from
  LIVE instance positions on every (re)route, so dragging in the GUI works;
* the collapsed device-to-device connectivity is persisted as the net table
  and (optionally) routed immediately with the gdsfactory backend.

Everything here is gf-generic MECHANISM: the only "knowledge" is gdsfactory's
own routing-component names (`DEFAULT_ROUTE_COMPONENTS`, overridable). No
process data — layers come from the user's component/PDK.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

#: gf.routing.* building blocks: instances of these factories are ROUTES,
#: not devices. Generic gdsfactory knowledge (not process data); override
#: via ``route_components=`` for exotic PDK route cells.
DEFAULT_ROUTE_COMPONENTS = frozenset({
    "straight", "straight_all_angle",
    "bend_euler", "bend_euler_all_angle",
    "bend_circular", "bend_circular_all_angle",
    "bend_s", "bezier", "wire_corner", "wire_corner45", "wire_straight",
    "taper", "taper_cross_section",
})


# --------------------------------------------------------------------------- #
# netlist digestion (pure)
# --------------------------------------------------------------------------- #

def split_gf_netlist(netlist: Mapping[str, Any],
                     route_components: frozenset[str] = DEFAULT_ROUTE_COMPONENTS,
                     ) -> tuple[dict[str, dict], list[tuple[tuple[str, str], tuple[str, str]]], list[str]]:
    """Split a gf netlist into device instances + collapsed device-level nets.

    Returns ``(device_instances, device_nets, problems)`` where
    ``device_instances`` maps netlist instance name -> its netlist entry,
    and each net is ``((inst, port), (inst, port))`` between DEVICES —
    chains through routing instances are collapsed away.
    """
    instances = dict(netlist.get("instances") or {})
    nets = list(netlist.get("nets") or [])
    devices = {name: entry for name, entry in instances.items()
               if str((entry or {}).get("component", "")) not in route_components}
    routers = set(instances) - set(devices)

    # Graph nodes are "inst,port" endpoints. Every route instance is a
    # pass-through: all its ports are internally one electrical/optical node.
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}

    def _endpoint(raw: str) -> tuple[str, str]:
        inst, _, port = str(raw).partition(",")
        return (inst, port)

    def _link(a: tuple[str, str], b: tuple[str, str]) -> None:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    router_ports: dict[str, list[tuple[str, str]]] = {name: [] for name in routers}
    for net in nets:
        a = _endpoint(net.get("p1", ""))
        b = _endpoint(net.get("p2", ""))
        _link(a, b)
        for ep in (a, b):
            if ep[0] in router_ports and ep not in router_ports[ep[0]]:
                router_ports[ep[0]].append(ep)
    for ports in router_ports.values():
        for i in range(len(ports) - 1):
            _link(ports[i], ports[i + 1])

    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    device_nets: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack = [start]
        component_nodes = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            component_nodes.append(node)
            stack.extend(adjacency.get(node, ()))
        endpoints = sorted(n for n in component_nodes if n[0] in devices)
        if len(endpoints) == 2:
            device_nets.append((endpoints[0], endpoints[1]))
        elif len(endpoints) == 1:
            problems.append(
                "dangling connection at %s,%s (route chain ends nowhere); "
                "not imported as a net" % endpoints[0])
        elif len(endpoints) > 2:
            problems.append(
                "net with %d device ports (%s): photonic nets are "
                "point-to-point — split it with an explicit splitter/MMI "
                "in the gf source" % (len(endpoints),
                                      ", ".join("%s,%s" % e for e in endpoints)))
    return devices, device_nets, problems


def _sanitize(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(token)) or "dev"


def klayout_cell_name_for(gf_cell) -> str:
    """Readable, settings-unique KLayout cell name for a gf cell."""
    base = _sanitize(getattr(gf_cell, "function_name", None) or gf_cell.name)
    tail = str(gf_cell.name).rsplit("_", 1)[-1]
    if tail and tail != base and re.fullmatch(r"[0-9a-f]{6,}", tail):
        return "GFDEV_%s_%s" % (base, tail[:8])
    return "GFDEV_%s" % base


def _cell_polygons_um(gf, gf_cell) -> dict[tuple[int, int], list[list[list[float]]]]:
    """Flattened polygons of one gf cell, child-local um, keyed by (L, D).

    Goes through the raw KLayout database (`kdb_cell` + recursive shape
    iterators), which works for every kfactory cell flavor — the
    ``get_polygons`` convenience only exists on gdsfactory Components.
    """
    layout = gf.kcl.layout
    kdb_cell = getattr(gf_cell, "kdb_cell", gf_cell)
    dbu = float(layout.dbu)
    out: dict[tuple[int, int], list[list[list[float]]]] = {}
    for layer_index in layout.layer_indexes():
        info = layout.get_info(layer_index)
        key = (int(info.layer), int(info.datatype))
        polygons = []
        iterator = kdb_cell.begin_shapes_rec(layer_index)
        while not iterator.at_end():
            shape = iterator.shape()
            if shape.is_polygon() or shape.is_box() or shape.is_path():
                polygon = shape.polygon.transformed(iterator.trans())
                points = [[p.x * dbu, p.y * dbu] for p in polygon.each_point_hull()]
                if len(points) >= 3:
                    polygons.append(points)
            iterator.next()
        if polygons:
            out[key] = polygons
    return out


# --------------------------------------------------------------------------- #
# live harvest from templates (drag-safe port derivation)
# --------------------------------------------------------------------------- #

def _apply_trans_um(point: Sequence[float], trans: Mapping[str, Any], dbu: float) -> list[float]:
    """Child-local um point -> parent um through an instance trans."""
    x, y = float(point[0]), float(point[1])
    mag = float(trans.get("magnification", 1.0) or 1.0)
    x *= mag
    y *= mag
    if trans.get("mirror"):
        y = -y
    angle = math.radians(float(trans.get("rotation_deg", 0.0) or 0.0))
    xr = x * math.cos(angle) - y * math.sin(angle)
    yr = x * math.sin(angle) + y * math.cos(angle)
    return [xr + float(trans.get("dx_dbu", 0)) * dbu,
            yr + float(trans.get("dy_dbu", 0)) * dbu]


def _apply_trans_angle(orientation: float, trans: Mapping[str, Any]) -> float:
    angle = -float(orientation) if trans.get("mirror") else float(orientation)
    return (angle + float(trans.get("rotation_deg", 0.0) or 0.0)) % 360.0


def harvest_gf_template_ports(
    client,
    parent_cell: str,
    *,
    tags: Mapping[str, str],
    templates: Mapping[str, Mapping[str, Any]],
    nets: Mapping[str, str] | None = None,
    port_layer: str = "999/99",
    mark_policy: str = "all",
) -> list[dict[str, Any]]:
    """Build port.mark params from LIVE instance positions + stored templates.

    The gf-native sibling of ``blackbox.harvest_instance_ports``: the port
    template comes from the imported gf cell's port table (persisted in the
    net spec) instead of a stub-scan. Identity rule is the same —
    ``{tag}{ordinal}_{gf_port_name}`` with ordinals in parent-iteration
    order, so names survive GUI drag edits.

    ``mark_policy``: the full port set is ALWAYS available (no information is
    ever dropped from the template) — this only controls what THIS call
    returns for visual marking. ``"all"`` (default) returns every harvested
    port, unchanged behavior. ``"used"`` returns only ports already assigned
    to a net (``nets.get(name)`` truthy) — the fix for gf components that
    expose many duplicate/unused access ports (e.g. a bond pad's 4 edges plus
    a redundant center ``port_type="pad"`` point): once a net is actually
    wired, only the port(s) carrying real connections stay visible. Callers
    that need the full set for internal bookkeeping (net validation,
    abutment detection) should call with ``mark_policy="all"`` and filter
    themselves for display.
    """
    if mark_policy not in ("all", "used"):
        raise ValueError(
            "mark_policy must be 'all' or 'used', got %r" % (mark_policy,))
    dbu = float(client.layout_info().get("dbu", 0.001))
    nets = nets or {}
    counters: dict[str, int] = {}
    marks: list[dict[str, Any]] = []
    result = client.call("instance.query", {"parent": parent_cell, "limit": 5000})
    for inst in result.get("instances", []):
        child = str(inst.get("child") or "")
        template = templates.get(child)
        if template is None or child not in tags:
            continue
        tag = tags[child]
        ordinal = counters.get(tag, 0)
        counters[tag] = ordinal + 1
        trans = inst.get("trans") or {}
        for port in template.get("ports", []):
            name = "%s%d_%s" % (tag, ordinal, port["name"])
            net = nets.get(name, "")
            if mark_policy == "used" and not net:
                continue
            center = _apply_trans_um(port["center_um"], trans, dbu)
            marks.append({
                "cell": parent_cell,
                "layer": port_layer,
                "name": name,
                "label": name,
                "center_um": [round(center[0], 4), round(center[1], 4)],
                "orientation": _apply_trans_angle(port.get("orientation", 0.0), trans),
                "width_um": float(port.get("width_um", 0.5)),
                "port_type": str(port.get("port_type", "optical")),
                "net": net,
                "target_layer": str(port.get("target_layer", "")),
                "access_mode": "point",
                "slide_allowed": False,
                "slide_edge": "",
                "show_label": True,
            })
    return marks


# --------------------------------------------------------------------------- #
# the one-call takeover
# --------------------------------------------------------------------------- #

def import_gf_component(
    client,
    component,
    *,
    cell: str | None = None,
    port_layer: str = "999/99",
    route_layer: str | None = None,
    spec_root: str | None = None,
    route_components: frozenset[str] = DEFAULT_ROUTE_COMPONENTS,
    route: bool = True,
) -> dict:
    """Take a user gdsfactory Component over into klink's interactive loop.

    Places its DEVICE instances as real KLayout cells/instances (batch RPC),
    persists per-device port templates + collapsed device-level nets in the
    net spec, marks ports from live positions, and (default) routes the nets
    with klink's gdsfactory backend. After this call the layout is fully
    drag -> ``photonics.reroute``-able; the original gf routes are replaced
    by klink-owned routes.

    ``route_layer`` defaults to the layer the device ports sit on when that
    is unambiguous; pass it explicitly otherwise (it is YOUR process fact).

    Idempotent + self-healing on re-import: a unique device cell that already
    exists is only reused as-is when its live shape count matches what this
    gf cell should produce; a mismatch (empty shell or partial write, e.g.
    left behind by a kfactory active-view hijack mid-write) clears that
    cell's existing shapes and refills it from the gf geometry. Cells healed
    this way are reported in ``result["healed_cells"]``.

    Visible-marker lifecycle: this call (``route=False``, wiring-check phase)
    marks EVERY harvested port so you can see all of them while wiring nets.
    Once nets are routed here or via ``photonics.reroute``, the visible marks
    are pruned down to only the ports that are actually in a net (a component
    with many duplicate/unused access ports, e.g. a bond pad's 4 edges + 1
    center point, no longer paints one marker per port). The full port set is
    never lost — it is still in the on-disk template — so re-running
    ``import_gf_component`` (or any ``route=False`` call) re-shows all of
    them for another wiring pass.
    """
    from klink.routing.backends.gdsfactory.gdsfactory_backend import _load_gdsfactory

    from .net_intent import NetTable, RouteStyle, _harvest_and_route

    gf_module = _load_gdsfactory()
    netlist = component.get_netlist()
    devices, device_nets, problems = split_gf_netlist(netlist, route_components)
    if not devices:
        return {"ok": False, "problems": [
            "no device instances found in component %r (only routing "
            "primitives?); nothing to import" % component.name]}

    # netlist instance name -> live ref, matched by placement (netlist
    # renames refs, so coordinates are the reliable join key).
    placements = dict(netlist.get("placements") or {})

    def _pkey(x, y, rotation, mirror):
        return (round(float(x), 3), round(float(y), 3),
                round(float(rotation), 1) % 360.0, bool(mirror))

    ref_by_key: dict[tuple, Any] = {}
    for ref in component.insts:
        t = ref.dcplx_trans
        key = _pkey(t.disp.x, t.disp.y, t.angle, t.mirror)
        ref_by_key.setdefault(key, ref)

    device_refs: dict[str, Any] = {}
    for name in devices:
        pl = placements.get(name) or {}
        key = _pkey(pl.get("x", 0.0), pl.get("y", 0.0),
                    pl.get("rotation", 0.0), pl.get("mirror", False))
        ref = ref_by_key.get(key)
        if ref is None:
            return {"ok": False, "problems": [
                "cannot match netlist instance %r (placement %s) to a live "
                "reference; overlapping identical placements?" % (name, pl)]}
        device_refs[name] = ref

    # Unique device cells -> KLayout cells (one batch shape insert per cell).
    parent = cell or "GF_%s" % _sanitize(component.name).upper()
    existing = {c["name"] for c in client.cell_list(limit=5000).get("cells", [])}

    gf_cells: dict[str, Any] = {}
    for ref in device_refs.values():
        gf_cells.setdefault(klayout_cell_name_for(ref.cell), ref.cell)

    created_cells = []
    healed_cells: list[dict[str, Any]] = []
    templates: dict[str, dict] = {}
    port_layers: set[str] = set()
    for klay_name, gf_cell in gf_cells.items():
        tports = []
        for port in gf_cell.ports:
            layer_info = getattr(port, "layer_info", None)
            if layer_info is not None:
                target = "%d/%d" % (layer_info.layer, layer_info.datatype)
            else:
                target = ""
            if target:
                port_layers.add(target)
            tports.append({
                "name": str(port.name),
                "center_um": [float(port.center[0]), float(port.center[1])],
                "orientation": float(port.orientation) % 360.0,
                "width_um": float(port.width),
                "port_type": str(port.port_type or "optical"),
                "target_layer": target,
            })
        templates[klay_name] = {"ports": tports, "gf_cell": str(gf_cell.name)}

        polygons_by_layer = _cell_polygons_um(gf_module, gf_cell)
        items = []
        for (layer, datatype), polygons in polygons_by_layer.items():
            for points in polygons:
                items.append({"kind": "polygon", "layer": layer,
                              "datatype": datatype, "points_um": points})

        if klay_name not in existing:
            client.cell_create(klay_name)
            created_cells.append(klay_name)
            for (layer, datatype) in polygons_by_layer:
                client.layer_ensure(layer, datatype)
            if items:
                client.shape_insert_many(klay_name, items)
            continue

        # Already exists: it may be a healthy previous import, OR an empty
        # shell / partial write left behind by a past kfactory active-view
        # hijack (cell created, geometry lost mid-flight). Self-heal: only
        # touch it when the live shape count disagrees with what THIS gf
        # cell should produce; a matching count is left untouched (idempotent).
        query = client.shape_query(klay_name, limit=5000)
        current_shapes = query.get("shapes", [])
        mismatch = query.get("truncated") or len(current_shapes) != len(items)
        if not mismatch:
            continue
        layer_map = {
            entry.get("layer_index"): "%d/%d" % (entry.get("layer"), entry.get("datatype"))
            for entry in client.layer_list().get("layers", [])
        }
        current_layers = sorted({
            layer_map[s.get("layer_index")] for s in current_shapes
            if s.get("layer_index") in layer_map
        })
        if current_layers:
            client.shape_delete(klay_name, layers=current_layers,
                                kinds=["polygons", "boxes", "paths", "texts"],
                                limit=20000)
        for (layer, datatype) in polygons_by_layer:
            client.layer_ensure(layer, datatype)
        if items:
            client.shape_insert_many(klay_name, items)
        healed_cells.append({
            "cell": klay_name,
            "shapes_before": len(current_shapes),
            "shapes_after": len(items),
        })

    # Tags: unique short prefix per device cell (port identity rule).
    tags: dict[str, str] = {}
    used_tags: set[str] = set()
    for klay_name, gf_cell in gf_cells.items():
        base = _sanitize(getattr(gf_cell, "function_name", None) or klay_name)
        tag = base
        suffix = 2
        while tag in used_tags:
            tag = "%s%d" % (base, suffix)
            suffix += 1
        used_tags.add(tag)
        tags[klay_name] = tag

    # Place ALL device instances in one batch, in sorted netlist-name order:
    # this order IS the ordinal order the harvest sees (parent-iteration).
    # Re-import is idempotent: the parent is a klink-managed takeover cell,
    # so an existing one is rebuilt from scratch (children are untouched).
    if parent in existing:
        client.cell_delete(parent, recursive=False)
    client.cell_create(parent)
    order = sorted(device_refs)
    counters: dict[str, int] = {}
    name_map: dict[str, str] = {}
    items = []
    for netlist_name in order:
        ref = device_refs[netlist_name]
        klay_name = klayout_cell_name_for(ref.cell)
        pl = placements.get(netlist_name) or {}
        tag = tags[klay_name]
        ordinal = counters.get(tag, 0)
        counters[tag] = ordinal + 1
        name_map[netlist_name] = "%s%d" % (tag, ordinal)
        items.append({
            "child": klay_name,
            "position_um": [float(pl.get("x", 0.0)), float(pl.get("y", 0.0))],
            "rotation": float(pl.get("rotation", 0.0)),
            "mirror": bool(pl.get("mirror", False)),
        })
    client.instance_insert_many(parent, items)

    # Collapsed device-level nets -> net table entries.
    if route_layer is None and len(port_layers) == 1:
        route_layer = next(iter(port_layers))
    style = RouteStyle(route_layer=route_layer)
    table = NetTable(cell=parent, tags=tags)
    table.harvest = {
        "mode": "gf_templates",
        "templates": templates,
        "route_layer": route_layer or "",
        "source_component": str(component.name),
    }
    for (inst_a, port_a), (inst_b, port_b) in device_nets:
        table.add_pair("%s_%s" % (name_map[inst_a], port_a),
                       "%s_%s" % (name_map[inst_b], port_b), style)

    spec_path = table.save(spec_root)
    result: dict[str, Any] = {
        "ok": not problems,
        "cell": parent,
        "device_cells": sorted(gf_cells),
        "created_cells": created_cells,
        "healed_cells": healed_cells,
        "instances": len(items),
        "nets": table.net_names(),
        "spec_path": spec_path,
        "problems": problems,
    }

    if route and table.entries:
        if not route_layer:
            result["ok"] = False
            result["problems"] = problems + [
                "cannot route: device ports sit on multiple layers %s — pass "
                "route_layer='L/D' explicitly (your process fact)"
                % sorted(port_layers)]
            return result
        report = _harvest_and_route(client, table, port_layer=port_layer,
                                    route_layer=route_layer)
        result["route_report"] = report
        result["ok"] = result["ok"] and bool(report.get("ok"))
        if report.get("problems"):
            result["problems"] = result["problems"] + list(report["problems"])
    else:
        # No immediate routing: still mark ports so the layout is inspectable.
        from .blackbox import mark_ports
        marks = harvest_gf_template_ports(
            client, parent, tags=tags, templates=templates,
            nets=table.nets_for_harvest(), port_layer=port_layer)
        client.call("port.delete_all", {"cell": parent, "layer": port_layer})
        mark_ports(client, marks)
        result["ports_marked"] = len(marks)

    client.call("view.show_cell", {"cell": parent, "zoom_fit": True})
    result["next"] = ("drag instances in KLayout, then call photonics.reroute "
                      "(cell=%r) to re-route from live positions" % parent)
    return result
