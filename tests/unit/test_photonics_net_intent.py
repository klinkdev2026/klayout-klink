"""Unit tests for SEND-driven net intent (resolution, naming, styles)."""

from __future__ import annotations

import pytest

from klink.domains.photonics.net_intent import (
    NetIntent,
    RouteStyle,
    assign_net,
    auto_net_name,
    resolve_selection_to_port,
    route_kwargs_for,
)

PORTS = [
    {"name": "GC0_0", "center_um": [-64.16, 21.04]},
    {"name": "MMI0_0", "center_um": [-20.5, 0.0]},
    {"name": "MMI0_1", "center_um": [20.5, -0.85]},
    {"name": "MMI0_2", "center_um": [20.5, 0.85]},
]


def _selection(bbox_dbu, *, marker=False, cell="X"):
    item = {"bbox_dbu": bbox_dbu, "is_cell_inst": marker, "cell": cell}
    return {"items": [item]}


def test_auto_net_name_is_deterministic_and_collision_free():
    name = auto_net_name("MMI0_2", "GC0_0")
    assert name == "n_GC0_0__MMI0_2"          # sorted endpoints
    assert auto_net_name("GC0_0", "MMI0_2") == name
    assert auto_net_name("GC0_0", "MMI0_2", existing=[name]) == name + "_2"


def test_resolution_prefers_port_marker_items():
    # Marker selected exactly at GC0_0.
    sel = _selection([-64660, 20540, -63660, 21540], marker=True, cell="klink_Port$1")
    res = resolve_selection_to_port(sel, PORTS)
    assert res.ok and res.port_name == "GC0_0" and res.method == "marker"


def test_resolution_nearest_within_tolerance():
    sel = _selection([18000, 600, 23000, 1100])  # near MMI0_2
    res = resolve_selection_to_port(sel, PORTS)
    assert res.port_name == "MMI0_2"


def test_resolution_rejects_far_clicks():
    sel = _selection([500000, 500000, 501000, 501000])
    res = resolve_selection_to_port(sel, PORTS)
    assert not res.ok and res.port_name is None
    assert "tolerance" in res.detail


def test_resolution_flags_ambiguity_between_close_ports():
    # Equidistant-ish between MMI0_1 (y=-0.85) and MMI0_2 (y=+0.85).
    sel = _selection([20000, -250, 21000, 250])
    res = resolve_selection_to_port(sel, PORTS)
    assert res.ambiguous_with is not None and not res.ok


def test_route_style_validation():
    assert RouteStyle().validate() == []
    assert any("bend" in p for p in RouteStyle(bend="circular").validate())
    assert any("PDK" in p for p in RouteStyle(named_cross_section="rib").validate())
    with pytest.raises(ValueError):
        route_kwargs_for(RouteStyle(width_um=-1), default_route_layer="1/0")


def test_route_kwargs_default_path_needs_no_gdsfactory():
    kwargs = route_kwargs_for(RouteStyle(separation_um=5.0), default_route_layer="1/0")
    assert kwargs == {
        "separation_um": 5.0,
        "router": "bundle",
        "auto_taper": False,
        "route_layer": "1/0",
        "cross_section": None,
    }


def _marker_item(port_name):
    return {
        "is_cell_inst": True,
        "cell": "Port$1",
        "bbox_dbu": [0, 0, 100, 100],
        "pcell": {"pcell_name": "Port", "lib": "klink_port",
                  "params": {"port_name": port_name}},
    }


def _send(sel_id, *port_names, extra_items=0):
    items = [_marker_item(n) for n in port_names]
    items += [{"is_cell_inst": True, "cell": "SomeBlackbox",
               "bbox_dbu": [0, 0, 100, 100]}] * extra_items
    return {"id": sel_id, "cell": "LOOP", "items": items}


def test_pairs_from_sends_two_marker_gesture():
    from klink.domains.photonics.net_intent import pairs_from_sends

    pairs, problems = pairs_from_sends([_send("s1", "A", "B"), _send("s2", "C", "D")])
    assert pairs == [("A", "B"), ("C", "D")] and problems == []


def test_pairs_from_sends_single_markers_pair_consecutively():
    from klink.domains.photonics.net_intent import pairs_from_sends

    pairs, problems = pairs_from_sends([_send("s1", "A"), _send("s2", "B")])
    assert pairs == [("A", "B")] and problems == []


def test_pairs_from_sends_problems_are_instructions():
    from klink.domains.photonics.net_intent import pairs_from_sends

    # Zero markers (selected a blackbox, not the Port marker) and an
    # unpaired leftover both produce actionable messages.
    pairs, problems = pairs_from_sends([
        _send("s1"),                 # no markers at all
        _send("s2", "A", "B", "C"),  # three markers
        _send("s3", "X"),            # unpaired
    ])
    assert pairs == []
    assert len(problems) == 3
    assert all("SEND" in p or "partner" in p for p in problems)
    # Non-marker instances in the selection do not break the gesture.
    pairs2, problems2 = pairs_from_sends([_send("s4", "A", "B", extra_items=1)])
    assert pairs2 == [("A", "B")] and problems2 == []


def test_net_table_roundtrip_and_duplicate_port_guard(tmp_path):
    from klink.domains.photonics.net_intent import NetTable, RouteStyle

    table = NetTable(cell="LOOP", tags={"BB": "BB"})
    entry = table.add_pair("A", "B", RouteStyle(radius_um=20.0))
    assert entry["net"] == "n_A__B"
    table.save(str(tmp_path))

    loaded = NetTable.load("LOOP", str(tmp_path))
    assert loaded is not None
    assert loaded.tags == {"BB": "BB"}
    assert loaded.entries[0]["style"]["radius_um"] == 20.0
    assert loaded.nets_for_harvest() == {"A": "n_A__B", "B": "n_A__B"}

    with pytest.raises(ValueError):
        loaded.add_pair("A", "C")  # A already connected -> explicit error


def test_assign_net_updates_both_ports():
    calls = []

    class FakeClient:
        def call(self, method, params):
            calls.append((method, params))
            return {"ok": True}

    intent = NetIntent(net="n_a__b", port_a="a", port_b="b")
    out = assign_net(FakeClient(), "CELL", intent)
    assert out["net"] == "n_a__b"
    assert [(m, p["name"], p["net"]) for m, p in calls] == [
        ("port.update", "a", "n_a__b"),
        ("port.update", "b", "n_a__b"),
    ]
