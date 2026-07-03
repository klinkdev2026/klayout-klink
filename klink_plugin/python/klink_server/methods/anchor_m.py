"""Thin Anchor PCell RPC methods.

This module is limited to KLayout-side Anchor PCell CRUD. Recognition of
hand-drawn anchor markers and routing policy lives in the external ``klink``
client package.
"""

from __future__ import annotations

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method
from ..txn import auto_txn
from .cell_m import _active_layout, _resolve_cell
from .shape_m import _point_from_um_or_dbu


# Stored as Layout meta info (follows the layout's lifetime); the old
# id(ly)-keyed module dict was unsafe to id() reuse and module reloads.
_DEFAULT_ANCHOR_LAYER_KEY = "klink_default_anchor_layer"


def _get_anchor_layer_str(params: dict, ly: pya.Layout) -> str:
    if "layer" in params:
        return str(params["layer"])
    try:
        value = ly.meta_info_value(_DEFAULT_ANCHOR_LAYER_KEY)
    except Exception:
        value = None
    return str(value) if value else "999/1"


def _parse_layer_ld(layer_str: str) -> tuple[int, int]:
    try:
        left, right = str(layer_str).split("/", 1)
        return int(left), int(right)
    except Exception:
        raise RpcError(ErrorCode.BAD_PARAMS, "layer must be 'L/D' format, e.g. '999/1'")


def _resolve_anchor_layer_idx(ly: pya.Layout, layer_str: str) -> int:
    layer, datatype = _parse_layer_ld(layer_str)
    info = pya.LayerInfo(layer, datatype)
    idx = ly.find_layer(info)
    if idx is None:
        idx = ly.insert_layer(info)
    return int(idx)


def _decl_name(decl) -> str:
    try:
        raw = decl.name
        return str(raw() if callable(raw) else raw)
    except Exception:
        return ""


def _pcell_params_to_dict(variant_cell) -> dict:
    out: dict = {}
    try:
        decl = variant_cell.pcell_declaration()
        if decl is None:
            return out
        pcell_name = _decl_name(decl)
        defs = list(decl.get_parameters())
        vals = list(variant_cell.pcell_parameters())
    except Exception:
        return out
    if pcell_name == "BendAnchor":
        out["kind"] = "bend_region"
    elif pcell_name == "WaypointAnchor":
        out["kind"] = "waypoint_region"
    elif pcell_name == "CorridorAnchor":
        out["kind"] = "corridor"
    for pdef, value in zip(defs, vals):
        try:
            name = str(pdef.name)
            if isinstance(value, pya.LayerInfo):
                value = "%d/%d" % (value.layer, value.datatype)
            out["id" if name == "anchor_id" else name] = value
        except Exception:
            pass
    return out


def _is_klink_anchor_instance(inst) -> bool:
    try:
        cell = inst.cell
        if cell is None:
            return False
        decl = cell.pcell_declaration()
        return decl is not None and _decl_name(decl) in (
            "BendAnchor",
            "WaypointAnchor",
            "CorridorAnchor",
        )
    except Exception:
        return False


def _inst_trans_dict(inst) -> dict:
    try:
        trans = inst.cplx_trans
    except Exception:
        try:
            trans = inst.trans
        except Exception:
            return {}
    out = {}
    try:
        out["dx_dbu"] = int(trans.disp.x)
        out["dy_dbu"] = int(trans.disp.y)
    except Exception:
        pass
    try:
        out["rotation_deg"] = float(trans.angle)
    except Exception:
        out["rotation_deg"] = 0.0
    try:
        out["mirror"] = bool(trans.is_mirror())
    except Exception:
        out["mirror"] = False
    try:
        out["magnification"] = float(trans.mag)
    except Exception:
        out["magnification"] = 1.0
    return out


def _make_anchor_dict(inst, ly: pya.Layout, cell_name: str = "") -> dict:
    params = _pcell_params_to_dict(inst.cell)
    trans = _inst_trans_dict(inst)
    dx = int(trans.get("dx_dbu", 0))
    dy = int(trans.get("dy_dbu", 0))
    out = {
        "recognized": True,
        "id": str(params.get("id", "?")),
        "name": str(params.get("id", "?")),
        "layer": str(params.get("layer", "")),
        "kind": str(params.get("kind", "bend_region")),
        "mode": str(params.get("mode", "flexible")),
        "net": str(params.get("net", "")),
        "label": str(params.get("label", "")),
        "show_label": bool(params.get("show_label", True)),
        "required": bool(params.get("required", True)),
        "priority": int(params.get("priority", 0)),
        "radius_um": float(params.get("radius_um", 0.0)),
        "width_um": float(params.get("width_um", 0.0)),
        "height_um": float(params.get("height_um", 0.0)),
        "orientation": float(params.get("orientation", 0.0)),
        "path_points": str(params.get("path_points", "")),
        "center_um": [dx * ly.dbu, dy * ly.dbu],
        "trans": trans,
    }
    if cell_name:
        out["cell"] = cell_name
    try:
        bbox = inst.bbox()
        if not bbox.empty():
            out["bbox_dbu"] = [bbox.left, bbox.bottom, bbox.right, bbox.top]
    except Exception:
        pass
    return out


def _build_anchor_pcell_params(
    anchor_layer_str: str,
    anchor_id: str,
    kind: str,
    mode: str,
    net: str,
    label: str,
    show_label: bool,
    required: bool,
    priority: int,
    radius_um: float,
    width_um: float,
    height_um: float,
    orientation: float,
    path_points: str,
) -> dict:
    layer, datatype = _parse_layer_ld(anchor_layer_str)
    common = {
        "layer": pya.LayerInfo(layer, datatype),
        "anchor_id": str(anchor_id),
        "mode": str(mode),
        "net": str(net) if net else "",
        "label": str(label) if label else "",
        "show_label": bool(show_label),
        "required": bool(required),
        "priority": int(priority),
    }
    if kind == "bend_region":
        common.update({
            "radius_um": float(radius_um),
            "orientation": float(orientation) % 360.0,
        })
    elif kind == "waypoint_region":
        common.update({
            "width_um": float(width_um),
            "height_um": float(height_um),
        })
    elif kind == "corridor":
        common.update({
            "width_um": float(width_um),
            "path_points": str(path_points) if path_points else "",
        })
    else:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "anchor kind must be bend_region, waypoint_region, or corridor",
        )
    return common


def _pcell_name_for_kind(kind: str) -> str:
    if kind == "bend_region":
        return "BendAnchor"
    if kind == "waypoint_region":
        return "WaypointAnchor"
    if kind == "corridor":
        return "CorridorAnchor"
    raise RpcError(
        ErrorCode.BAD_PARAMS,
        "anchor kind must be bend_region, waypoint_region, or corridor",
    )


def _create_anchor_variant(
    ly: pya.Layout,
    anchor_layer_str: str,
    anchor_id: str,
    kind: str,
    mode: str,
    net: str,
    label: str,
    show_label: bool,
    required: bool,
    priority: int,
    radius_um: float,
    width_um: float,
    height_um: float,
    orientation: float,
    path_points: str,
):
    params = _build_anchor_pcell_params(
        anchor_layer_str, anchor_id, kind, mode, net, label, show_label,
        required, priority, radius_um, width_um, height_um, orientation,
        path_points,
    )
    try:
        variant = ly.create_cell(_pcell_name_for_kind(kind), "klink_anchor", params)
    except Exception as exc:
        raise RpcError(
            ErrorCode.EXEC,
            "failed to create klink Anchor PCell: %s" % exc,
            hint="ensure klink_anchor library is registered",
        )
    if variant is None:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "klink Anchor PCell not found",
            hint="anchor_pcell.py must register the klink_anchor library on startup",
        )
    return variant


def _replace_anchor_instance(parent: pya.Cell, old_inst, variant) -> None:
    try:
        old_trans = old_inst.cplx_trans
    except Exception:
        old_trans = old_inst.trans
    try:
        na = int(old_inst.na)
        nb = int(old_inst.nb)
        a_vec = old_inst.a
        b_vec = old_inst.b
        has_array = na > 1 or nb > 1
    except Exception:
        has_array = False
    parent.erase(old_inst)
    if has_array:
        parent.insert(pya.CellInstArray(variant.cell_index(), old_trans, a_vec, b_vec, na, nb))
    else:
        parent.insert(pya.CellInstArray(variant.cell_index(), old_trans))


def _iter_anchor_instances(cell: pya.Cell) -> list:
    out = []
    try:
        for inst in cell.each_inst():
            if _is_klink_anchor_instance(inst):
                out.append(inst)
    except Exception:
        pass
    return out


def _find_anchor_by_id(cell: pya.Cell, anchor_id: str):
    for inst in _iter_anchor_instances(cell):
        params = _pcell_params_to_dict(inst.cell)
        if str(params.get("id", "")) == anchor_id:
            return inst
    return None


def _collect_anchor_ids(cell: pya.Cell) -> set[str]:
    ids = set()
    for inst in _iter_anchor_instances(cell):
        anchor_id = _pcell_params_to_dict(inst.cell).get("id", "")
        if anchor_id:
            ids.add(str(anchor_id))
    return ids


def _collect_anchor_id_counts(cell: pya.Cell) -> dict[str, int]:
    counts: dict[str, int] = {}
    for inst in _iter_anchor_instances(cell):
        anchor_id = str(_pcell_params_to_dict(inst.cell).get("id", ""))
        if anchor_id:
            counts[anchor_id] = counts.get(anchor_id, 0) + 1
    return counts


def _anchor_ids_need_repair(cell: pya.Cell) -> bool:
    seen: set[str] = set()
    for inst in _iter_anchor_instances(cell):
        anchor_id = str(_pcell_params_to_dict(inst.cell).get("id", ""))
        if not anchor_id or anchor_id in seen:
            return True
        seen.add(anchor_id)
    return False


def _auto_id(existing_ids: set[str], prefix: str = "A", index: int = 0) -> str:
    i = int(index)
    while True:
        candidate = "%s%d" % (prefix, i)
        if candidate not in existing_ids:
            return candidate
        i += 1


def _ensure_unique_anchor_id(cell: pya.Cell, anchor_id: str) -> None:
    if anchor_id in _collect_anchor_ids(cell):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "anchor id %r already exists in cell %s" % (anchor_id, cell.name),
            hint="anchor ids are unique handles; use net/label for semantics",
        )


def _repair_anchor_ids_in_cell(
    view,
    ly: pya.Layout,
    cell: pya.Cell,
    *,
    layer: str | None = None,
    prefix: str = "A",
) -> dict:
    used: set[str] = set()
    repaired = []
    targets = list(_iter_anchor_instances(cell))
    if not targets:
        return {"cell": cell.name, "repaired": 0, "anchors": [], "duplicate_ids_before": []}

    counts_before = _collect_anchor_id_counts(cell)
    duplicate_ids_before = sorted(anchor_id for anchor_id, count in counts_before.items() if count > 1)

    with auto_txn(view, "klink: anchor.repair_ids (%d anchors)" % len(targets)):
        for index, inst in enumerate(list(targets)):
            old = _pcell_params_to_dict(inst.cell)
            old_id = str(old.get("id", ""))
            if old_id and old_id not in used:
                used.add(old_id)
                continue

            new_id = _auto_id(used, prefix=prefix, index=index)
            used.add(new_id)
            parsed = _params_from_request({}, old)
            anchor_layer_str = str(old.get("layer") or layer or "999/1")
            variant = _create_anchor_variant(ly, anchor_layer_str, new_id, **parsed)
            _replace_anchor_instance(cell, inst, variant)
            repaired.append({
                "old_id": old_id,
                "id": new_id,
                "name": new_id,
                **parsed,
            })

    return {
        "cell": cell.name,
        "repaired": len(repaired),
        "anchors": repaired,
        "duplicate_ids_before": duplicate_ids_before,
    }


def _selected_anchor_instances(view, cell: pya.Cell) -> list:
    out = []
    try:
        for obj in view.each_object_selected():
            if not obj.is_cell_inst():
                continue
            try:
                inst = obj.inst()
                if _is_klink_anchor_instance(inst):
                    out.append(inst)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _params_from_request(params: dict, old: dict | None = None) -> dict:
    old = old or {}
    return {
        "kind": str(params.get("kind", old.get("kind", "bend_region"))),
        "mode": str(params.get("mode", old.get("mode", "flexible"))),
        "net": str(params.get("net", old.get("net", ""))),
        "label": str(params.get("label", old.get("label", ""))),
        "show_label": bool(params.get("show_label", old.get("show_label", True))),
        "required": bool(params.get("required", old.get("required", True))),
        "priority": int(params.get("priority", old.get("priority", 0))),
        "radius_um": float(params.get("radius_um", old.get("radius_um", 5.0))),
        "width_um": float(params.get("width_um", old.get("width_um", 10.0))),
        "height_um": float(params.get("height_um", old.get("height_um", 10.0))),
        "orientation": float(params.get("orientation", old.get("orientation", 0.0))) % 360.0,
        "path_points": str(params.get("path_points", old.get("path_points", ""))),
    }


@method(
    "anchor.set_layer",
    description="Configure the default Anchor PCell marker layer for this layout.",
    params_schema={
        "type": "object",
        "required": ["layer"],
        "properties": {"layer": {"type": "string", "description": "'L/D', e.g. '999/1'"}},
    },
    returns_schema={"type": "object", "properties": {"anchor_layer": {"type": "string"}}},
    mutates=True,
    tags=["anchor", "write"],
)
def anchor_set_layer(params, ctx):
    _, _, ly = _active_layout()
    layer_str = str(params["layer"])
    _resolve_anchor_layer_idx(ly, layer_str)
    ly.add_meta_info(pya.LayoutMetaInfo(_DEFAULT_ANCHOR_LAYER_KEY, layer_str))
    return {"anchor_layer": layer_str}


@method(
    "anchor.mark",
    description="Create one klink_Anchor PCell instance in a cell.",
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Target cell name or cell_index"},
            "layer": {"type": "string"},
            "id": {"type": "string"},
            "name": {"type": "string"},
            "center_um": {"type": "array", "minItems": 2, "maxItems": 2},
            "center_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
            "kind": {"type": "string"},
            "mode": {"type": "string"},
            "net": {"type": "string"},
            "label": {"type": "string"},
            "show_label": {"type": "boolean"},
            "required": {"type": "boolean"},
            "priority": {"type": "integer"},
            "radius_um": {"type": "number"},
            "width_um": {"type": "number"},
            "height_um": {"type": "number"},
            "orientation": {"type": "number"},
            "path_points": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["anchor", "write"],
)
def anchor_mark(params, ctx):
    view, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["cell"])
    anchor_layer_str = _get_anchor_layer_str(params, ly)
    x_dbu, y_dbu = _point_from_um_or_dbu(params, ly.dbu, "center_dbu", "center_um")

    anchor_id = params.get("id", params.get("name"))
    if anchor_id:
        anchor_id = str(anchor_id)
        _ensure_unique_anchor_id(parent, anchor_id)
    else:
        anchor_id = _auto_id(_collect_anchor_ids(parent))

    parsed = _params_from_request(params)
    _resolve_anchor_layer_idx(ly, anchor_layer_str)
    variant = _create_anchor_variant(ly, anchor_layer_str, anchor_id, **parsed)

    with auto_txn(view, "klink: anchor.mark %s" % anchor_id):
        parent.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(int(x_dbu), int(y_dbu))))

    return {
        "cell": parent.name,
        "id": anchor_id,
        "name": anchor_id,
        "center_um": [x_dbu * ly.dbu, y_dbu * ly.dbu],
        **parsed,
    }


@method(
    "anchor.list",
    description=(
        "List klink_Anchor PCell instances in a cell. When `layer` is given, "
        "only anchors whose marker layer matches it are returned (it also "
        "selects the repair layer); without it all anchors are returned."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "layer": {
                "type": "string",
                "description": "Marker layer filter in L/D format, e.g. '999/1'.",
            },
            "sort": {"type": "string", "enum": ["none", "id"], "default": "none"},
        },
    },
    returns_schema={"type": "object"},
    tags=["anchor", "read"],
)
def anchor_list(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    if _anchor_ids_need_repair(cell):
        _repair_anchor_ids_in_cell(
            view,
            ly,
            cell,
            layer=_get_anchor_layer_str(params, ly),
        )
    anchors = [_make_anchor_dict(inst, ly, cell.name) for inst in _iter_anchor_instances(cell)]
    if params.get("layer"):
        # Validate format, then filter by each anchor's own marker layer.
        layer_filter = "%d/%d" % _parse_layer_ld(params["layer"])
        anchors = [a for a in anchors if a.get("layer") == layer_filter]
    if params.get("sort", "none") == "id":
        anchors.sort(key=lambda a: a.get("id", ""))
    id_counts = _collect_anchor_id_counts(cell)
    duplicate_ids = sorted(anchor_id for anchor_id, count in id_counts.items() if count > 1)
    return {
        "cell": cell.name,
        "count": len(anchors),
        "anchors": anchors,
        "duplicate_ids": duplicate_ids,
    }


@method(
    "anchor.repair_ids",
    description=(
        "Repair duplicate or empty Anchor IDs in a cell. This is primarily for "
        "anchors inserted manually through the KLayout PCell GUI, which bypasses "
        "anchor.mark's uniqueness check."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "layer": {"type": "string"},
            "prefix": {"type": "string", "default": "A"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["anchor", "write"],
)
def anchor_repair_ids(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    prefix = str(params.get("prefix", "A") or "A")
    return _repair_anchor_ids_in_cell(
        view,
        ly,
        cell,
        layer=_get_anchor_layer_str(params, ly),
        prefix=prefix,
    )


@method(
    "anchor.update",
    description="Update a single Anchor PCell instance by immutable id.",
    params_schema={
        "type": "object",
        "required": ["cell", "id"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "id": {"type": "string"},
            "new_id": {"type": "string"},
            "layer": {"type": "string"},
            "kind": {"type": "string"},
            "mode": {"type": "string"},
            "net": {"type": "string"},
            "label": {"type": "string"},
            "show_label": {"type": "boolean"},
            "required": {"type": "boolean"},
            "priority": {"type": "integer"},
            "radius_um": {"type": "number"},
            "width_um": {"type": "number"},
            "height_um": {"type": "number"},
            "orientation": {"type": "number"},
            "path_points": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["anchor", "write"],
)
def anchor_update(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    anchor_id = str(params["id"])
    if "new_id" in params and str(params.get("new_id")) != anchor_id:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "anchor id is a unique identity handle and is not user-editable",
            hint="use label/net to express semantics",
        )
    inst = _find_anchor_by_id(cell, anchor_id)
    if inst is None:
        raise RpcError(ErrorCode.NOT_FOUND, "no anchor named %r in cell %s" % (anchor_id, cell.name))

    old = _pcell_params_to_dict(inst.cell)
    parsed = _params_from_request(params, old)
    anchor_layer_str = _get_anchor_layer_str(params, ly)
    variant = _create_anchor_variant(ly, anchor_layer_str, anchor_id, **parsed)
    with auto_txn(view, "klink: anchor.update %s" % anchor_id):
        _replace_anchor_instance(cell, inst, variant)
    return {"cell": cell.name, "old_id": anchor_id, "updated": {"id": anchor_id, "name": anchor_id, **parsed}}


@method(
    "anchor.transform",
    description="Batch-update Anchor PCell parameters by ids or GUI selection.",
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "ids": {"type": "array", "items": {"type": "string"}},
            "names": {"type": "array", "items": {"type": "string"}},
            "selection": {"type": "boolean", "default": False},
            "layer": {"type": "string"},
            "kind": {"type": "string"},
            "mode": {"type": "string"},
            "net": {"type": "string"},
            "label": {"type": "string"},
            "show_label": {"type": "boolean"},
            "required": {"type": "boolean"},
            "priority": {"type": "integer"},
            "radius_um": {"type": "number"},
            "width_um": {"type": "number"},
            "height_um": {"type": "number"},
            "orientation": {"type": "number"},
            "path_points": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["anchor", "write"],
)
def anchor_transform(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    anchor_layer_str = _get_anchor_layer_str(params, ly)
    targets = []
    if bool(params.get("selection", False)):
        targets.extend(_selected_anchor_instances(view, cell))
    for anchor_id in list(params.get("ids") or []) + list(params.get("names") or []):
        inst = _find_anchor_by_id(cell, str(anchor_id))
        if inst is not None and inst not in targets:
            targets.append(inst)
    if not targets:
        return {"cell": cell.name, "updated": 0, "anchors": []}

    updated = []
    with auto_txn(view, "klink: anchor.transform (%d anchors)" % len(targets)):
        for inst in list(targets):
            old = _pcell_params_to_dict(inst.cell)
            anchor_id = str(old.get("id", ""))
            parsed = _params_from_request(params, old)
            variant = _create_anchor_variant(ly, anchor_layer_str, anchor_id, **parsed)
            _replace_anchor_instance(cell, inst, variant)
            updated.append({"id": anchor_id, "name": anchor_id, **parsed})
    return {"cell": cell.name, "updated": len(updated), "anchors": updated}


@method(
    "anchor.unmark",
    description="Delete one Anchor PCell instance by id.",
    params_schema={
        "type": "object",
        "required": ["cell", "id"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "id": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["anchor", "write", "delete"],
)
def anchor_unmark(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    anchor_id = str(params["id"])
    inst = _find_anchor_by_id(cell, anchor_id)
    if inst is None:
        return {"cell": cell.name, "id": anchor_id, "name": anchor_id, "deleted": False}
    variant_ci = inst.cell_index
    with auto_txn(view, "klink: anchor.unmark %s" % anchor_id):
        cell.erase(inst)
        try:
            ly.prune_cell(variant_ci)
        except Exception:
            pass
    return {"cell": cell.name, "id": anchor_id, "name": anchor_id, "deleted": True}


@method(
    "anchor.delete_all",
    description="Delete all Anchor PCell instances in a cell.",
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "layer": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["anchor", "write", "delete"],
)
def anchor_delete_all(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    anchors = [(inst, inst.cell_index) for inst in _iter_anchor_instances(cell)]
    if not anchors:
        return {"cell": cell.name, "deleted_instances": 0, "deleted_shapes": 0}
    with auto_txn(view, "klink: anchor.delete_all (%d insts)" % len(anchors)):
        for inst, variant_ci in anchors:
            try:
                cell.erase(inst)
                try:
                    ly.prune_cell(variant_ci)
                except Exception:
                    pass
            except Exception:
                pass
    return {"cell": cell.name, "deleted_instances": len(anchors), "deleted_shapes": 0}
