"""Place gdsfactory components from klink component markers."""

from __future__ import annotations

import math
from typing import Any

from klink.routing.backends.gdsfactory.gdsfactory_backend import _load_gdsfactory, _parse_layer


def _transform_point(point: list[float], origin: list[float], rotation: float) -> list[float]:
    rad = math.radians(float(rotation))
    c = math.cos(rad)
    s = math.sin(rad)
    x = float(point[0])
    y = float(point[1])
    return [
        float(origin[0]) + x * c - y * s,
        float(origin[1]) + x * s + y * c,
    ]


def _component_for_marker(marker: dict):
    gf = _load_gdsfactory()
    component_name = str(marker.get("component", ""))
    if not component_name:
        raise ValueError("gdsfactory marker is missing component")
    factory = getattr(gf.components, component_name, None)
    if factory is None:
        raise ValueError("unknown gdsfactory component: %s" % component_name)
    params = dict(marker.get("params") or {})
    return factory(**params)


def gdsfactory_component_marker_to_shapes_and_ports(
    marker: dict,
    *,
    target_layer: str = "10/0",
    port_layer: str = "999/99",
) -> dict[str, Any]:
    """Convert one component marker into batch shape items and Port dicts.

    Marker schema:

    ```python
    {
        "id": "SPL1",
        "component": "mmi1x2",
        "center_um": [50, 10],      # component local origin in KLayout coords
        "rotation": 0,
        "params": {...},
        "port_nets": {"o1": "in", "o2": "out0", "o3": "out1"},
    }
    ```
    """

    gf = _load_gdsfactory()
    component = _component_for_marker(marker)
    origin = [float(v) for v in marker.get("center_um", marker.get("origin_um", [0.0, 0.0]))]
    rotation = float(marker.get("rotation", marker.get("orientation", 0.0)) or 0.0)
    marker_id = str(marker.get("id") or marker.get("name") or marker.get("component") or "GF")
    port_nets = dict(marker.get("port_nets") or {})
    layer, datatype = _parse_layer(target_layer)

    items: list[dict] = []
    scale = float(gf.kcl.dbu)
    for _layer_key, polygons in component.get_polygons(by="tuple", merge=False).items():
        for polygon in polygons:
            points = [
                _transform_point([float(point.x) * scale, float(point.y) * scale], origin, rotation)
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

    ports: list[dict] = []
    for port in component.ports:
        center = _transform_point([float(port.center[0]), float(port.center[1])], origin, rotation)
        name = "%s.%s" % (marker_id, port.name)
        ports.append(
            {
                "name": name,
                "center_um": center,
                "orientation": (float(port.orientation) + rotation) % 360.0,
                "width_um": float(port.width),
                "target_layer": target_layer,
                "port_type": str(port.port_type or "optical"),
                "net": str(port_nets.get(port.name, "")),
                "layer": port_layer,
            }
        )

    return {
        "id": marker_id,
        "component": str(marker.get("component", "")),
        "shape_items": items,
        "ports": ports,
    }


def place_gdsfactory_components(
    client,
    cell: str,
    markers: list[dict],
    *,
    target_layer: str = "10/0",
    port_layer: str = "999/99",
    clear: bool = True,
) -> dict:
    """Place gdsfactory components into KLayout and mark their ports."""

    all_items: list[dict] = []
    all_ports: list[dict] = []
    converted = []
    for marker in markers:
        result = gdsfactory_component_marker_to_shapes_and_ports(
            marker,
            target_layer=target_layer,
            port_layer=port_layer,
        )
        converted.append(result)
        all_items.extend(result["shape_items"])
        all_ports.extend(result["ports"])

    layer, datatype = _parse_layer(target_layer)
    client.layer_ensure(layer, datatype, name="GF_COMPONENTS")
    if clear:
        client.shape_delete(cell, layers=[target_layer], kinds=["polygons", "paths"], limit=10000)
    writeback = client.shape_insert_many(cell, all_items) if all_items else {"inserted": 0}

    client.layer_ensure(*_parse_layer(port_layer), name="GF_COMPONENT_PORTS")
    for port in all_ports:
        client.call(
            "port.mark",
            {
                "cell": cell,
                "layer": port_layer,
                "name": port["name"],
                "center_um": port["center_um"],
                "orientation": port["orientation"],
                "width_um": port["width_um"],
                "port_type": port["port_type"],
                "net": port["net"],
                "target_layer": target_layer,
                "show_label": True,
            },
        )

    return {
        "cell": cell,
        "components": converted,
        "shape_count": len(all_items),
        "port_count": len(all_ports),
        "writeback": writeback,
    }
