"""
Shared transaction helper.

Every mutating RPC wraps its edit in `with auto_txn(view, title)` so KLayout's
native undo stack gets one "klink: <op>" entry per RPC. Internally this uses
the documented `LayoutView.transaction(title)` / `LayoutView.commit()` pair.
Ctrl+Z in the GUI (or `edit.undo` from the client) then reverts one RPC at
a time.

Nested auto_txn calls on the same view reuse the outermost transaction,
because KLayout's Manager rejects nested begin() calls.

Viewer mode is refused, not worked around. When KLayout was started
without `-e` (`LayoutView.is_editable()` is False) its layouts are
non-editable containers: `Shapes.insert` inside a transaction raises
"No undo/redo support on non-editable shape lists", and erase/transform
are not permitted there at all. Skipping the transaction would let an
agent change the user's layout with no Ctrl+Z to take it back, which is
the one thing this helper exists to prevent. So the outermost auto_txn
raises ERR_VIEWER_MODE with the restart instruction instead (reported
by contributor PR #19 on the public repo).
"""

from __future__ import annotations

from contextlib import contextmanager

import pya

from .errors import ErrorCode, RpcError


# view_id -> current nesting depth of auto_txn
_AUTO_DEPTH: dict = {}
_CUSTOM_UNDO: list = []
_CUSTOM_REDO: list = []

VIEWER_MODE_NEXT_ACTION = (
    "KLayout is running in viewer mode, so layouts cannot be edited and "
    "nothing could be undone. Close KLayout and start it in editor mode: "
    "run `klayout -e` (or enable File > Setup > Application > Editing Mode "
    "> 'Use editing mode by default', then restart), then call "
    "klink.reconnect and retry this call. Read-only tools keep working."
)


def view_is_editable(view) -> bool | None:
    """True/False from `LayoutView.is_editable()`; None when unknown
    (no view, or a build without the query). Unknown never blocks."""
    if view is None:
        return None
    probe = getattr(view, "is_editable", None)
    if probe is None:
        return None
    try:
        return bool(probe())
    except Exception:
        return None


def require_editable(view) -> None:
    """Raise ERR_VIEWER_MODE when `view` is a viewer-mode view."""
    if view_is_editable(view) is False:
        raise RpcError(
            ErrorCode.VIEWER_MODE,
            "KLayout is in viewer mode (started without `-e`); write RPCs "
            "are refused because the edit could not be undone.",
            hint=VIEWER_MODE_NEXT_ACTION,
            data={"editable": False, "next_action": VIEWER_MODE_NEXT_ACTION},
        )


@contextmanager
def auto_txn(view: pya.LayoutView, title: str):
    """Wrap one RPC's worth of edits in a KLayout transaction titled
    `title`, so a single Ctrl+Z reverts the whole edit.

    Raises ERR_VIEWER_MODE (before touching the layout) when the view is
    not editable — see the module docstring."""
    vid = id(view)
    depth = _AUTO_DEPTH.get(vid, 0)

    own = depth == 0
    if own:
        require_editable(view)
        try:
            view.transaction(title)
        except Exception:
            own = False

    _AUTO_DEPTH[vid] = depth + 1
    try:
        yield
    finally:
        _AUTO_DEPTH[vid] = _AUTO_DEPTH.get(vid, 1) - 1
        if own:
            try:
                view.commit()
            except Exception:
                pass
            # Keep the layer panel in sync with the Layout. Without this,
            # layers created by layer.ensure / shape.insert_* exist in
            # the GDS but aren't rendered until the user manually
            # triggers a repaint.
            try:
                view.add_missing_layers()
            except Exception:
                pass
            try:
                view.update_content()
            except Exception:
                pass
            # Explicitly nudge the SignalHub so this RPC gets credited
            # in caused_by, even when the mutation doesn't organically
            # trigger KLayout's on_layer_list_changed (e.g. inserting
            # a shape on an already-known layer fires no view-level
            # event at all in 0.30.x). Still cheap: the debounce timer
            # coalesces this with any organic ticks.
            try:
                from .server import instance as _srv_instance
                srv = _srv_instance()
                hub = getattr(srv, "signals", None) if srv is not None else None
                if hub is not None:
                    hub._schedule_diff(source="auto_txn")
            except Exception:
                pass


def reset_for_reload() -> None:
    _AUTO_DEPTH.clear()
    _CUSTOM_UNDO.clear()
    _CUSTOM_REDO.clear()


def register_custom_edit(label: str, undo_fn, redo_fn) -> None:
    _CUSTOM_UNDO.append({"label": label, "undo": undo_fn, "redo": redo_fn})
    _CUSTOM_REDO.clear()


def custom_status() -> dict:
    return {
        "has_undo": bool(_CUSTOM_UNDO),
        "has_redo": bool(_CUSTOM_REDO),
        "undo_label": _CUSTOM_UNDO[-1]["label"] if _CUSTOM_UNDO else "",
        "redo_label": _CUSTOM_REDO[-1]["label"] if _CUSTOM_REDO else "",
    }


def custom_undo() -> bool:
    while _CUSTOM_UNDO:
        entry = _CUSTOM_UNDO.pop()
        result = entry["undo"]()
        if result is False:
            continue
        _CUSTOM_REDO.append(entry)
        return True
    return False


def custom_redo() -> bool:
    while _CUSTOM_REDO:
        entry = _CUSTOM_REDO.pop()
        result = entry["redo"]()
        if result is False:
            continue
        _CUSTOM_UNDO.append(entry)
        return True
    return False
