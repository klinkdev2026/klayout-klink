"""
Fill / tiling methods.

`cell.fill_region` : tile a fill cell across a region (KLayout Fill Utility)

Rationale
---------
This is the RPC face of KLayout's built-in Fill Utility (Edit > Utilities),
i.e. `pya.Cell#fill_region`: place instances of a fill cell on a regular
row/column raster so they cover a region — dummy metal fill for density,
device/test-structure arrays, photonic fill. The region is explicit
geometry (boxes/polygons in microns); callers that want "the area the user
sent/selected" resolve the selection to boxes first and pass them here.

Single-pass fill only: one fill cell, one raster. No second-order fill
with a smaller cell and no auto-densification — report `remaining_area_um2`
honestly instead. The call form (10-arg overload incl. remaining_parts)
is probe-verified against a live KLayout 0.30 session.
"""

from __future__ import annotations

import math

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..txn import auto_txn
from .cell_m import _active_layout, _resolve_cell


def _parse_layer_spec(spec):
    """Accept "L/D" strings or {layer, datatype} dicts."""
    if isinstance(spec, str):
        parts = spec.split("/")
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "exclude layer %r is not 'L/D' (e.g. '1/0')" % (spec,))
    if isinstance(spec, dict) and "layer" in spec:
        return int(spec["layer"]), int(spec.get("datatype", 0))
    raise RpcError(
        ErrorCode.BAD_PARAMS,
        "exclude layer entry %r must be 'L/D' or {layer, datatype}" % (spec,))


def circle_points_um(circ) -> list:
    """Expand one {center, radius, start_angle_deg?, end_angle_deg?,
    npoints?} spec into micron polygon points. This is the shared region
    language for circles/sectors: `cell.fill_region` and `view.highlight`
    both accept `circles_um` and expand it here, so a highlighted sector
    is exactly the filled sector."""
    try:
        cx, cy = (float(v) for v in circ["center"])
        radius = float(circ["radius"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "bad circle spec %r: %s" % (circ, exc))
    a0 = float(circ.get("start_angle_deg", 0.0))
    a1 = float(circ.get("end_angle_deg", a0 + 360.0))
    while a1 <= a0:
        a1 += 360.0
    span = min(a1 - a0, 360.0)
    full = span >= 360.0 - 1e-9
    npts = int(circ.get("npoints") or 64)
    n_arc = max(8, int(round(npts * span / 360.0)))
    pts = []
    if not full:
        pts.append([cx, cy])
    for i in range(n_arc + 1):
        ang = math.radians(a0 + span * i / n_arc)
        pts.append([cx + radius * math.cos(ang), cy + radius * math.sin(ang)])
    if full:
        pts.pop()  # closing point duplicates the first arc sample
    return pts


def _um_box(bbox_um, dbu) -> pya.Box:
    l, b, r, t = (float(v) for v in bbox_um)
    return pya.Box(int(round(l / dbu)), int(round(b / dbu)),
                   int(round(r / dbu)), int(round(t / dbu)))


def _fill_instance_count(target: pya.Cell, fill_index: int) -> int:
    n = 0
    for inst in target.each_inst():
        try:
            if inst.cell_index == fill_index:
                n += int(inst.size())
        except Exception:
            n += 1
    return n


@method(
    "cell.fill_region",
    description=(
        "Tile a fill cell across a region (KLayout's built-in Fill Utility, "
        "pya.Cell#fill_region): dummy fill, device arrays, test-structure "
        "tiling. Region = union of `boxes_um`, `polygons_um`, `circles_um` "
        "(full circles or angular sectors), and `region_layers` (fill "
        "wherever the target cell has geometry on those layers — e.g. a "
        "hand-drawn blob on a scratch layer), minus geometry of "
        "`exclude_layers` (grown by `exclude_margin_um`). Only tiles that "
        "fit ENTIRELY inside the region are placed, so curved boundaries "
        "leave an unfilled rim — reported in `remaining_area_um2`. The fill "
        "footprint defaults to the fill "
        "cell's bbox (`fc_bbox_um` overrides); raster steps default to that "
        "footprint (`row_step_um`/`column_step_um` override, e.g. for gaps "
        "between tiles). Single pass, one raster — check `remaining_area_um2` "
        "in the result for uncovered leftovers. Placed instances are one "
        "undo step (Ctrl+Z reverts the whole fill)."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "fill_cell"],
        "properties": {
            "cell": {"description": "Target cell name (str) or cell_index (int)"},
            "fill_cell": {
                "description": "Fill cell name (str) or cell_index (int); "
                               "must exist in the active layout",
            },
            "boxes_um": {
                "type": "array",
                "items": {"type": "array", "minItems": 4, "maxItems": 4},
                "description": "Region boxes [l, b, r, t] in microns.",
            },
            "polygons_um": {
                "type": "array",
                "items": {"type": "array",
                          "items": {"type": "array", "minItems": 2, "maxItems": 2}},
                "description": "Region polygons as [[x, y], ...] in microns.",
            },
            "circles_um": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["center", "radius"],
                    "properties": {
                        "center": {"type": "array", "minItems": 2, "maxItems": 2},
                        "radius": {"type": "number", "exclusiveMinimum": 0},
                        "start_angle_deg": {"type": "number"},
                        "end_angle_deg": {"type": "number"},
                        "npoints": {"type": "integer", "minimum": 8},
                    },
                },
                "description": "Circles/sectors in microns. Omit angles for "
                               "a full circle; give start/end (CCW degrees, "
                               "0 = +x) for a pie sector.",
            },
            "region_layers": {
                "type": "array",
                "description": "Layers ('L/D' or {layer,datatype}) whose "
                               "geometry in the target cell (incl. children) "
                               "IS the region — e.g. a hand-drawn polygon "
                               "on a scratch layer.",
            },
            "exclude_layers": {
                "type": "array",
                "description": "Layers ('L/D' or {layer,datatype}) whose "
                               "geometry in the target cell (incl. children) "
                               "is subtracted from the region.",
            },
            "exclude_margin_um": {"type": "number", "minimum": 0},
            "fc_bbox_um": {"type": "array", "minItems": 4, "maxItems": 4},
            "row_step_um": {"type": "array", "minItems": 2, "maxItems": 2},
            "column_step_um": {"type": "array", "minItems": 2, "maxItems": 2},
            "origin_um": {"type": "array", "minItems": 2, "maxItems": 2},
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "placed": {"type": "integer"},
            "instance_entries": {"type": "integer"},
            "region_area_um2": {"type": "number"},
            "remaining_area_um2": {"type": "number"},
            "cell": {"type": "string"},
            "fill_cell": {"type": "string"},
        },
    },
    mutates=True,
    tags=["cell", "fill", "write"],
)
def cell_fill_region(params, ctx):
    view, _, ly = _active_layout()
    dbu = float(ly.dbu)
    target = _resolve_cell(ly, params["cell"])
    fill = _resolve_cell(ly, params["fill_cell"])
    fidx = fill.cell_index()

    if fidx == target.cell_index():
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "fill_cell and target cell are the same cell")
    try:
        if target.cell_index() in set(fill.called_cells()):
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "fill_cell %r contains the target cell %r; filling would "
                "create a recursive hierarchy" % (fill.name, target.name))
    except RpcError:
        raise
    except Exception:
        pass

    boxes = params.get("boxes_um") or []
    polys = params.get("polygons_um") or []
    circles = params.get("circles_um") or []
    region_layers = params.get("region_layers") or []
    if not boxes and not polys and not circles and not region_layers:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "pass at least one region source: boxes_um, polygons_um, "
            "circles_um, or region_layers. For a hand-drawn area, draw it "
            "on a scratch layer and pass region_layers=['L/D']; for 'the "
            "area the user sent', resolve the selection (selection.get / "
            "interaction.selection.latest) and pass its bbox as boxes_um.")

    region = pya.Region()
    for bb in boxes:
        region.insert(_um_box(bb, dbu))
    for pts in polys:
        try:
            region.insert(pya.Polygon(
                [pya.Point(int(round(float(x) / dbu)), int(round(float(y) / dbu)))
                 for x, y in pts]))
        except Exception as exc:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           "bad polygon %r: %s" % (pts, exc))
    for circ in circles:
        pts = circle_points_um(circ)
        region.insert(pya.Polygon(
            [pya.Point(int(round(x / dbu)), int(round(y / dbu)))
             for x, y in pts]))

    for spec in region_layers:
        lnum, dnum = _parse_layer_spec(spec)
        li = ly.layer(lnum, dnum)
        src = pya.Region(target.begin_shapes_rec(li))
        if src.is_empty():
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "region_layers %s/%s has no geometry in cell %r; draw the "
                "region there first (or check layer numbers with "
                "layer.list)" % (lnum, dnum, target.name))
        region += src

    region.merge()
    region_area_um2 = region.area() * dbu * dbu

    for spec in params.get("exclude_layers") or []:
        lnum, dnum = _parse_layer_spec(spec)
        li = ly.layer(lnum, dnum)
        excl = pya.Region(target.begin_shapes_rec(li))
        margin = float(params.get("exclude_margin_um") or 0.0)
        if margin > 0:
            excl.size(int(round(margin / dbu)))
        region -= excl

    if params.get("fc_bbox_um"):
        fc_box = _um_box(params["fc_bbox_um"], dbu)
    else:
        fcb = fill.dbbox()
        if fcb.empty():
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "fill cell %r has no geometry, so its footprint cannot be "
                "derived; pass fc_bbox_um explicitly" % (fill.name,))
        fc_box = _um_box([fcb.left, fcb.bottom, fcb.right, fcb.top], dbu)
    if fc_box.width() <= 0 or fc_box.height() <= 0:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "fill footprint must have positive width and height")

    if params.get("row_step_um"):
        dx, dy = params["row_step_um"]
        row = pya.Vector(int(round(float(dx) / dbu)), int(round(float(dy) / dbu)))
    else:
        row = pya.Vector(fc_box.width(), 0)
    if params.get("column_step_um"):
        dx, dy = params["column_step_um"]
        col = pya.Vector(int(round(float(dx) / dbu)), int(round(float(dy) / dbu)))
    else:
        col = pya.Vector(0, fc_box.height())

    origin = None
    if params.get("origin_um"):
        ox, oy = params["origin_um"]
        origin = pya.Point(int(round(float(ox) / dbu)), int(round(float(oy) / dbu)))

    before = _fill_instance_count(target, fidx)
    entries_before = sum(1 for _ in target.each_inst())
    remaining = pya.Region()
    title = "klink: fill %s into %s" % (fill.name, target.name)
    with auto_txn(view, title):
        try:
            target.fill_region(region, fidx, fc_box, row, col, origin,
                               remaining, pya.Vector(), None, pya.Box())
        except Exception as exc:
            raise RpcError(ErrorCode.INTERNAL, "fill_region failed: %s" % (exc,))

    return {
        "placed": _fill_instance_count(target, fidx) - before,
        "instance_entries": sum(1 for _ in target.each_inst()) - entries_before,
        "region_area_um2": region_area_um2,
        "remaining_area_um2": remaining.area() * dbu * dbu,
        "cell": target.name,
        "fill_cell": fill.name,
    }
