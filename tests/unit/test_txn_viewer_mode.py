"""Viewer-mode guard in `klink_plugin/python/klink_server/txn.py` — offline.

Bug this pins (public PR #19): KLayout started without `-e` runs in viewer
mode, where layouts are non-editable containers. `auto_txn` opened an undo
transaction anyway and the first `Shapes.insert` died with the opaque
"No undo/redo support on non-editable shape lists". The contributor's fix
skipped the transaction there, which would have let an agent edit the
user's layout with nothing to undo. The adopted contract is the opposite:
the outermost `auto_txn` refuses with ERR_VIEWER_MODE and an instructive
`next_action` BEFORE touching the layout; editor mode is byte-for-byte the
old behaviour; an unknown answer never blocks.

Imports the plugin package directly, off-KLayout (needs the `klayout` pip
package for `import pya`), following tests/unit/test_dispatcher_required_params.py.
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


class FakeView:
    """Just enough LayoutView for auto_txn: transaction/commit bookkeeping
    plus the editable answer. `editable=None` models a build without
    `is_editable` (the attribute is absent)."""

    def __init__(self, editable):
        self.transactions: list[str] = []
        self.commits = 0
        self.refreshes = 0
        if editable is None:
            return
        self._editable = editable
        self.is_editable = lambda: self._editable

    def transaction(self, title):
        self.transactions.append(title)

    def commit(self):
        self.commits += 1

    def add_missing_layers(self):
        self.refreshes += 1

    def update_content(self):
        pass


@pytest.fixture(autouse=True)
def _clean_depth():
    txn.reset_for_reload()
    yield
    txn.reset_for_reload()


def test_viewer_mode_is_refused_before_any_edit():
    view = FakeView(editable=False)
    body_ran = False
    with pytest.raises(RpcError) as info:
        with txn.auto_txn(view, "klink: shape.insert_box"):
            body_ran = True
    err = info.value
    assert err.code == ErrorCode.VIEWER_MODE == "ERR_VIEWER_MODE"
    assert body_ran is False, "the edit body must not run in viewer mode"
    assert view.transactions == [] and view.commits == 0
    # errors are instructions: the fix is named in hint AND data.next_action
    assert "klayout -e" in err.hint
    assert err.data["editable"] is False
    assert "klayout -e" in err.data["next_action"]
    assert "klink.reconnect" in err.data["next_action"]
    # nesting bookkeeping is left clean for the next RPC
    assert txn._AUTO_DEPTH.get(id(view), 0) == 0


def test_editor_mode_unchanged_one_transaction_per_rpc():
    view = FakeView(editable=True)
    with txn.auto_txn(view, "outer"):
        with txn.auto_txn(view, "inner"):
            pass
    assert view.transactions == ["outer"]
    assert view.commits == 1
    assert view.refreshes == 1


def test_unknown_editable_never_blocks():
    # A LayoutView without is_editable (or one whose probe raises) must
    # behave exactly like editor mode: refusing on "unknown" would turn a
    # harmless API gap into a total write outage.
    view = FakeView(editable=None)
    with txn.auto_txn(view, "t"):
        pass
    assert view.transactions == ["t"] and view.commits == 1

    raising = FakeView(editable=True)
    raising.is_editable = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    with txn.auto_txn(raising, "t2"):
        pass
    assert raising.transactions == ["t2"]


def test_view_is_editable_tristate():
    assert txn.view_is_editable(None) is None
    assert txn.view_is_editable(FakeView(editable=None)) is None
    assert txn.view_is_editable(FakeView(editable=True)) is True
    assert txn.view_is_editable(FakeView(editable=False)) is False


def test_viewer_refusal_is_repeatable_across_rpcs():
    # The guard raises at __enter__ before depth is incremented, so a
    # refused RPC must not poison the next one (depth stuck at 1 would
    # silently skip the guard AND the transaction).
    view = FakeView(editable=False)
    for _ in range(3):
        with pytest.raises(RpcError):
            with txn.auto_txn(view, "again"):
                pass
    view._editable = True
    with txn.auto_txn(view, "now editable"):
        pass
    assert view.transactions == ["now editable"]
