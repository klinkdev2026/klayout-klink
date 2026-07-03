"""
View control methods.

`view.screenshot`  : render the active view to PNG, return base64 or
                     save to disk
`view.zoom_fit`    : fit the whole layout into the viewport
`view.zoom_box`    : zoom to a specific bbox (dbu)
`view.viewport`    : return the current viewport bbox (dbu) + pixel size

Screenshots are rendered by pya's `LayoutView.save_image_with_options`
(synchronous; runs in the Qt main thread). For M2 we ship a single
PNG path; SVG / vector export can be added later on demand.
"""

from __future__ import annotations

import base64
import os
import tempfile
from typing import Optional

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from .cell_m import _active_layout, _resolve_cell
from .shape_m import _box_from_param


@method(
    "view.list_tabs",
    description=(
        "List all layout tabs (views) in this KLayout window: index, title, "
        "file path, active cell, and which tab is current. Use together "
        "with view.activate_tab to inspect non-active layouts through the "
        "ordinary single-layout RPCs."
    ),
    params_schema={"type": "object", "properties": {}},
    returns_schema={
        "type": "object",
        "properties": {
            "current_index": {"type": "integer"},
            "tabs": {"type": "array"},
        },
    },
    tags=["view", "read"],
)
def view_list_tabs(params, ctx):
    mw = pya.Application.instance().main_window()
    if mw is None:
        raise RpcError(ErrorCode.INTERNAL, "no main window")
    current = int(mw.current_view_index)
    tabs = []
    for index in range(int(mw.views())):
        view = mw.view(index)
        entry: dict = {"index": index, "is_current": index == current}
        try:
            entry["title"] = str(view.title or "")
        except Exception as exc:
            entry["title"] = ""
            entry["title_error"] = str(exc)
        cellviews = []
        try:
            # active_cellview_index is a PROPERTY in pya, not a method.
            active_ci = int(view.active_cellview_index)
        except Exception:
            active_ci = -1
        for ci in range(int(view.cellviews())):
            try:
                cv = view.cellview(ci)
                if not cv.is_valid():
                    continue
                cellviews.append({
                    "index": ci,
                    "filename": cv.filename() or None,
                    "active_cell": cv.cell.name if cv.cell is not None else None,
                    "is_active": ci == active_ci,
                })
            except Exception as exc:
                cellviews.append({"index": ci, "error": str(exc)})
        entry["cellviews"] = cellviews
        tabs.append(entry)
    return {"current_index": current, "tabs": tabs}


@method(
    "view.activate_tab",
    description=(
        "Switch the current KLayout tab (view) by index from view.list_tabs. "
        "After switching, all single-layout RPCs (layout.info, cell.list, "
        "shape.query, ...) operate on that tab's layout."
    ),
    params_schema={
        "type": "object",
        "required": ["index"],
        "properties": {
            "index": {"type": "integer", "minimum": 0},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "current_index": {"type": "integer"},
            "title": {"type": "string"},
        },
    },
    mutates=True,
    tags=["view", "write"],
)
def view_activate_tab(params, ctx):
    mw = pya.Application.instance().main_window()
    if mw is None:
        raise RpcError(ErrorCode.INTERNAL, "no main window")
    index = int(params["index"])
    total = int(mw.views())
    if total == 0:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "no layout tabs are open in this KLayout window; open or load "
            "a layout first",
        )
    if index < 0 or index >= total:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            f"tab index {index} out of range (0..{total - 1}); "
            "call view.list_tabs first",
        )
    mw.current_view_index = index
    title = ""
    try:
        title = str(mw.view(index).title or "")
    except Exception:
        pass
    return {"current_index": int(mw.current_view_index), "title": title}


@method(
    "view.screenshot",
    description=(
        "Render a PNG screenshot of the active layout view. Two modes: "
        "'base64' embeds the PNG in the response as a data URL (great "
        "for LLMs with vision support); 'path' saves to disk and returns "
        "the absolute path (use for large images). Width/height are in "
        "pixels; defaults match what the user sees on screen. You can "
        "also clip to a bbox_dbu region."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["base64", "path"], "default": "base64"},
            "width_px": {"type": "integer", "minimum": 16, "maximum": 8192},
            "height_px": {"type": "integer", "minimum": 16, "maximum": 8192},
            "bbox_dbu": {
                "type": "array", "minItems": 4, "maxItems": 4,
                "description": "Optional clipping rectangle in dbu. Default: current viewport.",
            },
            "path": {
                "type": "string",
                "description": "Destination path (only used when mode='path'). Default: temp file.",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "width_px": {"type": "integer"},
            "height_px": {"type": "integer"},
            "data_url": {"type": "string", "description": "Present when mode='base64'"},
            "path": {"type": "string", "description": "Present when mode='path'"},
            "bytes": {"type": "integer"},
        },
    },
    tags=["view", "read"],
)
def view_screenshot(params, ctx):
    view, _, _ = _active_layout()

    mode = params.get("mode", "base64")
    if mode not in ("base64", "path"):
        raise RpcError(ErrorCode.BAD_PARAMS, "mode must be 'base64' or 'path'")

    # Defaults: use the current widget size if not specified
    try:
        w_default = max(16, int(view.viewport_width()))
        h_default = max(16, int(view.viewport_height()))
    except Exception:
        w_default, h_default = 1024, 768

    width = int(params.get("width_px") or w_default)
    height = int(params.get("height_px") or h_default)

    target_path: str
    if mode == "path" and params.get("path"):
        target_path = os.path.abspath(params["path"])
    else:
        # Always write to a temp file first; for base64 mode we then
        # read+encode it.
        tmp_fd, target_path = tempfile.mkstemp(prefix="klink_shot_", suffix=".png")
        os.close(tmp_fd)

    bbox = _box_from_param(params.get("bbox_dbu"))

    try:
        if bbox is not None:
            # save_image_with_options(path, width, height, linewidth=0,
            #   oversampling=0, resolution=0, target_box=None, monochrome=False)
            view.save_image_with_options(
                target_path, width, height, 0, 0, 0, bbox, False,
            )
        else:
            view.save_image(target_path, width, height)
    except Exception as e:
        # Clean up the temp file on failure
        if mode == "base64":
            try:
                os.unlink(target_path)
            except Exception:
                pass
        raise RpcError(
            ErrorCode.INTERNAL, f"screenshot failed: {e}",
            hint="make sure a view is open; try smaller width/height",
        )

    if not os.path.exists(target_path):
        raise RpcError(ErrorCode.INTERNAL, "screenshot file was not created")

    size_bytes = os.path.getsize(target_path)

    if mode == "path":
        return {
            "mode": "path",
            "path": target_path,
            "width_px": width,
            "height_px": height,
            "bytes": size_bytes,
        }

    # base64 mode
    with open(target_path, "rb") as f:
        raw = f.read()
    try:
        os.unlink(target_path)
    except Exception:
        pass
    b64 = base64.b64encode(raw).decode("ascii")
    return {
        "mode": "base64",
        "data_url": "data:image/png;base64," + b64,
        "width_px": width,
        "height_px": height,
        "bytes": size_bytes,
    }


@method(
    "view.zoom_fit",
    description="Fit the entire layout into the viewport (equivalent to GUI's 'Zoom Fit').",
    params_schema={"type": "object"},
    returns_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    mutates=True,
    tags=["view"],
)
def view_zoom_fit(params, ctx):
    view, _, _ = _active_layout()
    view.zoom_fit()
    return {"ok": True}


@method(
    "view.zoom_box",
    description="Zoom the viewport to show exactly the given bbox (dbu).",
    params_schema={
        "type": "object",
        "required": ["bbox_dbu"],
        "properties": {
            "bbox_dbu": {"type": "array", "minItems": 4, "maxItems": 4},
        },
    },
    returns_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    mutates=True,
    tags=["view"],
)
def view_zoom_box(params, ctx):
    view, _, _ = _active_layout()
    bbox = _box_from_param(params.get("bbox_dbu"))
    if bbox is None:
        raise RpcError(ErrorCode.BAD_PARAMS, "bbox_dbu is required")
    view.zoom_box(bbox)
    return {"ok": True}


@method(
    "view.viewport",
    description=(
        "Report the current viewport: visible bbox in dbu, pixel size of "
        "the view widget, and cellview index. Call this to align an "
        "external coordinate with what the user sees."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "bbox_dbu": {"type": "array"},
            "width_px": {"type": "integer"},
            "height_px": {"type": "integer"},
            "active_cellview": {"type": "integer"},
        },
    },
    tags=["view", "read"],
)
def view_viewport(params, ctx):
    view, _, _ = _active_layout()
    out: dict = {}
    try:
        bb = view.box()  # LayoutView.box() returns visible region as DBox
        if hasattr(bb, "left"):
            out["bbox_dbu"] = [bb.left, bb.bottom, bb.right, bb.top]
    except Exception:
        pass
    try:
        out["width_px"] = int(view.viewport_width())
        out["height_px"] = int(view.viewport_height())
    except Exception:
        pass
    try:
        out["active_cellview"] = view.active_cellview_index
    except Exception:
        pass
    return out


@method(
    "view.show_cell",
    description=(
        "Set the active cellview's displayed (top) cell. KLayout shows "
        "a single cell at a time per view; if you just created a cell "
        "with cell.create and want to actually see its contents, you "
        "must call this (or insert an instance of it into the current "
        "top). Also zoom-fits by default. Returns the cell that is now "
        "being shown."
    ),
    params_schema={
        "type": "object",
        "required": ["cell"],
        "properties": {
            "cell": {"description": "Cell name (str) or cell_index (int)."},
            "zoom_fit": {"type": "boolean", "default": True},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "cell": {"type": "string"},
            "cell_index": {"type": "integer"},
        },
    },
    tags=["view", "navigate"],
)
def view_show_cell(params, ctx):
    view, cv, ly = _active_layout()
    target = _resolve_cell(ly, params["cell"])
    target_name = target.name
    target_idx = int(target.cell_index())

    # Try every reasonable setter the KLayout docs mention, in order
    # of preference, until the visible state actually changes. pya has
    # at least three writable attributes / helper methods depending on
    # version: cell_name=, cell_index=, cell=, set_cell_name().
    attempts = []

    def _try(label, fn):
        try:
            fn()
            attempts.append((label, None))
        except Exception as e:
            attempts.append((label, repr(e)))

    _try("cell_name=",   lambda: setattr(cv, "cell_name", target_name))
    if cv.cell_name != target_name:
        _try("cell_index=", lambda: setattr(cv, "cell_index", target_idx))
    if cv.cell_name != target_name:
        _try("cell=",       lambda: setattr(cv, "cell", target))
    if cv.cell_name != target_name:
        _try("set_cell_name()", lambda: cv.set_cell_name(target_name))

    if cv.cell_name != target_name:
        raise RpcError(
            ErrorCode.INTERNAL,
            f"could not switch active cellview to {target_name!r} "
            f"(still on {cv.cell_name!r})",
            data={"attempts": attempts},
            hint="KLayout rejected every setter; likely an invalid cell",
        )

    # Force the view to re-sync with the new cellview state. Re-assigning
    # the active cellview index is a cheap refresh and fixes cases where
    # the hierarchy browser keeps showing the old cell.
    try:
        view.active_cellview_index = view.active_cellview_index
    except Exception:
        pass

    if bool(params.get("zoom_fit", True)):
        try:
            view.zoom_fit()
        except Exception:
            pass
    return {
        "cell": target_name,
        "cell_index": target_idx,
        "active_cellview": view.active_cellview_index,
        "attempts": attempts,
    }


@method(
    "view.close_tab",
    description=(
        "Close a layout view tab by index. Closes the active tab if no "
        "index is specified."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "view_index": {
                "type": "integer",
                "description": "0-based index of the view to close. Default: active view.",
            },
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "closed": {"type": "boolean"},
            "remaining_views": {"type": "integer"},
        },
    },
    mutates=True,
    tags=["view", "write"],
)
def view_close_tab(params, ctx):
    mw = pya.Application.instance().main_window()
    views_before = mw.views()

    view_index = params.get("view_index")
    if view_index is not None:
        if view_index < 0 or view_index >= views_before:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                f"view_index {view_index} out of range (0..{views_before - 1})",
            )
        lv = mw.view(view_index)
    else:
        lv = mw.current_view()

    if lv is None:
        raise RpcError(ErrorCode.NO_LAYOUT, "no view to close")

    lv.close()
    return {"closed": True, "remaining_views": mw.views()}


@method(
    "view.show_lvsdb",
    description=(
        "Load a saved KLayout LVS/netlist database from disk into the "
        "current view's Netlist Browser and SHOW it, so you can cross-probe "
        "layout<->netlist interactively (click a net/device -> highlight in "
        "layout). Like DRC's marker browser, but for LVS/connectivity. "
        "kind='lvs' (default) reads a .lvsdb (LayoutVsSchematic, with the "
        "matched/unmatched cross-reference); kind='l2n' reads a .l2n "
        "(extraction only). Pair with structdevice.lvs_check mode='lvsdb', "
        "which writes the .lvsdb and returns its path. Read-only (loads a "
        "file; does not modify the layout)."
    ),
    params_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "absolute path to a .lvsdb or .l2n file"},
            "kind": {"type": "string", "enum": ["lvs", "l2n"], "default": "lvs"},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "shown": {"type": "boolean"},
            "path": {"type": "string"},
            "db_index": {"type": "integer"},
        },
    },
    tags=["view", "read"],
)
def view_show_lvsdb(params, ctx):
    path = str(params.get("path") or "")
    if not path or not os.path.exists(path):
        raise RpcError(
            ErrorCode.NOT_FOUND,
            f"file not found: {path!r}",
            hint="pass an absolute path written by structdevice.lvs_check mode='lvsdb'",
        )
    mw = pya.Application.instance().main_window()
    if mw is None:
        raise RpcError(ErrorCode.INTERNAL, "no main window")
    view = mw.current_view()
    if view is None:
        raise RpcError(ErrorCode.NO_VIEW, "no current view; open a layout first")
    cv = view.active_cellview()
    cv_index = cv.cell_index if cv.cell is not None else 0
    kind = str(params.get("kind") or "lvs")
    try:
        if kind == "l2n":
            db = pya.LayoutToNetlist()
            db.read(path)
            idx = view.add_l2ndb(db)
            view.show_l2ndb(idx, cv_index)
        else:
            db = pya.LayoutVsSchematic()
            db.read(path)
            idx = view.add_lvsdb(db)
            view.show_lvsdb(idx, cv_index)
    except Exception as exc:
        raise RpcError(
            ErrorCode.INTERNAL,
            f"could not load/show {kind} database: {exc}",
            hint="check the file is a valid .lvsdb/.l2n and matches the open layout",
        )
    return {"shown": True, "path": path, "db_index": int(idx)}
