"""Failure contract of `auto_txn` (klink_plugin/python/klink_server/txn.py) — offline.

Bug this pins: `auto_txn` committed in `finally`, so an
RPC body that raised after a partial edit still landed that partial edit on
the layout as a "klink: <op>" undo entry while the client got an error; a
failing `view.transaction()` silently fell through to an un-undoable write;
a failing `commit()` was swallowed. Tools could not be retried safely.

Adopted contract (verified live on KLayout 0.30.7, see module docstring of
txn.py): transaction-open failure refuses the write (ERR_TXN_STATE, nothing
touched); body failure commits then undoes the entry it just made — only
when that entry is on top of the undo stack under this RPC's title, because
the Manager drops empty transactions — and reports `data.transaction`;
commit failure on the success path is surfaced as ERR_TXN_STATE with
`applied: True`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

pytest.importorskip("klayout.db", reason="klayout pip package not installed")

from klink_server import txn  # noqa: E402
from klink_server.errors import ErrorCode, RpcError  # noqa: E402


class FakeManager:
    """Undo stack with KLayout's observable semantics: a committed
    transaction that recorded nothing is dropped; undo moves the top
    entry to the redo stack."""

    def __init__(self):
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []

    def has_undo(self):
        return bool(self.undo_stack)

    def has_redo(self):
        return bool(self.redo_stack)

    def transaction_for_undo(self):
        return self.undo_stack[-1]

    def transaction_for_redo(self):
        return self.redo_stack[-1]

    def undo(self):
        self.redo_stack.append(self.undo_stack.pop())


class FakeView:
    """LayoutView stand-in whose `shapes` list is the "layout". Edits made
    inside an open transaction are journaled; commit() records a non-empty
    journal as an undo entry on the manager, undo() reverts it."""

    def __init__(self, manager: FakeManager | None, editable=True):
        self.manager = manager
        self.shapes: list[str] = []
        self.transactions: list[str] = []
        self.commits = 0
        self.refreshes = 0
        self._open: str | None = None
        self._journal: list[str] = []
        self.fail_transaction = False
        self.fail_commit = False
        self.is_editable = lambda: editable

    # -- what the RPC body does
    def insert(self, shape: str):
        assert self._open is not None, "edit outside a transaction"
        self.shapes.append(shape)
        self._journal.append(shape)

    # -- LayoutView API
    def transaction(self, title):
        if self.fail_transaction:
            raise RuntimeError("Internal error: transaction already open")
        self._open = title
        self._journal = []
        self.transactions.append(title)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("Internal error: m_opened was not true")
        if self._open is None:
            raise RuntimeError("Internal error: m_opened was not true")
        self.commits += 1
        if self._journal and self.manager is not None:
            self.manager.undo_stack.append(self._open)
            self.manager.redo_stack.clear()
            journal = list(self._journal)
            real_undo = self.manager.undo

            def undo_and_revert(_journal=journal, _prev=real_undo):
                _prev()
                for s in _journal:
                    self.shapes.remove(s)
                self.manager.undo = _prev
            self.manager.undo = undo_and_revert
        self._open = None
        self._journal = []

    def add_missing_layers(self):
        self.refreshes += 1

    def update_content(self):
        pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    txn.reset_for_reload()
    monkeypatch.setattr(txn, "_manager_for", lambda view: view.manager)
    monkeypatch.setattr(txn, "_schedule_diff", lambda: None)
    yield
    txn.reset_for_reload()


def test_partial_edit_is_rolled_back_and_reported():
    mgr = FakeManager()
    view = FakeView(mgr)
    with pytest.raises(RpcError) as info:
        with txn.auto_txn(view, "klink: shape.insert_many"):
            view.insert("box-1")
            view.insert("box-2")
            raise ValueError("item 3 has no layer")
    err = info.value
    # the layout is back to its pre-RPC state
    assert view.shapes == []
    # the transaction was closed exactly once; nothing is left open
    assert view.commits == 1 and view._open is None
    # the user's undo stack holds nothing of ours; the reverted edit is
    # available as redo until the next edit
    assert mgr.undo_stack == []
    assert mgr.redo_stack == ["klink: shape.insert_many"]
    # errors are instructions: the error says what happened
    assert err.code == ErrorCode.INTERNAL
    assert err.message == "ValueError: item 3 has no layer"
    assert err.data["transaction"]["partial_edit"] == "undone"
    assert err.data["transaction"]["redo_entry"] == "klink: shape.insert_many"
    assert isinstance(err.__cause__, ValueError)
    # retry is safe: the next RPC opens a fresh transaction
    with txn.auto_txn(view, "klink: shape.insert_many"):
        view.insert("box-1")
    assert view.shapes == ["box-1"]
    assert mgr.undo_stack == ["klink: shape.insert_many"]


def test_rollback_never_undoes_the_users_own_entry():
    # Body raised before changing anything -> the Manager dropped our
    # empty transaction -> the top undo entry is the USER's. It must stay.
    mgr = FakeManager()
    mgr.undo_stack.append("Move")  # the user's last interactive edit
    view = FakeView(mgr)
    with pytest.raises(RpcError) as info:
        with txn.auto_txn(view, "klink: cell.rename"):
            raise RpcError(ErrorCode.NOT_FOUND, "no cell 'X'")
    assert mgr.undo_stack == ["Move"]
    assert mgr.redo_stack == []
    err = info.value
    assert err.code == ErrorCode.NOT_FOUND          # original error kept
    assert err.data["transaction"]["partial_edit"] == "none"


def test_rpc_error_keeps_its_own_data():
    mgr = FakeManager()
    view = FakeView(mgr)
    with pytest.raises(RpcError) as info:
        with txn.auto_txn(view, "t"):
            view.insert("a")
            raise RpcError(ErrorCode.BAD_PARAMS, "bad", hint="h",
                           data={"field": "layer"})
    err = info.value
    assert err.data["field"] == "layer"
    assert err.data["transaction"]["partial_edit"] == "undone"
    assert err.hint == "h"
    assert view.shapes == []


def test_transaction_open_failure_refuses_write_without_fallback():
    view = FakeView(FakeManager())
    view.fail_transaction = True
    body_ran = False
    with pytest.raises(RpcError) as info:
        with txn.auto_txn(view, "klink: shape.insert_box"):
            body_ran = True
    assert body_ran is False
    err = info.value
    assert err.code == ErrorCode.TXN_STATE
    assert err.data["transaction"]["applied"] is False
    assert err.data["transaction"]["partial_edit"] == "none"
    assert "edit.status" in err.hint
    assert txn._AUTO_DEPTH.get(id(view), 0) == 0


def test_commit_failure_on_success_path_is_surfaced():
    view = FakeView(FakeManager())
    view.fail_commit = True
    with pytest.raises(RpcError) as info:
        with txn.auto_txn(view, "klink: layer.ensure"):
            view.insert("layer")
    err = info.value
    assert err.code == ErrorCode.TXN_STATE
    assert err.data["transaction"]["applied"] is True
    assert "edit.status" in err.hint
    # the view was still refreshed so the user sees the applied edit
    assert view.refreshes == 1
    assert txn._AUTO_DEPTH.get(id(view), 0) == 0


def test_manager_unreachable_reports_unknown_with_manual_instruction():
    view = FakeView(manager=None)
    with pytest.raises(RpcError) as info:
        with txn.auto_txn(view, "klink: shape.insert_box"):
            view.insert("a")
            raise RuntimeError("boom")
    tx = info.value.data["transaction"]
    assert tx["partial_edit"] == "unknown"
    assert "edit.undo" in tx["note"]
    assert view.commits == 1 and view._open is None


def test_nested_failure_rolls_back_once_at_the_outer_level():
    mgr = FakeManager()
    view = FakeView(mgr)
    with pytest.raises(RpcError):
        with txn.auto_txn(view, "outer"):
            view.insert("o")
            with txn.auto_txn(view, "inner"):
                view.insert("i")
                raise RuntimeError("inner failed")
    assert view.transactions == ["outer"]
    assert view.commits == 1
    assert view.shapes == []
    assert mgr.redo_stack == ["outer"]
    assert txn._AUTO_DEPTH.get(id(view), 0) == 0


def test_success_path_unchanged():
    mgr = FakeManager()
    view = FakeView(mgr)
    with txn.auto_txn(view, "klink: shape.insert_box"):
        view.insert("box")
    assert view.shapes == ["box"]
    assert view.commits == 1 and view.refreshes == 1
    assert mgr.undo_stack == ["klink: shape.insert_box"]
