"""klink-planned OPTICAL detours must round every 90-deg corner into an
euler (clothoid-pair) bend; ELECTRICAL/pad detours must stay sharp
Manhattan. This is a user ruling (optical routing is never right-angle),
enforced at the one native drawing landing spot inside
``_harvest_and_route`` that calls ``shape.insert_path`` directly (klink's
own visibility-router detour path -- gf-routed nets already get gf's own
euler bend=euler default and are out of scope here).

Pure offline: the gf bridge call and the harvest are both replaced; no
gdsfactory, no live KLayout. The bend geometry itself is
``klink.routing.geom.bends`` (tested for its own math elsewhere via
``max_turn_deg``/``euler_setback_ratio``); this file only locks the
net_intent.py INTEGRATION: which nets get bent, and which don't.
"""
from __future__ import annotations

import math

import klink.domains.photonics.net_intent as ni
from klink.domains.photonics.net_intent import NetTable, RouteStyle
from klink.routing.geom.bends import max_turn_deg


class FakeClient:
    """One child cell 'CHILD' with a local 4x4um footprint on 10/0."""

    def __init__(self, instances=None):
        self.instances = instances or []
        self.calls = []

    def layout_info(self):
        return {"dbu": 0.001}

    def layer_list(self):
        return {"layers": [{"layer": 10, "datatype": 0, "layer_index": 0}]}

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "instance.query":
            return {"instances": self.instances}
        if method == "shape.query":
            return {"shapes": [
                {"layer_index": 0, "bbox_dbu": [0, 0, 4000, 4000]},
            ]}
        return {"ok": True}


def _device_at(x_um, y_um):
    return {"child": "CHILD",
            "trans": {"dx_dbu": int(x_um * 1000), "dy_dbu": int(y_um * 1000),
                      "rotation_deg": 0, "mirror": False}}


def _run_two_nets(monkeypatch):
    """One electrical net (T0/T1, y=0) and one optical net (T2/T3, y=20),
    each blocked by its OWN foreign obstacle and forced through klink's
    native detour path. Dummy far-away devices (owner T0..T3) keep the real
    obstacles' owners (T4/T5) foreign to BOTH nets."""
    table = NetTable(cell="C", tags={"CHILD": "T"})
    table.add_pair("T0_0", "T1_0", RouteStyle())                    # electrical
    table.add_pair("T2_0", "T3_0", RouteStyle(radius_um=2.0))       # optical

    marks = [
        {"name": "T0_0", "center_um": [0.0, 0.0], "orientation": 0.0,
         "width_um": 0.5, "port_type": "electrical"},
        {"name": "T1_0", "center_um": [60.0, 0.0], "orientation": 180.0,
         "width_um": 0.5, "port_type": "electrical"},
        {"name": "T2_0", "center_um": [0.0, 20.0], "orientation": 0.0,
         "width_um": 0.5, "port_type": "optical"},
        {"name": "T3_0", "center_um": [60.0, 20.0], "orientation": 180.0,
         "width_um": 0.5, "port_type": "optical"},
    ]
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(bb, "harvest_instance_ports", lambda *a, **k: marks)
    monkeypatch.setattr(bb, "mark_ports", lambda c, m: len(m))

    def fake_route(client_, cell, **kwargs):
        # gf "fails" for both nets so klink's own visibility router (and
        # therefore the euler-bend landing spot under test) plans them.
        raise ValueError("gf bundle router failed")

    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp
    monkeypatch.setattr(gp, "route_gdsfactory_ports", fake_route)

    # T0..T3 dummy device bodies parked far away (own the harvested ports'
    # ordinals so the REAL obstacles below get distinct, foreign owners).
    devices = [
        _device_at(1000, 1000), _device_at(1000, 1010),
        _device_at(1000, 1020), _device_at(1000, 1030),
        _device_at(28, -2),    # owner T4: blocks the electrical corridor
        _device_at(28, 18),    # owner T5: blocks the optical corridor
    ]
    client = FakeClient(devices)
    result = ni._harvest_and_route(client, table, port_layer="999/99",
                                   route_layer="10/0", wg_layer="1/0",
                                   stub_size_um=0.5)
    paths = [params["points_um"] for method, params in client.calls
             if method == "shape.insert_path"]
    return result, paths


def test_optical_detour_is_a_smooth_euler_path(monkeypatch):
    result, paths = _run_two_nets(monkeypatch)
    assert result["ok"] is True
    assert result["detoured"] == 2
    assert result["failed"] == 0

    optical = next(p for p in paths if p[0][1] > 10.0)  # y=20 corridor
    assert len(optical) >= 40
    assert max_turn_deg(optical) <= 15.0
    assert math.hypot(optical[0][0] - 0.0, optical[0][1] - 20.0) < 1e-3
    assert math.hypot(optical[-1][0] - 60.0, optical[-1][1] - 20.0) < 1e-3


def test_electrical_detour_stays_a_sharp_manhattan_path(monkeypatch):
    result, paths = _run_two_nets(monkeypatch)
    assert result["ok"] is True

    electrical = next(p for p in paths if p[0][1] < 10.0)  # y=0 corridor
    assert len(electrical) <= 6
    assert math.hypot(electrical[0][0] - 0.0, electrical[0][1] - 0.0) < 1e-3
    assert math.hypot(electrical[-1][0] - 60.0, electrical[-1][1] - 0.0) < 1e-3
    # every interior turn is either ~0 (straight) or ~90 (sharp corner) --
    # never euler-smoothed.
    for i in range(1, len(electrical) - 1):
        turn = max_turn_deg(electrical[i - 1:i + 2])
        assert turn < 1.0 or turn > 85.0


def test_mixed_port_type_net_is_not_treated_as_optical(monkeypatch):
    """Both ends must be optical (or vertical_*); one electrical end keeps
    the net Manhattan."""
    table = NetTable(cell="C", tags={"CHILD": "T"})
    table.add_pair("T0_0", "T1_0", RouteStyle(radius_um=2.0))
    marks = [
        {"name": "T0_0", "center_um": [0.0, 0.0], "orientation": 0.0,
         "width_um": 0.5, "port_type": "optical"},
        {"name": "T1_0", "center_um": [60.0, 0.0], "orientation": 180.0,
         "width_um": 0.5, "port_type": "electrical"},
    ]
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(bb, "harvest_instance_ports", lambda *a, **k: marks)
    monkeypatch.setattr(bb, "mark_ports", lambda c, m: len(m))

    def fake_route(client_, cell, **kwargs):
        raise ValueError("gf bundle router failed")

    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp
    monkeypatch.setattr(gp, "route_gdsfactory_ports", fake_route)

    devices = [_device_at(1000, 1000), _device_at(1000, 1010),
               _device_at(28, -2)]
    client = FakeClient(devices)
    result = ni._harvest_and_route(client, table, port_layer="999/99",
                                   route_layer="10/0", wg_layer="1/0",
                                   stub_size_um=0.5)
    assert result["ok"] is True
    assert result["detoured"] == 1
    path = next(params["points_um"] for method, params in client.calls
                if method == "shape.insert_path")
    assert len(path) <= 6
