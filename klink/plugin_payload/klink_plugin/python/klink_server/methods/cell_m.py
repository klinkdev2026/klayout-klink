"""
Cell inspection methods.

Design notes
------------
* `cell.list` returns a flat, paginated catalogue. Meant for LLM agents
  that want to "see" what's in the file without pulling the full tree.
* `cell.tree` returns a hierarchical structure rooted at one top cell.
  It is bounded by `max_depth` and `max_nodes` so that a pathological
  layout (10^6 cells) cannot blow up the response.
* Cells are identified by name (stable across sessions) as the primary
  key, with `cell_index` added for performance-sensitive callers. LLMs
  should always use the name.

All handlers run on the Qt main thread; pya traversal is fast enough at
the scales M2 targets (<=100k cells) and we do not yield the event loop
here yet.
"""

from __future__ import annotations

from typing import Optional

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..txn import auto_txn


def _process_events_best_effort() -> None:
    try:
        pya.Application.instance().process_events()
    except Exception:
        pass


def _create_default_layout(mw, *, name: str = "TOP", dbu: float = 0.001):
    """Create a fresh KLayout tab with a valid top cell.

    KLayout can start with the plugin loaded but no layout tab open. Most
    klink methods operate on an active LayoutView, so we create the same
    default blank layout a user would create manually via File > New.
    """
    try:
        mw.create_layout(1)
    except Exception as e:
        raise RpcError(
            ErrorCode.INTERNAL,
            f"failed to create default layout: {e}",
            hint="try creating a new layout manually in KLayout",
        )

    view = mw.current_view()
    if view is None:
        raise RpcError(
            ErrorCode.NO_VIEW,
            "created default layout but no active layout view appeared",
        )

    cv = view.active_cellview()
    if cv is None or not cv.is_valid():
        raise RpcError(
            ErrorCode.NO_LAYOUT,
            "created default layout but active cellview is invalid",
        )

    ly = cv.layout()
    try:
        ly.dbu = float(dbu)
    except Exception:
        pass
    top = ly.cell(name) or ly.create_cell(name)
    try:
        cv.cell = top
    except Exception:
        try:
            cv.cell_name = top.name
        except Exception:
            pass
    _process_events_best_effort()
    return view, cv, ly


def _ensure_cellview_cell(view, cv):
    ly = cv.layout()
    if cv.cell is not None:
        return view, cv, ly
    try:
        tops = list(ly.top_cells())
    except Exception:
        tops = []
    top = tops[0] if tops else ly.create_cell("TOP")
    try:
        cv.cell = top
    except Exception:
        try:
            cv.cell_name = top.name
        except Exception:
            pass
    _process_events_best_effort()
    return view, cv, ly


def _active_layout():
    mw = pya.Application.instance().main_window()
    if mw is None:
        raise RpcError(ErrorCode.INTERNAL, "no main window")
    view = mw.current_view()
    if view is None:
        return _create_default_layout(mw)
    try:
        cv = view.active_cellview()
    except Exception:
        cv = None
    if cv is None or not cv.is_valid():
        return _create_default_layout(mw)
    try:
        return _ensure_cellview_cell(view, cv)
    except Exception:
        return _create_default_layout(mw)


def _resolve_cell(layout, cell_ref) -> pya.Cell:
    """Accept either a cell name (str) or an index (int)."""
    if isinstance(cell_ref, int):
        try:
            c = layout.cell(cell_ref)
        except Exception:
            c = None
        if c is None:
            raise RpcError(
                ErrorCode.NOT_FOUND, f"no cell with index {cell_ref}",
                hint="call cell.list to see available cells",
            )
        return c
    if isinstance(cell_ref, str):
        c = layout.cell(cell_ref)
        if c is None:
            raise RpcError(
                ErrorCode.NOT_FOUND, f"no cell named {cell_ref!r}",
                hint="call cell.list to see available cells",
            )
        return c
    raise RpcError(
        ErrorCode.BAD_PARAMS, "cell must be a name (string) or index (integer)",
    )


def _cell_summary(cell: pya.Cell, with_bbox: bool = False) -> dict:
    d = {
        "name": cell.name,
        "index": cell.cell_index(),
        "is_top": cell.is_top(),
        "is_leaf": cell.is_leaf(),
        "is_proxy": cell.is_proxy(),
    }
    # bbox() is cached; cheap for already-laid-out cells
    if with_bbox:
        try:
            bb = cell.bbox()
            if bb.empty():
                d["bbox_dbu"] = None
            else:
                d["bbox_dbu"] = [bb.left, bb.bottom, bb.right, bb.top]
        except Exception:
            d["bbox_dbu"] = None
    return d


@method(
    "cell.list",
    description=(
        "Flat, paginated list of cells in the active layout. Use this to "
        "discover what cells exist. For hierarchy use cell.tree instead. "
        "Filtering: 'name_prefix' is a case-sensitive prefix match; "
        "'top_only' restricts to top cells. Pagination: 'offset' + 'limit'."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "name_prefix": {"type": "string", "description": "Case-sensitive prefix filter on cell name"},
            "top_only": {"type": "boolean", "default": False},
            "with_bbox": {"type": "boolean", "default": False, "description": "Include bounding box in dbu"},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "total": {"type": "integer"},
            "offset": {"type": "integer"},
            "returned": {"type": "integer"},
            "cells": {"type": "array"},
        },
    },
    tags=["cell", "read"],
)
def cell_list(params, ctx):
    _, _, ly = _active_layout()
    prefix = params.get("name_prefix") or ""
    top_only = bool(params.get("top_only", False))
    with_bbox = bool(params.get("with_bbox", False))
    offset = int(params.get("offset", 0))
    limit = int(params.get("limit", 500))
    if limit < 1 or limit > 5000:
        raise RpcError(ErrorCode.BAD_PARAMS, "limit must be 1..5000")
    if offset < 0:
        raise RpcError(ErrorCode.BAD_PARAMS, "offset must be >= 0")

    if top_only:
        source = list(ly.top_cells())
    else:
        source = list(ly.each_cell())

    if prefix:
        source = [c for c in source if c.name.startswith(prefix)]

    total = len(source)
    page = source[offset: offset + limit]

    return {
        "total": total,
        "offset": offset,
        "returned": len(page),
        "cells": [_cell_summary(c, with_bbox=with_bbox) for c in page],
    }


@method(
    "cell.tree",
    description=(
        "Hierarchical cell tree rooted at a given cell (default: first "
        "top cell). Bounded by max_depth and max_nodes. Use this to "
        "understand how a layout is composed; the 'instances' count on "
        "each node tells you how many times its parent instances it."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "root": {
                "description": "Cell name (str) or cell_index (int). Default: first top cell.",
            },
            "max_depth": {"type": "integer", "minimum": 0, "default": 8},
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 2000},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "root": {"type": "string"},
            "truncated": {"type": "boolean"},
            "nodes": {"type": "integer"},
            "tree": {"type": "object"},
        },
    },
    tags=["cell", "read"],
)
def cell_tree(params, ctx):
    _, _, ly = _active_layout()

    root_ref = params.get("root")
    if root_ref is None:
        tops = list(ly.top_cells())
        if not tops:
            raise RpcError(ErrorCode.NO_LAYOUT, "layout has no top cell")
        root = tops[0]
    else:
        root = _resolve_cell(ly, root_ref)

    max_depth = int(params.get("max_depth", 8))
    max_nodes = int(params.get("max_nodes", 2000))
    if max_depth < 0:
        raise RpcError(ErrorCode.BAD_PARAMS, "max_depth must be >= 0")

    counter = {"n": 0, "truncated": False}

    def walk(cell: pya.Cell, depth: int, seen: set):
        if counter["n"] >= max_nodes:
            counter["truncated"] = True
            return None
        counter["n"] += 1
        node = {
            "name": cell.name,
            "index": cell.cell_index(),
            "instances": cell.child_instances(),
        }
        if depth >= max_depth:
            # record that we stopped expanding
            if cell.child_instances() > 0:
                node["more"] = True
            return node

        # Prevent infinite recursion in pathological (cyclic proxy?) layouts
        if cell.cell_index() in seen:
            node["cycle"] = True
            return node
        seen = seen | {cell.cell_index()}

        children = []
        for child_idx in cell.each_child_cell():
            if counter["n"] >= max_nodes:
                counter["truncated"] = True
                break
            ch = ly.cell(child_idx)
            if ch is None:
                continue
            sub = walk(ch, depth + 1, seen)
            if sub is not None:
                children.append(sub)
        if children:
            node["children"] = children
        return node

    tree = walk(root, 0, set())

    return {
        "root": root.name,
        "truncated": counter["truncated"],
        "nodes": counter["n"],
        "tree": tree,
    }


# ------------------------------------------------------------------
# M3 write operations
# ------------------------------------------------------------------


@method(
    "cell.create",
    description=(
        "Create a new cell in the active layout. If `name` is given and "
        "already taken, KLayout appends '$1', '$2', ... to keep names "
        "unique (the effective name is returned). If omitted, an "
        "anonymous auto-named cell ('$N') is created. Idempotent is NOT "
        "guaranteed - each call creates a fresh cell."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Desired cell name."},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell_index": {"type": "integer"},
            "name": {"type": "string"},
            "requested_name": {"type": ["string", "null"]},
            "renamed": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["cell", "write"],
)
def cell_create(params, ctx):
    view, _, ly = _active_layout()
    requested = params.get("name")
    if requested is not None and not isinstance(requested, str):
        raise RpcError(ErrorCode.BAD_PARAMS, "name must be a string if given")

    title = f"klink: create cell {requested!r}" if requested else "klink: create cell"
    with auto_txn(view, title):
        if requested:
            new_cell = ly.create_cell(requested)
        else:
            new_cell = ly.create_cell("CELL")

    effective = new_cell.name
    return {
        "cell_index": int(new_cell.cell_index()),
        "name": effective,
        "requested_name": requested,
        "renamed": bool(requested) and effective != requested,
    }


@method(
    "cell.delete",
    description=(
        "Delete a cell from the active layout. Use `recursive=true` to "
        "also delete child cells that become orphaned (no longer "
        "referenced by any other cell). Default is to delete only this "
        "cell; remaining instances are turned into 'ghost' references."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)."},
            "recursive": {
                "type": "boolean",
                "default": False,
                "description": "If true, also delete cells that become orphaned.",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "deleted_name": {"type": "string"},
            "deleted_index": {"type": "integer"},
            "recursive": {"type": "boolean"},
            "view_switched_from": {
                "type": ["integer", "null"],
                "description": "If non-null, the view was showing this cell_index and was moved away before deletion to prevent a KLayout crash.",
            },
        },
    },
    mutates=True,
    tags=["cell", "write"],
)
def cell_delete(params, ctx):
    view, _, ly = _active_layout()
    cell_ref = params.get("cell")
    if cell_ref is None:
        raise RpcError(ErrorCode.BAD_PARAMS, "cell is required")
    target = _resolve_cell(ly, cell_ref)

    # Capture identity before deletion (the Cell object becomes invalid after).
    name = target.name
    idx = int(target.cell_index())
    recursive = bool(params.get("recursive", False))

    # DEFENSIVE: if the active view is currently showing the cell we
    # are about to delete (or, for recursive deletes, any descendant
    # about to be swept away), switch the view to a safe replacement
    # FIRST. Deleting the currently-shown cell leaves KLayout's C++
    # CellView with a dangling pointer and the next redraw / delete
    # call segfaults the whole app. Discovered while running
    # 10_exec_demo.py twice in a row - see commit history.
    view_switched_from = None
    try:
        cv = view.active_cellview()
        if cv is not None and cv.is_valid():
            shown_idx = int(cv.cell_index)
            doomed: set = {idx}
            if recursive:
                # `called_cells` returns all cells reachable via
                # instances from `target` (direct + indirect), which
                # is a superset of what `delete_cell_rec` will nuke.
                # Being conservative is the safe failure mode here.
                try:
                    doomed |= set(int(x) for x in target.called_cells())
                except Exception:
                    pass
            if shown_idx in doomed:
                replacement = None
                for t in ly.top_cells():
                    tidx = int(t.cell_index())
                    if tidx not in doomed:
                        replacement = tidx
                        break
                view_switched_from = shown_idx
                try:
                    if replacement is not None:
                        cv.cell = ly.cell(replacement)
                    else:
                        # No safe top left - clear the shown cell.
                        # Setting cell to None is the documented way.
                        cv.cell = None
                except Exception:
                    pass
                # Let Qt process the view change before we mutate the
                # layout; KLayout caches pointers that need to catch up.
                try:
                    import pya as _pya
                    _pya.Application.instance().process_events()
                except Exception:
                    pass
    except Exception:
        pass

    with auto_txn(view, f"klink: delete cell {name!r}"):
        if recursive:
            ly.delete_cell_rec(idx)
        else:
            ly.delete_cell(idx)

    return {
        "deleted_name": name,
        "deleted_index": idx,
        "recursive": recursive,
        "view_switched_from": view_switched_from,
    }


@method(
    "cell.rename",
    description=(
        "Rename a cell. Fails if `new_name` is already taken (KLayout "
        "would otherwise silently append '$1'; we surface that as an "
        "error so callers can decide). Pass `allow_suffix=true` to opt "
        "into KLayout's auto-suffix behaviour."
    ),
    params_schema={
        "type": "object",
        "required": ["cell", "new_name"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)."},
            "new_name": {"type": "string"},
            "allow_suffix": {"type": "boolean", "default": False},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell_index": {"type": "integer"},
            "old_name": {"type": "string"},
            "new_name": {"type": "string"},
            "renamed": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["cell", "write"],
)
def cell_rename(params, ctx):
    view, _, ly = _active_layout()
    cell_ref = params.get("cell")
    new_name = params.get("new_name")
    if cell_ref is None or not isinstance(new_name, str) or not new_name:
        raise RpcError(ErrorCode.BAD_PARAMS, "cell and non-empty new_name are required")

    target = _resolve_cell(ly, cell_ref)
    old_name = target.name
    if old_name == new_name:
        return {
            "cell_index": int(target.cell_index()),
            "old_name": old_name,
            "new_name": new_name,
            "renamed": False,
        }

    allow_suffix = bool(params.get("allow_suffix", False))
    if not allow_suffix:
        clash = ly.cell(new_name)
        if clash is not None and clash.cell_index() != target.cell_index():
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                f"cell named {new_name!r} already exists",
                hint="pass allow_suffix=true to accept KLayout's '$N' suffix",
            )

    with auto_txn(view, f"klink: rename cell {old_name!r} -> {new_name!r}"):
        target.name = new_name

    effective = target.name
    return {
        "cell_index": int(target.cell_index()),
        "old_name": old_name,
        "new_name": effective,
        "renamed": True,
    }
