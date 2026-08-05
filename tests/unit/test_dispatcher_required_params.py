"""Offline unit tests for the dispatcher's required-params enforcement in
`klink_plugin/python/klink_server/dispatcher.py`.

Bug this pins: methods declare `required` in `params_schema`, but the
dispatcher never enforced it — a handler subscripting a missing required
param (e.g. `instance.query` without `parent`) raised KeyError, surfacing
as ERR_INTERNAL instead of an instructive BAD_PARAMS ("errors are
instructions"). The dispatcher now rejects the call before the handler
runs, naming every missing param and its schema description.

Imports the plugin's Python package directly, off-KLayout, following the
pattern of `tests/unit/test_view_zoom_units.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

pytest.importorskip("klayout.db", reason="klayout pip package not installed")

from klink_server import registry  # noqa: E402
from klink_server.dispatcher import Dispatcher  # noqa: E402
from klink_server.errors import ErrorCode  # noqa: E402


class FakeConn:
    conn_id = 1

    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)

    def send_event(self, *a, **kw):
        pass


@pytest.fixture()
def fresh_registry():
    saved = registry.all_specs()
    registry.reset_for_reload()
    yield
    registry.reset_for_reload()
    for spec in saved.values():
        registry._REGISTRY[spec.name] = spec


def _register(name, required, handler, properties=None):
    registry.method(
        name,
        params_schema={
            "type": "object",
            "required": required,
            "properties": properties or {},
        },
    )(handler)


def test_missing_required_param_is_instructive_bad_params(fresh_registry):
    def handler(params, ctx):  # would KeyError if reached
        return {"parent": params["parent"]}

    _register(
        "t.query", ["parent"], handler,
        properties={"parent": {"description": "parent cell (name or index)"}},
    )
    conn = FakeConn()
    Dispatcher().dispatch("r1", "t.query", {"limit": 3}, conn)

    resp = conn.sent[-1]
    err = resp["error"]
    assert err["code"] == ErrorCode.BAD_PARAMS
    assert "'parent'" in err["message"]
    assert "parent cell" in err["message"]  # description carried into the error
    assert "meta.methods" in err.get("hint", "")


def test_all_missing_params_named_at_once(fresh_registry):
    _register("t.two", ["a", "b"], lambda p, c: {"ok": True})
    conn = FakeConn()
    Dispatcher().dispatch("r2", "t.two", {}, conn)

    err = conn.sent[-1]["error"]
    assert err["code"] == ErrorCode.BAD_PARAMS
    assert "'a'" in err["message"] and "'b'" in err["message"]


def test_required_present_reaches_handler(fresh_registry):
    _register("t.ok", ["parent"], lambda p, c: {"got": p["parent"]})
    conn = FakeConn()
    Dispatcher().dispatch("r3", "t.ok", {"parent": "TOP"}, conn)

    resp = conn.sent[-1]
    assert "error" not in resp
    assert resp["result"] == {"got": "TOP"}


def test_no_schema_methods_still_dispatch(fresh_registry):
    registry.method("t.free")(lambda p, c: {"ok": True})
    conn = FakeConn()
    Dispatcher().dispatch("r4", "t.free", {}, conn)

    assert conn.sent[-1]["result"] == {"ok": True}
