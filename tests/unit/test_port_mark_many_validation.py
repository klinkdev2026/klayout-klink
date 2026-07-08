"""Offline unit tests for port.mark_many's validate-before-mutate pass.

`_validate_port_mark_many_items` (klink_plugin/python/klink_server/methods/
port_m.py) is a pure function: it never touches a pya.Layout/Cell, so it can
be exercised directly, off-KLayout, following the same "import the plugin's
Python package directly" pattern used by `tests/unit/test_view_zoom_units.py`
and `tests/unit/test_plugin_transfer_pending.py`.

It is the ONLY thing standing between a batch call and actually inserting
ports, so the invariant under test is: any single invalid item must reject
the WHOLE batch (naming the offending index) before anything is merged/
returned, and duplicate names -- whether within the batch or already present
in the cell -- must never slip through.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_PYTHON = Path(__file__).resolve().parents[2] / "klink_plugin" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

# klink_server imports pya at package level; the offline compat ships with
# `pip install klayout`. Gate on klayout.db (not "pya"): another test module
# may plant a minimal fake pya in sys.modules, which must not un-skip us.
pytest.importorskip("klayout.db", reason="klayout pip package not installed")

from klink_server.errors import ErrorCode, RpcError  # noqa: E402
from klink_server.methods.port_m import (  # noqa: E402
    _MAX_BATCH_PORTS,
    _validate_port_mark_many_items,
)


def _item(**kw):
    base = {"center_um": [0.0, 0.0]}
    base.update(kw)
    return base


# ----------------------------------------------------------------------
# items shape
# ----------------------------------------------------------------------

def test_rejects_non_list_items():
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items({"not": "a list"}, {}, set())
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "items must be a list" in excinfo.value.message


def test_rejects_empty_items():
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items([], {}, set())
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "must not be empty" in excinfo.value.message


def test_rejects_oversized_batch():
    items = [_item() for _ in range(_MAX_BATCH_PORTS + 1)]
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items(items, {}, set())
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "too large" in excinfo.value.message


def test_rejects_non_object_item_names_index():
    items = [_item(), "not an object"]
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items(items, {}, set())
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "items[1]" in excinfo.value.message


# ----------------------------------------------------------------------
# center_um / center_dbu presence + shape
# ----------------------------------------------------------------------

def test_rejects_item_missing_center_names_index():
    items = [_item(), {"name": "P1"}]
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items(items, {}, set())
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "items[1]" in excinfo.value.message
    assert "center_um" in excinfo.value.message or "center_dbu" in excinfo.value.message


def test_rejects_malformed_center_um_shape():
    items = [{"center_um": [1, 2, 3]}]
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items(items, {}, set())
    assert "items[0]" in excinfo.value.message
    assert "center_um" in excinfo.value.message


def test_rejects_malformed_center_dbu_shape():
    items = [{"center_dbu": [1]}]
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items(items, {}, set())
    assert "items[0]" in excinfo.value.message
    assert "center_dbu" in excinfo.value.message


def test_accepts_center_dbu_in_place_of_center_um():
    resolved = _validate_port_mark_many_items(
        [{"center_dbu": [1000, 2000]}], {}, set(),
    )
    assert resolved[0]["center_dbu"] == [1000, 2000]


# ----------------------------------------------------------------------
# name uniqueness (the whole point of validate-before-mutate here)
# ----------------------------------------------------------------------

def test_rejects_duplicate_explicit_name_within_batch():
    items = [_item(name="P0"), _item(name="P1"), _item(name="P0")]
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items(items, {}, set())
    assert "items[2]" in excinfo.value.message
    assert "P0" in excinfo.value.message


def test_rejects_explicit_name_already_in_cell():
    items = [_item(name="EXISTING")]
    with pytest.raises(RpcError) as excinfo:
        _validate_port_mark_many_items(items, {}, {"EXISTING", "OTHER"})
    assert "items[0]" in excinfo.value.message
    assert "EXISTING" in excinfo.value.message


def test_allows_omitted_names_even_when_repeated_absence():
    # Multiple items with no explicit name at all is fine here -- auto
    # naming happens later, at mutate time, in insertion order.
    resolved = _validate_port_mark_many_items(
        [_item(), _item(), _item()], {}, set(),
    )
    assert len(resolved) == 3
    assert all("name" not in r for r in resolved)


def test_distinct_explicit_names_all_pass():
    items = [_item(name="P0"), _item(name="P1"), _item(name="P2")]
    resolved = _validate_port_mark_many_items(items, {}, set())
    assert [r["name"] for r in resolved] == ["P0", "P1", "P2"]


# ----------------------------------------------------------------------
# defaults merging (item value always wins over a top-level default)
# ----------------------------------------------------------------------

def test_top_level_defaults_are_merged_into_items_missing_the_field():
    defaults = {"orientation": 90, "width_um": 3.0, "net": "shared"}
    items = [_item(), _item(orientation=0)]
    resolved = _validate_port_mark_many_items(items, defaults, set())

    # item[0] omitted orientation -> inherits the default.
    assert resolved[0]["orientation"] == 90
    assert resolved[0]["width_um"] == 3.0
    assert resolved[0]["net"] == "shared"

    # item[1] set its own orientation -> its value wins over the default.
    assert resolved[1]["orientation"] == 0
    assert resolved[1]["width_um"] == 3.0


def test_first_invalid_item_rejects_the_whole_batch_not_a_partial_result():
    # items[0] is valid, items[1] is invalid (no center) -- the function
    # must raise, not return a partial list containing items[0].
    items = [_item(name="OK"), {"name": "BAD"}]
    with pytest.raises(RpcError):
        _validate_port_mark_many_items(items, {}, set())
