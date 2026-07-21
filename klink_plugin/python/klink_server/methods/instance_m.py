"""
Instance insertion methods (M3 Round 4).

`instance.insert`        - place an existing cell inside another cell,
                           with optional array (rows x cols, pitch).
`instance.insert_pcell`  - build a PCell variant cell from a library
                           (e.g. Basic.CIRCLE, Basic.TEXT) and insert
                           one instance of it in a parent cell.

Design notes
------------
* Rotation is specified in degrees. 0/90/180/270 with magnification=1
  use the integer `pya.Trans` (most common, LLM-predictable, and the
  representation GDS itself supports). Anything else promotes to the
  complex `pya.ICplxTrans`, which the event diff engine already
  serialises.
* `array` is the KLayout "CellInstArray" — it lives on the single
  instance, not as `rows*cols` separate instances. Pitch is an
  orthogonal grid (a-vector along +x, b-vector along +y). Non-orthogonal
  arrays can be added later via explicit {a, b} vectors.
* PCell parameter values accept {"layer": int, "datatype": int} or
  "L/D" strings for layer parameters; numbers/bools/strings pass
  through. Agents should call `pcell.info` first to learn the exact
  parameter names - they differ per library.
"""

from __future__ import annotations

from typing import Optional

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..txn import auto_txn, register_custom_edit
from .cell_m import _active_layout, _resolve_cell
from .shape_m import _point_from_um_or_dbu, _scalar_from_um_or_dbu


_INT_ROT = {0: 0, 90: 1, 180: 2, 270: 3, -90: 3, -180: 2, -270: 1}
_MAX_BATCH_INSTANCES = 100_000


def _build_trans(params: dict, dbu: float):
    """Assemble the KLayout transformation from the usual param set.

    Returns a pya.Trans when the edit is axis-aligned with no mag (the
    common case - cleanest in the GDS), or a pya.ICplxTrans otherwise.
    """
    x, y = 0, 0
    if "position_um" in params or "position_dbu" in params:
        x, y = _point_from_um_or_dbu(params, dbu, "position_dbu", "position_um")

    rot_raw = params.get("rotation", 0)
    try:
        rot_f = float(rot_raw)
    except Exception:
        raise RpcError(ErrorCode.BAD_PARAMS, "rotation must be a number (degrees)")

    mirror = bool(params.get("mirror", False))
    try:
        mag = float(params.get("magnification", 1.0))
    except Exception:
        raise RpcError(ErrorCode.BAD_PARAMS, "magnification must be a number")

    # Normalise rotation into [-360, 360] and check if it's axis-aligned.
    rot_int = None
    if abs(rot_f - round(rot_f)) < 1e-9:
        r = int(round(rot_f)) % 360
        if r in _INT_ROT:
            rot_int = _INT_ROT[r]

    if rot_int is not None and abs(mag - 1.0) < 1e-12:
        return pya.Trans(rot_int, mirror, int(x), int(y))
    return pya.ICplxTrans(mag, rot_f, mirror, float(x), float(y))


def _trans_to_dict(t) -> dict:
    """JSON-safe serialisation of Trans / ICplxTrans."""
    out: dict = {}
    try:
        out["dx_dbu"] = int(t.disp.x)
        out["dy_dbu"] = int(t.disp.y)
    except Exception:
        pass
    try:
        out["rotation_deg"] = float(t.angle)
    except Exception:
        pass
    try:
        out["mirror"] = bool(t.is_mirror())
    except Exception:
        out["mirror"] = False
    try:
        out["magnification"] = float(t.mag)
    except Exception:
        out["magnification"] = 1.0
    return out


def _inst_bbox(inst) -> Optional[list]:
    try:
        bb = inst.bbox()
        if bb.empty():
            return None
        return [bb.left, bb.bottom, bb.right, bb.top]
    except Exception:
        return None


def _bbox_union_lists(boxes: list) -> Optional[list]:
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _layer_key(layout: pya.Layout, layer_idx: int) -> str:
    try:
        li = layout.get_info(layer_idx)
        return f"{li.layer}/{li.datatype}"
    except Exception:
        return f"idx_{layer_idx}"


def _cell_shapes_by_layer(cell: pya.Cell) -> dict:
    by_layer: dict = {}
    try:
        layout = cell.layout()
        layer_indexes = list(layout.layer_indexes())
    except Exception:
        return by_layer

    for layer_idx in layer_indexes:
        count = 0
        non_text = 0
        try:
            for shape in cell.shapes(layer_idx).each():
                count += 1
                if not shape.is_text():
                    non_text += 1
        except Exception:
            continue
        if count:
            key = _layer_key(layout, layer_idx)
            by_layer[key] = {"count": int(count), "non_text": int(non_text)}
    return by_layer


def _pcell_metadata(cell: pya.Cell) -> Optional[dict]:
    try:
        decl = cell.pcell_declaration()
    except Exception:
        decl = None
    if decl is None:
        return None

    name = ""
    try:
        raw_name = decl.name
        name = raw_name() if callable(raw_name) else raw_name
    except Exception:
        pass

    params = {}
    try:
        defs = list(decl.get_parameters())
        vals = list(cell.pcell_parameters())
        for pdef, value in zip(defs, vals):
            try:
                pname = str(pdef.name)
                if isinstance(value, pya.LayerInfo):
                    params[pname] = {
                        "layer": int(value.layer),
                        "datatype": int(value.datatype),
                        "name": value.name or None,
                    }
                elif isinstance(value, (bool, int, float, str)) or value is None:
                    params[pname] = value
                else:
                    params[pname] = str(value)
            except Exception:
                pass
    except Exception:
        pass

    return {"name": str(name), "params": params}


def _instance_to_dict(inst) -> dict:
    child = inst.cell
    out = {
        "child": child.name,
        "child_cell_index": int(child.cell_index()),
        "trans": _trans_to_dict(inst.cplx_trans),
        "bbox_dbu": _inst_bbox(inst),
        "array": None,
        "pcell": _pcell_metadata(child),
        "child_shapes_by_layer": _cell_shapes_by_layer(child),
    }
    try:
        na = int(inst.na)
        nb = int(inst.nb)
        if na > 1 or nb > 1:
            a = inst.a
            b = inst.b
            out["array"] = {
                "na": na,
                "nb": nb,
                "a_dbu": [int(a.x), int(a.y)],
                "b_dbu": [int(b.x), int(b.y)],
            }
    except Exception:
        pass
    return out


def _validate_batch_items(values, name: str) -> list:
    if not isinstance(values, list):
        raise RpcError(ErrorCode.BAD_PARAMS, f"{name} must be a list")
    if not values:
        raise RpcError(ErrorCode.BAD_PARAMS, f"{name} must not be empty")
    if len(values) > _MAX_BATCH_INSTANCES:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"{name} is too large ({len(values)} > {_MAX_BATCH_INSTANCES})",
        )
    for i, item in enumerate(values):
        if not isinstance(item, dict):
            raise RpcError(ErrorCode.BAD_PARAMS, f"{name}[{i}] must be an object")
    return values


def _resolve_child_cell(ly, child_ref, library=None):
    """Resolve the cell to instantiate. Without `library` this is an
    ordinary lookup in the active layout. With `library`, the cell is
    looked up in that registered library's layout and imported into the
    active layout as a library proxy (Layout#add_lib_cell — idempotent:
    repeated placements reuse one proxy), which is how cells from
    libraries registered via library.register_file become placeable by
    name without copying their tree in."""
    if library is None:
        return _resolve_cell(ly, child_ref)
    from .pcell_m import _resolve_library

    lib = _resolve_library(str(library))
    lib_ly = lib.layout()
    src = None
    try:
        src = lib_ly.cell(child_ref if isinstance(child_ref, int) else str(child_ref))
    except Exception:
        src = None
    if src is None:
        tops = []
        try:
            for tc in lib_ly.top_cells()[:20]:
                tops.append(lib_ly.cell_name(tc.cell_index()))
        except Exception:
            pass
        raise RpcError(
            ErrorCode.NOT_FOUND,
            f"library {lib.name()!r} has no cell {child_ref!r}",
            hint=f"top cells in this library: {tops}; library.list shows all libraries",
        )
    return ly.cell(ly.add_lib_cell(lib, src.cell_index()))


def _build_cell_inst_array(cell_index: int, trans, array, dbu: float):
    """Build a pya.CellInstArray for `cell_index` with optional array.

    `array` may be None (single placement) or a dict in one of two
    shapes:

      * Orthogonal (legacy / human-friendly):
          {"rows": nb, "cols": na,
           "pitch_x_um|_dbu": ..., "pitch_y_um|_dbu": ...}
        The A axis is +x, the B axis is +y.

      * General vectors (what the event stream emits so that rotated /
        sheared arrays like those placed in the GUI survive round-trip):
          {"na": int, "nb": int,
           "a_dbu": [x, y],  (or "a_um")
           "b_dbu": [x, y]}  (or "b_um")

    Returns (inst_spec, array_info_dict_or_None). The info dict always
    includes the canonical {na, nb, a_dbu, b_dbu} form plus the
    orthogonal aliases when applicable, so callers can report either
    view to the client.
    """
    if array is None:
        return pya.CellInstArray(cell_index, trans), None
    if not isinstance(array, dict):
        raise RpcError(ErrorCode.BAD_PARAMS, "array must be an object")

    has_vectors = ("a_dbu" in array) or ("a_um" in array) \
                  or ("b_dbu" in array) or ("b_um" in array) \
                  or ("na" in array) or ("nb" in array)

    if has_vectors:
        na = int(array.get("na", array.get("cols", 1)))
        nb = int(array.get("nb", array.get("rows", 1)))
        if na < 1 or nb < 1:
            raise RpcError(ErrorCode.BAD_PARAMS, "array.na/nb must be >= 1")
        a = array.get("a_dbu")
        if a is None and "a_um" in array:
            au = array["a_um"]
            a = [int(round(float(au[0]) / dbu)), int(round(float(au[1]) / dbu))]
        b = array.get("b_dbu")
        if b is None and "b_um" in array:
            bu = array["b_um"]
            b = [int(round(float(bu[0]) / dbu)), int(round(float(bu[1]) / dbu))]
        if a is None or b is None:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "array: provide both a_dbu/a_um and b_dbu/b_um (or use "
                "rows/cols + pitch_x/pitch_y for orthogonal grids)",
            )
        a_vec = pya.Vector(int(a[0]), int(a[1]))
        b_vec = pya.Vector(int(b[0]), int(b[1]))
    else:
        rows = int(array.get("rows", 1))
        cols = int(array.get("cols", 1))
        if rows < 1 or cols < 1:
            raise RpcError(ErrorCode.BAD_PARAMS, "array.rows/cols must be >= 1")
        px = _scalar_from_um_or_dbu(array, dbu, "pitch_x_dbu", "pitch_x_um")
        py = _scalar_from_um_or_dbu(array, dbu, "pitch_y_dbu", "pitch_y_um")
        a_vec = pya.Vector(int(px), 0)
        b_vec = pya.Vector(0, int(py))
        na, nb = cols, rows

    inst_spec = pya.CellInstArray(cell_index, trans, a_vec, b_vec, na, nb)
    info = {
        "na": na, "nb": nb,
        "a_dbu": [int(a_vec.x), int(a_vec.y)],
        "b_dbu": [int(b_vec.x), int(b_vec.y)],
    }
    # Expose legacy keys when the array is axis-aligned, so the response
    # matches what existing rows/cols callers would expect.
    if a_vec.y == 0 and b_vec.x == 0:
        info["rows"] = nb
        info["cols"] = na
        info["pitch_x_dbu"] = int(a_vec.x)
        info["pitch_y_dbu"] = int(b_vec.y)
    return inst_spec, info


# ----------------------------------------------------------------------
# instance.insert
# ----------------------------------------------------------------------
@method(
    "instance.insert",
    description=(
        "Place `child` inside `parent`. Position is in microns "
        "(position_um) or dbu (position_dbu); rotation is degrees "
        "(0/90/180/270 stay as integer transforms, else promoted to a "
        "complex transform with magnification). Optional `array` "
        "creates a grid. Two array shapes are accepted: orthogonal "
        "{rows, cols, pitch_x_um/dbu, pitch_y_um/dbu} and general "
        "{na, nb, a_dbu|a_um:[x,y], b_dbu|b_um:[x,y]} (the general "
        "form survives rotated/sheared arrays). Without an array you "
        "get a single instance. Cyclic hierarchy (child already "
        "contains parent) is rejected."
    ),
    params_schema={
        "type": "object",
        "required": ["parent", "child"],
        "properties": {
            "parent": {"description": "parent cell (name or index)"},
            "child":  {"description": "cell to instantiate (name or index; "
                                      "with `library`, a cell name inside "
                                      "that registered library)"},
            "library": {"type": "string",
                        "description": "registered library to take `child` "
                                       "from (e.g. one made by "
                                       "library.register_file); imported as "
                                       "a proxy, not copied"},
            "position_um":  {"type": "array", "minItems": 2, "maxItems": 2},
            "position_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
            "rotation":     {"type": "number", "default": 0, "description": "degrees CCW"},
            "mirror":       {"type": "boolean", "default": False, "description": "mirror across x-axis before rotation"},
            "magnification":{"type": "number", "default": 1.0},
            "array": {
                "type": "object",
                "properties": {
                    "rows": {"type": "integer", "minimum": 1},
                    "cols": {"type": "integer", "minimum": 1},
                    "pitch_x_um":  {"type": "number"},
                    "pitch_x_dbu": {"type": "integer"},
                    "pitch_y_um":  {"type": "number"},
                    "pitch_y_dbu": {"type": "integer"},
                    "na":   {"type": "integer", "minimum": 1},
                    "nb":   {"type": "integer", "minimum": 1},
                    "a_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
                    "a_um":  {"type": "array", "minItems": 2, "maxItems": 2},
                    "b_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
                    "b_um":  {"type": "array", "minItems": 2, "maxItems": 2},
                },
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "parent": {"type": "string"},
            "child":  {"type": "string"},
            "trans":  {"type": "object"},
            "bbox_dbu": {"type": ["array", "null"]},
            "array":  {"type": ["object", "null"]},
        },
    },
    mutates=True,
    tags=["instance", "write"],
)
def instance_insert(params, ctx):
    view, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["parent"])
    child = _resolve_child_cell(ly, params["child"], params.get("library"))
    if parent.cell_index() == child.cell_index():
        raise RpcError(ErrorCode.BAD_PARAMS, "a cell cannot instantiate itself")

    trans = _build_trans(params, ly.dbu)

    inst_spec, array_info = _build_cell_inst_array(
        child.cell_index(), trans, params.get("array"), ly.dbu
    )

    title = f"klink: insert {child.name} into {parent.name}"
    with auto_txn(view, title):
        try:
            inst = parent.insert(inst_spec)
        except Exception as e:
            raise RpcError(
                ErrorCode.EXEC,
                f"KLayout rejected the instance: {e}",
                hint=(
                    "common causes: cyclic hierarchy (child already "
                    "references parent) or a cell from a different layout"
                ),
            )

    return {
        "parent": parent.name,
        "child": child.name,
        "trans": _trans_to_dict(trans),
        "bbox_dbu": _inst_bbox(inst),
        "array": array_info,
    }


@method(
    "instance.insert_many",
    description=(
        "Insert many existing child-cell instances into one parent cell "
        "in a single RPC and one undo transaction. Each item has `child` "
        "plus the same transform/array fields accepted by instance.insert."
    ),
    params_schema={
        "type": "object",
        "required": ["parent", "items"],
        "properties": {
            "parent": {"description": "parent cell (name or index)"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["child"],
                    "properties": {
                        "child": {"description": "cell to instantiate (name or index)"},
                        "library": {"type": "string",
                                    "description": "registered library to "
                                                   "take `child` from"},
                        "position_um": {"type": "array", "minItems": 2, "maxItems": 2},
                        "position_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
                        "rotation": {"type": "number", "default": 0},
                        "mirror": {"type": "boolean", "default": False},
                        "magnification": {"type": "number", "default": 1.0},
                        "array": {"type": "object"},
                    },
                },
            },
            "dry_run": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "parent": {"type": "string"},
            "requested": {"type": "integer"},
            "inserted": {"type": "integer"},
            "by_child": {"type": "object"},
            "bbox_dbu": {"type": ["array", "null"]},
            "dry_run": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["instance", "write", "batch"],
)
def instance_insert_many(params, ctx):
    view, _, ly = _active_layout()
    if "parent" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'parent' is required")
    parent = _resolve_cell(ly, params["parent"])
    items = _validate_batch_items(params.get("items"), "items")
    dry_run = bool(params.get("dry_run", False))

    ops = []
    by_child: dict = {}
    for i, item in enumerate(items):
        if "child" not in item:
            raise RpcError(ErrorCode.BAD_PARAMS, f"items[{i}]: 'child' is required")
        child = _resolve_child_cell(ly, item["child"], item.get("library"))
        if parent.cell_index() == child.cell_index():
            raise RpcError(ErrorCode.BAD_PARAMS, f"items[{i}]: a cell cannot instantiate itself")
        trans = _build_trans(item, ly.dbu)
        inst_spec, array_info = _build_cell_inst_array(
            child.cell_index(), trans, item.get("array"), ly.dbu
        )
        ops.append((child, inst_spec, array_info))
        by_child[child.name] = by_child.get(child.name, 0) + 1

    inserted = []
    if not dry_run:
        with auto_txn(view, f"klink: insert {len(ops)} instances into {parent.name}"):
            for child, inst_spec, _ in ops:
                try:
                    inserted.append(parent.insert(inst_spec))
                except Exception as e:
                    raise RpcError(
                        ErrorCode.EXEC,
                        f"KLayout rejected instance of {child.name}: {e}",
                        hint="common causes: cyclic hierarchy or a cell from a different layout",
                    )

    return {
        "parent": parent.name,
        "requested": len(ops),
        "inserted": 0 if dry_run else len(inserted),
        "by_child": by_child,
        "bbox_dbu": None if dry_run else _bbox_union_lists([_inst_bbox(inst) for inst in inserted]),
        "dry_run": dry_run,
    }


# ----------------------------------------------------------------------
# instance.insert_pcell
# ----------------------------------------------------------------------
def _adapt_pcell_value(v):
    """Convert one JSON-safe value into the pya type a PCell expects.

    Supported magic dict shapes (for Basic library PCells that take
    geometric parameters):
      * Layer:    {"layer": int, "datatype": int}        -> pya.LayerInfo
      * Layer:    "L/D"                                   -> pya.LayerInfo
      * Box:      {"bbox_um": [x1,y1,x2,y2]}              -> pya.DBox
      * Polygon:  {"points_um": [[x,y],...]}              -> pya.DPolygon
      * Path:     {"points_um": [...], "width_um": num}   -> pya.DPath
      * DPoint:   {"point_um": [x,y]}                     -> pya.DPoint

    The caller can still pass raw pya objects or primitive values; they
    pass through unchanged.
    """
    # LayerInfo via "L/D" string
    if isinstance(v, str) and "/" in v:
        l_s, _, d_s = v.partition("/")
        try:
            return pya.LayerInfo(int(l_s), int(d_s))
        except Exception:
            pass

    if not isinstance(v, dict):
        return v

    # LayerInfo
    if "layer" in v and ("datatype" in v or len(v) <= 2):
        try:
            return pya.LayerInfo(int(v["layer"]), int(v.get("datatype", 0)))
        except Exception:
            pass

    # Point
    if "point_um" in v:
        p = v["point_um"]
        try:
            return pya.DPoint(float(p[0]), float(p[1]))
        except Exception:
            pass

    # Box
    if "bbox_um" in v:
        b = v["bbox_um"]
        try:
            return pya.DBox(float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        except Exception:
            pass

    # Path: points + width
    if "points_um" in v and ("width_um" in v or "width" in v):
        try:
            pts = [pya.DPoint(float(p[0]), float(p[1])) for p in v["points_um"]]
            w = float(v.get("width_um", v.get("width", 0.0)))
            return pya.DPath(pts, w)
        except Exception:
            pass

    # Polygon: points only
    if "points_um" in v:
        try:
            pts = [pya.DPoint(float(p[0]), float(p[1])) for p in v["points_um"]]
            return pya.DPolygon(pts)
        except Exception:
            pass

    return v


def _adapt_pcell_params(raw: dict) -> dict:
    """Apply _adapt_pcell_value to every param value in the dict."""
    return {k: _adapt_pcell_value(v) for k, v in raw.items()}


def _resolve_library(lib_name: str):
    lib = pya.Library.library_by_name(lib_name)
    if lib is None:
        try:
            for lid in pya.Library.library_ids():
                try:
                    candidate = pya.Library.library_by_id(lid)
                    if candidate is not None and candidate.name() == lib_name:
                        lib = candidate
                        break
                except Exception:
                    pass
        except Exception:
            pass
    if lib is None:
        try:
            names = list(pya.Library.library_names())
        except Exception:
            names = []
        raise RpcError(
            ErrorCode.NOT_FOUND,
            f"no library named {lib_name!r}",
            hint=f"known libraries: {names}",
        )
    return lib


@method(
    "instance.insert_pcell",
    description=(
        "Build a PCell variant cell from a library (e.g. Basic.CIRCLE, "
        "Basic.TEXT) using the supplied `params` dict, then insert one "
        "instance of it in `parent`. Call pcell.info(library, pcell) "
        "first to discover the exact parameter names and types. Layer "
        "parameters accept {'layer': int, 'datatype': int} or 'L/D'. "
        "Identical params reuse the same variant cell (KLayout merges "
        "on value-identity)."
    ),
    params_schema={
        "type": "object",
        "required": ["parent", "pcell"],
        "properties": {
            "parent":  {"description": "parent cell (name or index)"},
            "library": {"type": "string", "default": "Basic"},
            "pcell":   {"type": "string", "description": "PCell name, e.g. CIRCLE"},
            "params":  {"type": "object", "description": "PCell parameter dict (see pcell.info)"},
            "position_um":  {"type": "array"},
            "position_dbu": {"type": "array"},
            "rotation":     {"type": "number"},
            "mirror":       {"type": "boolean"},
            "magnification":{"type": "number"},
            "array": {
                "type": "object",
                "description": (
                    "Optional PCell array. Same shape as instance.insert: "
                    "either {rows, cols, pitch_x_um/dbu, pitch_y_um/dbu} "
                    "or {na, nb, a_dbu|a_um, b_dbu|b_um}."
                ),
                "properties": {
                    "rows": {"type": "integer", "minimum": 1},
                    "cols": {"type": "integer", "minimum": 1},
                    "pitch_x_um":  {"type": "number"},
                    "pitch_x_dbu": {"type": "integer"},
                    "pitch_y_um":  {"type": "number"},
                    "pitch_y_dbu": {"type": "integer"},
                    "na":   {"type": "integer", "minimum": 1},
                    "nb":   {"type": "integer", "minimum": 1},
                    "a_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
                    "a_um":  {"type": "array", "minItems": 2, "maxItems": 2},
                    "b_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
                    "b_um":  {"type": "array", "minItems": 2, "maxItems": 2},
                },
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "parent": {"type": "string"},
            "library": {"type": "string"},
            "pcell":   {"type": "string"},
            "variant_cell":       {"type": "string"},
            "variant_cell_index": {"type": "integer"},
            "trans":  {"type": "object"},
            "variant_bbox_dbu": {"type": ["array", "null"]},
            "adapted_params":       {"type": "object"},
            "variant_shape_count":  {"type": "integer"},
            "variant_shapes_by_layer": {"type": "object"},
            "array":  {"type": ["object", "null"]},
        },
    },
    mutates=True,
    tags=["instance", "pcell", "write"],
)
def instance_insert_pcell(params, ctx):
    view, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["parent"])

    lib_name = params.get("library", "Basic")
    pcell_name = params.get("pcell")
    if not isinstance(pcell_name, str) or not pcell_name:
        raise RpcError(ErrorCode.BAD_PARAMS, "'pcell' (name) is required")

    raw_params = params.get("params") or {}
    if not isinstance(raw_params, dict):
        raise RpcError(ErrorCode.BAD_PARAMS, "'params' must be an object")
    pcell_params = _adapt_pcell_params(raw_params)

    lib = pya.Library.library_by_name(lib_name)
    # KLayout ≥0.27: library_by_name(name) only finds libraries NOT bound
    # to a technology.  SiEPIC and other PDK libraries are technology-bound
    # and will be missed.  Fall back to iterating by ID and matching name.
    # https://github.com/KLayout/klayout/issues/879
    if lib is None:
        try:
            for lid in pya.Library.library_ids():
                try:
                    candidate = pya.Library.library_by_id(lid)
                    if candidate is not None and candidate.name() == lib_name:
                        lib = candidate
                        break
                except Exception:
                    pass
        except Exception:
            pass
        if lib is None:
            try:
                names = list(pya.Library.library_names())
            except Exception:
                names = []
            raise RpcError(
                ErrorCode.NOT_FOUND,
                f"no library named {lib_name!r}",
                hint=f"known libraries: {names}",
            )

    trans = _build_trans(params, ly.dbu)

    # `ly.create_cell(pcell, lib, params)` materialises a PCell variant
    # via the library; this step is inherently non-transactional on
    # KLayout's side and may clear the undo stack. We don't fight it -
    # the explicit txn system uses Layout.dup()/assign() for rollback,
    # so undo stack cleanliness is not required for correctness.
    with auto_txn(view, f"klink: insert pcell {lib_name}.{pcell_name}"):
        try:
            variant = ly.create_cell(pcell_name, lib_name, pcell_params)
        except Exception as e:
            raise RpcError(
                ErrorCode.EXEC,
                f"could not instantiate pcell {lib_name}.{pcell_name}: {e}",
                hint="call pcell.info to verify the parameter names/types",
            )
        if variant is None:
            raise RpcError(
                ErrorCode.NOT_FOUND,
                f"pcell {lib_name}.{pcell_name} not found",
                hint="call pcell.list to see available PCells in the library",
            )
        try:
            inst_spec, array_info = _build_cell_inst_array(
                variant.cell_index(), trans, params.get("array"), ly.dbu
            )
            parent.insert(inst_spec)
        except RpcError:
            raise
        except Exception as e:
            raise RpcError(ErrorCode.EXEC, f"instance insert failed: {e}")

    try:
        bb = variant.bbox()
        vbb = None if bb.empty() else [bb.left, bb.bottom, bb.right, bb.top]
    except Exception:
        vbb = None

    # Debug aid: report what the server actually handed to KLayout (so
    # the user can tell whether _adapt_pcell_value produced the expected
    # pya.{DBox,DPath,DPolygon,LayerInfo,...} object) and what came out
    # the other side (how many shapes the PCell actually produced, split
    # by layer). A param that converted cleanly but ended up with zero
    # shapes points to a PCell-produce issue, not a transport issue.
    adapted_types: dict = {}
    for k, v in pcell_params.items():
        try:
            tn = type(v).__name__
            s = repr(v)
            if len(s) > 160:
                s = s[:160] + "..."
            adapted_types[k] = f"{tn}: {s}"
        except Exception:
            adapted_types[k] = "<repr failed>"

    shape_total = 0
    shapes_by_layer: dict = {}
    try:
        # The variant cell may live in the library's layout, not ly,
        # so always iterate via variant.layout() rather than ly.
        v_ly = variant.layout()
        for lidx in v_ly.layer_indexes():
            try:
                li = v_ly.get_info(lidx)
                lkey = f"{li.layer}/{li.datatype}"
            except Exception:
                lkey = f"idx_{lidx}"
            try:
                n = variant.shapes(lidx).size()
            except Exception:
                n = 0
            if n:
                shapes_by_layer[lkey] = int(n)
                shape_total += int(n)
    except Exception:
        pass

    return {
        "parent": parent.name,
        "library": lib_name,
        "pcell": pcell_name,
        "variant_cell": variant.name,
        "variant_cell_index": int(variant.cell_index()),
        "trans": _trans_to_dict(trans),
        "variant_bbox_dbu": vbb,
        "adapted_params": adapted_types,
        "variant_shape_count": int(shape_total),
        "variant_shapes_by_layer": shapes_by_layer,
        "array": array_info,
    }


@method(
    "instance.insert_pcell_many",
    description=(
        "Insert many PCell instances into one parent cell in a single RPC "
        "and one undo transaction. Each item accepts library, pcell, params, "
        "transform fields, and optional array, matching instance.insert_pcell."
    ),
    params_schema={
        "type": "object",
        "required": ["parent", "items"],
        "properties": {
            "parent": {"description": "parent cell (name or index)"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["pcell"],
                    "properties": {
                        "library": {"type": "string", "default": "Basic"},
                        "pcell": {"type": "string"},
                        "params": {"type": "object"},
                        "position_um": {"type": "array"},
                        "position_dbu": {"type": "array"},
                        "rotation": {"type": "number"},
                        "mirror": {"type": "boolean"},
                        "magnification": {"type": "number"},
                        "array": {"type": "object"},
                    },
                },
            },
            "dry_run": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "parent": {"type": "string"},
            "requested": {"type": "integer"},
            "inserted": {"type": "integer"},
            "by_pcell": {"type": "object"},
            "variant_cells": {"type": "array"},
            "bbox_dbu": {"type": ["array", "null"]},
            "dry_run": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["instance", "pcell", "write", "batch"],
)
def instance_insert_pcell_many(params, ctx):
    view, _, ly = _active_layout()
    if "parent" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'parent' is required")
    parent = _resolve_cell(ly, params["parent"])
    items = _validate_batch_items(params.get("items"), "items")
    dry_run = bool(params.get("dry_run", False))

    ops = []
    by_pcell: dict = {}
    for i, item in enumerate(items):
        lib_name = item.get("library", "Basic")
        pcell_name = item.get("pcell")
        if not isinstance(lib_name, str) or not lib_name:
            raise RpcError(ErrorCode.BAD_PARAMS, f"items[{i}]: 'library' must be a string")
        if not isinstance(pcell_name, str) or not pcell_name:
            raise RpcError(ErrorCode.BAD_PARAMS, f"items[{i}]: 'pcell' is required")
        raw_params = item.get("params") or {}
        if not isinstance(raw_params, dict):
            raise RpcError(ErrorCode.BAD_PARAMS, f"items[{i}]: 'params' must be an object")
        _resolve_library(lib_name)
        pcell_params = _adapt_pcell_params(raw_params)
        trans = _build_trans(item, ly.dbu)
        ops.append((lib_name, pcell_name, pcell_params, trans, item.get("array")))
        key = f"{lib_name}/{pcell_name}"
        by_pcell[key] = by_pcell.get(key, 0) + 1

    inserted = []
    variants = []
    inserted_specs = []
    if not dry_run:
        prepared = []
        for lib_name, pcell_name, pcell_params, trans, array in ops:
            try:
                variant = ly.create_cell(pcell_name, lib_name, pcell_params)
            except Exception as e:
                raise RpcError(
                    ErrorCode.EXEC,
                    f"could not instantiate pcell {lib_name}.{pcell_name}: {e}",
                    hint="call pcell.info to verify the parameter names/types",
                )
            if variant is None:
                raise RpcError(
                    ErrorCode.NOT_FOUND,
                    f"pcell {lib_name}.{pcell_name} not found",
                    hint="call pcell.list to see available PCells in the library",
                )
            prepared.append((lib_name, pcell_name, variant, trans, array))

        with auto_txn(view, f"klink: insert {len(ops)} pcell instances into {parent.name}"):
            for lib_name, pcell_name, variant, trans, array in prepared:
                try:
                    inst_spec, _ = _build_cell_inst_array(
                        variant.cell_index(), trans, array, ly.dbu
                    )
                    inserted.append(parent.insert(inst_spec))
                    inserted_specs.append(inst_spec)
                    variants.append({
                        "library": lib_name,
                        "pcell": pcell_name,
                        "variant_cell": variant.name,
                        "variant_cell_index": int(variant.cell_index()),
                    })
                except RpcError:
                    raise
                except Exception as e:
                    raise RpcError(ErrorCode.EXEC, f"instance insert failed: {e}")

        parent_index = int(parent.cell_index())
        inserted_refs = list(inserted)
        specs_for_redo = list(inserted_specs)

        def _undo_inserted_pcells():
            p = ly.cell(parent_index)
            if p is None:
                return
            for inst in reversed(inserted_refs):
                try:
                    p.erase(inst)
                except Exception:
                    pass

        def _redo_inserted_pcells():
            p = ly.cell(parent_index)
            if p is None:
                return
            inserted_refs[:] = []
            for spec in specs_for_redo:
                try:
                    inserted_refs.append(p.insert(spec))
                except Exception:
                    pass

        register_custom_edit(
            f"klink: insert {len(inserted_specs)} pcell instances into {parent.name}",
            _undo_inserted_pcells,
            _redo_inserted_pcells,
        )

    return {
        "parent": parent.name,
        "requested": len(ops),
        "inserted": 0 if dry_run else len(inserted),
        "by_pcell": by_pcell,
        "variant_cells": variants,
        "bbox_dbu": None if dry_run else _bbox_union_lists([_inst_bbox(inst) for inst in inserted]),
        "dry_run": dry_run,
    }


# ----------------------------------------------------------------------
# instance.query
# ----------------------------------------------------------------------
@method(
    "instance.query",
    description=(
        "List direct child instances in a parent cell. Returns child name, "
        "bbox, transform, optional array, PCell metadata, and the child "
        "cell's direct shape counts by layer. This is a read primitive for "
        "clients that need to inspect or clean up instance/PCell geometry "
        "without using exec.python."
    ),
    params_schema={
        "type": "object",
        "required": ["parent"],
        "properties": {
            "parent": {"description": "parent cell (name or index)"},
            "child": {"description": "optional child cell filter (name or index)"},
            "bbox_dbu": {"type": "array", "minItems": 4, "maxItems": 4},
            "bbox_um": {"type": "array", "minItems": 4, "maxItems": 4},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 10000},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "parent": {"type": "string"},
            "returned": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "instances": {"type": "array"},
        },
    },
    tags=["instance", "read"],
)
def instance_query(params, ctx):
    _, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["parent"])

    child_ref = params.get("child")
    if isinstance(child_ref, str):
        try:
            child_ref = _resolve_cell(ly, child_ref).name
        except Exception:
            raise RpcError(
                ErrorCode.NOT_FOUND,
                f"no cell named {child_ref!r}",
                hint="call cell.list to see existing cells",
            )

    bbox = None
    if "bbox_dbu" in params or "bbox_um" in params:
        bbox = _box_from_um_or_dbu_local(params, ly.dbu)

    limit = int(params.get("limit", 10000))
    out: list = []
    truncated = False
    try:
        for inst in parent.each_inst():
            if len(out) >= limit:
                truncated = True
                break
            if not _inst_matches_child(inst, child_ref):
                continue
            if bbox is not None:
                try:
                    ibb = inst.bbox()
                    if ibb.empty() or not bbox.touches(ibb):
                        continue
                except Exception:
                    continue
            try:
                out.append(_instance_to_dict(inst))
            except Exception:
                pass
    except Exception as e:
        raise RpcError(ErrorCode.INTERNAL, f"iteration failed: {e}")

    return {
        "parent": parent.name,
        "returned": len(out),
        "truncated": bool(truncated),
        "instances": out,
    }


# ----------------------------------------------------------------------
# instance.delete  (M3 Round 5)
# ----------------------------------------------------------------------
#
# Same declarative-selector philosophy as shape.delete: pya doesn't
# offer a stable per-instance id, so the caller describes WHICH
# instances to remove (in a given parent, optionally filtered by
# child cell and/or by bbox-overlap), and the server collects + erases
# them in one transaction.
#
# Deleting an instance does NOT delete the child cell - only the
# reference to it in this parent. Use cell.delete for the child.

def _inst_matches_child(inst: pya.Instance, child_ref) -> bool:
    """child_ref can be a cell name (str) or cell_index (int); None = match all."""
    if child_ref is None:
        return True
    try:
        ci = int(inst.cell.cell_index())
    except Exception:
        return False
    if isinstance(child_ref, int):
        return ci == int(child_ref)
    if isinstance(child_ref, str):
        try:
            return inst.cell.name == child_ref
        except Exception:
            return False
    return False


@method(
    "instance.delete",
    description=(
        "Delete instances from `parent` that match a declarative "
        "selector. Optional filters: `child` (cell name or cell_index), "
        "`bbox_dbu`/`bbox_um` (touching). Removing an instance is "
        "NON-destructive to the child cell itself - only the reference "
        "in this parent goes away. Wrapped in one transaction so undo "
        "rolls the whole batch back. With `dry_run=true` only the count "
        "is reported. With no filters you must pass `all=true` to "
        "confirm deleting every instance in the parent."
    ),
    params_schema={
        "type": "object",
        "required": ["parent"],
        "properties": {
            "parent": {"description": "parent cell (name or index)"},
            "child": {"description": "filter by child cell (name or index); omit to match all children"},
            "bbox_dbu": {"type": "array", "minItems": 4, "maxItems": 4},
            "bbox_um":  {"type": "array", "minItems": 4, "maxItems": 4},
            "all": {
                "type": "boolean",
                "default": False,
                "description": "Must be true if NEITHER child nor bbox filter is given.",
            },
            "limit":   {"type": "integer", "minimum": 1, "maximum": 100000, "default": 10000},
            "dry_run": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "parent":    {"type": "string"},
            "matched":   {"type": "integer"},
            "deleted":   {"type": "integer"},
            "per_child": {"type": "object"},
            "truncated": {"type": "boolean"},
            "dry_run":   {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["instance", "write", "delete"],
)
def instance_delete(params, ctx):
    view, _, ly = _active_layout()
    parent = _resolve_cell(ly, params["parent"])

    child_ref = params.get("child")
    if isinstance(child_ref, str):
        # Resolve to a canonical name so comparisons are exact (and so we
        # fail fast on a typo rather than silently matching nothing).
        try:
            c = _resolve_cell(ly, child_ref)
            child_ref = c.name
        except Exception:
            raise RpcError(
                ErrorCode.NOT_FOUND,
                f"no cell named {child_ref!r}",
                hint="call cell.list to see existing cells",
            )

    bbox = None
    if "bbox_dbu" in params or "bbox_um" in params:
        bbox = _box_from_um_or_dbu_local(params, ly.dbu)

    has_filter = (child_ref is not None) or (bbox is not None)
    if not has_filter and not bool(params.get("all", False)):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "no filter given",
            hint="pass child=..., bbox=..., or explicitly all=true to delete every instance in parent",
        )

    limit = int(params.get("limit", 10000))
    dry_run = bool(params.get("dry_run", False))

    # Collect first, erase after - mutating during each_inst is undefined.
    to_delete = []
    truncated = False
    try:
        for inst in parent.each_inst():
            if len(to_delete) >= limit:
                truncated = True
                break
            if not _inst_matches_child(inst, child_ref):
                continue
            if bbox is not None:
                try:
                    ibb = inst.bbox()
                    if ibb.empty() or not bbox.touches(ibb):
                        continue
                except Exception:
                    continue
            to_delete.append(inst)
    except Exception as e:
        raise RpcError(ErrorCode.INTERNAL, f"iteration failed: {e}")

    per_child: dict = {}
    for inst in to_delete:
        try:
            nm = inst.cell.name
        except Exception:
            nm = "<unknown>"
        per_child[nm] = per_child.get(nm, 0) + 1

    if not dry_run and to_delete:
        with auto_txn(view, f"klink: delete {len(to_delete)} instance(s) in {parent.name}"):
            for inst in to_delete:
                try:
                    parent.erase(inst)
                except Exception:
                    # Skip; rare but possible if the Instance handle
                    # got invalidated by a sibling erase.
                    pass

    # See shape.delete for `matched` vs `deleted` rationale.
    return {
        "parent": parent.name,
        "matched": len(to_delete),
        "deleted": 0 if dry_run else len(to_delete),
        "per_child": per_child,
        "truncated": bool(truncated),
        "dry_run": dry_run,
    }


def _box_from_um_or_dbu_local(params: dict, dbu: float) -> pya.Box:
    """Parse bbox_dbu or bbox_um into a pya.Box (ints in dbu)."""
    if "bbox_dbu" in params:
        b = params["bbox_dbu"]
        if not (isinstance(b, list) and len(b) == 4):
            raise RpcError(ErrorCode.BAD_PARAMS, "bbox_dbu must be [x1,y1,x2,y2]")
        return pya.Box(int(b[0]), int(b[1]), int(b[2]), int(b[3]))
    if "bbox_um" in params:
        b = params["bbox_um"]
        if not (isinstance(b, list) and len(b) == 4):
            raise RpcError(ErrorCode.BAD_PARAMS, "bbox_um must be [x1,y1,x2,y2]")
        return pya.Box(
            int(round(float(b[0]) / dbu)), int(round(float(b[1]) / dbu)),
            int(round(float(b[2]) / dbu)), int(round(float(b[3]) / dbu)),
        )
    raise RpcError(ErrorCode.BAD_PARAMS, "provide bbox_dbu or bbox_um")
