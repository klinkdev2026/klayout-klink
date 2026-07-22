"""
Modification methods — edit EXISTING geometry instead of delete+redraw.

`shape.transform`         : move/rotate/mirror shapes matching a filter
`shape.change_layer`      : move shapes from one layer to another
`instance.transform`      : move/rotate/mirror placed instances
`cell.flatten`            : flatten a cell's hierarchy (with dry_run)
`pcell.convert_to_static` : PCell variant -> static cell, refs retargeted

Rationale
---------
Until now "move that trace up 2 um" meant delete + reinsert (clumsy undo,
easy to lose properties). These wrap the official editing surface
(probe-verified on 0.30.7: Shape#transform / Instance#transform with
ICplxTrans, Cell#flatten(levels, prune), Layout#convert_cell_to_static).

Rotation/mirror semantics: about the COMBINED bbox center of the matched
set (GUI-like), then `move_um` is applied — so "rotate these 90 degrees"
does not fling far-away geometry around the origin.

Filters are mandatory where a bare call would silently rewrite the whole
cell: shape.transform needs layers and/or bbox_um; instance.transform
needs child and/or bbox_um. Zero matches is an instructive error, never a
silent no-op. Every mutation is one undo step.
"""

from __future__ import annotations

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..txn import auto_txn
from .cell_m import _active_layout, _resolve_cell


def _parse_ld(spec, what="layer"):
    if isinstance(spec, str):
        parts = spec.split("/")
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "%s %r is not 'L/D' (e.g. '1/0')" % (what, spec))
    if isinstance(spec, dict) and "layer" in spec:
        return int(spec["layer"]), int(spec.get("datatype", 0))
    raise RpcError(ErrorCode.BAD_PARAMS,
                   "%s entry %r must be 'L/D' or {layer, datatype}" % (what, spec))


def _bbox_dbu(bbox_um, dbu):
    l, b, r, t = (float(v) for v in bbox_um)
    return pya.Box(int(round(l / dbu)), int(round(b / dbu)),
                   int(round(r / dbu)), int(round(t / dbu)))


def _build_trans(dbu, move_um, rotation, mirror, center_dbu):
    """Rotation/mirror about center_dbu, then translation by move_um."""
    dx = int(round(float((move_um or [0, 0])[0]) / dbu))
    dy = int(round(float((move_um or [0, 0])[1]) / dbu))
    rot = float(rotation or 0.0)
    mir = bool(mirror)
    if rot == 0.0 and not mir:
        return pya.ICplxTrans(1.0, 0.0, False, dx, dy)
    cx, cy = center_dbu
    to_origin = pya.ICplxTrans(1.0, 0.0, False, -cx, -cy)
    spin = pya.ICplxTrans(1.0, rot, mir, 0, 0)
    back = pya.ICplxTrans(1.0, 0.0, False, cx + dx, cy + dy)
    return back * spin * to_origin


def _need_action(params):
    if (not params.get("move_um") and not params.get("rotation")
            and not params.get("mirror")):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "nothing to do — pass move_um [dx, dy] and/or rotation "
            "(degrees CCW) and/or mirror (across x before rotation)")


@method(
    "shape.transform",
    description=(
        "Move/rotate/mirror EXISTING shapes in place (no delete+redraw). "
        "Filter: `layers` (['L/D', ...]) and/or `bbox_um` (touching) — at "
        "least one is required so a bare call cannot silently rewrite the "
        "whole cell. Action: `move_um` [dx, dy], `rotation` (degrees CCW), "
        "`mirror`. Rotation/mirror happen about the matched set's combined "
        "bbox center (GUI-like), then the move applies. One undo step; "
        "zero matches is an error, not a silent no-op."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "cell name or index"},
            "layers": {"type": "array"},
            "bbox_um": {"type": "array", "minItems": 4, "maxItems": 4},
            "move_um": {"type": "array", "minItems": 2, "maxItems": 2},
            "rotation": {"type": "number"},
            "mirror": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20000,
                      "default": 5000},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {"transformed": {"type": "integer"},
                       "bbox_um": {"type": "array"}},
    },
    mutates=True,
    tags=["shape", "write"],
)
def shape_transform(params, ctx):
    view, _, ly = _active_layout()
    dbu = float(ly.dbu)
    cell = _resolve_cell(ly, params["cell"])
    _need_action(params)
    if not params.get("layers") and not params.get("bbox_um"):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "pass layers and/or bbox_um to say WHICH shapes; to transform "
            "everything intentionally, pass the cell's bbox explicitly")

    if params.get("layers"):
        idxs = [ly.layer(*_parse_ld(s)) for s in params["layers"]]
    else:
        idxs = list(ly.layer_indexes())
    box = _bbox_dbu(params["bbox_um"], dbu) if params.get("bbox_um") else None
    limit = int(params.get("limit", 5000))

    matched = []
    for li in idxs:
        shapes = cell.shapes(li)
        it = shapes.each_touching(box) if box is not None else shapes.each()
        for s in it:
            matched.append(s)
            if len(matched) > limit:
                raise RpcError(
                    ErrorCode.BAD_PARAMS,
                    "more than %d shapes matched; tighten the filter or "
                    "raise limit" % limit)
    if not matched:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "no shapes matched the filter in cell %r (layers=%s bbox_um=%s)"
            % (cell.name, params.get("layers"), params.get("bbox_um")))

    total = pya.Box()
    for s in matched:
        total += s.bbox()
    center = (total.center().x, total.center().y)
    trans = _build_trans(dbu, params.get("move_um"), params.get("rotation"),
                         params.get("mirror"), center)

    with auto_txn(view, "klink: transform %d shapes in %s"
                  % (len(matched), cell.name)):
        for s in matched:
            s.transform(trans)

    nb = pya.Box()
    for s in matched:
        nb += s.bbox()
    return {"transformed": len(matched),
            "bbox_um": [nb.left * dbu, nb.bottom * dbu,
                        nb.right * dbu, nb.top * dbu]}


@method(
    "shape.change_layer",
    description=(
        "Move shapes from one layer to another within a cell (optionally "
        "only those touching `bbox_um`). The one-intention version of "
        "'redraw this on the right layer'. One undo step; zero matches is "
        "an error."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "from_layer", "to_layer"],
        "properties": {
            "cell": {"description": "cell name or index"},
            "from_layer": {"description": "'L/D'"},
            "to_layer": {"description": "'L/D'"},
            "bbox_um": {"type": "array", "minItems": 4, "maxItems": 4},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {"moved": {"type": "integer"},
                       "from_layer": {"type": "string"},
                       "to_layer": {"type": "string"}},
    },
    mutates=True,
    tags=["shape", "write"],
)
def shape_change_layer(params, ctx):
    view, _, ly = _active_layout()
    dbu = float(ly.dbu)
    cell = _resolve_cell(ly, params["cell"])
    src = _parse_ld(params["from_layer"], "from_layer")
    dst = _parse_ld(params["to_layer"], "to_layer")
    if src == dst:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "from_layer and to_layer are the same layer")
    src_li = ly.layer(*src)
    dst_li = ly.layer(*dst)
    box = _bbox_dbu(params["bbox_um"], dbu) if params.get("bbox_um") else None

    shapes = cell.shapes(src_li)
    it = shapes.each_touching(box) if box is not None else shapes.each()
    matched = [s for s in it]
    if not matched:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "no shapes on %d/%d in cell %r%s" % (
                src[0], src[1], cell.name,
                " touching bbox_um" if box is not None else ""))

    with auto_txn(view, "klink: %s: %d shapes %d/%d -> %d/%d"
                  % (cell.name, len(matched), src[0], src[1], dst[0], dst[1])):
        dst_shapes = cell.shapes(dst_li)
        for s in matched:
            dst_shapes.insert(s)
        for s in matched:
            shapes.erase(s)
    return {"moved": len(matched),
            "from_layer": "%d/%d" % src, "to_layer": "%d/%d" % dst}


@method(
    "instance.transform",
    description=(
        "Move/rotate/mirror PLACED instances (arrays move as one object). "
        "Filter: `child` (cell name) and/or `bbox_um` (touching) — at "
        "least one required. Action: `move_um`, `rotation` (degrees CCW), "
        "`mirror`; rotation/mirror about the matched set's combined bbox "
        "center. One undo step; zero matches is an error."
    ),
    params_schema={
        "type": "object",
        "required": ["parent"],
        "properties": {
            "parent": {"description": "parent cell name or index"},
            "child": {"description": "only instances of this cell"},
            "bbox_um": {"type": "array", "minItems": 4, "maxItems": 4},
            "move_um": {"type": "array", "minItems": 2, "maxItems": 2},
            "rotation": {"type": "number"},
            "mirror": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {"transformed": {"type": "integer"},
                       "bbox_um": {"type": "array"}},
    },
    mutates=True,
    tags=["instance", "write"],
)
def instance_transform(params, ctx):
    view, _, ly = _active_layout()
    dbu = float(ly.dbu)
    parent = _resolve_cell(ly, params["parent"])
    _need_action(params)
    if params.get("child") is None and not params.get("bbox_um"):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "pass child and/or bbox_um to say WHICH instances")

    child_idx = None
    if params.get("child") is not None:
        child_idx = _resolve_cell(ly, params["child"]).cell_index()
    box = _bbox_dbu(params["bbox_um"], dbu) if params.get("bbox_um") else None

    matched = []
    for inst in parent.each_inst():
        if child_idx is not None and inst.cell_index != child_idx:
            continue
        if box is not None and not inst.bbox().touches(box):
            continue
        matched.append(inst)
    if not matched:
        raise RpcError(
            ErrorCode.NOT_FOUND,
            "no instances matched in %r (child=%s bbox_um=%s)"
            % (parent.name, params.get("child"), params.get("bbox_um")))

    total = pya.Box()
    for inst in matched:
        total += inst.bbox()
    trans = _build_trans(dbu, params.get("move_um"), params.get("rotation"),
                         params.get("mirror"),
                         (total.center().x, total.center().y))

    with auto_txn(view, "klink: transform %d instances in %s"
                  % (len(matched), parent.name)):
        for inst in matched:
            inst.transform(trans)

    nb = pya.Box()
    for inst in matched:
        nb += inst.bbox()
    return {"transformed": len(matched),
            "bbox_um": [nb.left * dbu, nb.bottom * dbu,
                        nb.right * dbu, nb.top * dbu]}


@method(
    "cell.flatten",
    description=(
        "Flatten a cell's hierarchy: child instances are dissolved into "
        "plain shapes inside the cell. `levels` (default -1 = all levels), "
        "`prune` (default true: child cells left orphaned are deleted), "
        "`dry_run` (default false: only report what would happen). "
        "DESTRUCTIVE for the cell's hierarchy — the flatten itself is one "
        "undo step; use dry_run first when unsure."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "cell name or index"},
            "levels": {"type": "integer", "default": -1},
            "prune": {"type": "boolean", "default": True},
            "dry_run": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "instance_entries": {"type": "integer"},
            "placements": {"type": "integer"},
            "dry_run": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["cell", "write"],
)
def cell_flatten(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    entries = 0
    placements = 0
    for inst in cell.each_inst():
        entries += 1
        try:
            placements += int(inst.size())
        except Exception:
            placements += 1
    if params.get("dry_run", False):
        return {"cell": cell.name, "instance_entries": entries,
                "placements": placements, "dry_run": True}
    if entries == 0:
        raise RpcError(ErrorCode.NOT_FOUND,
                       "cell %r has no child instances to flatten"
                       % (cell.name,))
    with auto_txn(view, "klink: flatten %s" % cell.name):
        cell.flatten(int(params.get("levels", -1)),
                     bool(params.get("prune", True)))
    return {"cell": cell.name, "instance_entries": entries,
            "placements": placements, "dry_run": False}


@method(
    "pcell.convert_to_static",
    description=(
        "Convert a PCell variant into an ordinary static cell "
        "(Layout#convert_cell_to_static) and — because KLayout leaves "
        "existing placements pointing at the old variant — retarget every "
        "instance in the layout to the new static cell, then delete the "
        "orphaned variant (`prune_variant`, default true). Afterwards the "
        "geometry is frozen: parameter editing no longer applies. One "
        "undo step."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "PCell variant cell name or index"},
            "prune_variant": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "static_cell": {"type": "string"},
            "retargeted_instances": {"type": "integer"},
            "variant_deleted": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["pcell", "write"],
)
def pcell_convert_to_static(params, ctx):
    view, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    try:
        is_var = bool(cell.is_pcell_variant())
    except Exception:
        is_var = False
    if not is_var:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "cell %r is not a PCell variant (already static?); "
            "instance.query / cell.list to inspect" % (cell.name,))

    old_idx = cell.cell_index()
    with auto_txn(view, "klink: convert %s to static" % cell.name):
        new_idx = ly.convert_cell_to_static(old_idx)
        retargeted = 0
        if new_idx != old_idx:
            for c in ly.each_cell():
                if c.cell_index() == new_idx:
                    continue
                for inst in c.each_inst():
                    if inst.cell_index == old_idx:
                        try:
                            inst.cell_index = new_idx
                        except Exception:
                            ca = inst.cell_inst_array.dup()
                            ca.cell_index = new_idx
                            c.replace(inst, ca)
                        retargeted += 1
        deleted = False
        if params.get("prune_variant", True) and new_idx != old_idx:
            still_used = any(
                True for c in ly.each_cell()
                for inst in c.each_inst() if inst.cell_index == old_idx)
            if not still_used:
                ly.delete_cell(old_idx)
                deleted = True
    return {"static_cell": ly.cell(new_idx).name,
            "retargeted_instances": retargeted,
            "variant_deleted": deleted}
