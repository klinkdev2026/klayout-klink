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

Failure contract (what a caller may rely on when an RPC returns an error):

* `transaction()` could not be opened -> ERR_TXN_STATE, nothing was
  touched. There is no "write without undo" fallback.
* the RPC body raised after partially editing the layout -> the partial
  edit is rolled back before the error is returned. KLayout exposes no
  transaction abort, so the rollback is "commit, then undo the entry we
  just created". The undo Manager drops empty transactions (verified on
  0.30.7: a commit with no changes leaves `has_undo` False), so the undo
  only fires when the top undo entry carries this RPC's title; it never
  reverts something the user did. The reverted edit remains available as
  a redo entry until the next edit. The error's `data.transaction` block
  reports what happened (`partial_edit`: "none" | "undone" |
  "left_as_undo_entry" | "unknown").
* `commit()` itself failed on the success path -> ERR_TXN_STATE with
  `data.transaction.applied = True`; the edit is in the layout but its
  undo bookkeeping is not trustworthy. Never swallowed.
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


def _viewer_mode_error() -> RpcError:
    return RpcError(
        ErrorCode.VIEWER_MODE,
        "KLayout is in viewer mode (started without `-e`); write RPCs "
        "are refused because the edit could not be undone.",
        hint=VIEWER_MODE_NEXT_ACTION,
        data={"editable": False, "next_action": VIEWER_MODE_NEXT_ACTION},
    )


def require_editable(view) -> None:
    """Raise ERR_VIEWER_MODE when `view` is a viewer-mode view."""
    if view_is_editable(view) is False:
        raise _viewer_mode_error()


def app_is_editable() -> bool | None:
    """Process-level editability from `Application.instance().is_editable()`
    (the `-e` / `-ne` startup mode); None when unknown. Used where no view
    exists yet, e.g. before a write RPC would create its default tab."""
    try:
        app_cls = getattr(pya, "Application", None)
        app = app_cls.instance() if app_cls is not None else None
        probe = getattr(app, "is_editable", None) if app is not None else None
        if probe is None:
            return None
        return bool(probe())
    except Exception:
        return None


def require_editable_app() -> None:
    """Raise the same ERR_VIEWER_MODE as `require_editable` when the KLayout
    process runs in viewer mode. Unknown never blocks."""
    if app_is_editable() is False:
        raise _viewer_mode_error()


# ----------------------------------------------------------------------
# Transaction open / close / rollback primitives
# ----------------------------------------------------------------------

def _manager_for(view):
    """The undo Manager that owns `view`'s transactions, or None when it
    cannot be found. Uses the same lookup as edit.status so the two agree
    on what the "next undo" entry is. Tests monkeypatch this."""
    try:
        from .methods.edit_m import _get_manager
    except Exception:
        return None
    layout = None
    try:
        layout = view.active_cellview().layout()
    except Exception:
        layout = None
    try:
        mgr, _source = _get_manager(layout)
    except Exception:
        return None
    return mgr


def _open_transaction(view, title: str) -> None:
    """Begin the undo transaction or refuse the write. A write that could
    not be bracketed by a transaction would be a write without undo, which
    is exactly what auto_txn exists to prevent."""
    try:
        view.transaction(title)
    except Exception as exc:
        raise RpcError(
            ErrorCode.TXN_STATE,
            "could not open an undo transaction for this write (%s: %s); "
            "the call was refused and nothing was changed." % (
                type(exc).__name__, exc),
            hint="Call edit.status to inspect KLayout's undo state. If a "
                 "transaction is stuck open, finish or cancel the "
                 "interactive edit in KLayout (Esc) and retry; as a last "
                 "resort restart KLayout.",
            data={"transaction": {"title": title, "applied": False,
                                  "partial_edit": "none",
                                  "cause": repr(exc)}},
        ) from exc


def _rollback(view, title: str) -> dict:
    """Close the transaction opened for `title` after its body raised, and
    undo whatever it recorded. Returns the `transaction` report that is
    attached to the outgoing error."""
    info = {"title": title, "applied": False, "partial_edit": "unknown"}
    try:
        view.commit()
    except Exception as exc:
        # The transaction is not in a state we understand; say so rather
        # than guessing. Whatever the body did may still be in the layout.
        info["commit_error"] = repr(exc)
        info["partial_edit"] = "unknown"
        return info

    mgr = _manager_for(view)
    if mgr is None:
        info["partial_edit"] = "unknown"
        info["note"] = ("undo manager not reachable; if edit.status reports "
                        "undo_label == %r, call edit.undo to revert the "
                        "partial edit" % title)
        return info

    try:
        has_undo = bool(mgr.has_undo())
        top = mgr.transaction_for_undo() if has_undo else None
    except Exception as exc:
        info["partial_edit"] = "unknown"
        info["note"] = "undo manager query failed: %r" % (exc,)
        return info

    if top != title:
        # Empty transactions are dropped by the Manager, so "our title is
        # not on top" means the body changed nothing before it raised.
        info["partial_edit"] = "none"
        return info

    try:
        mgr.undo()
    except Exception as exc:
        info["partial_edit"] = "left_as_undo_entry"
        info["note"] = ("automatic undo failed (%r); the partial edit is "
                        "the top undo entry, call edit.undo or press "
                        "Ctrl+Z to revert it" % (exc,))
        return info

    info["partial_edit"] = "undone"
    info["redo_entry"] = title
    _refresh_view(view)
    return info


def _attach_transaction_info(exc: BaseException, info: dict) -> BaseException:
    """Return the exception to re-raise, carrying `info` under
    `data.transaction`. RpcErrors are annotated in place; anything else is
    wrapped into ERR_INTERNAL with the same message shape the dispatcher
    would have produced, so clients see one consistent format."""
    if isinstance(exc, RpcError):
        data = dict(exc.data) if isinstance(exc.data, dict) else {}
        data["transaction"] = info
        exc.data = data
        return exc
    wrapped = RpcError(
        ErrorCode.INTERNAL,
        "%s: %s" % (type(exc).__name__, exc),
        hint="check the KLayout console for the full stack trace",
        data={"transaction": info},
    )
    wrapped.__cause__ = exc
    return wrapped


def _refresh_view(view) -> None:
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


def _schedule_diff() -> None:
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


def _close_transaction(view, title: str) -> None:
    """Commit on the success path. A failing commit is surfaced, not
    swallowed: the edit is already in the layout, but its undo entry is
    not guaranteed, and the caller must know that."""
    try:
        view.commit()
    except Exception as exc:
        _refresh_view(view)
        _schedule_diff()
        raise RpcError(
            ErrorCode.TXN_STATE,
            "the edit was applied but its undo transaction could not be "
            "committed (%s: %s); Ctrl+Z may not revert it." % (
                type(exc).__name__, exc),
            hint="Call edit.status to inspect KLayout's undo state and "
                 "verify the result with a read RPC (shape.query, "
                 "layout.info) before continuing.",
            data={"transaction": {"title": title, "applied": True,
                                  "partial_edit": "none",
                                  "undo_entry": "unknown",
                                  "cause": repr(exc)}},
        ) from exc
    _refresh_view(view)
    _schedule_diff()


@contextmanager
def auto_txn(view: pya.LayoutView, title: str):
    """Wrap one RPC's worth of edits in a KLayout transaction titled
    `title`, so a single Ctrl+Z reverts the whole edit.

    Raises ERR_VIEWER_MODE (before touching the layout) when the view is
    not editable, ERR_TXN_STATE when no transaction could be opened, and
    rolls the partial edit back when the body raises — see the module
    docstring for the full failure contract."""
    vid = id(view)
    depth = _AUTO_DEPTH.get(vid, 0)
    own = depth == 0

    if own:
        require_editable(view)
        _open_transaction(view, title)

    _AUTO_DEPTH[vid] = depth + 1
    try:
        yield
    except BaseException as exc:
        _AUTO_DEPTH[vid] = depth
        if not own:
            raise
        info = _rollback(view, title)
        raise _attach_transaction_info(exc, info) from exc
    else:
        _AUTO_DEPTH[vid] = depth
        if own:
            _close_transaction(view, title)


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
