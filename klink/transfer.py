"""Cross-session transfer helpers.

This module owns transfer semantics. The KLayout plugin should stay thin:
source data is read through existing RPCs such as selection.get/layer.list, and
target writes use existing layer.ensure/shape.insert_many RPCs.
"""

from __future__ import annotations

import uuid
from typing import Any


class TransferError(ValueError):
    """Raised when a transfer request is invalid or unsafe for this mode."""


def review_flat_selection(
    selection: dict[str, Any],
    *,
    source_layers: dict[str, Any],
    source_dbu_um: float,
    layer_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    layer_lookup = _layer_lookup(source_layers)
    layer_map = layer_map or {}
    objects = _selection_objects(selection)

    shape_count = 0
    source_layer_keys: list[str] = []
    target_layer_keys: list[str] = []
    bboxes: list[list[float]] = []
    warnings: list[str] = []

    for obj in objects:
        shape = _shape_from_selection_object(obj)
        layer_key = _shape_layer_key(shape, layer_lookup)
        target_key = layer_map.get(layer_key, layer_key)
        _parse_layer_key(target_key)
        shape_count += 1
        if layer_key not in source_layer_keys:
            source_layer_keys.append(layer_key)
        if target_key not in target_layer_keys:
            target_layer_keys.append(target_key)
        bbox_dbu = shape.get("bbox_dbu")
        if isinstance(bbox_dbu, list) and len(bbox_dbu) == 4:
            bboxes.append([round(float(v) * source_dbu_um, 9) for v in bbox_dbu])

    if shape_count == 0:
        raise TransferError("empty selection")
    if bool(selection.get("truncated")):
        warnings.append("source selection was truncated")

    return {
        "copy_mode": "flat_selection",
        "shape_count": shape_count,
        "layers": source_layer_keys,
        "target_layers": target_layer_keys,
        "bbox_um": _bbox_union_um(bboxes),
        "warnings": warnings,
    }


def build_flat_selection_package(
    selection: dict[str, Any],
    *,
    source_layers: dict[str, Any],
    source_dbu_um: float,
    source_session: str,
    target_session: str,
    target_cell: str,
    layer_map: dict[str, str] | None = None,
    translate_um: list[float] | tuple[float, float] | None = None,
) -> dict[str, Any]:
    if source_session == target_session:
        raise TransferError("source_session and target_session must be different")
    if not target_cell:
        raise TransferError("target_cell is required")

    layer_lookup = _layer_lookup(source_layers)
    layer_map = layer_map or {}
    dx, dy = _translate(translate_um)
    review = review_flat_selection(
        selection,
        source_layers=source_layers,
        source_dbu_um=source_dbu_um,
        layer_map=layer_map,
    )
    items = []
    for obj in _selection_objects(selection):
        shape = _shape_from_selection_object(obj)
        layer_key = _shape_layer_key(shape, layer_lookup)
        target_layer, target_datatype = _parse_layer_key(layer_map.get(layer_key, layer_key))
        item = _shape_to_insert_item(
            shape,
            source_dbu_um=source_dbu_um,
            target_layer=target_layer,
            target_datatype=target_datatype,
            dx_um=dx,
            dy_um=dy,
        )
        items.append(item)

    return {
        "package_id": f"xfer_{uuid.uuid4().hex[:12]}",
        "version": 1,
        "copy_mode": "flat_selection",
        "source_session": source_session,
        "target_session": target_session,
        "target_cell": target_cell,
        "placement": {
            "mode": "translate",
            "translate_um": [dx, dy],
        },
        "review": review,
        "items": items,
    }


def build_shallow_instance_package(
    source_instances: dict[str, Any],
    *,
    target_cells: dict[str, Any],
    source_dbu_um: float,
    source_session: str,
    target_session: str,
    target_cell: str,
    translate_um: list[float] | tuple[float, float] | None = None,
) -> dict[str, Any]:
    if not target_cell:
        raise TransferError("target_cell is required")
    review = review_shallow_instance(
        source_instances,
        target_cells=target_cells,
        source_session=source_session,
        target_session=target_session,
    )
    if not review["ok_to_commit"]:
        raise TransferError(
            "missing target child cells required for shallow copy: "
            + ", ".join(review["missing_child_cells"])
        )
    dx, dy = _translate(translate_um)
    items = []
    for inst in source_instances["instances"]:
        child = inst.get("child") or inst.get("target_cell")
        trans = inst.get("trans") or {}
        x_um = _trans_value_um(trans, "dx_dbu", "x_dbu", source_dbu_um=source_dbu_um) + dx
        y_um = _trans_value_um(trans, "dy_dbu", "y_dbu", source_dbu_um=source_dbu_um) + dy
        item = {
            "child": child,
            "position_um": [round(x_um, 9), round(y_um, 9)],
            "rotation": float(trans.get("rotation_deg", trans.get("rotation", 0.0))),
            "mirror": bool(trans.get("mirror", False)),
            "magnification": float(trans.get("magnification", trans.get("mag", 1.0))),
        }
        if inst.get("array"):
            item["array"] = dict(inst["array"])
        items.append(item)

    return {
        "package_id": f"xfer_{uuid.uuid4().hex[:12]}",
        "version": 1,
        "copy_mode": "shallow_instance",
        "source_session": source_session,
        "target_session": target_session,
        "target_cell": target_cell,
        "placement": {
            "mode": "translate",
            "translate_um": [dx, dy],
        },
        "review": review,
        "items": items,
    }


def commit_flat_selection_package(target_client: Any, package: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    if package.get("version") != 1:
        raise TransferError("unsupported transfer package version")
    if package.get("copy_mode") != "flat_selection":
        raise TransferError("only flat_selection packages are supported")
    target_cell = package.get("target_cell")
    if not target_cell:
        raise TransferError("package target_cell is required")
    raw_items = package.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise TransferError("package has no items")

    layer_index_by_key: dict[str, int] = {}
    items = []
    for raw in raw_items:
        item = dict(raw)
        layer = int(item.pop("layer"))
        datatype = int(item.pop("datatype", 0))
        key = f"{layer}/{datatype}"
        if key not in layer_index_by_key:
            ensured = target_client.layer_ensure(layer, datatype)
            layer_index_by_key[key] = int(ensured["layer_index"])
        item["layer_index"] = layer_index_by_key[key]
        items.append(item)

    write = target_client.shape_insert_many(target_cell, items, dry_run=dry_run)
    return {
        "ok": True,
        "package_id": package.get("package_id"),
        "dry_run": bool(dry_run),
        "target_cell": target_cell,
        "review": package.get("review", {}),
        "write": write,
    }


def commit_shallow_instance_package(target_client: Any, package: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    if package.get("version") != 1:
        raise TransferError("unsupported transfer package version")
    if package.get("copy_mode") != "shallow_instance":
        raise TransferError("only shallow_instance packages are supported")
    target_cell = package.get("target_cell")
    if not target_cell:
        raise TransferError("package target_cell is required")
    raw_items = package.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise TransferError("package has no items")
    write = target_client.instance_insert_many(
        target_cell,
        [dict(item) for item in raw_items],
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "package_id": package.get("package_id"),
        "dry_run": bool(dry_run),
        "target_cell": target_cell,
        "review": package.get("review", {}),
        "write": write,
    }


def review_shallow_instance(
    source_instances: dict[str, Any],
    *,
    target_cells: dict[str, Any],
    source_session: str,
    target_session: str,
) -> dict[str, Any]:
    if source_session == target_session:
        raise TransferError("source_session and target_session must be different")
    instances = source_instances.get("instances")
    if not isinstance(instances, list) or not instances:
        raise TransferError("shallow_instance requires selected instances")

    target_names = _cell_names(target_cells)
    child_names: list[str] = []
    for inst in instances:
        child = inst.get("child") or inst.get("target_cell")
        if not isinstance(child, str) or not child:
            raise TransferError("source instance is missing child cell name")
        if child not in child_names:
            child_names.append(child)

    missing = [name for name in child_names if name not in target_names]
    reused = [name for name in child_names if name in target_names]
    warnings = []
    if missing:
        warnings.append("target is missing child cells required by shallow copy")
    if reused:
        warnings.append("shallow copy will reuse existing target child cells")

    return {
        "copy_mode": "shallow_instance",
        "source_session": source_session,
        "target_session": target_session,
        "instance_count": len(instances),
        "child_cells": child_names,
        "missing_child_cells": missing,
        "reused_target_cells": reused,
        "ok_to_commit": not missing,
        "warnings": warnings,
    }


def review_deep_cell_tree(
    source_tree: dict[str, Any],
    *,
    target_cells: dict[str, Any],
    source_session: str,
    target_session: str,
) -> dict[str, Any]:
    if source_session == target_session:
        raise TransferError("source_session and target_session must be different")
    source_names = _source_tree_cell_names(source_tree)
    if not source_names:
        raise TransferError("deep_cell_tree requires source cells")
    target_names = _cell_names(target_cells)
    conflicts = [name for name in source_names if name in target_names]
    return {
        "copy_mode": "deep_cell_tree",
        "source_session": source_session,
        "target_session": target_session,
        "source_top_cell": source_tree.get("top_cell") or (source_names[0] if source_names else None),
        "source_cells": source_names,
        "source_cell_count": len(source_names),
        "target_conflicts": conflicts,
        "rename_policy": "klayout_native_dollar_suffix",
        "expected_behavior": "KLayout creates unique variants such as CELL$1",
        "ok_to_commit": True,
        "warnings": (
            ["target has name conflicts; KLayout native copy will create $N variants"]
            if conflicts else []
        ),
    }


def _selection_objects(selection: dict[str, Any]) -> list[dict[str, Any]]:
    objects = selection.get("objects")
    if not isinstance(objects, list) or not objects:
        raise TransferError("empty selection")
    return objects


def _shape_from_selection_object(obj: dict[str, Any]) -> dict[str, Any]:
    if obj.get("is_cell_inst") or obj.get("kind") == "instance":
        raise TransferError("instances are not supported by flat_selection transfer")
    shape = obj.get("shape")
    if not isinstance(shape, dict):
        raise TransferError("selection object does not contain shape data")
    return shape


def _layer_lookup(source_layers: dict[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for layer in source_layers.get("layers", []):
        try:
            idx = int(layer["layer_index"])
            out[idx] = f"{int(layer['layer'])}/{int(layer.get('datatype', 0))}"
        except Exception:
            continue
    return out


def _shape_layer_key(shape: dict[str, Any], layer_lookup: dict[int, str]) -> str:
    try:
        layer_index = int(shape["layer_index"])
    except Exception as exc:
        raise TransferError("shape is missing layer_index") from exc
    layer_key = layer_lookup.get(layer_index)
    if layer_key is None:
        raise TransferError(f"source layer_index {layer_index} is not in layer.list")
    return layer_key


def _parse_layer_key(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or "/" not in value:
        raise TransferError(f"layer key must be 'L/D', got {value!r}")
    left, right = value.split("/", 1)
    try:
        return int(left), int(right)
    except ValueError as exc:
        raise TransferError(f"layer key must be 'L/D', got {value!r}") from exc


def _translate(value: list[float] | tuple[float, float] | None) -> tuple[float, float]:
    if value is None:
        return 0.0, 0.0
    if len(value) != 2:
        raise TransferError("translate_um must be [dx, dy]")
    return float(value[0]), float(value[1])


def _trans_value_um(trans: dict[str, Any], *keys: str, source_dbu_um: float) -> float:
    for key in keys:
        if key in trans:
            return float(trans[key]) * source_dbu_um
    return 0.0


def _shape_to_insert_item(
    shape: dict[str, Any],
    *,
    source_dbu_um: float,
    target_layer: int,
    target_datatype: int,
    dx_um: float,
    dy_um: float,
) -> dict[str, Any]:
    kind = str(shape.get("type", shape.get("kind", ""))).lower()
    base = {"kind": kind, "layer": target_layer, "datatype": target_datatype}
    if kind == "box":
        base["bbox_um"] = _bbox_to_um(shape["bbox_dbu"], source_dbu_um, dx_um, dy_um)
        return base
    if kind == "polygon":
        base["points_um"] = _points_to_um(shape["points_dbu"], source_dbu_um, dx_um, dy_um)
        return base
    if kind == "path":
        base["points_um"] = _points_to_um(shape["points_dbu"], source_dbu_um, dx_um, dy_um)
        base["width_um"] = round(float(shape["width_dbu"]) * source_dbu_um, 9)
        for source_key, target_key in (
            ("begin_ext_dbu", "begin_ext_um"),
            ("end_ext_dbu", "end_ext_um"),
        ):
            if source_key in shape:
                base[target_key] = round(float(shape[source_key]) * source_dbu_um, 9)
        return base
    if kind == "text":
        base["string"] = str(shape.get("string", shape.get("text", "")))
        point = shape.get("position_dbu") or shape.get("bbox_dbu", [0, 0])[:2]
        base["position_um"] = _point_to_um(point, source_dbu_um, dx_um, dy_um)
        if "size_dbu" in shape:
            base["size_um"] = round(float(shape["size_dbu"]) * source_dbu_um, 9)
        return base
    raise TransferError(f"unsupported shape type: {kind!r}")


def _bbox_to_um(bbox: list[Any], dbu_um: float, dx_um: float, dy_um: float) -> list[float]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise TransferError("bbox_dbu must be [x1,y1,x2,y2]")
    return [
        round(float(bbox[0]) * dbu_um + dx_um, 9),
        round(float(bbox[1]) * dbu_um + dy_um, 9),
        round(float(bbox[2]) * dbu_um + dx_um, 9),
        round(float(bbox[3]) * dbu_um + dy_um, 9),
    ]


def _point_to_um(point: list[Any], dbu_um: float, dx_um: float, dy_um: float) -> list[float]:
    if not isinstance(point, list) or len(point) != 2:
        raise TransferError("point must be [x,y]")
    return [
        round(float(point[0]) * dbu_um + dx_um, 9),
        round(float(point[1]) * dbu_um + dy_um, 9),
    ]


def _points_to_um(points: list[Any], dbu_um: float, dx_um: float, dy_um: float) -> list[list[float]]:
    if not isinstance(points, list):
        raise TransferError("points_dbu must be a list")
    return [_point_to_um(point, dbu_um, dx_um, dy_um) for point in points]


def _bbox_union_um(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _cell_names(cell_list: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for cell in cell_list.get("cells", []):
        name = cell.get("name")
        if isinstance(name, str):
            out.add(name)
    return out


def _source_tree_cell_names(source_tree: dict[str, Any]) -> list[str]:
    if isinstance(source_tree.get("cells"), list):
        names = []
        for cell in source_tree["cells"]:
            name = cell.get("name")
            if isinstance(name, str) and name not in names:
                names.append(name)
        return names

    tree = source_tree.get("tree")
    names: list[str] = []

    def walk(node):
        if not isinstance(node, dict):
            return
        name = node.get("name")
        if isinstance(name, str) and name not in names:
            names.append(name)
        for child in node.get("children", []) or []:
            walk(child)

    walk(tree)
    return names
