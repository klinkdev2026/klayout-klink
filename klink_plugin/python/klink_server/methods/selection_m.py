"""
Selection methods.

`selection.get`    : inspect what the user currently has selected
`selection.clear`  : wipe the current selection
`selection.set_box`: select all objects whose bbox intersects a region
`selection.send_context`: explicitly send current selection to AI context

`selection.set_box` is the first write-ish method, but it does NOT edit
layout geometry - it only changes the GUI selection, which is cheap and
fully undoable through KLayout's own selection history.

Design note on object references
--------------------------------
pya `ObjectInstPath` encodes a full hierarchy path (top cell -> instance
-> ... -> shape). That path is the canonical "selectable reference" in
KLayout. In JSON we expose the minimal information an LLM typically
needs (which cell, which layer, which shape kind + bbox); the full
ObjectInstPath is NOT round-trippable as plain JSON and is therefore
not part of M2. M3 will add `selection.set_by_query` for semantic
selection.
"""

from __future__ import annotations

from typing import List

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from .cell_m import _active_layout
from .shape_m import _box_from_param, _shape_to_dict, _SHAPE_KINDS


def _emit_selection_changed(view, ctx) -> None:
    """Emit selection_changed for RPC-driven selection edits.

    KLayout GUI selection signals are not guaranteed to fire when the
    selection is changed through `view.object_selection`, but external
    interaction memory needs a deterministic event for both GUI and RPC
    selection changes.
    """
    try:
        from ..dispatcher import current_request
        from ..server import instance as _srv_instance
        from ..signals import _summarise_selection

        srv = _srv_instance()
        if srv is None:
            return
        data = _summarise_selection(view, max_items=10)
        cause = current_request()
        if cause:
            data["caused_by"] = [cause]
        srv.events.emit("selection_changed", data)
    except Exception as exc:
        print(f"[klink] explicit selection_changed emit failed: {exc}")


def _serialise_object(obj: pya.ObjectInstPath) -> dict:
    d: dict = {}
    try:
        c = obj.cell()
        d["cell"] = c.name if c is not None else None
    except Exception:
        d["cell"] = None
    try:
        d["cell_index"] = obj.cell_index()
    except Exception:
        pass
    d["is_cell_inst"] = bool(obj.is_cell_inst())

    if obj.is_cell_inst():
        try:
            inst = obj.inst()
            d["kind"] = "instance"
            d["target_cell"] = inst.cell.name if inst.cell is not None else None
            try:
                bb = inst.bbox()
                if not bb.empty():
                    d["bbox_dbu"] = [bb.left, bb.bottom, bb.right, bb.top]
            except Exception:
                pass
        except Exception:
            d["kind"] = "instance"
    else:
        try:
            shape = obj.shape
            d["kind"] = "shape"
            d["layer_index"] = obj.layer
            sd = _shape_to_dict(shape, obj.layer, set(_SHAPE_KINDS))
            if sd is not None:
                d["shape"] = sd
        except Exception:
            d["kind"] = "shape"
    return d


@method(
    "selection.get",
    description=(
        "Return the current object selection in the active view. Each "
        "entry is either a shape (with layer + bbox) or an instance "
        "(with target cell + bbox). Empty selection returns an empty "
        "list - not an error."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "objects": {"type": "array"},
        },
    },
    tags=["selection", "read"],
)
def selection_get(params, ctx):
    view, _, _ = _active_layout()
    limit = int(params.get("limit", 500))
    if limit < 1 or limit > 5000:
        raise RpcError(ErrorCode.BAD_PARAMS, "limit must be 1..5000")

    objs: List[dict] = []
    truncated = False

    # each_object_selected() is the portable way to iterate the GUI
    # selection in pya; object_selection is the bulk-setter (see below).
    for obj in view.each_object_selected():
        if len(objs) >= limit:
            truncated = True
            break
        try:
            objs.append(_serialise_object(obj))
        except Exception:
            continue

    return {
        "count": len(objs),
        "truncated": truncated,
        "objects": objs,
    }


@method(
    "selection.clear",
    description="Clear the current object selection in the active view.",
    params_schema={"type": "object"},
    returns_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    mutates=True,
    tags=["selection"],
)
def selection_clear(params, ctx):
    view, _, _ = _active_layout()
    view.clear_object_selection()
    _emit_selection_changed(view, ctx)
    return {"ok": True}


@method(
    "selection.set_box",
    description=(
        "Select every shape in 'cell' whose bbox intersects 'bbox_dbu' "
        "on any of the given 'layers' (default: all layers). Replaces "
        "the current selection. Returns the number of objects selected."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "bbox_dbu"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            "bbox_dbu": {
                "type": "array", "minItems": 4, "maxItems": 4,
                "description": "[x1, y1, x2, y2] in dbu",
            },
            "layers": {
                "type": "array",
                "description": "Layer indexes / 'L/D' / {layer, datatype}. None = all.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 5000},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "selected": {"type": "integer"},
            "truncated": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["selection"],
)
def selection_set_box(params, ctx):
    from .cell_m import _resolve_cell
    from .shape_m import _resolve_layers

    view, cv, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])
    bbox = _box_from_param(params.get("bbox_dbu"))
    if bbox is None:
        raise RpcError(ErrorCode.BAD_PARAMS, "bbox_dbu is required")

    layer_idxs = _resolve_layers(ly, params.get("layers"))
    if layer_idxs is None:
        layer_idxs = list(ly.layer_indexes())

    limit = int(params.get("limit", 5000))
    if limit < 1 or limit > 20000:
        raise RpcError(ErrorCode.BAD_PARAMS, "limit must be 1..20000")

    objs = []
    truncated = False
    cv_index = view.active_cellview_index

    for li in layer_idxs:
        if truncated:
            break
        shapes = cell.shapes(li)
        try:
            it = shapes.each_touching(bbox)
        except Exception:
            it = shapes.each()
        for s in it:
            if len(objs) >= limit:
                truncated = True
                break
            # Build ObjectInstPath for a shape in the top cell
            oip = pya.ObjectInstPath()
            oip.cv_index = cv_index
            oip.layer = li
            oip.shape = s
            oip.top = cell.cell_index()
            objs.append(oip)

    view.object_selection = objs
    _emit_selection_changed(view, ctx)

    return {"selected": len(objs), "truncated": truncated}


@method(
    "selection.send_context",
    description=(
        "Explicitly send the current non-empty KLayout selection as a "
        "selection_sent event for external AI interaction context. This does "
        "not store memory in the plugin."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "default": "rpc"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "message": {"type": "string"},
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
        },
    },
    tags=["selection", "events"],
)
def selection_send_context(params, ctx):
    from ..context_capture import send_current_selection

    max_items = int(params.get("max_items", 50))
    if max_items < 1 or max_items > 500:
        raise RpcError(ErrorCode.BAD_PARAMS, "max_items must be 1..500")
    source = params.get("source", "rpc")
    if not isinstance(source, str):
        raise RpcError(ErrorCode.BAD_PARAMS, "source must be a string")
    return send_current_selection(source=source, max_items=max_items)
