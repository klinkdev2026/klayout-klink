"""Bug 2: gf-template mark visibility only shows in-net ports after routing.

A gf component routinely carries duplicate/unused access ports (e.g. a bond
pad's 4 edges e1..e4 PLUS a redundant center point with port_type "pad", or
a heater's 8 electrical pins when the script only wires 2 of them). Before
this fix, ``_harvest_and_route`` marked EVERY harvested port regardless of
net membership, so a single big pad painted 5 markers and a heater painted
8. The fix: in gf-template mode, only ports that are actually IN A NET get
drawn (``mark_ports`` receives the filtered list); the full template is
untouched on disk (``harvest_gf_template_ports`` itself still computes and
can return every port — see test_gf_import.py's mark_policy test). Stub
(waveguide-blackbox) mode is UNCHANGED: SEND point-selection depends on
every stub port being visible, so it keeps marking the full set.

Pure offline: the gf bridge call and both harvest functions are replaced;
no gdsfactory import, no live KLayout.
"""
from __future__ import annotations

import klink.domains.photonics.net_intent as ni
from klink.domains.photonics.net_intent import NetTable, RouteStyle


class FakeClient:
    def __init__(self):
        self.calls = []

    def layout_info(self):
        return {"dbu": 0.001}

    def layer_list(self):
        return {"layers": []}

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "instance.query":
            return {"instances": []}
        return {"ok": True}


def _fake_route_ok(source_name, target_name):
    def fake_route(client_, cell, **kwargs):
        return {"routes": [{
            "route_id": "r0", "source": source_name, "target": target_name,
            "points_um": [[0, 0], [10, 0]], "width_um": 0.5,
            "length_um": 10.0, "layer": "10/0",
        }], "writeback": {"inserted": 1}}
    return fake_route


def test_gf_mode_marks_only_in_net_ports(monkeypatch):
    """A pad-like device: 2 in-net optical-ish ports + 2 unused access
    points (an unused edge + the redundant pad-type center point) must
    surface as ONLY the 2 in-net marks once routed."""
    table = NetTable(cell="C", tags={"GFDEV_PAD": "p"})
    table.harvest = {"mode": "gf_templates", "templates": {},
                     "route_layer": "10/0"}
    table.add_pair("p0_o1", "p0_o2", RouteStyle(route_layer="10/0"))

    all_marks = [
        {"name": "p0_o1", "net": "n_p0_o1__p0_o2", "center_um": [0.0, 0.0],
         "orientation": 0.0, "width_um": 0.5, "port_type": "optical"},
        {"name": "p0_o2", "net": "n_p0_o1__p0_o2", "center_um": [10.0, 0.0],
         "orientation": 180.0, "width_um": 0.5, "port_type": "optical"},
        {"name": "p0_e3", "net": "", "center_um": [5.0, 5.0],
         "orientation": 0.0, "width_um": 10.0, "port_type": "electrical"},
        {"name": "p0_pad", "net": "", "center_um": [5.0, 0.0],
         "orientation": 0.0, "width_um": 60.0, "port_type": "pad"},
    ]

    import klink.domains.photonics.gf_import as gi
    seen_policy = []

    def fake_harvest(client, cell, *, tags, templates, nets=None,
                     port_layer="999/99", mark_policy="all"):
        seen_policy.append(mark_policy)
        return [dict(m) for m in all_marks]

    monkeypatch.setattr(gi, "harvest_gf_template_ports", fake_harvest)

    marked_calls = []
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(
        bb, "mark_ports",
        lambda c, m: marked_calls.append(list(m)) or len(m))

    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp
    monkeypatch.setattr(gp, "route_gdsfactory_ports",
                        _fake_route_ok("p0_o1", "p0_o2"))

    client = FakeClient()
    result = ni._harvest_and_route(client, table, port_layer="999/99",
                                   route_layer="10/0")

    # harvest_gf_template_ports is always asked for the FULL set: internal
    # bookkeeping (missing-port validation, abutment detection) needs every
    # harvested port, not just the ones already wired.
    assert seen_policy == ["all"]

    # Only the ONE mark_ports call happens, and it only carries in-net ports.
    assert len(marked_calls) == 1
    assert {m["name"] for m in marked_calls[0]} == {"p0_o1", "p0_o2"}
    assert result["ok"] is True
    assert result["harvested_ports"] == len(all_marks)  # mechanism unchanged


def test_stub_mode_still_marks_the_full_harvested_set(monkeypatch):
    """Waveguide-stub (blackbox) convention: SEND point-selection needs
    every harvested port visible, in-net or not — unaffected by the gf-mode
    fix above."""
    table = NetTable(cell="C", tags={"CHILD": "T"})
    table.add_pair("T0_0", "T1_0", RouteStyle(route_layer="10/0"))

    all_marks = [
        {"name": "T0_0", "net": "n_T0_0__T1_0", "center_um": [0.0, 0.0],
         "orientation": 0.0, "width_um": 0.5, "port_type": "optical"},
        {"name": "T1_0", "net": "n_T0_0__T1_0", "center_um": [10.0, 0.0],
         "orientation": 180.0, "width_um": 0.5, "port_type": "optical"},
        {"name": "T0_1", "net": "", "center_um": [5.0, 0.0],
         "orientation": 0.0, "width_um": 0.5, "port_type": "optical"},
    ]
    import klink.domains.photonics.blackbox as bb
    monkeypatch.setattr(bb, "harvest_instance_ports",
                        lambda *a, **k: [dict(m) for m in all_marks])
    marked_calls = []
    monkeypatch.setattr(
        bb, "mark_ports",
        lambda c, m: marked_calls.append(list(m)) or len(m))

    import klink.routing.backends.gdsfactory.gdsfactory_ports as gp
    monkeypatch.setattr(gp, "route_gdsfactory_ports",
                        _fake_route_ok("T0_0", "T1_0"))

    client = FakeClient()
    result = ni._harvest_and_route(client, table, port_layer="999/99",
                                   route_layer="10/0", wg_layer="1/0",
                                   stub_size_um=0.5)

    assert len(marked_calls) == 1
    assert {m["name"] for m in marked_calls[0]} == {"T0_0", "T1_0", "T0_1"}
    assert result["ok"] is True
