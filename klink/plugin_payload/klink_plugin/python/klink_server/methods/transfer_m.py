"""Thin pending-transfer RPCs.

The external klink package owns transfer review, layer mapping, conflict
policy, and package generation. These plugin methods only store and paste an
already-reviewed flat-selection package in the target KLayout process.
"""

from __future__ import annotations

from pathlib import Path

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method
from ..transfer_pending import clear_pending, get_pending, set_pending, status
from ..txn import auto_txn, register_custom_edit
from .cell_m import _active_layout, _resolve_cell
from .instance_m import _bbox_union_lists, _build_cell_inst_array, _build_trans, _inst_bbox, _validate_batch_items
from .shape_m import _shape_op_from_item

_DEEP_COPY_UNDO_BACKEND = "klink_custom"
_DEEP_COPY_UNDO_NOTE = (
    "Package-based cross-layout Cell.copy_tree import uses KLayout native copy "
    "semantics and registers a klink custom undo/redo entry for the created cell tree."
)


@method(
    "transfer.pending_set",
    description="Store an already-reviewed flat-selection transfer package in this KLayout window.",
    params_schema={
        "type": "object",
        "required": ["package"],
        "properties": {"package": {"type": "object"}},
    },
    returns_schema={"type": "object"},
    mutates=False,
    tags=["transfer"],
)
def transfer_pending_set(params, ctx):
    try:
        return set_pending(params.get("package"))
    except ValueError as exc:
        raise RpcError(ErrorCode.BAD_PARAMS, str(exc))


@method(
    "transfer.pending_status",
    description="Return the pending transfer package status for this KLayout window.",
    params_schema={"type": "object"},
    returns_schema={"type": "object"},
    tags=["transfer"],
)
def transfer_pending_status(params, ctx):
    return status()


@method(
    "transfer.pending_clear",
    description="Clear this KLayout window's pending transfer package without writing layout geometry.",
    params_schema={"type": "object"},
    returns_schema={"type": "object"},
    mutates=False,
    tags=["transfer"],
)
def transfer_pending_clear(params, ctx):
    return clear_pending()


@method(
    "transfer.paste_pending",
    description=(
        "Paste the currently pending flat-selection transfer package into "
        "this KLayout window. The package must already contain final target "
        "layers and coordinates."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "dry_run": {"type": "boolean", "default": False},
            "clear_after": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["transfer", "write"],
)
def transfer_paste_pending(params, ctx):
    package = get_pending()
    if package is None:
        raise RpcError(ErrorCode.NOT_FOUND, "no pending transfer package")
    copy_mode = package.get("copy_mode")
    if package.get("version") != 1 or copy_mode not in {"flat_selection", "shallow_instance"}:
        raise RpcError(ErrorCode.BAD_PARAMS, "unsupported pending transfer package")

    dry_run = bool(params.get("dry_run", False))
    clear_after = bool(params.get("clear_after", True))
    target_cell = package.get("target_cell")
    items = package.get("items")
    if not target_cell:
        raise RpcError(ErrorCode.BAD_PARAMS, "pending package target_cell is required")
    if not isinstance(items, list) or not items:
        raise RpcError(ErrorCode.BAD_PARAMS, "pending package items must not be empty")

    view, _, ly = _active_layout()
    if copy_mode == "shallow_instance":
        return _paste_pending_shallow_instance(view, ly, package, target_cell, items, dry_run, clear_after)
    return _paste_pending_flat_selection(view, ly, package, target_cell, items, dry_run, clear_after)


def _paste_pending_flat_selection(view, ly, package, target_cell, items, dry_run: bool, clear_after: bool):
    cell = _resolve_cell(ly, target_cell)
    by_layer = {}
    by_kind = {}
    bboxes = []

    if dry_run:
        for index, raw in enumerate(items):
            try:
                kind, layer_key, bbox = _dry_run_item_summary(ly, raw)
            except RpcError as exc:
                raise RpcError(exc.code, f"items[{index}]: {exc.message}", hint=exc.hint)
            bboxes.append(bbox)
            by_layer[layer_key] = by_layer.get(layer_key, 0) + 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
    else:
        ops = []
        with auto_txn(view, f"klink: paste transfer {package.get('package_id')}"):
            for index, raw in enumerate(items):
                try:
                    item = _with_layer_index(ly, raw, create_missing=True)
                    kind, layer_idx, shape, bbox = _shape_op_from_item(ly, item)
                except RpcError as exc:
                    raise RpcError(exc.code, f"items[{index}]: {exc.message}", hint=exc.hint)
                ops.append((kind, layer_idx, shape))
                bboxes.append(bbox)
                layer_key = _layer_key(ly, layer_idx)
                by_layer[layer_key] = by_layer.get(layer_key, 0) + 1
                by_kind[kind] = by_kind.get(kind, 0) + 1
            for _, layer_idx, shape in ops:
                cell.shapes(layer_idx).insert(shape)
        if clear_after:
            clear_pending()

    bbox = _bbox_union(bboxes)
    return {
        "ok": True,
        "package_id": package.get("package_id"),
        "cell": cell.name,
        "requested": len(items),
        "inserted": 0 if dry_run else len(items),
        "bbox_dbu": [bbox.left, bbox.bottom, bbox.right, bbox.top],
        "by_kind": by_kind,
        "by_layer": by_layer,
        "dry_run": dry_run,
        "pending_cleared": (not dry_run) and clear_after,
    }


def _paste_pending_shallow_instance(view, ly, package, target_cell, items, dry_run: bool, clear_after: bool):
    parent = _resolve_cell(ly, target_cell)
    items = _validate_batch_items(items, "items")
    ops = []
    by_child = {}
    bboxes = []
    for index, item in enumerate(items):
        if "child" not in item:
            raise RpcError(ErrorCode.BAD_PARAMS, f"items[{index}]: 'child' is required")
        child = _resolve_cell(ly, item["child"])
        if parent.cell_index() == child.cell_index():
            raise RpcError(ErrorCode.BAD_PARAMS, f"items[{index}]: a cell cannot instantiate itself")
        trans = _build_trans(item, ly.dbu)
        inst_spec, array_info = _build_cell_inst_array(
            child.cell_index(), trans, item.get("array"), ly.dbu
        )
        ops.append((child, inst_spec, array_info))
        by_child[child.name] = by_child.get(child.name, 0) + 1

    if dry_run:
        return {
            "ok": True,
            "package_id": package.get("package_id"),
            "parent": parent.name,
            "requested": len(ops),
            "inserted": 0,
            "by_child": by_child,
            "bbox_dbu": None,
            "dry_run": True,
            "pending_cleared": False,
        }

    with auto_txn(view, f"klink: paste transfer {package.get('package_id')}"):
        for _, inst_spec, _ in ops:
            inst = parent.insert(inst_spec)
            bboxes.append(_inst_bbox(inst))
    if clear_after:
        clear_pending()
    return {
        "ok": True,
        "package_id": package.get("package_id"),
        "parent": parent.name,
        "requested": len(ops),
        "inserted": len(ops),
        "by_child": by_child,
        "bbox_dbu": _bbox_union_lists(bboxes),
        "dry_run": False,
        "pending_cleared": clear_after,
    }


@method(
    "transfer.import_cell_tree_package",
    description=(
        "Import one cell tree from a GDS/OAS package into this KLayout "
        "window using KLayout's native Cell.copy_tree behavior. Name "
        "conflicts are resolved by KLayout with '$N' suffixes."
    ),
    params_schema={
        "type": "object",
        "required": ["path", "source_cell"],
        "properties": {
            "path": {"type": "string"},
            "source_cell": {"type": "string"},
            "dry_run": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    returns_schema={"type": "object"},
    mutates=True,
    long_running=True,
    tags=["transfer", "write"],
)
def transfer_import_cell_tree_package(params, ctx):
    path = Path(str(params.get("path") or ""))
    source_cell_name = str(params.get("source_cell") or "")
    dry_run = bool(params.get("dry_run", False))
    if not path.exists():
        raise RpcError(ErrorCode.NOT_FOUND, f"package file not found: {path}")
    if not source_cell_name:
        raise RpcError(ErrorCode.BAD_PARAMS, "source_cell is required")

    view, _, target_layout = _active_layout()
    source_layout = pya.Layout()
    try:
        source_layout.read(str(path))
    except Exception as exc:
        raise RpcError(ErrorCode.BAD_PARAMS, f"failed to read package: {exc}")
    source_cell = source_layout.cell(source_cell_name)
    if source_cell is None:
        raise RpcError(ErrorCode.NOT_FOUND, f"source cell {source_cell_name!r} not found in package")

    conflicts = sorted(name for name in _cell_tree_names(source_cell) if target_layout.cell(name) is not None)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "source_cell": source_cell_name,
            "source_cells": sorted(_cell_tree_names(source_cell)),
            "target_conflicts": conflicts,
            "rename_policy": "klayout_native_dollar_suffix",
            "undoable": True,
            "undo_backend": _DEEP_COPY_UNDO_BACKEND,
            "undo_note": _DEEP_COPY_UNDO_NOTE,
            "copied_top_cell": None,
            "created_cells": [],
        }

    result = _copy_cell_tree_into_layout(view, target_layout, source_cell, source_cell_name)
    copied_top_name = result["copied_top_cell"]
    created = result["created_cells"]
    state = {
        "copied_top_cell": copied_top_name,
        "created_cells": list(created),
    }

    def _undo_imported_tree():
        return _delete_imported_cell_tree(target_layout, state["copied_top_cell"], state["created_cells"])

    def _redo_imported_tree():
        source_layout_redo = pya.Layout()
        source_layout_redo.read(str(path))
        source_cell_redo = source_layout_redo.cell(source_cell_name)
        if source_cell_redo is None:
            return False
        redo = _copy_cell_tree_into_layout(
            view,
            target_layout,
            source_cell_redo,
            source_cell_name,
            use_transaction=False,
        )
        state["copied_top_cell"] = redo["copied_top_cell"]
        state["created_cells"] = list(redo["created_cells"])
        return bool(state["created_cells"])

    if created:
        register_custom_edit(
            f"klink: import cell tree {copied_top_name!r}",
            _undo_imported_tree,
            _redo_imported_tree,
        )

    return {
        "ok": True,
        "dry_run": False,
        "source_cell": source_cell_name,
        "source_cells": sorted(_cell_tree_names(source_cell)),
        "target_conflicts": conflicts,
        "rename_policy": "klayout_native_dollar_suffix",
        "undoable": True,
        "undo_backend": _DEEP_COPY_UNDO_BACKEND,
        "undo_note": _DEEP_COPY_UNDO_NOTE,
        "copied_top_cell": copied_top_name,
        "created_cells": created,
        "created_cell_count": len(created),
    }


def _copy_cell_tree_into_layout(
    view,
    target_layout: pya.Layout,
    source_cell: pya.Cell,
    source_cell_name: str,
    *,
    use_transaction: bool = True,
) -> dict:
    before = _cell_names(target_layout)
    if use_transaction:
        with auto_txn(view, f"klink: import cell tree {source_cell_name!r}"):
            copied_top = target_layout.create_cell(source_cell.name)
            copied_top.copy_tree(source_cell)
    else:
        copied_top = target_layout.create_cell(source_cell.name)
        copied_top.copy_tree(source_cell)
        _refresh_view_after_deep_import(view)
    after = _cell_names(target_layout)
    return {
        "copied_top_cell": copied_top.name,
        "created_cells": sorted(after - before),
    }


def _refresh_view_after_deep_import(view) -> None:
    try:
        if view is not None:
            view.add_missing_layers()
    except Exception:
        pass
    try:
        if view is not None:
            view.update_content()
    except Exception:
        pass


def _delete_imported_cell_tree(layout: pya.Layout, copied_top_cell: str, created_cells: list[str]) -> bool:
    created = {str(name) for name in created_cells}
    if not created:
        return False
    had_cells = any(layout.cell(name) is not None for name in created)
    if not had_cells:
        return False
    top = layout.cell(str(copied_top_cell))
    if top is not None and top.name in created:
        try:
            layout.delete_cell_rec(top.cell_index())
        except Exception:
            try:
                layout.delete_cell(top.cell_index())
            except Exception:
                pass

    # delete_cell_rec should remove orphaned children. Sweep any leftovers in
    # parent-before-child order for cases where KLayout kept a referenced cell.
    remaining = set(created)
    for _ in range(len(created) + 1):
        deleted_any = False
        for name in list(remaining):
            cell = layout.cell(name)
            if cell is None:
                remaining.remove(name)
                continue
            if _has_parent_in_set(layout, cell, remaining):
                continue
            try:
                layout.delete_cell(cell.cell_index())
            except Exception:
                continue
            remaining.remove(name)
            deleted_any = True
        if not remaining or not deleted_any:
            break
    return True


def _has_parent_in_set(layout: pya.Layout, cell: pya.Cell, names: set[str]) -> bool:
    cell_index = int(cell.cell_index())
    for name in names:
        parent = layout.cell(name)
        if parent is None or int(parent.cell_index()) == cell_index:
            continue
        try:
            for child_index in parent.each_child_cell():
                if int(child_index) == cell_index:
                    return True
        except Exception:
            pass
    return False


def _with_layer_index(layout: pya.Layout, raw: dict, *, create_missing: bool) -> dict:
    if not isinstance(raw, dict):
        raise RpcError(ErrorCode.BAD_PARAMS, "each item must be an object")
    item = dict(raw)
    if "layer_index" in item:
        return item
    if "layer" not in item:
        raise RpcError(ErrorCode.BAD_PARAMS, "item requires layer or layer_index")
    layer = int(item.pop("layer"))
    datatype = int(item.pop("datatype", 0))
    info = pya.LayerInfo(layer, datatype)
    idx = layout.find_layer(info)
    if idx is None:
        if not create_missing:
            raise RpcError(ErrorCode.NOT_FOUND, f"layer {layer}/{datatype} not present")
        idx = layout.insert_layer(info)
    item["layer_index"] = int(idx)
    return item


def _dry_run_item_summary(layout: pya.Layout, raw: dict) -> tuple[str, str, pya.Box]:
    if not isinstance(raw, dict):
        raise RpcError(ErrorCode.BAD_PARAMS, "each item must be an object")
    kind = str(raw.get("kind", raw.get("type", ""))).lower()
    if "layer_index" in raw:
        layer_key = _layer_key(layout, int(raw["layer_index"]))
    else:
        layer = int(raw.get("layer"))
        datatype = int(raw.get("datatype", 0))
        layer_key = f"{layer}/{datatype}"
    bbox = _item_bbox_dbu(layout, raw, kind)
    return kind, layer_key, bbox


def _item_bbox_dbu(layout: pya.Layout, item: dict, kind: str) -> pya.Box:
    dbu = float(layout.dbu)
    if "bbox_dbu" in item:
        b = item["bbox_dbu"]
        return pya.Box(int(b[0]), int(b[1]), int(b[2]), int(b[3]))
    if "bbox_um" in item:
        b = item["bbox_um"]
        return pya.Box(_um_to_dbu(b[0], dbu), _um_to_dbu(b[1], dbu), _um_to_dbu(b[2], dbu), _um_to_dbu(b[3], dbu))
    if kind in ("polygon", "path"):
        points = item.get("points_dbu")
        if points is None:
            points = [[_um_to_dbu(x, dbu), _um_to_dbu(y, dbu)] for x, y in item.get("points_um", [])]
        xs = [int(p[0]) for p in points]
        ys = [int(p[1]) for p in points]
        if not xs or not ys:
            raise RpcError(ErrorCode.BAD_PARAMS, f"{kind} item requires points")
        half = 0
        if kind == "path":
            width = item.get("width_dbu")
            if width is None:
                width = _um_to_dbu(item.get("width_um", 0), dbu)
            half = int(width) // 2
        return pya.Box(min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
    if kind == "text":
        point = item.get("position_dbu")
        if point is None:
            p = item.get("position_um", [0, 0])
            point = [_um_to_dbu(p[0], dbu), _um_to_dbu(p[1], dbu)]
        return pya.Box(int(point[0]), int(point[1]), int(point[0]), int(point[1]))
    raise RpcError(ErrorCode.BAD_PARAMS, f"unknown item kind {kind!r}")


def _um_to_dbu(value, dbu: float) -> int:
    return int(round(float(value) / dbu))


def _cell_names(layout: pya.Layout) -> set[str]:
    return {cell.name for cell in layout.each_cell()}


def _cell_tree_names(root: pya.Cell) -> set[str]:
    names: set[str] = set()

    def walk(cell: pya.Cell) -> None:
        if cell.name in names:
            return
        names.add(cell.name)
        layout = cell.layout()
        for child_index in cell.each_child_cell():
            child = layout.cell(child_index)
            if child is not None:
                walk(child)

    walk(root)
    return names


def _layer_key(layout: pya.Layout, layer_idx: int) -> str:
    try:
        info = layout.get_info(layer_idx)
        return f"{info.layer}/{info.datatype}"
    except Exception:
        return f"idx_{layer_idx}"


def _bbox_union(boxes: list) -> pya.Box:
    if not boxes:
        return pya.Box(0, 0, 0, 0)
    left = min(box.left for box in boxes)
    bottom = min(box.bottom for box in boxes)
    right = max(box.right for box in boxes)
    top = max(box.top for box in boxes)
    return pya.Box(left, bottom, right, top)
