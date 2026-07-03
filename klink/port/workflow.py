"""Client-side port workflows built from low-level RPCs.

The KLayout plugin exposes primitive operations. Higher-level recognition and
policy decisions live here so they can evolve without coupling the plugin to
the Python client package.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from klink.errors import KLinkServerError

from .inference import (
    align_triangle_to_nearby_edge,
    infer_box_direction,
    infer_path_direction,
    infer_polygon_points,
)
from .naming import auto_name


def _bbox_center(bbox: Sequence[float]) -> list[int]:
    return [
        int(round((float(bbox[0]) + float(bbox[2])) / 2.0)),
        int(round((float(bbox[1]) + float(bbox[3])) / 2.0)),
    ]


def _serialize_edge_dbu(edge: Sequence[float] | None) -> str:
    if edge is None or len(edge) < 4:
        return ""
    x0, y0, x1, y1 = [int(round(float(v))) for v in edge[:4]]
    if (x1, y1) < (x0, y0):
        x0, y0, x1, y1 = x1, y1, x0, y0
    return "%d,%d,%d,%d" % (x0, y0, x1, y1)


def _unique_points(points: Iterable[Sequence[float]]) -> list[list[float]]:
    unique: list[list[float]] = []
    for point in points:
        pair = [float(point[0]), float(point[1])]
        if pair not in unique:
            unique.append(pair)
    return unique


def is_handdrawn_port_marker(shape: dict) -> bool:
    """Return whether a raw shape is an accepted hand-drawn port marker."""
    if shape.get("type") != "polygon":
        return False
    return len(_unique_points(shape.get("points_dbu") or [])) == 3


def shape_edges(shape: dict) -> list[list[float]]:
    """Return dbu edge segments for a shape dict returned by shape.query."""
    kind = shape.get("type")
    if kind == "box":
        left, bottom, right, top = shape.get("bbox_dbu", [0, 0, 0, 0])
        pts = [[left, bottom], [right, bottom], [right, top], [left, top]]
        return [
            [pts[i][0], pts[i][1], pts[(i + 1) % 4][0], pts[(i + 1) % 4][1]]
            for i in range(4)
        ]

    pts = shape.get("points_dbu") or []
    if len(pts) < 2:
        return []
    if kind == "path":
        return [
            [pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]]
            for i in range(len(pts) - 1)
        ]
    if kind == "polygon":
        return [
            [pts[i][0], pts[i][1], pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]]
            for i in range(len(pts))
        ]
    return []


def infer_shape_port(
    shape: dict,
    *,
    dbu: float,
    edges: Iterable[Sequence[float]] = (),
    direction_guess: str = "long_edge",
    fallback_orientation: float = 0.0,
) -> dict:
    """Infer a port descriptor from one raw marker shape dict."""
    kind = shape.get("type")
    bbox = shape.get("bbox_dbu") or [0, 0, 0, 0]

    if kind == "box":
        width_dbu = float(bbox[2]) - float(bbox[0])
        height_dbu = float(bbox[3]) - float(bbox[1])
        orientation, width_um = infer_box_direction(
            width_dbu,
            height_dbu,
            dbu,
            direction_guess=direction_guess,
            fallback_orientation=fallback_orientation,
        )
        center = _bbox_center(bbox)
        attached = False
        slide_edge = ""
    elif kind == "path":
        orientation, width_um = infer_path_direction(
            shape.get("points_dbu") or [],
            float(shape.get("width_dbu", 0)),
            dbu,
            fallback_orientation=fallback_orientation,
        )
        center = _bbox_center(bbox)
        attached = False
        slide_edge = ""
    else:
        points = shape.get("points_dbu") or []
        orientation, width_um = infer_polygon_points(
            points,
            dbu,
            direction_guess=direction_guess,
            fallback_orientation=fallback_orientation,
        )
        aligned = align_triangle_to_nearby_edge(
            points,
            edges,
            inferred_orientation=orientation,
        )
        orientation = float(aligned.get("orientation", orientation))
        if aligned.get("center") is not None:
            center = [
                int(round(aligned["center"][0])),
                int(round(aligned["center"][1])),
            ]
        else:
            center = _bbox_center(bbox)
        attached = bool(aligned.get("attached", False))
        slide_edge = _serialize_edge_dbu(aligned.get("edge")) if attached else ""

    return {
        "orientation": orientation,
        "width_um": width_um,
        "center_dbu": center,
        "attached": attached,
        "access_mode": "edge" if attached else "point",
        "slide_allowed": attached,
        "slide_edge": slide_edge,
        "source_shape": shape,
    }


def _layer_index_map(client) -> dict[int, str]:
    layers = client.layer_list()
    return {
        int(item["layer_index"]): "%d/%d" % (int(item["layer"]), int(item["datatype"]))
        for item in layers.get("layers", [])
    }


def _existing_port_names(client, cell, layer: str) -> set[str]:
    listed = client.call("port.list", {"cell": cell, "layer": layer})
    return {
        str(p.get("name"))
        for p in listed.get("ports", [])
        if p.get("recognized") and p.get("name")
    }


def _delete_marker_instances(client, cell, layer: str, limit: int) -> int:
    try:
        query = client.instance_query(cell, limit=limit)
    except KLinkServerError as exc:
        if exc.code == "ERR_UNKNOWN_METHOD":
            return 0
        raise

    deleted = 0
    for inst in query.get("instances", []):
        pcell = inst.get("pcell") or {}
        if pcell.get("name") == "Port":
            continue
        by_layer = inst.get("child_shapes_by_layer") or {}
        marker_layer = by_layer.get(layer) or {}
        if int(marker_layer.get("non_text", 0)) <= 0:
            continue
        bbox = inst.get("bbox_dbu")
        if not bbox:
            continue
        result = client.instance_delete(cell, child=inst.get("child"), bbox_dbu=bbox)
        deleted += int(result.get("deleted", 0))
    return deleted


def _mark_inferred_ports(
    client,
    cell,
    shapes: list[dict],
    *,
    dbu: float,
    port_layer: str,
    port_type: str,
    target_layer_for_shape,
    naming: str,
    direction_guess: str,
    fallback_orientation: float,
    net: str = "",
    show_label: bool = True,
) -> list[dict]:
    existing_names = _existing_port_names(client, cell, port_layer)
    ports = []
    for index, shape in enumerate(shapes):
        inferred = infer_shape_port(
            shape,
            dbu=dbu,
            edges=(),
            direction_guess=direction_guess,
            fallback_orientation=fallback_orientation,
        )
        style = "direction" if naming == "direction" else "index"
        name = auto_name(
            inferred["orientation"],
            existing_names,
            port_type=port_type,
            index=index,
            style=style,
        )
        existing_names.add(name)
        marked = client.call(
            "port.mark",
            {
                "cell": cell,
                "layer": port_layer,
                "name": name,
                "center_dbu": inferred["center_dbu"],
                "orientation": inferred["orientation"],
                "width_um": inferred["width_um"],
                "port_type": port_type,
                "net": net,
                "target_layer": target_layer_for_shape(shape),
                "show_label": show_label,
                "access_mode": inferred["access_mode"],
                "slide_allowed": inferred["slide_allowed"],
                "slide_edge": inferred["slide_edge"],
            },
        )
        ports.append(marked)
    return ports


def import_ports_from_layer(
    client,
    cell,
    *,
    source_layer: str,
    port_layer: str = "999/99",
    direction_guess: str = "long_edge",
    fallback_orientation: float = 0.0,
    port_type: str = "electrical",
    naming: str = "direction",
    net: str = "",
    show_label: bool = True,
    limit: int = 5000,
) -> dict:
    """Create Port PCells from shapes on a source layer using client logic."""
    layout = client.layout_info()
    dbu = float(layout["dbu"])
    query = client.shape_query(
        cell,
        layers=[source_layer],
        kinds=["boxes", "polygons", "paths"],
        limit=limit,
    )
    shapes = list(query.get("shapes", []))
    ports = _mark_inferred_ports(
        client,
        cell,
        shapes,
        dbu=dbu,
        port_layer=port_layer,
        port_type=port_type,
        target_layer_for_shape=lambda _shape: source_layer,
        naming=naming,
        direction_guess=direction_guess,
        fallback_orientation=fallback_orientation,
        net=net,
        show_label=show_label,
    )
    return {
        "cell": cell,
        "source_layer": source_layer,
        "imported": len(ports),
        "ports": ports,
        "truncated": bool(query.get("truncated", False)),
    }


def import_ports_from_selection(
    client,
    cell,
    *,
    port_layer: str = "999/99",
    direction_guess: str = "long_edge",
    fallback_orientation: float = 0.0,
    port_type: str = "electrical",
    naming: str = "direction",
    net: str = "",
    show_label: bool = True,
    limit: int = 5000,
) -> dict:
    """Create Port PCells from currently selected shape objects."""
    layout = client.layout_info()
    dbu = float(layout["dbu"])
    layer_by_index = _layer_index_map(client)
    selection = client.selection_get(limit=limit)
    shapes = []
    for obj in selection.get("objects", []):
        if obj.get("kind") != "shape":
            continue
        shape = obj.get("shape")
        if not shape:
            continue
        shapes.append(shape)

    ports = _mark_inferred_ports(
        client,
        cell,
        shapes,
        dbu=dbu,
        port_layer=port_layer,
        port_type=port_type,
        target_layer_for_shape=lambda shape: layer_by_index.get(
            int(shape.get("layer_index", -1)), ""
        ),
        naming=naming,
        direction_guess=direction_guess,
        fallback_orientation=fallback_orientation,
        net=net,
        show_label=show_label,
    )
    return {
        "cell": cell,
        "imported": len(ports),
        "ports": ports,
        "selected_shapes": len(shapes),
        "truncated": bool(selection.get("truncated", False)),
    }


def recognize_handdrawn_ports(
    client,
    cell,
    *,
    layer: str = "999/99",
    direction_guess: str = "long_edge",
    fallback_orientation: float = 0.0,
    port_type: str = "electrical",
    net: str = "",
    target_layer: str = "",
    show_label: bool = True,
    delete_markers: bool = True,
    delete_marker_instances: bool = True,
    limit: int = 5000,
) -> dict:
    """Convert raw marker shapes into Port PCells using client-side logic."""
    layout = client.layout_info()
    dbu = float(layout["dbu"])

    marker_query = client.shape_query(
        cell,
        layers=[layer],
        kinds=["boxes", "polygons", "paths"],
        limit=limit,
    )
    marker_shapes = list(marker_query.get("shapes", []))
    marker_layer_indices = {s.get("layer_index") for s in marker_shapes}
    edge_query = client.shape_query(
        cell,
        kinds=["boxes", "polygons", "paths"],
        limit=limit,
    )
    edges: list[list[float]] = []
    for shape in edge_query.get("shapes", []):
        if shape.get("layer_index") in marker_layer_indices:
            continue
        edges.extend(shape_edges(shape))

    existing_names = _existing_port_names(client, cell, layer)

    ports = []
    valid_markers = [shape for shape in marker_shapes if is_handdrawn_port_marker(shape)]
    skipped_markers = len(marker_shapes) - len(valid_markers)

    for index, shape in enumerate(valid_markers):
        inferred = infer_shape_port(
            shape,
            dbu=dbu,
            edges=edges,
            direction_guess=direction_guess,
            fallback_orientation=fallback_orientation,
        )
        name = auto_name(
            inferred["orientation"],
            existing_names,
            port_type=port_type,
            index=index,
            style="index",
        )
        existing_names.add(name)
        marked = client.call(
            "port.mark",
            {
                "cell": cell,
                "layer": layer,
                "name": name,
                "center_dbu": inferred["center_dbu"],
                "orientation": inferred["orientation"],
                "width_um": inferred["width_um"],
                "port_type": port_type,
                "net": net,
                "target_layer": target_layer,
                "show_label": show_label,
                "access_mode": inferred["access_mode"],
                "slide_allowed": inferred["slide_allowed"],
                "slide_edge": inferred["slide_edge"],
            },
        )
        marked["attached"] = inferred["attached"]
        marked["slide_edge"] = inferred["slide_edge"]
        ports.append(marked)

    deleted = 0
    deleted_instances = 0
    if delete_markers:
        deleted_result = client.shape_delete(
            cell,
            layers=[layer],
            kinds=["boxes", "polygons", "paths"],
            limit=limit,
        )
        deleted = int(deleted_result.get("deleted", 0))
        if delete_marker_instances:
            deleted_instances = _delete_marker_instances(client, cell, layer, limit)

    return {
        "cell": cell,
        "recognized": len(ports),
        "ports": ports,
        "deleted_markers": deleted,
        "deleted_marker_instances": deleted_instances,
        "skipped_markers": skipped_markers,
    }
