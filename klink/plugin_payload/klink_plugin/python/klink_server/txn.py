"""
Shared transaction helper.

Every mutating RPC wraps its edit in `with auto_txn(view, title)` so KLayout's
native undo stack gets one "klink: <op>" entry per RPC. Internally this uses
the documented `LayoutView.transaction(title)` / `LayoutView.commit()` pair.
Ctrl+Z in the GUI (or `edit.undo` from the client) then reverts one RPC at
a time.

Nested auto_txn calls on the same view reuse the outermost transaction,
because KLayout's Manager rejects nested begin() calls.
"""

from __future__ import annotations

from contextlib import contextmanager

import pya


# view_id -> current nesting depth of auto_txn
_AUTO_DEPTH: dict = {}
_CUSTOM_UNDO: list = []
_CUSTOM_REDO: list = []


@contextmanager
def auto_txn(view: pya.LayoutView, title: str):
    """Wrap one RPC's worth of edits in a KLayout transaction titled
    `title`, so a single Ctrl+Z reverts the whole edit."""
    vid = id(view)
    depth = _AUTO_DEPTH.get(vid, 0)

    own = depth == 0
    if own:
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
