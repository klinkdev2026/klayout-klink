"""
Layout / view inspection methods.

M1 ships a single read-only method, `layout.info`, as the end-to-end
smoke test for the protocol. More read methods (cell.list, shape.query,
selection.get, ...) arrive in M2.
"""

from __future__ import annotations

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode


def _mw():
    return pya.Application.instance().main_window()


@method(
    "layout.info",
    description=(
        "Snapshot of the currently active layout view: number of open "
        "views, active cellview index, top cell name, source file path, "
        "database unit, the full top-cell list and the registered "
        "layer/datatype pairs. Safe to call often - this is the method "
        "an LLM agent should use to refresh its world view."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "verbosity": {
                "type": "string",
                "enum": ["summary", "normal", "full"],
                "default": "normal",
                "description": "'summary' omits layer list; 'full' adds hierarchy counts",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "has_view": {"type": "boolean"},
            "views": {"type": "integer"},
            "active_cellview": {"type": "integer"},
            "cell": {"type": ["string", "null"]},
            "cell_index": {"type": ["integer", "null"]},
            "file": {"type": ["string", "null"]},
            "dbu": {"type": ["number", "null"]},
            "top_cells": {"type": "array", "items": {"type": "string"}},
            "layers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "layer": {"type": "integer"},
                        "datatype": {"type": "integer"},
                        "name": {"type": ["string", "null"]},
                    },
                },
            },
            "auto_created_layout": {
                "type": "boolean",
                "description": "True when layout.info created a default blank TOP layout because none was open.",
            },
        },
    },
    tags=["layout", "read"],
)
def layout_info(params, ctx):
    verbosity = params.get("verbosity", "normal")
    if verbosity not in ("summary", "normal", "full"):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"unknown verbosity: {verbosity!r}",
            hint="choose one of: summary, normal, full",
        )

    mw = _mw()
    if mw is None:
        raise RpcError(
            ErrorCode.INTERNAL,
            "no main window",
            hint="klink is meant to run inside the KLayout GUI; batch mode is not supported",
        )

    n_views = mw.views()
    had_layout = False
    try:
        before_view = mw.current_view()
        before_cv = before_view.active_cellview() if before_view is not None else None
        had_layout = before_cv is not None and before_cv.is_valid() and before_cv.cell is not None
    except Exception:
        had_layout = False

    # Ensure a blank TOP layout exists when KLayout was opened with only the
    # plugin loaded. This makes examples and agents work immediately after
    # launching KLayout, without a manual File > New step.
    try:
        from .cell_m import _active_layout
        view, cv, ly = _active_layout()
    except RpcError:
        raise
    except Exception as e:
        raise RpcError(
            ErrorCode.INTERNAL,
            f"failed to ensure active layout: {e}",
            hint="try creating a new layout manually in KLayout",
        )

    out = {
        "has_view": True,
        "views": mw.views(),
        "auto_created_layout": not had_layout,
    }

    # `active_cellview_index` is an attribute on LayoutView.
    try:
        out["active_cellview"] = view.active_cellview_index
    except Exception:
        out["active_cellview"] = None

    top = cv.cell

    out["cell"] = top.name
    out["cell_index"] = top.cell_index()
    try:
        out["file"] = cv.filename() or None
    except Exception:
        out["file"] = None
    out["dbu"] = ly.dbu
    out["top_cells"] = [c.name for c in ly.top_cells()]

    if verbosity == "summary":
        return out

    # layer_indexes() + get_info() is portable across pya versions.
    layers = []
    try:
        for idx in ly.layer_indexes():
            info = ly.get_info(idx)
            layers.append({
                "index": idx,
                "layer": info.layer,
                "datatype": info.datatype,
                "name": info.name if info.name else None,
            })
    except Exception:
        # Fallback path
        try:
            for info in ly.layer_infos():
                layers.append({
                    "layer": info.layer,
                    "datatype": info.datatype,
                    "name": info.name if info.name else None,
                })
        except Exception:
            pass
    out["layers"] = layers

    if verbosity == "full":
        try:
            out["cells_total"] = ly.cells()
        except Exception:
            pass
        try:
            out["hier_levels"] = top.hierarchy_levels()
        except Exception:
            pass

    return out


@method(
    "layout.show_file",
    description=(
        "Load a GDS/OAS file into KLayout. If the file is already open "
        "in a tab, reload it. Otherwise open it in the current view "
        "(mode='replace') or a new tab (mode='new'). "
        "When recording is active, all shape/cell events triggered by the "
        "file load are merged into a single `layout_show_file()` line."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the GDS/OAS file to load",
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "new"],
                "default": "replace",
                "description": "'replace' loads into the current view; 'new' opens a new tab",
            },
            "keep_position": {
                "type": "boolean",
                "default": True,
                "description": "Restore viewport after loading",
            },
            "technology": {
                "type": "string",
                "description": "KLayout technology name to apply to the loaded layout",
            },
        },
        "required": ["path"],
    },
    returns_schema={
        "type": "object",
        "properties": {
            "loaded": {"type": "string"},
            "type": {"type": "string", "enum": ["open", "reload"]},
            "cells": {"type": "integer"},
        },
    },
    mutates=True,
    long_running=True,
    tags=["layout", "write"],
)
def layout_show_file(params, ctx):
    path = params["path"]
    mode = params.get("mode", "replace")
    keep_position = params.get("keep_position", True)
    technology = params.get("technology", None)

    mw = _mw()
    if mw is None:
        raise RpcError(
            ErrorCode.INTERNAL,
            "no main window",
            hint="klink is meant to run inside the KLayout GUI; batch mode is not supported",
        )

    current_view = mw.current_view()
    previous_view = current_view.box() if current_view else None

    # Tell the recorder what file is being loaded so it can emit
    # `c.layout_show_file(path)` instead of individual shape events.
    try:
        from ..recorder import instance as _rec
        rec = _rec()
        rec._show_file_path = path
    except Exception:
        pass

    load_type = "open"
    view = None
    try:
        # Check if file is already open in any tab
        for i in range(mw.views()):
            v = mw.view(i)
            for j in range(v.cellviews()):
                try:
                    if v.active_cellview().filename() == path:
                        mw.current_view_index = i
                        v.active_setview_index = j
                        v.reload_layout(j)
                        if technology is not None:
                            try:
                                available = pya.Technology.technology_names()
                                if technology in available:
                                    if v.active_cellview().technology != technology:
                                        v.active_cellview().technology = technology
                            except Exception:
                                pass
                        if v.active_cellview().cell is None:
                            try:
                                v.active_cellview().cell = (
                                    v.active_cellview().layout().top_cells()[0]
                                )
                            except Exception:
                                pass
                        load_type = "reload"
                        view = v
                        break
                except Exception:
                    continue
            if view is not None:
                break

        if view is None:
            if mode == "new" and mw.views() > 0:
                new_cview = mw.load_layout(path, 1)
                view = new_cview.view()
            else:
                mw.load_layout(path, 1)
                view = mw.current_view()
            view.max_hier()
            if previous_view and keep_position:
                try:
                    view.zoom_box(previous_view)
                except Exception:
                    pass
            if technology is not None:
                try:
                    available = pya.Technology.technology_names()
                    if technology in available:
                        if view.active_cellview().technology != technology:
                            view.active_cellview().technology = technology
                except Exception:
                    pass

    finally:
        # DO NOT clear _show_file_path here — the debounced events
        # haven't fired yet. The recorder will clear it when it sees
        # the first non-file-load event.
        pass

    cv = view.active_cellview() if view is not None else None
    n_cells = 0
    if cv is not None and cv.is_valid():
        try:
            n_cells = cv.layout().cells()
        except Exception:
            pass

    return {
        "loaded": path,
        "type": load_type,
        "cells": n_cells,
    }


@method(
    "layout.save_file",
    description=(
        "Save the active layout to a GDS or OASIS file on disk. "
        "Extension determines format: .gds/.gds2 for GDSII, .oas/.oasis for OASIS."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to write. Extension determines format.",
            },
            "cellview_index": {
                "type": "integer",
                "default": 0,
                "description": "Which cellview to save (0 = active).",
            },
        },
        "required": ["path"],
    },
    returns_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "format": {"type": "string"},
            "file_size": {"type": "integer"},
        },
    },
    mutates=True,
    tags=["layout", "write"],
)
def layout_save_file(params, ctx):
    import os

    path = params["path"]
    cv_idx = params.get("cellview_index", 0)

    mw = _mw()
    lv = mw.current_view()
    if lv is None:
        raise RpcError(ErrorCode.NO_LAYOUT, "no layout view open")

    cv = lv.cellview(cv_idx)
    if not cv.is_valid():
        raise RpcError(ErrorCode.BAD_PARAMS, f"cellview {cv_idx} is not valid")

    layout = cv.layout()

    ext = os.path.splitext(path)[1].lower()
    opts = pya.SaveLayoutOptions()
    if ext in (".oas", ".oasis"):
        opts.format = "OASIS"
    else:
        opts.format = "GDS2"

    layout.write(path, opts)

    file_size = os.path.getsize(path) if os.path.exists(path) else 0
    return {"path": path, "format": opts.format, "file_size": file_size}


@method(
    "layout.clear",
    description=(
        "Clear the entire layout: removes all cells, shapes, and hierarchy "
        "in one operation. Leaves an empty layout ready for new content. "
        "Useful before restoring a version-control snapshot."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "cellview_index": {
                "type": "integer",
                "default": 0,
                "description": "Which cellview to clear (0 = active).",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
        },
    },
    mutates=True,
    tags=["layout", "write"],
)
def layout_clear(params, ctx):
    cv_idx = params.get("cellview_index", 0)

    mw = _mw()
    lv = mw.current_view()
    if lv is None:
        raise RpcError(ErrorCode.NO_LAYOUT, "no layout view open")

    cv = lv.cellview(cv_idx)
    if not cv.is_valid():
        raise RpcError(ErrorCode.BAD_PARAMS, f"cellview {cv_idx} is not valid")

    layout = cv.layout()
    layout.clear()

    # layout.clear() deletes all cells but the cellview still references
    # the now-deleted cell. Create a fresh default cell so subsequent
    # layout.info / shape.insert calls don't crash on a dangling cell.
    new_top = layout.create_cell("TOP")
    try:
        cv.cell = new_top
    except Exception:
        # Fallback: try cell_name= assignment
        try:
            cv.cell_name = "TOP"
        except Exception:
            pass

    return {"ok": True}
