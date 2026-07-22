"""
Geometry computation methods (pya.Region service).

`geometry.boolean`  : AND/OR/XOR/NOT between two layer sources (+optional write)
`geometry.cell_xor` : per-layer geometric diff between two cells (pure report)
`geometry.density`  : covered-area density of a layer, optionally in a window

Rationale
---------
Verification math for agents, without exec.python: "do these layers
overlap", "is my edit's footprint exactly what I intended", "what is the
fill density here". Runs INSIDE KLayout (design ruling: what klayout.db
can do still happens in the KLayout process — klink core stays
dependency-free). All inputs are hierarchical (begin_shapes_rec: child
cells included) and merged before math; areas are reported in um^2.
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


def _region_of(ly, cell, ld) -> pya.Region:
    li = ly.layer(ld[0], ld[1])
    reg = pya.Region(cell.begin_shapes_rec(li))
    reg.merge()
    return reg


def _region_stats(reg: pya.Region, dbu: float) -> dict:
    stats = {"polygon_count": int(reg.count()),
             "area_um2": reg.area() * dbu * dbu}
    if not reg.is_empty():
        bb = reg.bbox()
        stats["bbox_um"] = [bb.left * dbu, bb.bottom * dbu,
                            bb.right * dbu, bb.top * dbu]
    return stats


@method(
    "geometry.boolean",
    description=(
        "Boolean between two layer sources: `op` is one of and/or/xor/not "
        "(not = a minus b). `a` and `b` are {cell, layer} — cells may "
        "differ (defaults: b.cell = a.cell), hierarchy is included and "
        "inputs are merged. Returns polygon_count / area_um2 / bbox_um of "
        "the result; pass `write_to` {cell?, layer} to ALSO write the "
        "result polygons (one undo step; default target cell = a.cell). "
        "Typical checks: overlap between two layers (op=and, area>0 means "
        "a short/contact), difference against an intent region (op=xor, "
        "area==0 means exact match)."
    ),
    params_schema={
        "type": "object",
        "required": ["a", "b", "op"],
        "properties": {
            "a": {"type": "object", "required": ["cell", "layer"],
                  "properties": {"cell": {}, "layer": {}}},
            "b": {"type": "object", "required": ["layer"],
                  "properties": {"cell": {}, "layer": {}}},
            "op": {"type": "string", "enum": ["and", "or", "xor", "not"]},
            "write_to": {"type": "object", "required": ["layer"],
                         "properties": {"cell": {}, "layer": {}}},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "op": {"type": "string"},
            "polygon_count": {"type": "integer"},
            "area_um2": {"type": "number"},
            "bbox_um": {"type": "array"},
            "written": {"type": ["object", "null"]},
        },
    },
    mutates=True,
    tags=["geometry", "write"],
)
def geometry_boolean(params, ctx):
    view, _, ly = _active_layout()
    dbu = float(ly.dbu)
    a = params["a"]
    b = params["b"]
    cell_a = _resolve_cell(ly, a["cell"])
    cell_b = _resolve_cell(ly, b["cell"]) if b.get("cell") is not None else cell_a
    reg_a = _region_of(ly, cell_a, _parse_ld(a["layer"], "a.layer"))
    reg_b = _region_of(ly, cell_b, _parse_ld(b["layer"], "b.layer"))

    op = str(params["op"]).lower()
    if op == "and":
        res = reg_a & reg_b
    elif op == "or":
        res = reg_a | reg_b
    elif op == "xor":
        res = reg_a ^ reg_b
    elif op == "not":
        res = reg_a - reg_b
    else:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "op %r must be one of and/or/xor/not" % (op,))
    res.merge()

    out = {"op": op, **_region_stats(res, dbu), "written": None}

    wt = params.get("write_to")
    if wt is not None:
        tgt = _resolve_cell(ly, wt["cell"]) if wt.get("cell") is not None else cell_a
        ld = _parse_ld(wt["layer"], "write_to.layer")
        li = ly.layer(ld[0], ld[1])
        title = "klink: geometry.%s -> %s %d/%d" % (op, tgt.name, ld[0], ld[1])
        with auto_txn(view, title):
            tgt.shapes(li).insert(res)
        out["written"] = {"cell": tgt.name, "layer": "%d/%d" % ld,
                          "polygon_count": int(res.count())}
    return out


@method(
    "geometry.cell_xor",
    description=(
        "Geometric diff between two cells, per layer (pure report, writes "
        "nothing): for every layer present in either cell, XOR the merged "
        "hierarchical geometry and report diff polygon_count / area_um2. "
        "`layers` restricts the comparison; `only_differing` (default "
        "true) omits identical layers from the listing. equal==true means "
        "byte-level geometric identity on every compared layer. THE tool "
        "for 'did my edit change only what I intended' — compare a "
        "backup/reference cell against the edited one."
    ),
    params_schema={
        "type": "object",
        "required": ["cell_a", "cell_b"],
        "properties": {
            "cell_a": {"description": "cell name or index"},
            "cell_b": {"description": "cell name or index"},
            "layers": {"type": "array",
                       "description": "['L/D', ...]; default = union of "
                                      "layers present in either cell"},
            "only_differing": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "equal": {"type": "boolean"},
            "layers_compared": {"type": "integer"},
            "layers_differing": {"type": "integer"},
            "diff": {"type": "array", "items": {"type": "object"}},
        },
    },
    tags=["geometry", "read"],
)
def geometry_cell_xor(params, ctx):
    _view, _, ly = _active_layout()
    dbu = float(ly.dbu)
    cell_a = _resolve_cell(ly, params["cell_a"])
    cell_b = _resolve_cell(ly, params["cell_b"])

    if params.get("layers"):
        lds = [_parse_ld(s) for s in params["layers"]]
    else:
        lds = sorted({(info.layer, info.datatype)
                      for li in ly.layer_indexes()
                      for info in [ly.get_info(li)]})
    only_diff = bool(params.get("only_differing", True))

    diff = []
    differing = 0
    for ld in lds:
        d = (_region_of(ly, cell_a, ld) ^ _region_of(ly, cell_b, ld))
        d.merge()
        entry = {"layer": "%d/%d" % ld, **_region_stats(d, dbu)}
        is_diff = not d.is_empty()
        if is_diff:
            differing += 1
        if is_diff or not only_diff:
            diff.append(entry)
    return {
        "equal": differing == 0,
        "layers_compared": len(lds),
        "layers_differing": differing,
        "diff": diff,
    }


@method(
    "geometry.density",
    description=(
        "Covered-area density of one layer in a cell: merged hierarchical "
        "geometry area divided by the window area. `window_um` [l,b,r,t] "
        "defaults to the cell's bbox on that layer. Returns area_um2, "
        "window_area_um2 and density (0..1). The pre-check for dummy-fill "
        "decisions (pair with cell.fill_region)."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "layer"],
        "properties": {
            "cell": {"description": "cell name or index"},
            "layer": {"description": "'L/D' or {layer, datatype}"},
            "window_um": {"type": "array", "minItems": 4, "maxItems": 4},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "area_um2": {"type": "number"},
            "window_area_um2": {"type": "number"},
            "density": {"type": "number"},
        },
    },
    tags=["geometry", "read"],
)
def geometry_density(params, ctx):
    _view, _, ly = _active_layout()
    dbu = float(ly.dbu)
    cell = _resolve_cell(ly, params["cell"])
    reg = _region_of(ly, cell, _parse_ld(params["layer"]))

    if params.get("window_um"):
        l, b, r, t = (float(v) for v in params["window_um"])
        win = pya.Box(int(round(l / dbu)), int(round(b / dbu)),
                      int(round(r / dbu)), int(round(t / dbu)))
        reg &= pya.Region(win)
        win_area = max((r - l) * (t - b), 0.0)
    else:
        if reg.is_empty():
            return {"area_um2": 0.0, "window_area_um2": 0.0, "density": 0.0}
        bb = reg.bbox()
        win_area = (bb.width() * dbu) * (bb.height() * dbu)

    area = reg.area() * dbu * dbu
    density = (area / win_area) if win_area > 0 else 0.0
    return {"area_um2": area, "window_area_um2": win_area,
            "density": density}
