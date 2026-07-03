"""Unit tests for the PDK blackbox stub-port harvester (pure math + fakes)."""

from __future__ import annotations

import pytest

from klink.domains.photonics.blackbox import (
    _apply_trans,
    _apply_trans_angle,
    harvest_instance_ports,
    stub_template,
)

DBU = 0.001


class FakeClient:
    """Serves one blackbox child with two stubs (west + east) and instances."""

    def __init__(self, instances):
        self._instances = instances
        # Child bbox spans x in [-10000, 10000] dbu; body on layer_index 1,
        # stubs (500x500 dbu) on wg layer_index 0 at both x extremes plus a
        # facet-like tall box that must be filtered out.
        self._shapes = [
            {"type": "box", "layer_index": 1, "bbox_dbu": [-9500, -3000, 9500, 3000]},
            {"type": "box", "layer_index": 0, "bbox_dbu": [-10000, -250, -9500, 250]},
            {"type": "box", "layer_index": 0, "bbox_dbu": [9500, -250, 10000, 250]},
            {"type": "box", "layer_index": 0, "bbox_dbu": [9500, -5250, 10000, 5250]},
        ]

    def layer_list(self):
        return {"layers": [
            {"layer_index": 0, "layer": 1, "datatype": 0},
            {"layer_index": 1, "layer": 100, "datatype": 0},
        ]}

    def layout_info(self):
        return {"dbu": DBU}

    def call(self, method, params):
        if method == "shape.query":
            return {"shapes": list(self._shapes)}
        if method == "instance.query":
            return {"instances": list(self._instances)}
        raise AssertionError(f"unexpected RPC {method}")


def _trans(dx=0, dy=0, rot=0.0, mirror=False):
    return {"dx_dbu": dx, "dy_dbu": dy, "rotation_deg": rot,
            "mirror": mirror, "magnification": 1.0}


def test_apply_trans_rotation_and_mirror():
    # Point (10, 0) rotated 90 -> (0, 10), plus translation in um.
    assert _apply_trans([10000, 0], _trans(dx=5000, rot=90), DBU) == pytest.approx([5.0, 10.0])
    # Mirror flips y BEFORE rotation: (0, 10) -> (0, -10), rot 90 -> (10, 0).
    assert _apply_trans([0, 10000], _trans(rot=90, mirror=True), DBU) == pytest.approx([10.0, 0.0])


def test_legacy_blackbox_import_path_reexports_public_api():
    legacy = __import__("klink.port." + "blackbox", fromlist=["stub_template"])

    assert legacy.harvest_instance_ports is harvest_instance_ports
    assert legacy.stub_template is stub_template


def test_apply_trans_angle():
    assert _apply_trans_angle(0.0, _trans(rot=90)) == 90.0
    assert _apply_trans_angle(180.0, _trans(rot=270)) == 90.0
    # Mirror negates the angle before rotation.
    assert _apply_trans_angle(90.0, _trans(rot=0, mirror=True)) == 270.0


def test_stub_template_filters_and_orients():
    client = FakeClient(instances=[])
    stubs = stub_template(client, "BB", wg_layer="1/0", stub_size_um=0.5, dbu=DBU)
    # The 0.5x10.5 um facet box is rejected; two true stubs remain.
    assert len(stubs) == 2
    west, east = stubs  # sorted by x
    assert west["orientation"] == 180.0
    assert east["orientation"] == 0.0
    # Port anchor sits on the OUTER face of each stub.
    assert west["center_dbu"] == [-10000, 0.0]
    assert east["center_dbu"] == [10000, 0.0]


def test_harvest_identity_names_and_nets_survive_moves():
    instances = [
        {"child": "BB", "trans": _trans(dx=0, dy=0)},
        {"child": "BB", "trans": _trans(dx=100000, dy=0)},
        {"child": "OTHER", "trans": _trans()},  # untagged -> skipped
    ]
    client = FakeClient(instances)
    nets = {"BB0_1": "n1", "BB1_0": "n1"}
    marks = harvest_instance_ports(client, "TOP", tags={"BB": "BB"}, nets=nets, wg_layer="1/0", stub_size_um=0.5)
    names = [m["name"] for m in marks]
    assert names == ["BB0_0", "BB0_1", "BB1_0", "BB1_1"]
    by_name = {m["name"]: m for m in marks}
    assert by_name["BB0_1"]["net"] == "n1"
    assert by_name["BB1_0"]["net"] == "n1"
    assert by_name["BB0_0"]["net"] == ""

    # Drag instance 1 elsewhere: names (and therefore nets) are unchanged,
    # only coordinates move.
    instances[1]["trans"] = _trans(dx=250000, dy=80000, rot=180)
    marks2 = harvest_instance_ports(client, "TOP", tags={"BB": "BB"}, nets=nets, wg_layer="1/0", stub_size_um=0.5)
    assert [m["name"] for m in marks2] == names
    assert {m["name"]: m["net"] for m in marks2} == {m["name"]: m["net"] for m in marks}
    assert marks2[2]["center_um"] != marks[2]["center_um"]
