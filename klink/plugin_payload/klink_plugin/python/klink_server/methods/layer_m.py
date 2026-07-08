"""
Layer methods.

`layer.list`    : enumerate all layers in the active layout
`layer.ensure`  : idempotent create-or-get for a (layer, datatype) pair

Rationale
---------
Shape insertion RPCs (M3) take a `layer_index` handle, not raw
layer/datatype, so that repeated calls don't have to re-look-up the
index every time. `layer.ensure` is the one-stop entry point that
guarantees the index exists before you call `shape.insert_*`:

    idx = c.layer_ensure(layer=101, datatype=0)["layer_index"]
    c.shape_insert_box(cell="TOP", layer_index=idx, bbox_um=[0,0,10,5])

This also matches how you'd write it by hand with pya
(`layout.layer(pya.LayerInfo(101,0))`), just JSON-safe and undoable.
"""

from __future__ import annotations

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..txn import auto_txn
from .cell_m import _active_layout


@method(
    "layer.list",
    description=(
        "List all layers currently defined in the active layout. Each "
        "entry has `layer_index` (the runtime handle used by other "
        "RPCs), `layer`/`datatype` (GDS numbers), and optional `name`. "
        "Also returns `dbu_um` so the client can convert between "
        "microns and database units."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "dbu_um": {"type": "number"},
            "layers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "layer_index": {"type": "integer"},
                        "layer": {"type": "integer"},
                        "datatype": {"type": "integer"},
                        "name": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
    tags=["layer", "read"],
)
def layer_list(params, ctx):
    _, _, ly = _active_layout()
    items = []
    for idx in ly.layer_indexes():
        info = ly.get_info(idx)
        items.append({
            "layer_index": int(idx),
            "layer": int(info.layer),
            "datatype": int(info.datatype),
            "name": info.name or None,
        })
    return {
        "count": len(items),
        "dbu_um": float(ly.dbu),
        "layers": items,
    }


@method(
    "layer.ensure",
    description=(
        "Ensure a GDS layer (layer/datatype) exists in the active "
        "layout. If missing, it is created inside an undo-able "
        "transaction. Returns the `layer_index` handle and whether the "
        "layer was just created. Safe to call repeatedly - it's a "
        "pure upsert."
    ),
    params_schema={
        "type": "object",
        "required": ["layer"],
        "properties": {
            "layer": {"type": "integer", "description": "GDS layer number, e.g. 101"},
            "datatype": {"type": "integer", "default": 0, "description": "GDS datatype (default 0)"},
            "name": {
                "type": "string",
                "description": "Optional display name shown in the layer panel.",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "layer_index": {"type": "integer"},
            "layer": {"type": "integer"},
            "datatype": {"type": "integer"},
            "name": {"type": ["string", "null"]},
            "created": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["layer", "write"],
)
def layer_ensure(params, ctx):
    view, _, ly = _active_layout()

    try:
        l = int(params["layer"])
        d = int(params.get("datatype", 0))
    except Exception:
        raise RpcError(ErrorCode.BAD_PARAMS, "layer/datatype must be integers")

    name = params.get("name")
    if name is not None and not isinstance(name, str):
        raise RpcError(ErrorCode.BAD_PARAMS, "name must be a string if given")

    info_probe = pya.LayerInfo(l, d)

    existing = ly.find_layer(info_probe)
    if existing is not None:
        cur = ly.get_info(existing)
        # Optional: if caller passed a name and it differs, update it
        # inside a transaction so Ctrl+Z can revert.
        renamed = False
        if name is not None and (cur.name or "") != name:
            with auto_txn(view, f"klink: rename layer {l}/{d} -> {name!r}"):
                ly.set_info(existing, pya.LayerInfo(l, d, name))
            renamed = True
        cur = ly.get_info(existing)
        return {
            "layer_index": int(existing),
            "layer": int(cur.layer),
            "datatype": int(cur.datatype),
            "name": cur.name or None,
            "created": False,
            "renamed": renamed,
        }

    info = pya.LayerInfo(l, d, name) if name else pya.LayerInfo(l, d)
    with auto_txn(view, f"klink: create layer {l}/{d}"):
        idx = ly.insert_layer(info)

    cur = ly.get_info(idx)
    return {
        "layer_index": int(idx),
        "layer": int(cur.layer),
        "datatype": int(cur.datatype),
        "name": cur.name or None,
        "created": True,
        "renamed": False,
    }
