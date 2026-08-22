"""
Layout / view inspection methods.

M1 ships a single read-only method, `layout.info`, as the end-to-end
smoke test for the protocol. More read methods (cell.list, shape.query,
selection.get, ...) arrive in M2. `layout.export_clean` is the
fail-closed delivery exit (docs/REGION_INTENT_DESIGN.md sect.10.1).
"""

from __future__ import annotations

import os
import re

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


def _safe_file_manifest(path):
    """Best-effort manifest for load/import returns: the load already
    succeeded, so a manifest failure must not fail the call."""
    try:
        return _file_manifest(path)
    except Exception:
        return None


def _file_manifest(path, detail="summary"):
    """What does the FILE itself contain? Read into a throwaway
    pya.Layout and discard — the live session is never touched, so the
    answer cannot be contaminated by open tabs (a measured agent
    failure mode: import merged a file into a dirty layout, then
    session queries reported leftover cells/layers as the file's)."""
    import os

    if not path or not os.path.isfile(path):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "path %r does not exist or is not a file; pass an absolute "
            "path to a layout file KLayout can read" % (path,))
    tmp = pya.Layout()
    try:
        tmp.read(str(path))
    except Exception as exc:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "KLayout could not read %r as a layout file: %s" % (path, exc))
    tops = sorted(c.name for c in tmp.top_cells())
    layers = []
    counts = {}
    for li in tmp.layer_indexes():
        info = tmp.get_info(li)
        ent = {"layer": int(info.layer), "datatype": int(info.datatype)}
        if info.name:
            ent["name"] = str(info.name)
        layers.append(ent)
        if detail == "counts":
            kinds = {"boxes": 0, "polygons": 0, "paths": 0,
                     "texts": 0, "others": 0}
            for cell in tmp.each_cell():
                for sh in cell.shapes(li).each():
                    if sh.is_box():
                        kinds["boxes"] += 1
                    elif sh.is_path():
                        kinds["paths"] += 1
                    elif sh.is_text():
                        kinds["texts"] += 1
                    elif sh.is_polygon():
                        kinds["polygons"] += 1
                    else:
                        kinds["others"] += 1
            kinds["total"] = (kinds["boxes"] + kinds["polygons"]
                              + kinds["paths"] + kinds["texts"]
                              + kinds["others"])
            counts["%d/%d" % (info.layer, info.datatype)] = kinds
    layers.sort(key=lambda e: (e["layer"], e["datatype"]))
    bbox_of = {}
    for name in tops:
        b = tmp.cell(name).bbox()
        bbox_of[name] = [b.left, b.bottom, b.right, b.top]
    out = {
        "path": str(path),
        "dbu": tmp.dbu,
        "cells_total": int(tmp.cells()),
        "top_cells": tops,
        "layers": layers,
        "bbox_dbu_of": bbox_of,
    }
    if detail == "counts":
        out["layer_shape_counts"] = counts
    return out


@method(
    "layout.file_info",
    description=(
        "Answer \"what is inside this layout FILE?\" without touching "
        "the session: the file is read into a throwaway layout and "
        "discarded, so open tabs cannot contaminate the answer (a "
        "measured agent failure mode: importing merged the file into a "
        "dirty layout, then session queries reported leftover "
        "cells/layers as the file's). Returns dbu, top cells, total "
        "cell count, the file's own layer list, and each top cell's "
        "bbox in dbu; detail='counts' adds per-layer stored-shape "
        "counts split by kind (boxes/polygons/paths/texts/others + "
        "total; slower on big files) -- a question about 'boxes' means "
        "the 'boxes' entry, not 'total'. Use THIS to inspect a file; use "
        "layout.show_file to open one in a tab; layout.import_file "
        "MERGES into the active layout."
    ),
    params_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the layout file",
            },
            "detail": {
                "type": "string",
                "enum": ["summary", "counts"],
                "default": "summary",
                "description": "'counts' adds per-layer stored-shape "
                               "counts (walks every cell; slower on "
                               "very large files)",
            },
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "dbu": {"type": "number"},
            "cells_total": {"type": "integer"},
            "top_cells": {"type": "array", "items": {"type": "string"}},
            "layers": {"type": "array"},
            "bbox_dbu_of": {"type": "object"},
            "layer_shape_counts": {"type": "object"},
        },
    },
    mutates=False,
    long_running=True,
    tags=["layout", "read"],
)
def layout_file_info(params, ctx):
    return _file_manifest(params.get("path"),
                          str(params.get("detail") or "summary"))


@method(
    "layout.show_file",
    description=(
        "Load a GDS/OAS file into KLayout. If the file is already open "
        "in a tab, reload it. Otherwise open it in the current view "
        "(mode='replace') or a new tab (mode='new'). The return's "
        "`file_info` block reports what the FILE itself contains (read "
        "separately from the file, immune to session state) -- answer "
        "file-content questions from it, not from session queries. "
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
            "file_info": {"type": "object"},
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
        # DO NOT clear _show_file_path here -- the debounced events
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

    out = {
        "loaded": path,
        "type": load_type,
        "cells": n_cells,
    }
    out["file_info"] = _safe_file_manifest(path)
    return out


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


@method(
    "layout.import_file",
    description=(
        "MERGE a layout file (GDS/OASIS/...) into the ACTIVE layout -- the "
        "load-time mapping workflow (official LoadLayoutOptions), unlike "
        "layout.show_file which opens a file in its own tab. `layer_map` "
        "remaps layers while reading ([{from: 'L/D', to: 'L/D'}, ...]); "
        "`create_other_layers` (default true) controls whether unlisted "
        "layers are read too; `on_conflict` decides same-name cells: "
        "'rename' (default, new cells get a $1-style suffix), 'add' "
        "(content merged into the existing cell), 'overwrite' (old cell "
        "replaced), 'skip' (new cell dropped). One undo step. Returns "
        "cells/layers added, the new top cells, and a `file_info` block "
        "describing what the FILE itself contained -- after a merge, "
        "session queries (cell.list/layer.list) describe the MIXED "
        "layout, never the file; answer file-content questions from "
        "file_info or layout.file_info instead. To merely INSPECT a "
        "file, do not import it: use layout.file_info (session "
        "untouched) or layout.show_file (own tab)."
    ),
    params_schema={
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "layer_map": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["from", "to"],
                    "properties": {"from": {"type": "string"},
                                   "to": {"type": "string"}},
                },
                "description": "[{from: 'L/D', to: 'L/D'}, ...]",
            },
            "create_other_layers": {"type": "boolean", "default": True},
            "on_conflict": {
                "type": "string",
                "enum": ["rename", "add", "overwrite", "skip"],
                "default": "rename",
            },
        },
        "additionalProperties": False,
    },
    returns_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "cells_added": {"type": "integer"},
            "new_cells": {"type": "array", "items": {"type": "string"}},
            "layers_added": {"type": "array", "items": {"type": "string"}},
            "on_conflict": {"type": "string"},
            "file_info": {"type": "object"},
        },
    },
    mutates=True,
    tags=["layout", "write"],
)
def layout_import_file(params, ctx):
    import os

    from .cell_m import _active_layout
    from ..txn import auto_txn

    path = str(params.get("path") or "")
    if not path or not os.path.isfile(path):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "path %r does not exist or is not a file; pass an absolute "
            "path to a layout file KLayout can read" % (path,))

    conflict_map = {
        "rename": pya.LoadLayoutOptions.CellConflictResolution.RenameCell,
        "add": pya.LoadLayoutOptions.CellConflictResolution.AddToCell,
        "overwrite": pya.LoadLayoutOptions.CellConflictResolution.OverwriteCell,
        "skip": pya.LoadLayoutOptions.CellConflictResolution.SkipNewCell,
    }
    conflict = str(params.get("on_conflict") or "rename").lower()
    if conflict not in conflict_map:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "on_conflict %r must be one of %s" % (conflict,
                                                  sorted(conflict_map)))

    def _ld(spec, what):
        parts = str(spec).split("/")
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "%s %r is not 'L/D' (e.g. '1/0')" % (what, spec))

    entries = params.get("layer_map") or []
    parsed_map = []
    for i, ent in enumerate(entries):
        parsed_map.append((_ld(ent.get("from"), "layer_map[%d].from" % i),
                           _ld(ent.get("to"), "layer_map[%d].to" % i)))
    create_other = bool(params.get("create_other_layers", True))
    if parsed_map and conflict == "add":
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "layer_map with on_conflict='add' is ambiguous: 'add' merges "
            "imported content into EXISTING cells, so remapping layers "
            "afterwards would also move the cells' pre-existing shapes. "
            "Use on_conflict rename/skip/overwrite with layer_map.")

    view, _cv, ly = _active_layout()
    cells_before = {ly.cell_name(c.cell_index()) for c in ly.each_cell()}
    layers_before = {(info.layer, info.datatype)
                     for li in ly.layer_indexes()
                     for info in [ly.get_info(li)]}

    opt = pya.LoadLayoutOptions()
    opt.cell_conflict_resolution = conflict_map[conflict]

    title = "klink: import %s (%s)" % (os.path.basename(path), conflict)
    with auto_txn(view, title):
        try:
            ly.read(path, opt)
        except Exception as exc:
            raise RpcError(
                ErrorCode.BAD_PARAMS,
                "KLayout could not read %r into the active layout: %s"
                % (path, exc))

        # Layer mapping is applied POST-read, restricted to the cells this
        # read created. Rationale (probe-verified on 0.30.7): a
        # view-attached layout ignores LoadLayoutOptions' layer map (a
        # detached pya.Layout in the same process honors it), so we move
        # shapes instead -- observable semantics match load-time mapping
        # for the imported subtree.
        if parsed_map or not create_other:
            new_cell_objs = [
                ly.cell(name)
                for name in ({ly.cell_name(c.cell_index())
                              for c in ly.each_cell()} - cells_before)
            ]
            move = {}
            for src, dst in parsed_map:
                move[ly.layer(src[0], src[1])] = ly.layer(dst[0], dst[1])
            keep = set(move.values())
            for cell in new_cell_objs:
                if cell is None:
                    continue
                for src_li, dst_li in move.items():
                    if src_li == dst_li:
                        continue
                    s = cell.shapes(src_li)
                    if s.size():
                        cell.shapes(dst_li).insert(s)
                        s.clear()
                if not create_other:
                    for li in ly.layer_indexes():
                        if li in keep or li in move:
                            continue
                        cell.shapes(li).clear()
            # drop layers this read created that ended up empty everywhere
            for li in list(ly.layer_indexes()):
                info = ly.get_info(li)
                if (info.layer, info.datatype) in layers_before:
                    continue
                if any(c.shapes(li).size() for c in ly.each_cell()):
                    continue
                try:
                    ly.delete_layer(li)
                except Exception:
                    pass

    cells_after = {ly.cell_name(c.cell_index()) for c in ly.each_cell()}
    layers_after = {(info.layer, info.datatype)
                    for li in ly.layer_indexes()
                    for info in [ly.get_info(li)]}
    new_cells = sorted(cells_after - cells_before)
    return {
        "path": path,
        "cells_added": len(new_cells),
        "new_cells": new_cells[:50],
        "layers_added": sorted("%d/%d" % ld
                               for ld in layers_after - layers_before),
        "on_conflict": conflict,
        # what the FILE contained -- the merged layout no longer knows
        "file_info": _safe_file_manifest(path),
    }


# ---------------------------------------------------------------------------
# layout.export_clean -- the fail-closed delivery exit
# ---------------------------------------------------------------------------

_KLINK_MARKER_LIBS = ("klink_port", "klink_anchor", "klink_region")
_KLINK_KEEPOUT_LAYER = "900/0"


def _reserved_layer_registry(ly) -> dict:
    """The ACTUAL configured reserved layers (no wildcards -- marker layers
    are configurable via *.set_layer, and a user PDK may genuinely use
    999/x)."""
    from .anchor_m import _DEFAULT_ANCHOR_LAYER_KEY
    from .port_m import _DEFAULT_PORT_LAYER_KEY
    from .region_m import _DEFAULT_REGION_LAYER, _DEFAULT_REGION_LAYER_KEY

    def _meta(key, fallback):
        try:
            value = ly.meta_info_value(key)
        except Exception:
            value = None
        return str(value) if value else fallback

    return {
        "port": _meta(_DEFAULT_PORT_LAYER_KEY, "999/99"),
        "anchor": _meta(_DEFAULT_ANCHOR_LAYER_KEY, "999/1"),
        "region": _meta(_DEFAULT_REGION_LAYER_KEY, _DEFAULT_REGION_LAYER),
        "keepout": _KLINK_KEEPOUT_LAYER,
    }


def _parse_ld(layer_str: str):
    try:
        left, right = str(layer_str).split("/", 1)
        return int(left), int(right)
    except Exception:
        raise RpcError(ErrorCode.BAD_PARAMS,
                       "layer must be 'L/D' format, e.g. '10/0'")


def _is_klink_marker_cell(cell) -> bool:
    try:
        if cell.pcell_declaration() is None:
            return False
        lib = cell.library()
        return lib is not None and str(lib.name()) in _KLINK_MARKER_LIBS
    except Exception:
        return False


@method(
    "layout.export_clean",
    description=(
        "Fail-closed delivery export: write ONLY an explicit process-layer "
        "allowlist to a new GDS/OASIS file, with every klink marker "
        "(Port/Anchor/Region PCell instance) removed and PCell context "
        "stripped. Works on a scratch copy -- the live layout is never "
        "modified. The output is re-read and verified (no reserved layers, "
        "no marker cells) before being atomically promoted; on any "
        "verification failure the temp file is deleted and the call errors. "
        "Refuses allowlists that include a reserved marker layer. Pass "
        "`cells` to export ONLY those top cells (+ their hierarchy) -- "
        "without it the WHOLE layout's top cells go into the file, "
        "including unrelated ones from a shared session. Use "
        "layout.save_file for full working archives; use THIS for masks "
        "and hand-offs."
    ),
    params_schema={
        "type": "object",
        "required": ["path", "allowlist_layers"],
        "properties": {
            "path": {"type": "string",
                     "description": "Output file (.gds/.oas)."},
            "allowlist_layers": {
                "type": "array", "minItems": 1,
                "items": {"type": "string"},
                "description": "EXACT process layers 'L/D' to export -- your "
                               "project decides; klink ships no default.",
            },
            "cells": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top cell name(s) to export (each with its "
                               "full hierarchy). Omit = every top cell in "
                               "the layout.",
            },
            "cellview_index": {"type": "integer", "default": 0},
        },
    },
    returns_schema={"type": "object"},
    mutates=True,
    long_running=True,
    tags=["layout", "write"],
)
def layout_export_clean(params, ctx):
    mw = _mw()
    lv = mw.current_view()
    if lv is None:
        raise RpcError(ErrorCode.NO_LAYOUT, "no layout view open")
    cv = lv.cellview(int(params.get("cellview_index", 0)))
    if not cv.is_valid():
        raise RpcError(ErrorCode.BAD_PARAMS, "cellview is not valid")
    ly = cv.layout()

    path = str(params["path"])
    allow = [str(v) for v in params["allowlist_layers"]]
    allow_pairs = {_parse_ld(v) for v in allow}

    registry = _reserved_layer_registry(ly)
    reserved_pairs = {name: _parse_ld(value)
                      for name, value in registry.items()}
    conflicts = [
        "%s (%s)" % (registry[name], name)
        for name, pair in reserved_pairs.items() if pair in allow_pairs
    ]
    if conflicts:
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "allowlist contains reserved klink marker layer(s): %s"
            % ", ".join(conflicts),
            hint="reserved layers never go on masks; if your process truly "
                 "uses this layer number, move the marker layer first "
                 "(port.set_layer / anchor.set_layer / region.set_layer)",
        )

    # scratch copy -- the live layout is never touched
    ly2 = ly.dup()

    marker_cells = [c for c in ly2.each_cell() if _is_klink_marker_cell(c)]
    marker_indexes = {c.cell_index() for c in marker_cells}
    removed_instances = 0
    for cell in ly2.each_cell():
        if cell.cell_index() in marker_indexes:
            continue
        doomed = [inst for inst in cell.each_inst()
                  if inst.cell is not None
                  and inst.cell.cell_index() in marker_indexes]
        for inst in doomed:
            cell.erase(inst)
            removed_instances += 1
    for idx in sorted(marker_indexes, reverse=True):
        try:
            ly2.delete_cell(idx)
        except Exception:
            pass

    # optional top-cell scoping (blind-test finding: without it, unrelated
    # top cells from a shared session ride along into the delivery file).
    # Tolerant input shape: a stale/loose MCP client may deliver the array
    # as its string form — accept JSON-array strings and comma/space lists,
    # and NEVER fall through to iterating a string character by character
    # (the 0.5.1 blind test hit exactly that as "cell '[' not found").
    raw_cells = params.get("cells")
    if isinstance(raw_cells, str):
        text = raw_cells.strip()
        if text.startswith("["):
            import json as _json
            try:
                raw_cells = _json.loads(text)
            except Exception:
                raise RpcError(
                    ErrorCode.BAD_PARAMS,
                    "cells looks like a JSON array string but does not parse",
                    hint='pass a real array: cells: ["TOP_A", "TOP_B"]',
                )
        else:
            raw_cells = [t for t in re.split(r"[,\s]+", text) if t]
    if raw_cells is not None and not isinstance(raw_cells, (list, tuple)):
        raise RpcError(
            ErrorCode.BAD_PARAMS,
            "cells must be an array of top cell names",
            hint='cells: ["TOP_A"] — or omit it to export every top cell',
        )
    cell_names = [str(v) for v in (raw_cells or [])]
    selected_indexes = []
    for name in cell_names:
        cell2 = ly2.cell(name)
        if cell2 is None:
            raise RpcError(
                ErrorCode.NOT_FOUND,
                "cell %r not found for export scoping" % name,
                hint="cell.list shows the available cells",
            )
        selected_indexes.append(cell2.cell_index())

    opts = pya.SaveLayoutOptions()
    ext = os.path.splitext(path)[1].lower()
    opts.format = "OASIS" if ext in (".oas", ".oasis") else "GDS2"
    if selected_indexes:
        opts.clear_cells()
        for idx in selected_indexes:
            opts.add_cell(idx)
    opts.deselect_all_layers()
    layers_written = []
    for layer_str in allow:
        layer, datatype = _parse_ld(layer_str)
        idx = ly2.find_layer(pya.LayerInfo(layer, datatype))
        if idx is not None:
            opts.add_layer(int(idx), pya.LayerInfo(layer, datatype))
            layers_written.append(layer_str)
    opts.write_context_info = False
    opts.no_empty_cells = True

    tmp_path = path + ".klink_tmp"
    ly2.write(tmp_path, opts)

    # re-read and verify before promoting (fail-closed)
    problems = []
    try:
        check = pya.Layout()
        check.read(tmp_path)
        out_pairs = set()
        for idx in check.layer_indexes():
            info = check.get_info(idx)
            out_pairs.add((int(info.layer), int(info.datatype)))
        illegal = out_pairs - allow_pairs
        if illegal:
            problems.append("output contains non-allowlisted layers: %s"
                            % sorted(illegal))
        reserved_hit = out_pairs & set(reserved_pairs.values())
        if reserved_hit:
            problems.append("output contains reserved layers: %s"
                            % sorted(reserved_hit))
        if cell_names:
            out_tops = {c.name for c in check.top_cells()}
            extra_tops = out_tops - set(cell_names)
            if extra_tops:
                problems.append(
                    "output contains top cells outside the requested "
                    "scope: %s" % sorted(extra_tops))
    except Exception as exc:
        problems.append("re-read failed: %s" % exc)

    if problems:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise RpcError(
            ErrorCode.EXEC,
            "clean export verification failed: %s" % "; ".join(problems),
            hint="nothing was written to the final path",
        )

    os.replace(tmp_path, path)
    return {
        "path": path,
        "format": opts.format,
        "cells": cell_names or "all_top_cells",
        "layers_written": layers_written,
        "layers_requested": allow,
        "marker_instances_removed": removed_instances,
        "marker_cells_removed": len(marker_indexes),
        "reserved_registry": registry,
        "verified": True,
        "file_size": os.path.getsize(path),
    }
