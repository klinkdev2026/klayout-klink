"""Offline tests for KLinkClient's self-describing surface.

A blind test showed an agent abandoning klink mid-task because it could not
work out what to call: it tried `view.new_tab()` (the RPC name), assumed
`cell_create()` returned a 'cell' key, and passed positional args to a
keyword-only wrapper. The errors it got back named none of those things, so
it fell back to hand-rolled analysis -- the very outcome the routing rules
exist to prevent. Discoverability is therefore a correctness feature here.
"""
from __future__ import annotations

import pytest

from klink.client import KLinkClient


SPECS = {"methods": [
    {"name": "view.new_tab",
     "description": "Open a new empty layout tab.",
     "params": {"required": ["cell_name"],
                "properties": {"cell_name": {"type": "string",
                                             "description": "top cell"},
                               "dbu": {"type": "number"}}},
     "returns": {"properties": {"index": {}, "previous_current_index": {}}}},
    {"name": "shape.query",
     "params": {"required": ["cell"],
                "properties": {"cell": {"type": "string"},
                               "layers": {"type": "array"}}},
     "returns": {"properties": {"shapes": {}, "truncated": {}}}},
    {"name": "cell.create",
     "params": {"properties": {"name": {"type": "string"}}},
     "returns": {"properties": {"name": {}, "renamed": {}}}},
]}


class FakeClient(KLinkClient):
    """Real class, faked transport: records calls, serves the schema."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def call(self, method, params=None, timeout=None):
        self.sent.append((method, params or {}))
        if method == "meta.methods":
            return SPECS
        return {"ok": True, "echo": method}


def test_rpc_name_works_as_a_method():
    c = FakeClient()
    out = c.view_new_tab(cell_name="TOP")           # the RPC name, not new_tab
    assert out["echo"] == "view.new_tab"
    assert ("view.new_tab", {"cell_name": "TOP"}) in c.sent


def test_hand_written_wrapper_still_wins():
    c = FakeClient()
    c.cell_create("X")                              # real wrapper, positional
    assert ("cell.create", {"name": "X"}) in c.sent


def test_unknown_attribute_names_the_alternatives():
    c = FakeClient()
    with pytest.raises(AttributeError) as e:
        c.shape_qeury
    msg = str(e.value)
    assert "shape_query" in msg or "shape.query" in msg
    assert "help(" in msg                           # points at the next step


def test_help_reports_params_and_result_keys():
    c = FakeClient()
    text = c.help("shape.query")
    assert "cell" in text and "(required)" in text
    assert "returns:" in text and "truncated" in text


def test_help_accepts_the_underscore_spelling_too():
    c = FakeClient()
    assert c.help("shape_query") == c.help("shape.query")


def test_help_answers_the_return_key_question():
    # the agent assumed cell_create() returned 'cell'; it returns 'name'
    c = FakeClient()
    text = c.help("cell.create")
    assert "name" in text.split("returns:")[1]


def test_help_lists_candidates():
    c = FakeClient()
    listing = c.help(contains="cell")
    assert "cell.create" in listing and "view.new_tab" not in listing


def test_private_attributes_are_not_hijacked():
    c = FakeClient()
    with pytest.raises(AttributeError):
        c._not_a_thing


def test_schema_is_fetched_once():
    c = FakeClient()
    c.help("shape.query")
    c.help("cell.create")
    assert sum(1 for m, _ in c.sent if m == "meta.methods") == 1
