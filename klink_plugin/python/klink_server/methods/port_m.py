"""Thin Port PCell RPC methods.

This module is intentionally limited to KLayout-side Port PCell CRUD:
create, list, update, transform, and delete. Higher-level interpretation
such as hand-drawn marker recognition, layer import, routing policy, and
validation lives in the external ``klink`` client package and composes these
RPCs with shape/selection/instance primitives.
"""

from __future__ import annotations

import math

import pya

from ..errors import ErrorCode, RpcError
from ..registry import method
from ..txn import auto_txn
from .cell_m import _active_layout, _resolve_cell
from .shape_m import _point_from_um_or_dbu


# Default marker layer is stored as Layout meta info so it follows the
# layout object's lifetime. The previous id(ly)-keyed module dict was
# unsafe: id() values are reused after garbage collection and module
# reloads dropped the mapping.
_DEFAULT_PORT_LAYER_KEY = "klink_default_port_layer"


def _get_port_layer_str(params: dict, ly: pya.Layout) -> str:
    if "layer" in params:
        return str(params["layer"])
    try:
        value = ly.meta_info_value(_DEFAULT_PORT_LAYER_KEY)
    except Exception:
        value = None
    return str(value) if value else "999/99"


def _parse_layer_ld(layer_str: str) -> tuple[int, int]:
    try:
        left, right = str(layer_str).split("/", 1)
        return int(left), int(right)
    except Exception:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "layer must be 'L/D' format, e.g. '999/99'",
        )


def _resolve_port_layer_idx(ly: pya.Layout, layer_str: str) -> int:
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
        defs = list(decl.get_parameters())
        vals = list(variant_cell.pcell_parameters())
    except Exception:
        return out

    for pdef, value in zip(defs, vals):
        try:
            name = str(pdef.name)
            if isinstance(value, pya.LayerInfo):
                value = "%d/%d" % (value.layer, value.datatype)
            out["name" if name == "port_name" else name] = value
        except Exception:
            pass
    return out


def _is_klink_port_instance(inst) -> bool:
    try:
        cell = inst.cell
        if cell is None:
            return False
        decl = cell.pcell_declaration()
        return decl is not None and _decl_name(decl) == "Port"
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
    out: dict = {}
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


def _make_port_dict(inst, ly: pya.Layout, cell_name: str = "") -> dict:
    params = _pcell_params_to_dict(inst.cell)
    trans = _inst_trans_dict(inst)
    dx = int(trans.get("dx_dbu", 0))
    dy = int(trans.get("dy_dbu", 0))
    out = {
        "recognized": True,
        "name": str(params.get("name", "?")),
        "label": str(params.get("label", "")),
        "layer": str(params.get("layer", "")),
        "orientation": float(params.get("orientation", 0.0)),
        "width_um": float(params.get("width_um", 0.0)),
        "port_type": str(params.get("port_type", "electrical")),
        "net": str(params.get("net", "")),
        "target_layer": str(params.get("target_layer", "")),
        "show_label": bool(params.get("show_label", True)),
        "access_mode": str(params.get("access_mode", "point")),
        "slide_allowed": bool(params.get("slide_allowed", False)),
        "slide_edge": str(params.get("slide_edge", "")),
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


def _build_port_pcell_params(
    port_layer_str: str,
    name: str,
    label: str,
    orientation: float,
    width_um: float,
    port_type: str,
    net: str,
    target_layer: str,
    show_label: bool = True,
    access_mode: str = "point",
    slide_allowed: bool = False,
    slide_edge: str = "",
) -> dict:
    layer, datatype = _parse_layer_ld(port_layer_str)
    return {
        "layer": pya.LayerInfo(layer, datatype),
        "port_name": str(name),
        "label": str(label) if label else "",
        "orientation": float(orientation),
        "width_um": float(width_um),
        "port_type": str(port_type),
        "net": str(net) if net else "",
        "target_layer": str(target_layer),
        "show_label": bool(show_label),
        "access_mode": str(access_mode),
        "slide_allowed": bool(slide_allowed),
        "slide_edge": str(slide_edge),
    }


def _create_port_variant(
    ly: pya.Layout,
    port_layer_str: str,
    name: str,
    label: str,
    orientation: float,
    width_um: float,
    port_type: str,
    net: str,
    target_layer: str,
    show_label: bool = True,
    access_mode: str = "point",
    slide_allowed: bool = False,
    slide_edge: str = "",
):
    params = _build_port_pcell_params(
        port_layer_str,
        name,
        label,
        orientation,
        width_um,
        port_type,
        net,
        target_layer,
        show_label,
        access_mode,
        slide_allowed,
        slide_edge,
    )
    try:
        variant = ly.create_cell("Port", "klink_port", params)
    except Exception as exc:
        raise RpcError(
            ErrorCode.EXEC,
            "failed to create klink_Port PCell: %s" % exc,
            hint="ensure klink_port library is registered",
        )
    if variant is None:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "klink_Port PCell not found",
            hint="port_pcell.py must register the klink_port library on startup",
        )
    return variant


def _replace_port_instance(parent: pya.Cell, old_inst, variant) -> None:
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


def _iter_port_instances(cell: pya.Cell) -> list:
    out = []
    try:
        for inst in cell.each_inst():
            if _is_klink_port_instance(inst):
                out.append(inst)
    except Exception:
        pass
    return out


def _find_port_by_name(cell: pya.Cell, name: str):
    for inst in _iter_port_instances(cell):
        params = _pcell_params_to_dict(inst.cell)
        if str(params.get("name", "")) == name:
            return inst
    return None


def _collect_port_names(cell: pya.Cell) -> set[str]:
    names: set[str] = set()
    for inst in _iter_port_instances(cell):
        name = _pcell_params_to_dict(inst.cell).get("name", "")
        if name:
            names.add(str(name))
    return names


def _collect_port_name_counts(cell: pya.Cell) -> dict[str, int]:
    counts: dict[str, int] = {}
    for inst in _iter_port_instances(cell):
        name = str(_pcell_params_to_dict(inst.cell).get("name", ""))
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _port_names_need_repair(cell: pya.Cell) -> bool:
    seen: set[str] = set()
    for inst in _iter_port_instances(cell):
        name = str(_pcell_params_to_dict(inst.cell).get("name", ""))
        if not name or name in seen:
            return True
        seen.add(name)
    return False


def _auto_name(existing_names: set[str], prefix: str = "P", index: int = 0) -> str:
    i = int(index)
    while True:
        candidate = "%s%d" % (prefix, i)
        if candidate not in existing_names:
            return candidate
        i += 1


def _ensure_unique_port_name(cell: pya.Cell, name: str) -> None:
    if name in _collect_port_names(cell):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "port name %r already exists in cell %s" % (name, cell.name),
            hint="port names are unique handles; use net to express connectivity",
        )


def _sort_clockwise(ports: list) -> list:
    if len(ports) <= 2:
        return list(ports)
    cx = sum(p["center_um"][0] for p in ports) / len(ports)
    cy = sum(p["center_um"][1] for p in ports) / len(ports)
    return sorted(
        ports,
        key=lambda p: (180.0 - math.degrees(
            math.atan2(p["center_um"][1] - cy, p["center_um"][0] - cx)
        )) % 360.0,
    )


def _selected_port_instances(view, cell: pya.Cell) -> list:
    out = []
    try:
        for obj in view.each_object_selected():
            if not obj.is_cell_inst():
                continue
            try:
                inst = obj.inst()
                if _is_klink_port_instance(inst):
                    out.append(inst)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _port_params_from_request(params: dict, old: dict | None = None) -> dict:
    old = old or {}
    return {
        "label": str(params.get("label", old.get("label", ""))),
        "orientation": float(params.get("orientation", old.get("orientation", 0.0))) % 360.0,
        "width_um": float(params.get("width_um", old.get("width_um", 5.0))),
        "port_type": str(params.get("port_type", old.get("port_type", "electrical"))),
        "net": str(params.get("net", old.get("net", ""))),
        "target_layer": str(params.get("target_layer", old.get("target_layer", "1/0"))),
        "show_label": bool(params.get("show_label", old.get("show_label", True))),
        "access_mode": str(params.get("access_mode", old.get("access_mode", "point"))),
        "slide_allowed": bool(params.get("slide_allowed", old.get("slide_allowed", False))),
        "slide_edge": str(params.get("slide_edge", old.get("slide_edge", ""))),
    }


def _repair_port_names_in_cell(
    view,
    ly: pya.Layout,
    cell: pya.Cell,
    *,
    layer: str | None = None,
    prefix: str = "P",
) -> dict:
    used: set[str] = set()
    repaired = []
    targets = list(_iter_port_instances(cell))
    if not targets:
        return {"cell": cell.name, "repaired": 0, "ports": [], "duplicate_names_before": []}

    counts_before = _collect_port_name_counts(cell)
    duplicate_names_before = sorted(name for name, count in counts_before.items() if count > 1)

    with auto_txn(view, "klink: port.repair_names (%d ports)" % len(targets)):
        for index, inst in enumerate(list(targets)):
            old = _pcell_params_to_dict(inst.cell)
            old_name = str(old.get("name", ""))
            if old_name and old_name not in used:
                used.add(old_name)
                continue

            new_name = _auto_name(used, prefix=prefix, index=index)
            used.add(new_name)
            parsed = _port_params_from_request({}, old)
            port_layer_str = str(old.get("layer") or layer or "999/99")
            variant = _create_port_variant(
                ly,
                port_layer_str,
                new_name,
                parsed["label"],
                parsed["orientation"],
                parsed["width_um"],
                parsed["port_type"],
                parsed["net"],
                parsed["target_layer"],
                parsed["show_label"],
                parsed["access_mode"],
                parsed["slide_allowed"],
                parsed["slide_edge"],
            )
            _replace_port_instance(cell, inst, variant)
            repaired.append({"old_name": old_name, "name": new_name, **parsed})

    return {
        "cell": cell.name,
        "repaired": len(repaired),
        "ports": repaired,
        "duplicate_names_before": duplicate_names_before,
    }


@method(
    "port.set_layer",
    description="Configure the default Port PCell marker layer for this layout.",
    params_schema={
        "type": "object",
        "required": ["layer"],
        "properties": {
            "layer": {"type": "string", "description": "'L/D', e.g. '999/99'"},
        },
    },
    returns_schema={"type": "object", "properties": {"port_layer": {"type": "string"}}},
    mutates=True,
    tags=["port", "write"],
)
def port_set_layer(params, ctx):
    _, _, ly = _active_layout()
    layer_str = str(params["layer"])
    _resolve_port_layer_idx(ly, layer_str)
    ly.add_meta_info(pya.LayoutMetaInfo(_DEFAULT_PORT_LAYER_KEY, layer_str))
    return {"port_layer": layer_str}


@method(
    "port.mark",
    description="Create one klink_Port PCell instance in a cell.",
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Target cell name or cell_index"},
            "layer": {"type": "string"},
            "name": {"type": "string"},
            "label": {"type": "string"},
            "center_um": {"type": "array", "minItems": 2, "maxItems": 2},
            "center_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
            "orientation": {"type": "number", "default": 0},
            "width_um": {"type": "number", "default": 5.0},
            "port_type": {"type": "string", "default": "electrical"},
            "net": {"type": "string"},
            "target_layer": {"type": "string", "default": "1/0"},
            "show_label": {"type": "boolean", "default": True},
            "access_mode": {"type": "string", "enum": ["point", "edge"], "default": "point"},
            "slide_allowed": {"type": "boolean", "default": False},
            "slide_edge": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["port", "write"],
)
def port_mark(params, ctx):
    view, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["cell"])
    port_layer_str = _get_port_layer_str(params, ly)
    x_dbu, y_dbu = _point_from_um_or_dbu(params, ly.dbu, "center_dbu", "center_um")

    orientation = float(params.get("orientation", 0.0)) % 360.0
    label = str(params.get("label", ""))
    width_um = float(params.get("width_um", 5.0))
    port_type = str(params.get("port_type", "electrical"))
    net = str(params.get("net", ""))
    target_layer = str(params.get("target_layer", "1/0"))
    show_label = bool(params.get("show_label", True))
    access_mode = str(params.get("access_mode", "point"))
    slide_allowed = bool(params.get("slide_allowed", False))
    slide_edge = str(params.get("slide_edge", ""))

    name = params.get("name")
    if name:
        name = str(name)
        _ensure_unique_port_name(parent, name)
    else:
        name = _auto_name(_collect_port_names(parent))

    _resolve_port_layer_idx(ly, port_layer_str)
    variant = _create_port_variant(
        ly, port_layer_str, name, label, orientation, width_um,
        port_type, net, target_layer, show_label,
        access_mode, slide_allowed, slide_edge,
    )

    with auto_txn(view, "klink: port.mark %s" % name):
        parent.insert(pya.CellInstArray(variant.cell_index(), pya.Trans(int(x_dbu), int(y_dbu))))

    return {
        "cell": parent.name,
        "name": name,
        "label": label,
        "center_um": [x_dbu * ly.dbu, y_dbu * ly.dbu],
        "orientation": orientation,
        "width_um": width_um,
        "port_type": port_type,
        "net": net,
        "show_label": show_label,
        "target_layer": target_layer,
        "access_mode": access_mode,
        "slide_allowed": slide_allowed,
        "slide_edge": slide_edge,
    }


@method(
    "port.list",
    description=(
        "List klink_Port PCell instances in a cell. When `layer` is given, "
        "only ports whose marker layer matches it are returned (it also "
        "selects the repair layer); without it all ports are returned."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "layer": {
                "type": "string",
                "description": "Marker layer filter in L/D format, e.g. '999/99'.",
            },
            "sort": {"type": "string", "enum": ["none", "clockwise", "name"], "default": "none"},
        },
    },
    returns_schema={"type": "object"},
    tags=["port", "read"],
)
def port_list(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    if _port_names_need_repair(cell):
        _repair_port_names_in_cell(
            view,
            ly,
            cell,
            layer=_get_port_layer_str(params, ly),
        )
    ports = [_make_port_dict(inst, ly, cell.name) for inst in _iter_port_instances(cell)]
    if params.get("layer"):
        # Validate format, then filter by each port's own marker layer.
        layer_filter = "%d/%d" % _parse_layer_ld(params["layer"])
        ports = [p for p in ports if p.get("layer") == layer_filter]
    sort_mode = params.get("sort", "none")
    if sort_mode == "clockwise":
        ports = _sort_clockwise(ports)
    elif sort_mode == "name":
        ports.sort(key=lambda p: p.get("name", ""))
    name_counts = _collect_port_name_counts(cell)
    duplicate_names = sorted(name for name, count in name_counts.items() if count > 1)
    return {
        "cell": cell.name,
        "count": len(ports),
        "ports": ports,
        "duplicate_names": duplicate_names,
    }


@method(
    "port.repair_names",
    description=(
        "Repair duplicate or empty Port names in a cell. This is primarily for "
        "ports inserted manually through the KLayout PCell GUI, which bypasses "
        "port.mark's uniqueness check."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "layer": {"type": "string"},
            "prefix": {"type": "string", "default": "P"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["port", "write"],
)
def port_repair_names(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    prefix = str(params.get("prefix", "P") or "P")
    return _repair_port_names_in_cell(
        view,
        ly,
        cell,
        layer=_get_port_layer_str(params, ly),
        prefix=prefix,
    )


@method(
    "port.update",
    description="Update a single Port PCell instance by immutable name.",
    params_schema={
        "type": "object",
        "required": ["cell", "name"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "name": {"type": "string"},
            "label": {"type": "string"},
            "orientation": {"type": "number"},
            "width_um": {"type": "number"},
            "port_type": {"type": "string"},
            "net": {"type": "string"},
            "target_layer": {"type": "string"},
            "show_label": {"type": "boolean"},
            "access_mode": {"type": "string", "enum": ["point", "edge"]},
            "slide_allowed": {"type": "boolean"},
            "slide_edge": {"type": "string"},
            "layer": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["port", "write"],
)
def port_update(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    target_name = str(params["name"])
    if "new_name" in params and str(params.get("new_name")) != target_name:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "port name is a unique identity handle and is not user-editable",
            hint="use net to express connectivity",
        )

    inst = _find_port_by_name(cell, target_name)
    if inst is None:
        raise RpcError(ErrorCode.NOT_FOUND, "no port named %r in cell %s" % (target_name, cell.name))

    old = _pcell_params_to_dict(inst.cell)
    label = str(params.get("label", old.get("label", "")))
    orientation = float(params.get("orientation", old.get("orientation", 0.0)))
    width_um = float(params.get("width_um", old.get("width_um", 5.0)))
    port_type = str(params.get("port_type", old.get("port_type", "electrical")))
    net = str(params.get("net", old.get("net", "")))
    target_layer = str(params.get("target_layer", old.get("target_layer", "1/0")))
    show_label = bool(params.get("show_label", old.get("show_label", True)))
    access_mode = str(params.get("access_mode", old.get("access_mode", "point")))
    slide_allowed = bool(params.get("slide_allowed", old.get("slide_allowed", False)))
    slide_edge = str(params.get("slide_edge", old.get("slide_edge", "")))
    port_layer_str = _get_port_layer_str(params, ly)

    variant = _create_port_variant(
        ly, port_layer_str, target_name, label, orientation, width_um,
        port_type, net, target_layer, show_label,
        access_mode, slide_allowed, slide_edge,
    )
    with auto_txn(view, "klink: port.update %s" % target_name):
        _replace_port_instance(cell, inst, variant)

    return {
        "cell": cell.name,
        "old_name": target_name,
        "updated": {
            "name": target_name,
            "label": label,
            "orientation": orientation,
            "width_um": width_um,
            "port_type": port_type,
            "net": net,
            "target_layer": target_layer,
            "show_label": show_label,
            "access_mode": access_mode,
            "slide_allowed": slide_allowed,
            "slide_edge": slide_edge,
        },
    }


@method(
    "port.transform",
    description="Batch-update Port PCell parameters by names or GUI selection.",
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "names": {"type": "array", "items": {"type": "string"}},
            "selection": {"type": "boolean", "default": False},
            "label": {"type": "string"},
            "orientation": {"type": "number"},
            "rotate_delta": {"type": "number"},
            "width_um": {"type": "number"},
            "port_type": {"type": "string"},
            "net": {"type": "string"},
            "target_layer": {"type": "string"},
            "show_label": {"type": "boolean"},
            "access_mode": {"type": "string", "enum": ["point", "edge"]},
            "slide_allowed": {"type": "boolean"},
            "slide_edge": {"type": "string"},
            "layer": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["port", "write"],
)
def port_transform(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    port_layer_str = _get_port_layer_str(params, ly)

    targets = []
    if bool(params.get("selection", False)):
        targets.extend(_selected_port_instances(view, cell))
    for name in params.get("names") or []:
        inst = _find_port_by_name(cell, str(name))
        if inst is not None and inst not in targets:
            targets.append(inst)

    if not targets:
        return {"cell": cell.name, "updated": 0, "ports": []}

    updated_ports = []
    with auto_txn(view, "klink: port.transform (%d ports)" % len(targets)):
        for inst in list(targets):
            old = _pcell_params_to_dict(inst.cell)
            name = str(old.get("name", ""))
            label = str(params.get("label", old.get("label", "")))
            orientation = float(old.get("orientation", 0.0))
            if "orientation" in params:
                orientation = float(params["orientation"]) % 360.0
            if "rotate_delta" in params:
                orientation = (orientation + float(params["rotate_delta"])) % 360.0
            width_um = float(params.get("width_um", old.get("width_um", 5.0)))
            port_type = str(params.get("port_type", old.get("port_type", "electrical")))
            net = str(params.get("net", old.get("net", "")))
            target_layer = str(params.get("target_layer", old.get("target_layer", "1/0")))
            show_label = bool(params.get("show_label", old.get("show_label", True)))
            access_mode = str(params.get("access_mode", old.get("access_mode", "point")))
            slide_allowed = bool(params.get("slide_allowed", old.get("slide_allowed", False)))
            slide_edge = str(params.get("slide_edge", old.get("slide_edge", "")))
            variant = _create_port_variant(
                ly, port_layer_str, name, label, orientation, width_um,
                port_type, net, target_layer, show_label,
                access_mode, slide_allowed, slide_edge,
            )
            _replace_port_instance(cell, inst, variant)
            updated_ports.append({
                "name": name,
                "label": label,
                "orientation": orientation,
                "width_um": width_um,
                "port_type": port_type,
                "net": net,
                "target_layer": target_layer,
                "show_label": show_label,
                "access_mode": access_mode,
                "slide_allowed": slide_allowed,
                "slide_edge": slide_edge,
            })

    return {"cell": cell.name, "updated": len(updated_ports), "ports": updated_ports}


@method(
    "port.unmark",
    description="Delete one Port PCell instance by name.",
    params_schema={
        "type": "object",
        "required": ["cell", "name"],
        "properties": {
            "cell": {"description": "Cell name or cell_index"},
            "name": {"type": "string"},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    tags=["port", "write", "delete"],
)
def port_unmark(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    name = str(params["name"])
    inst = _find_port_by_name(cell, name)
    if inst is None:
        return {"cell": cell.name, "name": name, "deleted": False}
    variant_ci = inst.cell_index
    with auto_txn(view, "klink: port.unmark %s" % name):
        cell.erase(inst)
        try:
            ly.prune_cell(variant_ci)
        except Exception:
            pass
    return {"cell": cell.name, "name": name, "deleted": True}


@method(
    "port.delete_all",
    description="Delete all Port PCell instances in a cell.",
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
    tags=["port", "write", "delete"],
)
def port_delete_all(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    ports = [(inst, inst.cell_index) for inst in _iter_port_instances(cell)]
    if not ports:
        return {"cell": cell.name, "deleted_instances": 0, "deleted_shapes": 0}
    with auto_txn(view, "klink: port.delete_all (%d insts)" % len(ports)):
        for inst, variant_ci in ports:
            try:
                cell.erase(inst)
                try:
                    ly.prune_cell(variant_ci)
                except Exception:
                    pass
            except Exception:
                pass
    return {"cell": cell.name, "deleted_instances": len(ports), "deleted_shapes": 0}
