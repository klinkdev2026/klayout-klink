"""
Shape query methods.

`shape.query` reads geometry from one cell (one level, no recursion) and
returns it in JSON form. Designed to be safe to call from an LLM:

* Always paginated - a huge layer does not blow up a single response.
* Always filtered - callers must specify a cell and (optionally) layers
  and a bounding box.
* Coordinates are returned in integer database units (dbu). Client can
  convert to microns via `x * dbu` (dbu is in layout.info).
* Unknown shape kinds are skipped, not errored: callers get a stable
  subset ({box, polygon, path, text}).

Stable-shape-id is NOT exposed in M2 (KLayout's pya does not provide a
cross-session shape id by default). Shapes are referenced for
subsequent operations via their ordinal index within the returned page.
"""

from __future__ import annotations

from typing import List, Optional

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..txn import auto_txn
from .cell_m import _active_layout, _resolve_cell


_SHAPE_KINDS = ("polygons", "boxes", "paths", "texts")


def _resolve_layers(layout: pya.Layout, spec) -> Optional[List[int]]:
    """
    Resolve a 'layers' parameter into a list of layer indexes.
    Accepted input:
      - None / missing  -> None (means "all layers")
      - list of int     -> layer indexes as-is (must be valid)
      - list of objects -> [{"layer": int, "datatype": int}, ...]
      - list of strings -> "L/D" form, e.g. "1/0"
    """
    if spec is None:
        return None
    if not isinstance(spec, list):
        raise RpcError(ErrorCode.BAD_PARAMS, "'layers' must be a list")

    out: List[int] = []
    for item in spec:
        if isinstance(item, int):
            out.append(item)
            continue
        if isinstance(item, str):
            if "/" not in item:
                raise RpcError(
                    ErrorCode.BAD_PARAMS,
                    f"layer string {item!r} must be 'L/D' (e.g. '1/0')",
                )
            l_s, d_s = item.split("/", 1)
            try:
                li = pya.LayerInfo(int(l_s), int(d_s))
            except Exception:
                raise RpcError(ErrorCode.BAD_PARAMS, f"cannot parse {item!r}")
            idx = layout.find_layer(li)
            if idx is None:
                raise RpcError(
                    ErrorCode.NOT_FOUND, f"layer {item} not present in layout",
                )
            out.append(idx)
            continue
        if isinstance(item, dict):
            if "layer" not in item:
                raise RpcError(
                    ErrorCode.BAD_PARAMS, "layer spec dict needs 'layer'",
                )
            li = pya.LayerInfo(int(item["layer"]), int(item.get("datatype", 0)))
            idx = layout.find_layer(li)
            if idx is None:
                raise RpcError(
                    ErrorCode.NOT_FOUND,
                    f"layer {item['layer']}/{item.get('datatype', 0)} not present in layout",
                )
            out.append(idx)
            continue
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"layer entry must be int, 'L/D' string or object, got {type(item).__name__}",
        )
    return out


def _box_from_param(bbox) -> Optional[pya.Box]:
    if bbox is None:
        return None
    if not (isinstance(bbox, list) and len(bbox) == 4):
        raise RpcError(ErrorCode.BAD_PARAMS, "bbox_dbu must be [x1, y1, x2, y2]")
    x1, y1, x2, y2 = (int(v) for v in bbox)
    return pya.Box(x1, y1, x2, y2)


def _shape_to_dict(shape: pya.Shape, layer_idx: int, kinds: set) -> Optional[dict]:
    """Convert one pya.Shape into a JSON-friendly dict. Returns None if
    the shape does not match the requested kinds or is an unsupported
    kind (edges, points, user objects)."""
    if shape.is_box() and "boxes" in kinds:
        b = shape.box
        return {
            "type": "box",
            "layer_index": layer_idx,
            "bbox_dbu": [b.left, b.bottom, b.right, b.top],
        }
    if shape.is_polygon() and "polygons" in kinds:
        p = shape.polygon
        bb = p.bbox()
        d = {
            "type": "polygon",
            "layer_index": layer_idx,
            "bbox_dbu": [bb.left, bb.bottom, bb.right, bb.top],
            "points_dbu": [[pt.x, pt.y] for pt in p.each_point_hull()],
        }
        if p.holes() > 0:
            d["holes_dbu"] = [
                [[pt.x, pt.y] for pt in p.each_point_hole(i)]
                for i in range(p.holes())
            ]
        return d
    if shape.is_simple_polygon() and "polygons" in kinds:
        p = shape.simple_polygon
        bb = p.bbox()
        return {
            "type": "polygon",
            "layer_index": layer_idx,
            "bbox_dbu": [bb.left, bb.bottom, bb.right, bb.top],
            "points_dbu": [[pt.x, pt.y] for pt in p.each_point()],
        }
    if shape.is_path() and "paths" in kinds:
        p = shape.path
        bb = p.bbox()
        return {
            "type": "path",
            "layer_index": layer_idx,
            "bbox_dbu": [bb.left, bb.bottom, bb.right, bb.top],
            "points_dbu": [[pt.x, pt.y] for pt in p.each_point()],
            "width_dbu": p.width,
        }
    if shape.is_text() and "texts" in kinds:
        t = shape.text
        return {
            "type": "text",
            "layer_index": layer_idx,
            "bbox_dbu": [t.x, t.y, t.x, t.y],
            "string": t.string,
            "position_dbu": [t.x, t.y],
        }
    return None


@method(
    "shape.query",
    description=(
        "Read shapes from ONE cell (no recursion) as JSON. Strongly "
        "recommended: narrow with 'layers' and 'bbox_dbu', and respect "
        "pagination via 'limit' (default 500, max 5000). Coordinates "
        "are in database units (dbu); multiply by layout dbu (from "
        "layout.info) to get microns. The 'truncated' flag means more "
        "shapes matched than 'limit' - call again with a tighter bbox."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            "layers": {
                "type": "array",
                "description": "Layer indexes, 'L/D' strings or {layer, datatype} objects. None = all layers.",
            },
            "bbox_dbu": {
                "type": "array",
                "minItems": 4, "maxItems": 4,
                "description": "[x1, y1, x2, y2] in dbu. Shapes overlapping this region are returned.",
            },
            "kinds": {
                "type": "array",
                "items": {"enum": list(_SHAPE_KINDS)},
                "description": "Subset of shape kinds to return. Default: all.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "returned": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "shapes": {"type": "array"},
        },
    },
    tags=["shape", "read"],
)
def shape_query(params, ctx):
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")

    _, _, ly = _active_layout()
    cell = _resolve_cell(ly, params["cell"])

    layer_idxs = _resolve_layers(ly, params.get("layers"))
    if layer_idxs is None:
        layer_idxs = list(ly.layer_indexes())

    bbox = _box_from_param(params.get("bbox_dbu"))
    kinds = set(params.get("kinds") or _SHAPE_KINDS)
    unknown = kinds - set(_SHAPE_KINDS)
    if unknown:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"unknown kinds: {sorted(unknown)}",
            hint=f"allowed: {_SHAPE_KINDS}",
        )
    limit = int(params.get("limit", 500))
    if limit < 1 or limit > 5000:
        raise RpcError(ErrorCode.BAD_PARAMS, "limit must be 1..5000")

    out_shapes: list = []
    truncated = False

    for li in layer_idxs:
        if truncated:
            break
        shapes = cell.shapes(li)
        if bbox is not None:
            try:
                it = shapes.each_touching(bbox)
            except Exception:
                it = shapes.each()
        else:
            it = shapes.each()

        for s in it:
            if len(out_shapes) >= limit:
                truncated = True
                break
            d = _shape_to_dict(s, li, kinds)
            if d is not None:
                out_shapes.append(d)

    return {
        "cell": cell.name,
        "returned": len(out_shapes),
        "truncated": truncated,
        "shapes": out_shapes,
    }


# ------------------------------------------------------------------
# M3 write operations: shape.insert_*
# ------------------------------------------------------------------


def _resolve_target_layer_idx(layout: pya.Layout, params: dict) -> int:
    """Resolve the target layer for a write RPC.

    Accepts (in precedence order):
      - layer_index: int (direct handle, must be valid)
      - layer: int, datatype: int = 0 (must already exist; use
        layer.ensure first to create on demand)
    Intentionally does NOT implicitly create layers here: keeps
    shape.insert_* pure and makes the layer creation step visible in
    the event stream / undo history.
    """
    if "layer_index" in params:
        idx = int(params["layer_index"])
        if not layout.is_valid_layer(idx):
            raise RpcError(
                ErrorCode.NOT_FOUND,
                f"no layer with index {idx}",
                hint="call layer.list or layer.ensure to get a valid layer_index",
            )
        return idx
    if "layer" in params:
        li = pya.LayerInfo(int(params["layer"]), int(params.get("datatype", 0)))
        found = layout.find_layer(li)
        if found is None:
            raise RpcError(
                ErrorCode.NOT_FOUND,
                f"layer {li.layer}/{li.datatype} not present",
                hint="call layer.ensure to create it, then pass its layer_index",
            )
        return int(found)
    raise RpcError(
        ErrorCode.BAD_PARAMS,
        "specify layer_index OR layer(+datatype)",
    )


def _um_to_dbu(v: float, dbu: float) -> int:
    return int(round(float(v) / dbu))


def _box_from_um_or_dbu(params: dict, dbu: float) -> pya.Box:
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
            _um_to_dbu(b[0], dbu), _um_to_dbu(b[1], dbu),
            _um_to_dbu(b[2], dbu), _um_to_dbu(b[3], dbu),
        )
    raise RpcError(ErrorCode.BAD_PARAMS, "provide bbox_dbu or bbox_um")


def _points_from_um_or_dbu(params: dict, dbu: float,
                           key_dbu: str = "points_dbu",
                           key_um: str = "points_um",
                           min_points: int = 2) -> list:
    if key_dbu in params:
        pts = params[key_dbu]
        if not isinstance(pts, list) or len(pts) < min_points:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           f"{key_dbu} must be a list of >= {min_points} [x,y]")
        return [pya.Point(int(p[0]), int(p[1])) for p in pts]
    if key_um in params:
        pts = params[key_um]
        if not isinstance(pts, list) or len(pts) < min_points:
            raise RpcError(ErrorCode.BAD_PARAMS,
                           f"{key_um} must be a list of >= {min_points} [x,y]")
        return [pya.Point(_um_to_dbu(p[0], dbu), _um_to_dbu(p[1], dbu)) for p in pts]
    raise RpcError(ErrorCode.BAD_PARAMS, f"provide {key_dbu} or {key_um}")


def _point_from_um_or_dbu(params: dict, dbu: float,
                          key_dbu: str = "position_dbu",
                          key_um: str = "position_um") -> tuple:
    if key_dbu in params:
        p = params[key_dbu]
        if not (isinstance(p, list) and len(p) == 2):
            raise RpcError(ErrorCode.BAD_PARAMS, f"{key_dbu} must be [x,y]")
        return int(p[0]), int(p[1])
    if key_um in params:
        p = params[key_um]
        if not (isinstance(p, list) and len(p) == 2):
            raise RpcError(ErrorCode.BAD_PARAMS, f"{key_um} must be [x,y]")
        return _um_to_dbu(p[0], dbu), _um_to_dbu(p[1], dbu)
    raise RpcError(ErrorCode.BAD_PARAMS, f"provide {key_dbu} or {key_um}")


def _scalar_from_um_or_dbu(params: dict, dbu: float,
                           key_dbu: str, key_um: str) -> int:
    if key_dbu in params:
        return int(params[key_dbu])
    if key_um in params:
        return _um_to_dbu(params[key_um], dbu)
    raise RpcError(ErrorCode.BAD_PARAMS, f"provide {key_dbu} or {key_um}")


def _shape_result(cell: pya.Cell, layer_idx: int, kind: str, bbox: pya.Box,
                  extra: Optional[dict] = None) -> dict:
    d = {
        "cell": cell.name,
        "cell_index": int(cell.cell_index()),
        "layer_index": int(layer_idx),
        "kind": kind,
        "bbox_dbu": [bbox.left, bbox.bottom, bbox.right, bbox.top],
    }
    if extra:
        d.update(extra)
    return d


_LAYER_PARAM_SCHEMA = {
    "layer_index": {"type": "integer", "description": "Runtime layer handle from layer.list/layer.ensure (preferred)."},
    "layer": {"type": "integer", "description": "GDS layer number (alternative, must already exist)."},
    "datatype": {"type": "integer", "description": "GDS datatype when `layer` is given. Default 0."},
}


_MAX_BATCH_SHAPES = 100_000


def _validate_batch_size(values, name: str) -> list:
    if not isinstance(values, list):
        raise RpcError(ErrorCode.BAD_PARAMS, f"{name} must be a list")
    if not values:
        raise RpcError(ErrorCode.BAD_PARAMS, f"{name} must not be empty")
    if len(values) > _MAX_BATCH_SHAPES:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"{name} is too large ({len(values)} > {_MAX_BATCH_SHAPES})",
        )
    return values


def _boxes_from_um_or_dbu(params: dict, dbu: float) -> list:
    has_dbu = "boxes_dbu" in params
    has_um = "boxes_um" in params
    if has_dbu == has_um:
        raise RpcError(ErrorCode.BAD_PARAMS, "provide exactly one of boxes_dbu or boxes_um")

    key = "boxes_dbu" if has_dbu else "boxes_um"
    items = _validate_batch_size(params[key], key)
    boxes = []
    for i, bbox in enumerate(items):
        try:
            boxes.append(_box_from_um_or_dbu({"bbox_dbu" if has_dbu else "bbox_um": bbox}, dbu))
        except RpcError as exc:
            raise RpcError(exc.code, f"{key}[{i}]: {exc.message}", hint=exc.hint)
    return boxes


def _bbox_union(boxes: list) -> pya.Box:
    if not boxes:
        return pya.Box(0, 0, 0, 0)
    left = min(b.left for b in boxes)
    bottom = min(b.bottom for b in boxes)
    right = max(b.right for b in boxes)
    top = max(b.top for b in boxes)
    return pya.Box(left, bottom, right, top)


def _layer_key(layout: pya.Layout, layer_idx: int) -> str:
    try:
        info = layout.get_info(layer_idx)
        return f"{info.layer}/{info.datatype}"
    except Exception:
        return f"idx_{layer_idx}"


def _shape_op_from_item(layout: pya.Layout, item: dict) -> tuple:
    if not isinstance(item, dict):
        raise RpcError(ErrorCode.BAD_PARAMS, "each item must be an object")

    kind = str(item.get("kind", item.get("type", ""))).lower()
    layer_idx = _resolve_target_layer_idx(layout, item)
    dbu = layout.dbu

    if kind == "box":
        shape = _box_from_um_or_dbu(item, dbu)
        return kind, layer_idx, shape, shape

    if kind == "polygon":
        pts = _points_from_um_or_dbu(item, dbu, min_points=3)
        shape = pya.Polygon(pts)
        return kind, layer_idx, shape, shape.bbox()

    if kind == "path":
        pts = _points_from_um_or_dbu(item, dbu, min_points=2)
        width_dbu = _scalar_from_um_or_dbu(item, dbu, "width_dbu", "width_um")
        half = width_dbu // 2
        bext = half
        eext = half
        if "begin_ext_um" in item or "begin_ext_dbu" in item:
            bext = _scalar_from_um_or_dbu(item, dbu, "begin_ext_dbu", "begin_ext_um")
        if "end_ext_um" in item or "end_ext_dbu" in item:
            eext = _scalar_from_um_or_dbu(item, dbu, "end_ext_dbu", "end_ext_um")
        shape = pya.Path(pts, width_dbu, bext, eext, bool(item.get("round_ends", False)))
        return kind, layer_idx, shape, shape.bbox()

    if kind == "text":
        string = item.get("string", item.get("text"))
        if not isinstance(string, str):
            raise RpcError(ErrorCode.BAD_PARAMS, "text item requires string")
        x, y = _point_from_um_or_dbu(item, dbu)
        shape = pya.Text(string, pya.Trans(x, y))
        if "size_um" in item or "size_dbu" in item:
            try:
                shape.size = _scalar_from_um_or_dbu(item, dbu, "size_dbu", "size_um")
            except Exception:
                pass
        return kind, layer_idx, shape, pya.Box(x, y, x, y)

    raise RpcError(
        ErrorCode.BAD_PARAMS,
        f"unknown item kind {kind!r}",
        hint="allowed kinds: box, polygon, path, text",
    )


@method(
    "shape.insert_box",
    description=(
        "Insert an axis-aligned rectangle into `cell` on the given layer. "
        "Provide the box as `bbox_um=[x1,y1,x2,y2]` (microns, most "
        "natural) or `bbox_dbu` (integer database units). The edit is "
        "wrapped in a single-step transaction so Ctrl+Z undoes it."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            **_LAYER_PARAM_SCHEMA,
            "bbox_um": {"type": "array", "minItems": 4, "maxItems": 4},
            "bbox_dbu": {"type": "array", "minItems": 4, "maxItems": 4},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "layer_index": {"type": "integer"},
            "kind": {"type": "string"},
            "bbox_dbu": {"type": "array"},
        },
    },
    mutates=True,
    tags=["shape", "write"],
)
def shape_insert_box(params, ctx):
    view, _, ly = _active_layout()
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")
    cell = _resolve_cell(ly, params["cell"])
    layer_idx = _resolve_target_layer_idx(ly, params)
    box = _box_from_um_or_dbu(params, ly.dbu)

    with auto_txn(view, f"klink: insert box on {cell.name}"):
        cell.shapes(layer_idx).insert(box)

    return _shape_result(cell, layer_idx, "box", box)


@method(
    "shape.insert_boxes",
    description=(
        "Insert many axis-aligned rectangles into one cell/layer in a "
        "single RPC and one undo transaction. Provide `boxes_um` or "
        "`boxes_dbu` as a list of [x1,y1,x2,y2] boxes. Use this instead "
        "of many `shape.insert_box` calls for large generated layouts."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            **_LAYER_PARAM_SCHEMA,
            "boxes_um": {"type": "array", "items": {"type": "array", "minItems": 4, "maxItems": 4}},
            "boxes_dbu": {"type": "array", "items": {"type": "array", "minItems": 4, "maxItems": 4}},
            "dry_run": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "layer_index": {"type": "integer"},
            "kind": {"type": "string"},
            "requested": {"type": "integer"},
            "inserted": {"type": "integer"},
            "bbox_dbu": {"type": "array"},
            "dry_run": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["shape", "write", "batch"],
)
def shape_insert_boxes(params, ctx):
    view, _, ly = _active_layout()
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")
    cell = _resolve_cell(ly, params["cell"])
    layer_idx = _resolve_target_layer_idx(ly, params)
    boxes = _boxes_from_um_or_dbu(params, ly.dbu)
    dry_run = bool(params.get("dry_run", False))
    bbox = _bbox_union(boxes)

    if not dry_run:
        with auto_txn(view, f"klink: insert {len(boxes)} boxes on {cell.name}"):
            shapes = cell.shapes(layer_idx)
            for box in boxes:
                shapes.insert(box)

    return _shape_result(cell, layer_idx, "boxes", bbox, {
        "requested": len(boxes),
        "inserted": 0 if dry_run else len(boxes),
        "dry_run": dry_run,
    })


@method(
    "shape.insert_many",
    description=(
        "Insert a mixed list of shapes into one cell in a single RPC and "
        "one undo transaction. Each item has kind/type = box, polygon, "
        "path, or text, its own layer selector, and the same geometry "
        "fields used by the corresponding single-shape RPC."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "items"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            "items": {"type": "array", "items": {"type": "object"}},
            "dry_run": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "requested": {"type": "integer"},
            "inserted": {"type": "integer"},
            "bbox_dbu": {"type": "array"},
            "by_kind": {"type": "object"},
            "by_layer": {"type": "object"},
            "dry_run": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["shape", "write", "batch"],
)
def shape_insert_many(params, ctx):
    view, _, ly = _active_layout()
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")
    cell = _resolve_cell(ly, params["cell"])
    items = _validate_batch_size(params.get("items"), "items")
    dry_run = bool(params.get("dry_run", False))

    ops = []
    bboxes = []
    by_kind: dict = {}
    by_layer: dict = {}
    for i, item in enumerate(items):
        try:
            kind, layer_idx, shape, bbox = _shape_op_from_item(ly, item)
        except RpcError as exc:
            raise RpcError(exc.code, f"items[{i}]: {exc.message}", hint=exc.hint)
        ops.append((kind, layer_idx, shape))
        bboxes.append(bbox)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        layer_key = _layer_key(ly, layer_idx)
        by_layer[layer_key] = by_layer.get(layer_key, 0) + 1

    bbox = _bbox_union(bboxes)
    if not dry_run:
        with auto_txn(view, f"klink: insert {len(ops)} mixed shapes on {cell.name}"):
            for _, layer_idx, shape in ops:
                cell.shapes(layer_idx).insert(shape)

    return {
        "cell": cell.name,
        "cell_index": int(cell.cell_index()),
        "requested": len(ops),
        "inserted": 0 if dry_run else len(ops),
        "bbox_dbu": [bbox.left, bbox.bottom, bbox.right, bbox.top],
        "by_kind": by_kind,
        "by_layer": by_layer,
        "dry_run": dry_run,
    }


@method(
    "shape.insert_polygon",
    description=(
        "Insert a polygon (hull only; no holes yet) into `cell`. Points "
        "are given as `points_um=[[x,y],...]` (microns) or `points_dbu` "
        "(dbu). At least 3 points required. The polygon is closed "
        "automatically."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            **_LAYER_PARAM_SCHEMA,
            "points_um": {"type": "array"},
            "points_dbu": {"type": "array"},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "layer_index": {"type": "integer"},
            "kind": {"type": "string"},
            "bbox_dbu": {"type": "array"},
            "point_count": {"type": "integer"},
        },
    },
    mutates=True,
    tags=["shape", "write"],
)
def shape_insert_polygon(params, ctx):
    view, _, ly = _active_layout()
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")
    cell = _resolve_cell(ly, params["cell"])
    layer_idx = _resolve_target_layer_idx(ly, params)
    pts = _points_from_um_or_dbu(params, ly.dbu, min_points=3)

    poly = pya.Polygon(pts)
    with auto_txn(view, f"klink: insert polygon on {cell.name}"):
        cell.shapes(layer_idx).insert(poly)

    bb = poly.bbox()
    return _shape_result(cell, layer_idx, "polygon", bb, {"point_count": len(pts)})


@method(
    "shape.insert_path",
    description=(
        "Insert a path (center line with width) into `cell`. Points via "
        "`points_um`/`points_dbu`, width via `width_um`/`width_dbu`. "
        "Optional `begin_ext`/`end_ext` extensions (defaults to "
        "width/2 - flush) and `round_ends` for rounded caps."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            **_LAYER_PARAM_SCHEMA,
            "points_um": {"type": "array"},
            "points_dbu": {"type": "array"},
            "width_um": {"type": "number"},
            "width_dbu": {"type": "integer"},
            "begin_ext_um": {"type": "number"},
            "begin_ext_dbu": {"type": "integer"},
            "end_ext_um": {"type": "number"},
            "end_ext_dbu": {"type": "integer"},
            "round_ends": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "layer_index": {"type": "integer"},
            "kind": {"type": "string"},
            "bbox_dbu": {"type": "array"},
            "width_dbu": {"type": "integer"},
            "point_count": {"type": "integer"},
        },
    },
    mutates=True,
    tags=["shape", "write"],
)
def shape_insert_path(params, ctx):
    view, _, ly = _active_layout()
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")
    cell = _resolve_cell(ly, params["cell"])
    layer_idx = _resolve_target_layer_idx(ly, params)
    pts = _points_from_um_or_dbu(params, ly.dbu, min_points=2)

    width_dbu = _scalar_from_um_or_dbu(params, ly.dbu, "width_dbu", "width_um")

    half = width_dbu // 2
    bext = half
    eext = half
    if "begin_ext_um" in params or "begin_ext_dbu" in params:
        bext = _scalar_from_um_or_dbu(params, ly.dbu, "begin_ext_dbu", "begin_ext_um")
    if "end_ext_um" in params or "end_ext_dbu" in params:
        eext = _scalar_from_um_or_dbu(params, ly.dbu, "end_ext_dbu", "end_ext_um")

    round_ends = bool(params.get("round_ends", False))
    path = pya.Path(pts, width_dbu, bext, eext, round_ends)

    with auto_txn(view, f"klink: insert path on {cell.name}"):
        cell.shapes(layer_idx).insert(path)

    bb = path.bbox()
    return _shape_result(cell, layer_idx, "path", bb, {
        "width_dbu": int(width_dbu),
        "point_count": len(pts),
    })


@method(
    "shape.insert_text",
    description=(
        "Insert a text label into `cell`. Position via "
        "`position_um`/`position_dbu`. Optional `size_um` maps to "
        "KLayout text size (pya.Text.size). Labels are non-geometric "
        "annotations - they snap to integer coordinates but do not "
        "produce mask geometry."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "string"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            **_LAYER_PARAM_SCHEMA,
            "string": {"type": "string"},
            "position_um": {"type": "array", "minItems": 2, "maxItems": 2},
            "position_dbu": {"type": "array", "minItems": 2, "maxItems": 2},
            "size_um": {"type": "number"},
            "size_dbu": {"type": "integer"},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "layer_index": {"type": "integer"},
            "kind": {"type": "string"},
            "bbox_dbu": {"type": "array"},
            "position_dbu": {"type": "array"},
            "string": {"type": "string"},
        },
    },
    mutates=True,
    tags=["shape", "write"],
)
def shape_insert_text(params, ctx):
    view, _, ly = _active_layout()
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")
    if not isinstance(params.get("string"), str):
        raise RpcError(ErrorCode.BAD_PARAMS, "'string' must be a string")
    cell = _resolve_cell(ly, params["cell"])
    layer_idx = _resolve_target_layer_idx(ly, params)
    x, y = _point_from_um_or_dbu(params, ly.dbu)

    text = pya.Text(params["string"], pya.Trans(x, y))
    if "size_um" in params or "size_dbu" in params:
        try:
            size_dbu = _scalar_from_um_or_dbu(params, ly.dbu, "size_dbu", "size_um")
            text.size = size_dbu
        except Exception:
            pass

    with auto_txn(view, f"klink: insert text on {cell.name}"):
        cell.shapes(layer_idx).insert(text)

    bb = pya.Box(x, y, x, y)
    return _shape_result(cell, layer_idx, "text", bb, {
        "position_dbu": [x, y],
        "string": params["string"],
    })


# ----------------------------------------------------------------------
# shape.delete  (M3 Round 5)
# ----------------------------------------------------------------------
#
# Addressing philosophy
# ---------------------
# pya does not expose a stable, cross-session shape id, and the
# ordinal index within a Shapes container shifts as soon as anything
# is inserted or deleted. We therefore use a declarative SELECTOR:
# caller describes WHICH shapes to kill (by cell, layer(s), bbox
# overlap, kind) and the server enumerates + erases them atomically
# inside one transaction. This matches the mental model LLMs use
# ("delete all boxes on layer 101/0 inside this region") and is
# trivially re-run-safe.
#
# Safety rails
# ------------
# * If no layer selector is given, the caller must set `all_layers=true`
#   explicitly. Otherwise the request is rejected.
# * `dry_run=true` runs the whole selector but deletes nothing; the
#   response reports what *would* have been removed.
# * `limit` caps how many shapes we'll delete in one call (default
#   10_000). Exceeding the limit is reported as `truncated=true`.

def _collect_matching_shapes(cell: pya.Cell,
                             layer_idxs,
                             bbox: Optional[pya.Box],
                             kinds: set,
                             limit: int):
    """Return (shape_refs, truncated). shape_refs is a list of
    (layer_idx, shape) tuples suitable for bulk erase."""
    refs = []
    truncated = False
    for li in layer_idxs:
        if truncated:
            break
        shapes = cell.shapes(li)
        try:
            it = shapes.each_touching(bbox) if bbox is not None else shapes.each()
        except Exception:
            it = shapes.each()
        for s in it:
            if len(refs) >= limit:
                truncated = True
                break
            kind_ok = (
                (s.is_box() and "boxes" in kinds) or
                (s.is_polygon() and "polygons" in kinds) or
                (s.is_simple_polygon() and "polygons" in kinds) or
                (s.is_path() and "paths" in kinds) or
                (s.is_text() and "texts" in kinds)
            )
            if kind_ok:
                refs.append((li, s))
    return refs, truncated


@method(
    "shape.delete",
    description=(
        "Delete shapes from ONE cell that match a declarative selector. "
        "Select by layer (layer_index / layer+datatype / layers=[...] / "
        "all_layers=true), optional bbox_dbu|bbox_um (touching), and "
        "optional kinds=['boxes','polygons','paths','texts']. Use "
        "`dry_run=true` to preview the count before actually deleting. "
        "The whole erase runs inside a single transaction so Ctrl+Z / "
        "edit.undo rolls it all back at once. Returns `{deleted, "
        "per_layer, truncated}`. If nothing matches, `deleted=0` and "
        "the call succeeds without error."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)"},
            "layer_index": {"type": "integer"},
            "layer": {"type": "integer"},
            "datatype": {"type": "integer"},
            "layers": {
                "type": "array",
                "description": "Multi-layer form (same entries shape.query accepts).",
            },
            "all_layers": {
                "type": "boolean",
                "default": False,
                "description": "Must be true if NO layer selector is given - guards against accidental nukes.",
            },
            "bbox_dbu": {"type": "array", "minItems": 4, "maxItems": 4},
            "bbox_um":  {"type": "array", "minItems": 4, "maxItems": 4},
            "kinds": {
                "type": "array",
                "items": {"enum": list(_SHAPE_KINDS)},
                "description": "Kind filter; defaults to all shape kinds.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 10000},
            "dry_run": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "matched": {"type": "integer"},
            "deleted": {"type": "integer"},
            "per_layer": {"type": "object"},
            "truncated": {"type": "boolean"},
            "dry_run":   {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["shape", "write", "delete"],
)
def shape_delete(params, ctx):
    view, _, ly = _active_layout()
    if "cell" not in params:
        raise RpcError(ErrorCode.BAD_PARAMS, "'cell' is required")
    cell = _resolve_cell(ly, params["cell"])

    # Resolve layer selector.
    has_specific_layer = (
        "layer_index" in params or "layer" in params or "layers" in params
    )
    if has_specific_layer:
        if "layers" in params:
            layer_idxs = _resolve_layers(ly, params["layers"])
        else:
            layer_idxs = [_resolve_target_layer_idx(ly, params)]
    else:
        if not bool(params.get("all_layers", False)):
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "no layer selector given",
                hint="pass layer_index, layer(+datatype), layers=[...] OR "
                     "explicitly all_layers=true to target every layer",
            )
        layer_idxs = list(ly.layer_indexes())

    # Resolve optional bbox.
    bbox = None
    if "bbox_dbu" in params or "bbox_um" in params:
        try:
            bbox = _box_from_um_or_dbu(params, ly.dbu)
        except RpcError:
            raise

    kinds = set(params.get("kinds") or _SHAPE_KINDS)
    unknown = kinds - set(_SHAPE_KINDS)
    if unknown:
        raise RpcError(
            ErrorCode.BAD_PARAMS, f"unknown kinds: {sorted(unknown)}",
            hint=f"allowed: {_SHAPE_KINDS}",
        )
    limit = int(params.get("limit", 10000))
    dry_run = bool(params.get("dry_run", False))

    refs, truncated = _collect_matching_shapes(
        cell, layer_idxs, bbox, kinds, limit,
    )

    per_layer: dict = {}
    for li, _ in refs:
        try:
            info = ly.get_info(li)
            key = f"{info.layer}/{info.datatype}"
        except Exception:
            key = f"idx_{li}"
        per_layer[key] = per_layer.get(key, 0) + 1

    if not dry_run and refs:
        with auto_txn(view, f"klink: delete {len(refs)} shape(s) in {cell.name}"):
            for li, s in refs:
                try:
                    cell.shapes(li).erase(s)
                except Exception:
                    # Skip silently; mid-iteration invalidation is rare
                    # with our pre-collect approach but not impossible.
                    pass

    # `matched` is always the selector hit count; `deleted` is 0 in
    # dry_run (nothing actually went away). Callers that just want to
    # count matches should read `matched` - this lets the same RPC
    # double as "how many would shape.delete remove?" without a quirky
    # special case.
    return {
        "cell": cell.name,
        "matched": len(refs),
        "deleted": 0 if dry_run else len(refs),
        "per_layer": per_layer,
        "truncated": bool(truncated),
        "dry_run": dry_run,
    }
