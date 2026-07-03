"""
Edit-history methods (M3 Round 5).

`edit.undo`  - reverse the most recent undoable operation
`edit.redo`  - replay the most recently undone operation
`edit.status`- report whether undo/redo is currently available and show
               the label of the next-to-undo / next-to-redo transaction

Every mutating klink RPC runs inside an `auto_txn` wrapper which calls
`LayoutView.transaction(title)` / `view.commit()`. That pushes a named
entry onto KLayout's undo stack, identical to what an interactive edit
in the GUI would do - so these three RPCs let an LLM agent say
"oops, revert that" without the user reaching for Ctrl+Z.

Underlying API, by what actually works in KLayout 0.30
-----------------------------------------------------
To perform the action we use `MainWindow.cm_undo()` / `cm_redo()` -
the same code path the Edit menu uses. It handles view refresh, layer
panel sync, etc.

To report status we need access to the undo manager. In this KLayout
version neither `Layout.manager()` nor `LayoutView.manager()` return
anything useful - the canonical place is `MainWindow.manager()`, which
returns a `pya.Manager` exposing:

  has_undo() / has_redo()              -> bool
  transaction_for_undo() / ..._redo()  -> str  (next-entry description)

We still probe `Layout.manager()` and `LayoutView.manager()` first for
forward/backward compatibility - on builds where they do return a
manager the caller's active layout is a safer reference point than a
global main window.

If you need to debug what's exposed by `pya.Manager` on some other
KLayout version, call `edit.status({"debug": true})` - the reply will
include `_mgr_src`, `_mgr_attrs`, and per-attribute raw returns.
"""

from __future__ import annotations

import pya

from ..registry import method
from ..errors import RpcError, ErrorCode
from ..txn import custom_redo, custom_status, custom_undo
from .cell_m import _active_layout


def _pump_events() -> None:
    """Flush queued Qt signals so the Manager reflects the current undo
    stack before we read it. Most versions don't strictly need this
    (cm_undo returns synchronously and the Manager mutates in-place),
    but it's cheap insurance when reading right after a mutation."""
    try:
        app = pya.Application.instance()
        for fn_name in ("process_events", "sync_queued_slots"):
            fn = getattr(app, fn_name, None)
            if fn is not None:
                try:
                    fn()
                except Exception:
                    pass
    except Exception:
        pass


def _try_mainwindow_action(action: str) -> bool:
    """Run cm_undo / cm_redo on the KLayout MainWindow if we can."""
    try:
        mw = pya.Application.instance().main_window()
    except Exception:
        mw = None
    if mw is None:
        return False
    try:
        fn = getattr(mw, action, None)
        if fn is None:
            return False
        fn()
        return True
    except Exception:
        return False


def _try_manager_action(ly: pya.Layout, action: str) -> bool:
    """Fallback path for headless tests: drive the Manager directly."""
    mgr, _ = _get_manager(ly)
    if mgr is None:
        return False
    try:
        if action == "cm_undo":
            mgr.undo()
        elif action == "cm_redo":
            mgr.redo()
        else:
            return False
        return True
    except Exception:
        return False


def _call_or_value(obj, name):
    """Get `obj.name`, invoking it if callable. Returns (value, err|None)."""
    if obj is None:
        return None, None
    try:
        v = getattr(obj, name, None)
    except Exception as e:
        return None, f"getattr: {e!r}"
    if v is None:
        return None, None
    if callable(v):
        try:
            return v(), None
        except Exception as e:
            return None, f"call: {e!r}"
    return v, None


def _get_manager(ly, debug_sink=None):
    """Find the undo/redo Manager. We try - in this order:
      1. pya.Layout.manager()
      2. pya.LayoutView.current().manager()
      3. pya.MainWindow.instance().manager()        <- this is the
         one that actually works in KLayout 0.30.
    On older builds (1) or (2) may succeed; that's why we still try
    them. A debug_sink dict, if supplied, receives a `_try_*` trace of
    every attempt for use by edit.status({'debug': True})."""
    def record(k, v):
        if debug_sink is not None:
            debug_sink[k] = v

    # 1. layout
    val, err = _call_or_value(ly, "manager")
    record("_try_layout_manager", repr(val) if err is None else err)
    if val is not None:
        return val, "layout"

    # 2. current view
    mw = view = None
    try:
        app = pya.Application.instance()
        mw = app.main_window() if app is not None else None
        view = mw.current_view() if mw is not None else None
    except Exception:
        pass
    val, err = _call_or_value(view, "manager")
    record("_try_view_manager", repr(val) if err is None else err)
    if val is not None:
        return val, "view"

    # 3. main window (the path that works on current KLayout)
    val, err = _call_or_value(mw, "manager")
    record("_try_mw_manager", repr(val) if err is None else err)
    if val is not None:
        return val, "mainwindow"

    return None, "none"


def _stack_depths(ly: pya.Layout, debug: bool = False) -> dict:
    """Report undo/redo availability + next-entry description.

    Keys in the returned dict:
      has_undo   (bool)  - is there anything the next edit.undo would touch?
      has_redo   (bool)  - likewise for edit.redo.
      undo_label (str)   - description of the transaction that edit.undo
                            would reverse, if has_undo is True.
      redo_label (str)   - likewise for edit.redo.
    With debug=True the reply also carries `_mgr_src`, per-attempt
    traces, and attribute dumps to help diagnose build differences."""
    out: dict = {}
    sink = out if debug else None
    mgr, src = _get_manager(ly, debug_sink=sink)

    if debug:
        out["_mgr_src"] = src
        out["_mgr_type"] = type(mgr).__name__ if mgr is not None else "None"
        try:
            app = pya.Application.instance()
            mw = app.main_window() if app is not None else None
            view = mw.current_view() if mw is not None else None
        except Exception:
            mw = view = None

        def _attrs(o, tag):
            if o is None:
                out[f"_attrs_{tag}"] = "<None>"
                return
            try:
                names = sorted(
                    a for a in dir(o)
                    if not a.startswith("_")
                    and ("undo" in a.lower() or "redo" in a.lower()
                         or "transaction" in a.lower()
                         or "manager" in a.lower()
                         or "available" in a.lower())
                )
                out[f"_attrs_{tag}"] = names
            except Exception as e:
                out[f"_attrs_{tag}"] = f"err: {e!r}"
        _attrs(ly, "layout")
        _attrs(view, "view")
        _attrs(mw, "mw")

    if mgr is None:
        return out

    for name, key in (("has_undo", "has_undo"), ("has_redo", "has_redo")):
        v, err = _call_or_value(mgr, name)
        if debug:
            out[f"_raw_{name}"] = repr(v) if err is None else err
        if v is None:
            continue
        if isinstance(v, (tuple, list)) and v:
            out[key] = bool(v[0])
        else:
            out[key] = bool(v)

    for name, key in (("transaction_for_undo", "undo_label"),
                      ("transaction_for_redo", "redo_label")):
        v, err = _call_or_value(mgr, name)
        if debug:
            out[f"_raw_{name}"] = repr(v) if err is None else err
        if not v:
            continue
        if isinstance(v, (tuple, list)) and len(v) >= 2 and v[1]:
            out[key] = str(v[1])
        elif isinstance(v, str):
            out[key] = v

    if debug:
        try:
            out["_mgr_attrs"] = sorted(
                a for a in dir(mgr)
                if not a.startswith("_")
                and ("undo" in a.lower() or "redo" in a.lower()
                     or "transaction" in a.lower()
                     or "available" in a.lower())
            )
        except Exception as e:
            out["_mgr_attrs_err"] = repr(e)

    return out


def _merge_custom_status(out: dict, custom: dict, *, include_debug: bool = False) -> dict:
    merged = dict(out)
    merged["has_undo"] = bool(merged.get("has_undo", False) or custom["has_undo"])
    merged["has_redo"] = bool(merged.get("has_redo", False) or custom["has_redo"])
    if not merged.get("undo_label") and custom["undo_label"]:
        merged["undo_label"] = custom["undo_label"]
    if not merged.get("redo_label") and custom["redo_label"]:
        merged["redo_label"] = custom["redo_label"]
    if include_debug:
        merged["custom"] = custom
    return merged


def _do_edit_action(rpc_name: str, action: str) -> dict:
    view, _, ly = _active_layout()
    _pump_events()
    before = _stack_depths(ly)
    custom_before = custom_status()

    if not before.get("has_undo" if action == "cm_undo" else "has_redo", False):
        ran_custom = custom_undo() if action == "cm_undo" else custom_redo()
        if ran_custom:
            _pump_events()
            try:
                if view is not None:
                    view.add_missing_layers()
            except Exception:
                pass
            try:
                if view is not None:
                    view.update_content()
            except Exception:
                pass
            return {
                "action": action.replace("cm_", ""),
                "path": "klink_custom",
                "before": _merge_custom_status(before, custom_before, include_debug=True),
                "after": _merge_custom_status(_stack_depths(ly), custom_status(), include_debug=True),
            }

    ok = _try_mainwindow_action(action)
    path = "mainwindow"
    if not ok:
        ok = _try_manager_action(ly, action)
        path = "manager"
    if not ok:
        raise RpcError(
            ErrorCode.EXEC,
            f"{rpc_name}: neither MainWindow nor Manager could run the action",
            hint="is the active layout still alive? try reopening the file",
        )

    _pump_events()
    after = _stack_depths(ly)

    try:
        if view is not None:
            view.add_missing_layers()
    except Exception:
        pass
    try:
        if view is not None:
            view.update_content()
    except Exception:
        pass

    return {
        "action": action.replace("cm_", ""),
        "path":   path,
        "before": before,
        "after":  after,
    }


@method(
    "edit.undo",
    description=(
        "Undo the most recent undoable operation (a klink mutating RPC, "
        "a Macro IDE edit, or a GUI edit). This is the programmatic "
        "equivalent of Edit > Undo. Returns before/after stack snapshots "
        "so callers can detect no-op undos. Non-undoable things (e.g. "
        "view zoom) are skipped automatically by KLayout."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "path":   {"type": "string"},
            "before": {"type": "object"},
            "after":  {"type": "object"},
        },
    },
    mutates=True,
    tags=["edit", "undo"],
)
def edit_undo(params, ctx):
    return _do_edit_action("edit.undo", "cm_undo")


@method(
    "edit.redo",
    description=(
        "Redo the most recently undone operation. Pair with edit.undo. "
        "Returns before/after stack snapshots so callers can verify the "
        "stack actually advanced."
    ),
    params_schema={"type": "object"},
    returns_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "path":   {"type": "string"},
            "before": {"type": "object"},
            "after":  {"type": "object"},
        },
    },
    mutates=True,
    tags=["edit", "undo"],
)
def edit_redo(params, ctx):
    return _do_edit_action("edit.redo", "cm_redo")


@method(
    "edit.status",
    description=(
        "Report current undo/redo availability. Use this to decide "
        "whether an edit.undo / edit.redo call would actually do "
        "anything. Pass debug=true to surface KLayout Manager "
        "introspection fields (useful when diagnosing a build where "
        "has_undo / has_redo come back absent)."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "debug": {"type": "boolean",
                       "description": "Include Manager-introspection "
                                       "fields in the reply."},
        },
    },
    returns_schema={
        "type": "object",
        "properties": {
            "has_undo":   {"type": "boolean"},
            "has_redo":   {"type": "boolean"},
            "undo_label": {"type": "string"},
            "redo_label": {"type": "string"},
        },
    },
    tags=["edit", "read"],
)
def edit_status(params, ctx):
    _, _, ly = _active_layout()
    _pump_events()
    debug = bool(params.get("debug", False)) if isinstance(params, dict) else False
    out = _stack_depths(ly, debug=debug)
    return _merge_custom_status(out, custom_status(), include_debug=debug)
