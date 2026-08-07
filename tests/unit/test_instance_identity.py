"""Stable instance identity (klink_id) — the issue-#13 regression suite.

Real-user bug (issue #13): ordinal assignment keyed on instance.query
order, which is KLayout's internal each_inst() iteration order — NOT the
insertion order, and NOT stable across save/reload. Two same-type
instances with different rotations swapped ordinals after a reload, so
the net table resolved a port through the wrong transform (0°/240°
instead of 0°/180°) and rerouting failed.

The fix stamps each imported instance with a ``klink_id`` user property
(GDS-safe integer property key) and makes the harvesters key identity on
it. These tests lock:

* stamped ids win regardless of query order (order-permutation invariant,
  including the exact rotation-swap from the user's report);
* layouts without stamped ids fall back to query-order ordinals WITH a
  RuntimeWarning, skipping ordinals already taken by stamped ids;
* duplicate stamped ids (GUI copy) raise an instructive ValueError.

Pure offline: no gdsfactory, no live KLayout.
"""
from __future__ import annotations

import pytest

from klink.domains.photonics.blackbox import assign_stable_prefixes
from klink.domains.photonics.gf_import import harvest_gf_template_ports


def _inst(child, kid=None, **trans):
    d = {"child": child, "trans": trans}
    if kid is not None:
        d["klink_id"] = kid
    return d


# --------------------------------------------------------------------------- #
# assign_stable_prefixes
# --------------------------------------------------------------------------- #

def test_stamped_prefixes_are_query_order_invariant():
    a = _inst("GFDEV_GC", "gc3", dx_dbu=425000, rotation_deg=60)
    b = _inst("GFDEV_GC", "gc4", dx_dbu=360000, rotation_deg=0)
    tags = {"GFDEV_GC": "gc"}
    fwd = dict((id(i), p) for i, p in assign_stable_prefixes([a, b], tags))
    rev = dict((id(i), p) for i, p in assign_stable_prefixes([b, a], tags))
    assert fwd == rev == {id(a): "gc3", id(b): "gc4"}


def test_legacy_fallback_counts_in_order_and_warns():
    a = _inst("GFDEV_GC")
    b = _inst("GFDEV_GC")
    with pytest.warns(RuntimeWarning, match="no stamped klink_id"):
        out = assign_stable_prefixes([a, b], {"GFDEV_GC": "gc"})
    assert [p for _, p in out] == ["gc0", "gc1"]


def test_mixed_legacy_skips_stamped_ordinals():
    stamped = _inst("GFDEV_GC", "gc1")
    with pytest.warns(RuntimeWarning):
        out = assign_stable_prefixes(
            [_inst("GFDEV_GC"), stamped, _inst("GFDEV_GC")],
            {"GFDEV_GC": "gc"})
    assert [p for _, p in out] == ["gc0", "gc1", "gc2"]
    assert out[1][0] is stamped


def test_duplicate_stamped_id_is_instructive():
    with pytest.raises(ValueError, match="duplicate klink_id 'gc0'"):
        assign_stable_prefixes(
            [_inst("GFDEV_GC", "gc0"), _inst("GFDEV_GC", "gc0")],
            {"GFDEV_GC": "gc"})


def test_untagged_children_are_skipped():
    out = assign_stable_prefixes(
        [_inst("OTHER", "x9"), _inst("GFDEV_GC", "gc0")],
        {"GFDEV_GC": "gc"})
    assert [(i["child"], p) for i, p in out] == [("GFDEV_GC", "gc0")]


# --------------------------------------------------------------------------- #
# harvest_gf_template_ports — the user's exact rotation-swap scenario
# --------------------------------------------------------------------------- #

class _SwapClient:
    """instance.query in 'reloaded spatial order': gc4 (rot 0) comes back
    BEFORE gc3 (rot 60) even though gc3 sorts first by name. Under the old
    ordinal rule this swapped their identities."""

    def __init__(self, order):
        self._order = order

    def layout_info(self):
        return {"dbu": 0.001}

    def call(self, method, params):
        assert method == "instance.query"
        gc3 = _inst("GFDEV_GC", "gc3", dx_dbu=425000, dy_dbu=-190000,
                    rotation_deg=60, mirror=False)
        gc4 = _inst("GFDEV_GC", "gc4", dx_dbu=360000, dy_dbu=30000,
                    rotation_deg=0, mirror=False)
        both = {"gc3": gc3, "gc4": gc4}
        return {"instances": [both[k] for k in self._order]}


TEMPLATES = {"GFDEV_GC": {"ports": [
    {"name": "o1", "center_um": [0.0, 0.0], "orientation": 180.0,
     "width_um": 0.5, "target_layer": "1/0"},
]}}


@pytest.mark.parametrize("order", [("gc3", "gc4"), ("gc4", "gc3")])
def test_harvest_binds_orientation_to_stamped_identity(order):
    marks = harvest_gf_template_ports(
        _SwapClient(order), "TOP", tags={"GFDEV_GC": "gc"},
        templates=TEMPLATES)
    by_name = {m["name"]: m["orientation"] for m in marks}
    # gc4 is the unrotated instance: its o1 must stay 180°, never 240°.
    assert by_name == {"gc3_o1": 240.0, "gc4_o1": 180.0}
