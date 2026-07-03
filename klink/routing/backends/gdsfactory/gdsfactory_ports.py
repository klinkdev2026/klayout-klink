"""Route klink Port markers through the optional gdsfactory backend."""

from __future__ import annotations

import uuid

from klink.routing.backends.gdsfactory.gdsfactory_backend import (
    GF_ROUTERS,
    _normalize_router,
    component_polygons_to_shape_items,
    route_bundle_with_gdsfactory,
)
from klink.routing.geom.geometry import crossing_pairs
from klink.routing.geom.port import match_ports
from klink.routing.geom.writeback import commit_routes


def _is_candidate_sink(port: dict) -> bool:
    return str(port.get("port_type", "")).lower() == "candidate_sink"


def _ports_by_name(ports: list[dict]) -> dict[str, dict]:
    return {str(port.get("name", "")): port for port in ports if port.get("name")}


def _orientation_close(value: object, target: float, eps: float = 1e-6) -> bool:
    return abs((float(value) % 360.0) - (float(target) % 360.0)) <= eps


def _filter_prefix(ports: list[dict], prefix: str) -> list[dict]:
    return [port for port in ports if str(port.get("name", "")).startswith(prefix)]


def _filter_orientation(ports: list[dict], orientation: float) -> list[dict]:
    return [port for port in ports if _orientation_close(port.get("orientation", 0.0), orientation)]


def _axis_sorted(ports: list[dict]) -> list[dict]:
    if not ports:
        return []
    orientation = float(ports[0].get("orientation", 0.0)) % 360.0
    if _orientation_close(orientation, 0.0) or _orientation_close(orientation, 180.0):
        return sorted(ports, key=lambda p: (float((p.get("center_um") or [0.0, 0.0])[1]), str(p.get("name", ""))))
    if _orientation_close(orientation, 90.0) or _orientation_close(orientation, 270.0):
        return sorted(ports, key=lambda p: (float((p.get("center_um") or [0.0, 0.0])[0]), str(p.get("name", ""))))
    return sorted(ports, key=lambda p: str(p.get("name", "")))


def _pair_groups(ports1: list[dict], ports2: list[dict], pair_by: str) -> tuple[list[dict], list[dict]]:
    if len(ports1) != len(ports2):
        raise ValueError("selected port groups have different sizes: %d vs %d" % (len(ports1), len(ports2)))
    if not ports1:
        raise ValueError("selected port groups are empty")
    if pair_by == "axis":
        return _axis_sorted(ports1), _axis_sorted(ports2)
    if pair_by == "order":
        return ports1, ports2
    pairs = match_ports(ports1, ports2, strategy=pair_by)
    if len(pairs) != len(ports1):
        raise ValueError("pairing by %s produced %d pairs for %d ports" % (pair_by, len(pairs), len(ports1)))
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _select_two_port_nets(ports: list[dict]) -> tuple[list[dict], list[dict]]:
    by_net: dict[str, list[dict]] = {}
    for port in ports:
        net = str(port.get("net", "") or "")
        if net:
            by_net.setdefault(net, []).append(port)
    pairs = []
    skipped = []
    for net in sorted(by_net):
        members = by_net[net]
        if len(members) == 2:
            pairs.append((members[0], members[1]))
        elif len(members) > 2:
            skipped.append("%s(%d)" % (net, len(members)))
    if skipped:
        raise ValueError("multi-port nets need explicit topology: %s" % ", ".join(skipped))
    if not pairs:
        raise ValueError("no two-port nets found")
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _pair_by_two_port_nets(ports1: list[dict], ports2: list[dict]) -> tuple[list[dict], list[dict]]:
    right_by_net: dict[str, list[dict]] = {}
    for port in ports2:
        net = str(port.get("net", "") or "")
        if net:
            right_by_net.setdefault(net, []).append(port)

    pairs = []
    errors = []
    for left in ports1:
        net = str(left.get("net", "") or "")
        matches = right_by_net.get(net, [])
        if not net:
            errors.append("%s has empty net" % left.get("name", ""))
        elif len(matches) != 1:
            errors.append("%s net %r has %d target matches" % (left.get("name", ""), net, len(matches)))
        else:
            pairs.append((left, matches[0]))
    if errors:
        raise ValueError("net pairing failed: %s" % "; ".join(errors))
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _select_multidrop_star(ports: list[dict], *, net: str, root: str) -> tuple[list[dict], list[dict]]:
    members = [port for port in ports if str(port.get("net", "") or "") == net]
    count = len(members)
    raise ValueError(
        "gdsfactory photonic routing only supports point-to-point optical nets; "
        "net %r has %d ports. Insert an explicit splitter/MMI/Y-branch and "
        "route the resulting two-port nets instead." % (net, count)
    )


def select_gdsfactory_port_groups(
    ports: list[dict],
    *,
    source: list[str] | None = None,
    target: list[str] | None = None,
    source_prefix: str | None = None,
    target_prefix: str | None = None,
    source_orientation: float | None = None,
    target_orientation: float | None = None,
    net: str | None = None,
    all_two_port_nets: bool = False,
    multidrop_net: str | None = None,
    root: str | None = None,
    pair_by: str = "axis",
) -> tuple[list[dict], list[dict]]:
    """Select and pair port groups for gdsfactory route_bundle."""

    source = list(source or [])
    target = list(target or [])
    normal_ports = [port for port in ports if not _is_candidate_sink(port)]
    if source or target:
        by_name = _ports_by_name(normal_ports)
        missing = [name for name in source + target if name not in by_name]
        if missing:
            raise ValueError("missing port(s): %s" % ", ".join(missing))
        return _pair_groups([by_name[name] for name in source], [by_name[name] for name in target], pair_by)

    if net:
        selected = [port for port in normal_ports if str(port.get("net", "")) == net]
        if len(selected) != 2:
            raise ValueError("net %r has %d ports; expected exactly 2" % (net, len(selected)))
        return [selected[0]], [selected[1]]

    if multidrop_net:
        if not root:
            raise ValueError("multidrop routing requires a root port")
        return _select_multidrop_star(normal_ports, net=multidrop_net, root=root)

    if all_two_port_nets:
        return _select_two_port_nets(normal_ports)

    ports1 = None
    ports2 = None
    if source_prefix or target_prefix:
        if not (source_prefix and target_prefix):
            raise ValueError("source_prefix and target_prefix must be provided together")
        ports1 = _filter_prefix(normal_ports, source_prefix)
        ports2 = _filter_prefix(normal_ports, target_prefix)
    if source_orientation is not None or target_orientation is not None:
        if not (source_orientation is not None and target_orientation is not None):
            raise ValueError("source_orientation and target_orientation must be provided together")
        if ports1 is not None:
            raise ValueError("use either prefix selection or orientation selection, not both")
        ports1 = _filter_orientation(normal_ports, source_orientation)
        ports2 = _filter_orientation(normal_ports, target_orientation)
    if ports1 is not None and ports2 is not None:
        if pair_by == "net":
            return _pair_by_two_port_nets(ports1, ports2)
        return _pair_groups(ports1, ports2, pair_by)

    return _select_two_port_nets(normal_ports)


#: kwargs consumed by port-group selection; everything else is forwarded to
#: the chosen routing strategy (which validates it against its parameter set).
_SELECTION_KEYS = frozenset({
    "source", "target", "source_prefix", "target_prefix",
    "source_orientation", "target_orientation", "net", "all_two_port_nets",
    "multidrop_net", "root", "pair_by",
})


def route_gdsfactory_ports(
    client,
    cell: str,
    *,
    port_layer: str = "999/99",
    route_layer: str = "10/0",
    gf_route_layer: str | None = None,
    output_mode: str = "batch_polygons",
    clear: bool = True,
    allow_crossing: bool = False,
    router: str = "bundle",
    **kwargs,
) -> dict:
    """Read KLayout Port markers, route with one gdsfactory strategy, write back.

    Selection kwargs (`source`/`target`, prefixes, orientations, `net`,
    `all_two_port_nets`, `pair_by`, ...) pick and pair the ports; every other
    kwarg goes to the strategy named by `router` and is validated against
    what that strategy honors (see GF_ROUTERS in the backend module).
    """

    selection = {k: kwargs.pop(k) for k in list(kwargs) if k in _SELECTION_KEYS}
    router_name = _normalize_router(router)
    spec = GF_ROUTERS.get(router_name)
    if spec is None:
        raise ValueError(
            "unknown gdsfactory router %r; available: %s"
            % (router, ", ".join(sorted(GF_ROUTERS))))

    snapshot = client.call("port.list", {"cell": cell, "layer": port_layer, "sort": "name"})
    ports = list(snapshot.get("ports", []))
    ports1, ports2 = select_gdsfactory_port_groups(ports, **selection)
    gf_ports1 = ports1
    gf_ports2 = ports2
    if gf_route_layer and gf_route_layer != route_layer:
        gf_ports1 = [dict(port, target_layer=gf_route_layer) for port in ports1]
        gf_ports2 = [dict(port, target_layer=gf_route_layer) for port in ports2]

    # Manhattan bundle strategies require all starts to share one heading and
    # all targets one heading per call -> partition into per-(start, end)-angle
    # bundles. Within one angle pair, only nets that are actual PARALLEL
    # NEIGHBOURS (sources near each other AND targets near each other, within
    # bundle_gather_um) share a bundle: a bundle is a river of adjacent lines,
    # and lumping unrelated stages of a chain into one river makes the bundle
    # router weave them into collisions. Free-heading strategies
    # (single/all_angle/dubins/astar) take the whole set at once — astar in
    # particular must, so later pairs avoid earlier pairs' routes.
    gather = float(kwargs.pop("bundle_gather_um", 30.0))

    def _angle_key(port: dict) -> int:
        try:
            return int(round(float(port.get("orientation", 0.0)))) % 360
        except (TypeError, ValueError):
            return 0

    def _near(a: dict, b: dict) -> bool:
        ca = a.get("center_um") or [0.0, 0.0]
        cb = b.get("center_um") or [0.0, 0.0]
        return (abs(float(ca[0]) - float(cb[0])) <= gather
                and abs(float(ca[1]) - float(cb[1])) <= gather)

    bundle_groups: dict[tuple, list[int]] = {}
    if spec["pairing"] == "angle_group":
        angle_groups: dict[tuple, list[int]] = {}
        for index in range(len(gf_ports1)):
            key = (_angle_key(gf_ports1[index]), _angle_key(gf_ports2[index]))
            angle_groups.setdefault(key, []).append(index)
        for key, indices in angle_groups.items():
            parent = {i: i for i in indices}

            def _find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for pos, i in enumerate(indices):
                for j in indices[pos + 1:]:
                    if (_near(gf_ports1[i], gf_ports1[j])
                            and _near(gf_ports2[i], gf_ports2[j])):
                        parent[_find(i)] = _find(j)
            clusters: dict[int, list[int]] = {}
            for i in indices:
                clusters.setdefault(_find(i), []).append(i)
            for root, members in sorted(clusters.items()):
                bundle_groups[key + (root,)] = members
    else:
        bundle_groups[("free",)] = list(range(len(gf_ports1)))

    sub_reports: list[dict] = []
    merged_routes: list[dict] = []
    for group_no, (_, indices) in enumerate(sorted(bundle_groups.items())):
        sub_report = route_bundle_with_gdsfactory(
            [gf_ports1[i] for i in indices],
            [gf_ports2[i] for i in indices],
            layer=gf_route_layer or route_layer,
            router=router_name,
            # Unique per call: gf/kfactory keeps created Components in a
            # process-wide KCLayout and rejects duplicate cell names, which
            # would break the SECOND routing call in any long-lived process
            # (e.g. the MCP server).
            component_name="gf_route_%s_g%d_%s" % (cell, group_no, uuid.uuid4().hex[:8]),
            **kwargs,
        )
        sub_reports.append(sub_report)
        merged_routes.extend(sub_report["routes"])
    for index, route in enumerate(merged_routes):
        route["route_id"] = "gf_route_%d" % index

    if sub_reports:
        report = dict(sub_reports[0])
    else:
        report = {"routes": [], "gf_component": None}
    report["routes"] = merged_routes
    report["gf_components"] = [r.get("gf_component") for r in sub_reports]
    report["bundle_group_count"] = len(bundle_groups)
    route_crossings = crossing_pairs(report["routes"])
    if route_crossings and not allow_crossing:
        raise ValueError("gdsfactory produced crossing route backbones")

    writeback = None
    if output_mode == "klink_paths":
        writeback = commit_routes(client, cell, report["routes"], route_layer=route_layer, clear=clear)
        client.show_cell(cell, zoom_fit=True)
    elif output_mode == "gdsfactory_show":
        for group_no, component in enumerate(report["gf_components"]):
            component.name = "GF_ROUTE_%s_G%d" % (cell, group_no)
            component.show(keep_position=True)
    elif output_mode == "batch_polygons":
        layer_map = None
        if gf_route_layer and gf_route_layer != route_layer:
            layer_map = {gf_route_layer: route_layer}
        items = []
        for component in report["gf_components"]:
            items.extend(component_polygons_to_shape_items(
                component,
                layer_map=layer_map,
                exclude_layers={(1000, 0), "1000/0"},
            ))
        layer_s, datatype_s = route_layer.split("/", 1)
        client.layer_ensure(int(layer_s), int(datatype_s), name="GF_ROUTE_POLYGONS")
        if clear:
            client.shape_delete(cell, layers=[route_layer], kinds=["polygons", "paths"], limit=10000)
        for item in items:
            client.layer_ensure(int(item["layer"]), int(item.get("datatype", 0)))
        writeback = client.shape_insert_many(cell, items)
        client.show_cell(cell, zoom_fit=True)
    elif output_mode != "dry_run":
        raise ValueError("unknown output_mode: %s" % output_mode)

    return {
        **report,
        "cell": cell,
        "ports1": ports1,
        "ports2": ports2,
        "crossings": route_crossings,
        "writeback": writeback,
        "output_mode": output_mode,
    }
