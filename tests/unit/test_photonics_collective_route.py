"""Collective routing verdict in _harvest_and_route.

The gf bundle router is called once per style group and (inside the bridge)
once per angle/cluster group; no single call sees the whole picture. These
tests lock the collective checks that run over the FULL route set afterwards:

* crossings BETWEEN style groups are detected (each per-group call alone
  cannot see them) — but only between routes on the SAME layer;
* routes cutting through a placed component's interior ON THEIR OWN LAYER
  are detected (a heater's metal overhang must not block the optical route);
* problems come back as instructions naming the NETS, not internal route ids;
* a clean multi-group route reports ok=True;
* device bboxes are never handed to the gf call (kfactory `bboxes` are
  escape heuristics, proven to wrap collinear chains into loops).

Pure offline: the gf bridge call and the harvest are both replaced; no
gdsfactory, no live KLayout.
"""
from __future__ import annotations

import klink.domains.photonics.net_intent as ni
from klink.domains.photonics.net_intent import NetTable, RouteStyle


class FakeClient:
    """Layout facts for the layer-aware collective checks.

    One child cell 'CHILD' whose 10/0 footprint is a local 4x4um box;
    instances place it wherever a test needs a device body.
    """

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


def _table_two_groups():
    """Two nets in DIFFERENT style groups (different widths)."""
    table = NetTable(cell="C", tags={"CHILD": "T"})
    table.add_pair("T0_0", "T1_0", RouteStyle(width_um=0.5))
    table.add_pair("T2_0", "T3_0", RouteStyle(width_um=1.0))
    return table


def _run(monkeypatch, table, client, routes_per_call):
    """Drive _harvest_and_route with canned harvest + canned gf routes."""
    marks = [{"name": p} for e in table.entries for p in (e["a"], e["b"])]
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(bb, "harvest_instance_ports",
                        lambda *a, **k: marks)
    monkeypatch.setattr(bb, "mark_ports", lambda c, m: len(m))

    # _harvest_and_route now routes in phases (dry-run plan, then write), so
    # the fake answers by SOURCE PORT rather than by call order.
    by_source = {r["source"]: r for group in routes_per_call for r in group}
    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp

    def fake_route(client_, cell, **kwargs):
        found = [dict(by_source[s]) for s in (kwargs.get("source") or [])
                 if s in by_source]
        return {"routes": found, "writeback": {"inserted": 1}}

    monkeypatch.setattr(gp, "route_gdsfactory_ports", fake_route)
    return ni._harvest_and_route(client, table, port_layer="999/99",
                                 route_layer="10/0", wg_layer="1/0",
                                 stub_size_um=0.5)


def _route(rid, source, points, width, layer="10/0"):
    return {"route_id": rid, "source": source, "points_um": points,
            "width_um": width, "length_um": 10.0, "layer": layer}


def test_cross_style_group_crossing_is_detected(monkeypatch):
    table = _table_two_groups()
    # Group 1 route runs horizontal; group 2 route runs vertical through it.
    result = _run(monkeypatch, table, FakeClient(), [
        [_route("r0", "T0_0", [[0, 0], [10, 0]], 0.5)],
        [_route("r1", "T2_0", [[5, -5], [5, 5]], 1.0)],
    ])
    assert result["ok"] is False
    assert result["crossings"] == 1
    nets = table.net_names()
    assert any(nets[0] in p and nets[1] in p for p in result["problems"])


def test_crossing_on_different_layers_is_fine(monkeypatch):
    """An electrical riser crossing an optical route is NOT a conflict."""
    table = _table_two_groups()
    result = _run(monkeypatch, table, FakeClient(), [
        [_route("r0", "T0_0", [[0, 0], [10, 0]], 0.5, layer="10/0")],
        [_route("r1", "T2_0", [[5, -5], [5, 5]], 1.0, layer="49/0")],
    ])
    assert result["crossings"] == 0
    assert result["ok"] is True


def test_route_through_foreign_device_interior_is_detected(monkeypatch):
    table = _table_two_groups()
    # This net's router does not honor waypoints_um, so phase 2's fixable
    # check can't auto-detour it; the raw device hit reaches the collective
    # verdict unchanged (which is what this test locks).
    table.entries[0]["style"]["router"] = "sbend"
    # Devices T0 (far away) and T1 (body at 4..8, -2..2) on 10/0; route r0
    # belongs to T0 and slices through T1 — a FOREIGN device.
    client = FakeClient(instances=[_device_at(-20, -20), _device_at(4, -2)])
    result = _run(monkeypatch, table, client, [
        [_route("r0", "T0_0", [[0, 0], [12, 0]], 0.5)],
        [_route("r1", "T2_0", [[0, 10], [12, 10]], 1.0)],
    ])
    assert result["ok"] is False
    assert result["device_hits"] == 1
    assert any("cuts through" in p for p in result["problems"])
    # The clean route is not blamed.
    assert not any(table.net_names()[1] in p for p in result["problems"])


def test_routes_own_endpoint_device_is_exempt(monkeypatch):
    """A rotated instance's axis-aligned bbox inflates past the footprint;
    a route must be free to leave the device that owns its port."""
    table = _table_two_groups()
    # Only device T0 (body at 4..8, -2..2); route r0 STARTS on T0's port
    # and crosses its own bbox on the way out.
    client = FakeClient(instances=[_device_at(4, -2)])
    result = _run(monkeypatch, table, client, [
        [_route("r0", "T0_0", [[0, 0], [12, 0]], 0.5)],
        [_route("r1", "T2_0", [[0, 10], [12, 10]], 1.0)],
    ])
    assert result["device_hits"] == 0
    assert result["ok"] is True


def test_device_body_on_other_layer_is_no_obstacle(monkeypatch):
    """The device footprint only blocks routes on ITS OWN layer."""
    table = _table_two_groups()
    client = FakeClient(instances=[_device_at(4, -2)])
    result = _run(monkeypatch, table, client, [
        [_route("r0", "T0_0", [[0, 0], [12, 0]], 0.5, layer="49/0")],
        [_route("r1", "T2_0", [[0, 10], [12, 10]], 1.0, layer="49/0")],
    ])
    assert result["device_hits"] == 0
    assert result["ok"] is True


def test_route_ending_on_device_boundary_is_not_a_hit(monkeypatch):
    table = _table_two_groups()
    # Routes END exactly on the device boundary (port on the bbox edge):
    # legitimate, must not be reported as cutting through.
    client = FakeClient(instances=[_device_at(10, -2)])
    result = _run(monkeypatch, table, client, [
        [_route("r0", "T0_0", [[0, 0], [10, 0]], 0.5)],
        [_route("r1", "T2_0", [[0, 1], [10, 1]], 1.0)],
    ])
    assert result["device_hits"] == 0
    assert result["ok"] is True
    assert "problems" not in result


def test_clean_two_group_route_is_ok(monkeypatch):
    table = _table_two_groups()
    result = _run(monkeypatch, table, FakeClient(), [
        [_route("r0", "T0_0", [[0, 0], [10, 0]], 0.5)],
        [_route("r1", "T2_0", [[0, 5], [10, 5]], 1.0)],
    ])
    assert result["ok"] is True
    assert result["crossings"] == 0
    assert result["device_hits"] == 0
    assert result["routes"] == 2


def test_routes_matched_to_nets_by_source_not_order(monkeypatch):
    """The bridge reorders routes (angle/cluster partition); net names must
    follow the SOURCE PORT, not positional zip."""
    table = _table_two_groups()
    # both nets in ONE style group -> one call returning routes REVERSED
    table.entries[1]["style"] = dict(table.entries[0]["style"])
    result = _run(monkeypatch, table, FakeClient(), [
        [_route("gf_route_0", "T2_0", [[0, 5], [10, 5]], 0.5),
         _route("gf_route_1", "T0_0", [[0, 0], [10, 0]], 0.5)],
    ])
    nets = table.net_names()
    assert set(result["lengths_um"]) == set(nets)


def test_gf_failure_triggers_klink_detour(monkeypatch):
    """gf cannot build the T2/T3 net at all; klink plans a visibility detour
    around the FOREIGN device blocking the straight corridor, verifies it
    dry, then writes it. The T0/T1 net (same style group, no obstacle in its
    way) routes normally."""
    table = _table_two_groups()
    table.entries[1]["style"] = dict(table.entries[0]["style"])
    marks = [
        {"name": "T0_0"},
        {"name": "T1_0"},
        {"name": "T2_0", "center_um": [0.0, 0.0], "orientation": 0.0, "width_um": 0.5},
        {"name": "T3_0", "center_um": [60.0, 0.0], "orientation": 180.0, "width_um": 0.5},
    ]
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(bb, "harvest_instance_ports", lambda *a, **k: marks)
    monkeypatch.setattr(bb, "mark_ports", lambda c, m: len(m))

    def fake_route(client_, cell, **kwargs):
        sources = kwargs.get("source") or []
        targets = kwargs.get("target") or []
        waypoints = kwargs.get("waypoints_um")
        routes = []
        for i, src in enumerate(sources):
            tgt = targets[i] if i < len(targets) else ""
            if "T2_0" in src:
                if not waypoints:
                    raise ValueError(
                        "gf bundle router fell back to an error path")
                routes.append({
                    "route_id": f"gf_route_{i}", "source": src, "target": tgt,
                    "points_um": [list(p) for p in waypoints],
                    "width_um": 0.5, "length_um": 10.0, "layer": "10/0"})
            else:
                # Far from the T2/T3 detour corridor (y up to ~13um once
                # expanded) so it is not itself an obstacle for it.
                routes.append({
                    "route_id": f"gf_route_{i}", "source": src, "target": tgt,
                    "points_um": [[0, 100], [10, 100]],
                    "width_um": 0.5, "length_um": 10.0, "layer": "10/0"})
        return {"routes": routes, "writeback": {"inserted": 1}}

    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp
    monkeypatch.setattr(gp, "route_gdsfactory_ports", fake_route)

    # Obstacle 28..32, -2..2 blocks the T2_0(0,0)->T3_0(60,0) straight
    # corridor, well clear of both port launch points once klink's own
    # bend-radius safety margin (~11um) expands it for detour planning. It
    # is owned by T0 (first instance placed), a FOREIGN device relative to
    # the T2/T3 net's ports.
    client = FakeClient(instances=[_device_at(28, -2)])
    result = ni._harvest_and_route(client, table, port_layer="999/99",
                                   route_layer="10/0", wg_layer="1/0",
                                   stub_size_um=0.5)
    assert result["detoured"] == 1
    assert result["failed"] == 0
    assert result["ok"] is True


def test_unroutable_net_reports_instructive_failure(monkeypatch):
    """gf fails to build a net, AND klink's own visibility router can't
    rescue it either (the launch point is boxed in by a foreign device with
    no clear corridor out at all): the net is left undrawn and reported with
    an instructive, net-named problem instead of crashing or writing
    garbage. (klink now draws detours as a native path instead of handing
    waypoints back to gf, so a genuinely unroutable net has to fail at the
    geometric-planning step, not a second gf call.)"""
    table = _table_two_groups()
    marks = [
        {"name": "T0_0"},
        {"name": "T1_0"},
        {"name": "T2_0", "center_um": [0.0, 0.0], "orientation": 0.0, "width_um": 0.5},
        {"name": "T3_0", "center_um": [30.0, 0.0], "orientation": 180.0, "width_um": 0.5},
    ]
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(bb, "harvest_instance_ports", lambda *a, **k: marks)
    monkeypatch.setattr(bb, "mark_ports", lambda c, m: len(m))

    def fake_route(client_, cell, **kwargs):
        sources = kwargs.get("source") or []
        targets = kwargs.get("target") or []
        routes = []
        for i, src in enumerate(sources):
            tgt = targets[i] if i < len(targets) else ""
            if "T2_0" in src:
                raise ValueError("gf bundle router cannot build this net")
            routes.append({
                "route_id": f"gf_route_{i}", "source": src, "target": tgt,
                "points_um": [[0, 10], [10, 10]],
                "width_um": 0.5, "length_um": 10.0, "layer": "10/0"})
        return {"routes": routes, "writeback": {"inserted": 1}}

    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp
    monkeypatch.setattr(gp, "route_gdsfactory_ports", fake_route)

    # Device T0 at (-1,-3), a 4x4 footprint -> bbox [-1,-3,3,1]; once
    # expanded by klink's own obstacle clearance it swallows T2_0's launch
    # point (1,0) entirely, so route_points_geometric cannot even start.
    client = FakeClient(instances=[_device_at(-1, -3)])
    result = ni._harvest_and_route(client, table, port_layer="999/99",
                                   route_layer="10/0", wg_layer="1/0",
                                   stub_size_um=0.5)
    net_t2t3 = table.net_names()[1]
    assert result["failed"] == 1
    assert result["ok"] is False
    assert any(net_t2t3 in p for p in result["problems"])


def test_device_bboxes_stay_out_of_the_gf_call(monkeypatch):
    """Proven live: kfactory `bboxes` are escape-length heuristics, not
    obstacle avoidance, and wrap collinear chains into giant loops. Device
    bboxes must feed klink's own post-check ONLY, never the gf call."""
    table = _table_two_groups()
    client = FakeClient(instances=[_device_at(0, 0)])
    seen_kwargs = []
    marks = [{"name": p} for e in table.entries for p in (e["a"], e["b"])]
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(bb, "harvest_instance_ports", lambda *a, **k: marks)
    monkeypatch.setattr(bb, "mark_ports", lambda c, m: len(m))
    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp

    def fake_route(client_, cell, **kwargs):
        seen_kwargs.append(kwargs)
        return {"routes": [], "writeback": {"inserted": 0}}

    monkeypatch.setattr(gp, "route_gdsfactory_ports", fake_route)
    ni._harvest_and_route(client, table, port_layer="999/99",
                          route_layer="10/0", wg_layer="1/0",
                          stub_size_um=0.5)
    assert seen_kwargs
    for kwargs in seen_kwargs:
        assert "bboxes_um" not in kwargs
