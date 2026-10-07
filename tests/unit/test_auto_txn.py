"""Transaction-mode and edit-disable regression tests without KLayout."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import patch


TXN_PATH = (
    Path(__file__).resolve().parents[2]
    / "klink_plugin/python/klink_server/txn.py"
)


class FakeView:
    def __init__(self, *, editable: bool, disabled_edits: int = 0):
        self.editable = editable
        self.disabled_edits = disabled_edits
        self.transactions = []
        self.commits = 0
        self.layer_refreshes = 0
        self.content_refreshes = 0

    def is_editable(self):
        return self.editable

    def enable_edits(self, enable):
        # KLayout counts disables independently of viewer/editable mode.
        if enable:
            self.disabled_edits = max(0, self.disabled_edits - 1)
        else:
            self.disabled_edits += 1

    def transaction(self, title):
        self.transactions.append(title)

    def commit(self):
        self.commits += 1

    def add_missing_layers(self):
        self.layer_refreshes += 1

    def update_content(self):
        self.content_refreshes += 1


class AutoTxnTests(unittest.TestCase):
    def setUp(self):
        # Load only the helper, avoiding the plugin's GUI initialization.
        spec = importlib.util.spec_from_file_location("_txn_under_test", TXN_PATH)
        self.txn = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"pya": types.SimpleNamespace(LayoutView=FakeView)}):
            spec.loader.exec_module(self.txn)

    def test_viewer_runs_body_without_opening_transaction(self):
        view = FakeView(editable=False)
        edits = []
        with self.txn.auto_txn(view, "viewer edit"):
            edits.append("applied")
        self.assertEqual(edits, ["applied"])
        self.assertEqual(view.transactions, [])
        self.assertEqual(view.commits, 0)
        self.assertEqual(view.layer_refreshes, 1)
        self.assertEqual(view.content_refreshes, 1)

    def test_repeated_nested_rpcs_preserve_edit_disable_guards(self):
        for editable in (False, True):
            for disabled_edits in (0, 1, 2):
                with self.subTest(editable=editable, disabled_edits=disabled_edits):
                    view = FakeView(editable=editable, disabled_edits=disabled_edits)
                    for _ in range(3):
                        with self.txn.auto_txn(view, "outer"):
                            with self.txn.auto_txn(view, "inner"):
                                self.assertEqual(view.disabled_edits, disabled_edits)
                        self.assertEqual(view.disabled_edits, disabled_edits)
                    self.assertEqual(view.is_editable(), editable)

    def test_viewer_body_failure_preserves_guards_and_refreshes(self):
        view = FakeView(editable=False, disabled_edits=2)
        with self.assertRaisesRegex(ValueError, "edit failed"):
            with self.txn.auto_txn(view, "failing edit"):
                raise ValueError("edit failed")
        self.assertEqual(view.disabled_edits, 2)
        self.assertEqual(view.transactions, [])
        self.assertEqual(view.commits, 0)
        self.assertEqual(view.layer_refreshes, 1)
        self.assertEqual(view.content_refreshes, 1)

    def test_nested_editor_uses_one_transaction_and_refresh(self):
        view = FakeView(editable=True)
        with self.txn.auto_txn(view, "outer"):
            with self.txn.auto_txn(view, "inner"):
                pass
        self.assertEqual(view.transactions, ["outer"])
        self.assertEqual(view.commits, 1)
        self.assertEqual(view.layer_refreshes, 1)
        self.assertEqual(view.content_refreshes, 1)

    def test_editor_body_failure_closes_transaction_for_next_rpc(self):
        view = FakeView(editable=True, disabled_edits=1)
        with self.assertRaisesRegex(ValueError, "edit failed"):
            with self.txn.auto_txn(view, "failing edit"):
                raise ValueError("edit failed")
        with self.txn.auto_txn(view, "next edit"):
            pass
        self.assertEqual(view.transactions, ["failing edit", "next edit"])
        self.assertEqual(view.commits, 2)
        self.assertEqual(view.disabled_edits, 1)
