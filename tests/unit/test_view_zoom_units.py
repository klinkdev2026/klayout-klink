"""Offline unit tests for the view.zoom_box / view.viewport unit-conversion
fix in `klink_plugin/python/klink_server/methods/view_m.py`.

Bug this pins: `pya.LayoutView.zoom_box()` and `.box()` are DBox APIs
(microns). The old handler built an integer `pya.Box` straight from
`bbox_dbu` and passed it to `zoom_box()`; the implicit Box -> DBox
conversion reinterprets the raw dbu integers as microns WITHOUT dividing
by dbu, so a real dbu value silently mis-scaled the viewport by ~1/dbu
(e.g. 1000x too large when dbu=0.001).

These tests exercise the extracted pure conversion helpers directly (no
live KLayout view/window needed) using the offline `pya` compat module
that ships with `pip install klayout`, following the same "import the
plugin's Python package directly, off-KLayout" pattern used by
`tests/unit/test_plugin_transfer_pending.py` and
`tests/unit/test_klive_compat.py`.
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
from klink_server.methods import view_m  # noqa: E402
from klink_server.methods.view_m import (  # noqa: E402
    _dbox_from_real_dbu,
    _dbox_from_um,
    _real_dbu_from_dbox,
    _resolve_zoom_target,
)


# ----------------------------------------------------------------------
# bbox_um path
# ----------------------------------------------------------------------

def test_dbox_from_um_builds_micron_box_directly():
    dbox = _dbox_from_um([1.5, -2.0, 10.0, 4.25])
    assert (dbox.left, dbox.bottom, dbox.right, dbox.top) == (1.5, -2.0, 10.0, 4.25)


def test_dbox_from_um_rejects_wrong_shape():
    with pytest.raises(RpcError) as excinfo:
        _dbox_from_um([1, 2, 3])
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "bbox_um" in excinfo.value.message


def test_dbox_from_um_rejects_non_numeric():
    with pytest.raises(RpcError) as excinfo:
        _dbox_from_um([0, 0, "x", 1])
    assert excinfo.value.code == ErrorCode.BAD_PARAMS


# ----------------------------------------------------------------------
# bbox_dbu path (REAL integer database units)
# ----------------------------------------------------------------------

def test_dbox_from_real_dbu_converts_through_layout_dbu():
    # This is the exact regression case from the bug report: dbu=0.001,
    # a real dbu box of [5000, 0, 10000, 5000] must become the 5x5 um box
    # [5, 0, 10, 5], NOT be reinterpreted as microns directly.
    dbox = _dbox_from_real_dbu([5000, 0, 10000, 5000], 0.001)
    assert (dbox.left, dbox.bottom, dbox.right, dbox.top) == (5.0, 0.0, 10.0, 5.0)


def test_dbox_from_real_dbu_with_non_trivial_dbu():
    dbox = _dbox_from_real_dbu([0, 0, 100, 200], 0.005)
    assert (dbox.left, dbox.bottom, dbox.right, dbox.top) == (0.0, 0.0, 0.5, 1.0)


def test_dbox_from_real_dbu_rejects_wrong_shape():
    with pytest.raises(RpcError) as excinfo:
        _dbox_from_real_dbu([1, 2, 3, 4, 5], 0.001)
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "bbox_dbu" in excinfo.value.message


def test_dbox_from_real_dbu_truncates_like_the_shape_family_int_cast():
    # bbox_dbu is documented as integer database units; a fractional input
    # truncates via int(), matching shape_m._box_from_param's convention
    # for the same field name elsewhere in klink (not re-validated here).
    dbox = _dbox_from_real_dbu([0, 0, 1.9, 1], 0.001)
    assert dbox.right == pytest.approx(0.001)


# ----------------------------------------------------------------------
# _resolve_zoom_target: param precedence / mutual exclusivity
# ----------------------------------------------------------------------

def test_resolve_zoom_target_prefers_um_path():
    dbox = _resolve_zoom_target({"bbox_um": [0, 0, 10, 5]}, dbu=0.001)
    assert (dbox.left, dbox.bottom, dbox.right, dbox.top) == (0.0, 0.0, 10.0, 5.0)


def test_resolve_zoom_target_dbu_path_matches_equivalent_um_path():
    dbu = 0.001
    from_um = _resolve_zoom_target({"bbox_um": [0.0, 0.0, 20.0, 10.0]}, dbu)
    from_dbu = _resolve_zoom_target({"bbox_dbu": [0, 0, 20000, 10000]}, dbu)
    assert (from_um.left, from_um.bottom, from_um.right, from_um.top) == (
        from_dbu.left, from_dbu.bottom, from_dbu.right, from_dbu.top,
    )


def test_resolve_zoom_target_rejects_both_params():
    with pytest.raises(RpcError) as excinfo:
        _resolve_zoom_target(
            {"bbox_um": [0, 0, 1, 1], "bbox_dbu": [0, 0, 1000, 1000]},
            dbu=0.001,
        )
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "exactly one" in excinfo.value.message
    assert "bbox_um" in (excinfo.value.hint or "")


def test_resolve_zoom_target_rejects_neither_param():
    with pytest.raises(RpcError) as excinfo:
        _resolve_zoom_target({}, dbu=0.001)
    assert excinfo.value.code == ErrorCode.BAD_PARAMS
    assert "bbox_um" in excinfo.value.message or "bbox_dbu" in excinfo.value.message
    assert excinfo.value.hint  # an example, not a bare rejection


# ----------------------------------------------------------------------
# view.viewport's real-dbu reporting
# ----------------------------------------------------------------------

def test_real_dbu_from_dbox_round_trips_through_dbu():
    dbox = _dbox_from_um([0.0, 0.0, 5.0, 2.5])
    assert _real_dbu_from_dbox(dbox, 0.001) == [0, 0, 5000, 2500]


def test_real_dbu_from_dbox_is_not_a_relabelled_um_copy():
    # The pre-fix bug reported bbox_dbu == bbox_um verbatim (mislabelled).
    # Any non-1.0 dbu must produce a numerically different list.
    dbox = _dbox_from_um([0.0, 0.0, 5.0, 2.5])
    dbu_values = _real_dbu_from_dbox(dbox, 0.001)
    um_values = [dbox.left, dbox.bottom, dbox.right, dbox.top]
    assert dbu_values != um_values


def test_resolve_optional_bbox_none_when_absent():
    assert view_m._resolve_optional_bbox({}, 0.001) is None


def test_resolve_optional_bbox_um_path():
    box = view_m._resolve_optional_bbox({"bbox_um": [0, 0, 10, 5]}, 0.001)
    assert (box.left, box.bottom, box.right, box.top) == (0, 0, 10, 5)


def test_resolve_optional_bbox_real_dbu_path():
    box = view_m._resolve_optional_bbox({"bbox_dbu": [0, 0, 10000, 5000]}, 0.001)
    assert (box.left, box.bottom, box.right, box.top) == (0, 0, 10, 5)


def test_resolve_optional_bbox_rejects_both():
    with pytest.raises(Exception, match="at most one"):
        view_m._resolve_optional_bbox(
            {"bbox_um": [0, 0, 1, 1], "bbox_dbu": [0, 0, 1000, 1000]}, 0.001
        )


# ----------------------------------------------------------------------
# view.hier_levels validation (pure helper)

def test_hier_levels_read_only_when_no_params():
    assert view_m._validate_hier_levels({}, 0, 1) == (0, 1, False)


def test_hier_levels_set_max_keeps_current_min():
    assert view_m._validate_hier_levels({"max": 4}, 0, 1) == (0, 4, True)


def test_hier_levels_set_both():
    assert view_m._validate_hier_levels({"min": 1, "max": 3}, 0, 1) == (1, 3, True)


def test_hier_levels_rejects_min_above_max():
    with pytest.raises(Exception, match="must be <= max"):
        view_m._validate_hier_levels({"min": 5}, 0, 1)


def test_hier_levels_rejects_non_integer_and_negative():
    with pytest.raises(Exception, match="must be an integer"):
        view_m._validate_hier_levels({"max": 1.5}, 0, 1)
    with pytest.raises(Exception, match=">= 0"):
        view_m._validate_hier_levels({"max": -1}, 0, 1)
    with pytest.raises(Exception, match="must be an integer"):
        view_m._validate_hier_levels({"max": True}, 0, 1)
