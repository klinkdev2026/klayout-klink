"""Client-side Anchor workflows composed from primitive RPCs."""

from __future__ import annotations

from klink.errors import KLinkServerError

from .inference import infer_anchor_marker
from .naming import auto_id


def _existing_anchor_ids(client, cell) -> set[str]:
    listed = client.call("anchor.list", {"cell": cell})
    return {
        str(a.get("id"))
        for a in listed.get("anchors", [])
        if a.get("recognized") and a.get("id")
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
        if pcell.get("name") in ("Anchor", "BendAnchor", "WaypointAnchor", "CorridorAnchor"):
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


def recognize_handdrawn_anchors(
    client,
    cell,
    *,
    layer: str = "999/1",
    net: str = "",
    mode: str = "flexible",
    required: bool = True,
    show_label: bool = True,
    delete_markers: bool = True,
    delete_marker_instances: bool = True,
    limit: int = 5000,
) -> dict:
    """Convert raw anchor marker shapes into Anchor PCells.

    Valid raw grammar:
      - triangle polygon -> bend_region
      - box              -> waypoint_region
      - path             -> corridor
    """
    layout = client.layout_info()
    dbu = float(layout["dbu"])
    query = client.shape_query(
        cell,
        layers=[layer],
        kinds=["boxes", "polygons", "paths"],
        limit=limit,
    )
    shapes = list(query.get("shapes", []))
    existing_ids = _existing_anchor_ids(client, cell)

    anchors = []
    skipped_markers = 0
    for index, shape in enumerate(shapes):
        inferred = infer_anchor_marker(
            shape,
            dbu=dbu,
            default_net=net,
            default_mode=mode,
            default_required=required,
        )
        if inferred is None:
            skipped_markers += 1
            continue
        anchor_id = auto_id(existing_ids, index=index)
        existing_ids.add(anchor_id)
        marked = client.call(
            "anchor.mark",
            {
                "cell": cell,
                "layer": layer,
                "id": anchor_id,
                "center_dbu": inferred["center_dbu"],
                "kind": inferred["kind"],
                "mode": inferred["mode"],
                "net": inferred["net"],
                "required": inferred["required"],
                "radius_um": inferred["radius_um"],
                "width_um": inferred["width_um"],
                "height_um": inferred["height_um"],
                "orientation": inferred["orientation"],
                "path_points": inferred["path_points"],
                "show_label": show_label,
            },
        )
        marked["source_type"] = shape.get("type")
        anchors.append(marked)

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
        "layer": layer,
        "recognized": len(anchors),
        "anchors": anchors,
        "deleted_markers": deleted,
        "deleted_marker_instances": deleted_instances,
        "skipped_markers": skipped_markers,
        "truncated": bool(query.get("truncated", False)),
    }


def standardize_anchors(
    client,
    cell,
    *,
    layer: str = "999/1",
    net: str = "",
    mode: str = "flexible",
    required: bool = True,
    show_label: bool = True,
    repair_existing: bool = True,
    delete_markers: bool = True,
    delete_marker_instances: bool = True,
    limit: int = 5000,
) -> dict:
    """Standardize the anchor control layer for routing workflows.

    This is the user-facing "make anchors usable" operation:
      1. repair GUI-inserted Anchor PCells with empty/duplicate ids,
      2. convert hand-drawn triangle/box/path markers into concrete anchors,
      3. repair once more so the final layer has stable unique ids.
    """
    before_repair = {"repaired": 0, "anchors": [], "duplicate_ids_before": []}
    after_repair = {"repaired": 0, "anchors": [], "duplicate_ids_before": []}
    if repair_existing:
        before_repair = client.call("anchor.repair_ids", {"cell": cell, "layer": layer})

    recognized = recognize_handdrawn_anchors(
        client,
        cell,
        layer=layer,
        net=net,
        mode=mode,
        required=required,
        show_label=show_label,
        delete_markers=delete_markers,
        delete_marker_instances=delete_marker_instances,
        limit=limit,
    )

    if repair_existing:
        after_repair = client.call("anchor.repair_ids", {"cell": cell, "layer": layer})

    listed = client.call("anchor.list", {"cell": cell, "layer": layer, "sort": "id"})
    return {
        "cell": cell,
        "layer": layer,
        "pre_repaired": int(before_repair.get("repaired", 0)),
        "recognized": int(recognized.get("recognized", 0)),
        "post_repaired": int(after_repair.get("repaired", 0)),
        "skipped_markers": int(recognized.get("skipped_markers", 0)),
        "deleted_markers": int(recognized.get("deleted_markers", 0)),
        "deleted_marker_instances": int(recognized.get("deleted_marker_instances", 0)),
        "anchors": listed.get("anchors", []),
        "count": int(listed.get("count", 0)),
        "duplicate_ids": listed.get("duplicate_ids", []),
        "recognized_detail": recognized,
        "pre_repair_detail": before_repair,
        "post_repair_detail": after_repair,
    }
