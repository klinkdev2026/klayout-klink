"""Offline tests for the "probe card first" pad-ring mechanism added to
``klink.domains.structdevice.layout_engine.route_and_draw_flexdr`` (its
``io_pads=`` kwarg) and ``klink.routing.backends.flexdr.flexdr.
flexpa_access_nets`` (its ``extra_terminals=`` kwarg). No KLayout, no
gdsfactory/numpy, no network -- CI installs only pytest+klayout and this
file imports neither pya nor optional deps.

Methodology mirrors tests/unit/test_flexdr.py / tests/test_flexdr_rust_parity.py:
build a tiny, fully synthetic 2-device netlist + device geometry directly (no
fixture files beyond a scratch device_geom.json written to tmp_path for the
route_and_draw_flexdr calls, which read geometry from a PATH), and drive the
real mechanism functions -- not a re-implementation of them.

Tiny synthetic setup (documented once here, reused by every test below):
  - process: 2 layers M1/0 (H) / M2/0 (V), one via between them -- the
    MINIMUM a ProcessProfile needs (route_and_draw_flexdr always derives PDN
    rail/strap layers via derive_pdn_layers, even when the netlist has no
    VDD/GND terminals, so at least one via must exist).
  - one device cell "T": a 10x10um body, two terminals G (west, M1/0) and D
    (east, M1/0).
  - two instances X1 (dx=0) and X2 (dx=20um): a net "SIG" ties X1.D to X2.G.
  - a "probe pad" box far outside the device area (x=[100,103], y=[-2,2] um)
    on M1/0 -- always outside the DEFAULT routing bbox (margin_um=5 keeps
    that bbox roughly [-10,30] in x), so it is a clean bbox_include_um /
    extra_terminals probe.
"""
from __future__ import annotations

import json

import pytest

from klink.domains.structdevice import layout_engine as eng
from klink.routing.backends.flexdr.flexdr import flexpa_access_nets, route_flexdr
from klink.routing.grid.process_profile import ProcessProfile

PAD_BOX = (100.0, -2.0, 103.0, 2.0)   # far outside the 2-device block; see module docstring
PAD_LAYER = "M1/0"


def _tiny_profile() -> ProcessProfile:
    return ProcessProfile(
        routing_layers=("M1/0", "M2/0"),
        gate_layer="M1/0", sd_layer="M1/0", channel_layer="CH/0",
        vias=(("M1/0", "150/0", "M2/0"),),   # cut-layer name must be numeric ("L/D"), like real GDS layers
        wire_width_um=1.0, wire_clear_um=0.5, via_pad_um=1.0, litho_tol_um=0.1,
        y_step_um=10.0, col_pitch_um=10.0,
        layer_directions={"M1/0": "H", "M2/0": "V"},
        grid_pitch_um=1.0, margin_um=5.0, y_top_um=0.0,
    )


def _tiny_geom_raw() -> dict:
    return {
        "T": {
            "body": [-5.0, -5.0, 5.0, 5.0],
            "channel": [-2.0, -2.0, 2.0, 2.0],
            "pads": {"G": [-5.0, -2.0, -2.0, 2.0], "D": [2.0, -2.0, 5.0, 2.0]},
            "terms": {
                "G": {"center": [-3.5, 0.0], "orientation": 180.0, "length": 4.0, "layer": "M1/0"},
                "D": {"center": [3.5, 0.0], "orientation": 0.0, "length": 4.0, "layer": "M1/0"},
            },
        }
    }


def _tiny_placement() -> dict:
    # two "T" devices, 20um apart (their 10x10um bodies clear by 10um)
    return {"X1": ("T", 0.0, 0.0), "X2": ("T", 20.0, 0.0)}


def _tiny_netlist(extra_nets=()) -> dict:
    return {"nets": [{"net_id": "SIG", "terminals": ["X1.D", "X2.G"]}, *extra_nets]}


def _grid_and_tables():
    profile = _tiny_profile()
    dg, dp, terms = eng._geom_tables(_tiny_geom_raw())
    placement = _tiny_placement()
    nl = _tiny_netlist()
    layers = list(profile.routing_layers)
    vias = profile.via_rules()
    return profile, dg, dp, terms, placement, nl, layers, vias


# --------------------------------------------------------------------------- #
# 1. build_grid(..., bbox_include_um=...) widens the grid to cover the box
# --------------------------------------------------------------------------- #
def test_build_grid_without_bbox_include_does_not_reach_far_pad():
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    g0, _, _ = eng.build_grid(nl, placement, profile=profile, layers=layers, vias=vias,
                              device_geom=dg, device_pads=dp, terms=terms)
    x_max_nm = g0.x0_nm + (g0.nx - 1) * g0.pitch_nm
    assert x_max_nm < PAD_BOX[0] * 1000, (
        "sanity: the default routing bbox (device block + margin_um) must NOT "
        "already reach the far pad, or this test proves nothing")


def test_build_grid_bbox_include_um_widens_grid_to_cover_the_box():
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    g1, _, _ = eng.build_grid(nl, placement, profile=profile, layers=layers, vias=vias,
                              device_geom=dg, device_pads=dp, terms=terms,
                              bbox_include_um=[PAD_BOX])
    x_min_nm = g1.x0_nm
    x_max_nm = g1.x0_nm + (g1.nx - 1) * g1.pitch_nm
    assert x_min_nm <= PAD_BOX[0] * 1000
    assert x_max_nm >= PAD_BOX[2] * 1000, "grid must be widened to cover the whole pad box"


# --------------------------------------------------------------------------- #
# 2. flexpa_access_nets(..., extra_terminals=...) appends a target cellset
# covering the pad cells (registered via extra_pads_by_layer + bbox_include_um
# per the function's own docstring contract), and raises the instructive
# ValueError when the pad is outside the grid.
# --------------------------------------------------------------------------- #
def test_flexpa_extra_terminals_appends_pad_cellset():
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    extra_pads = {PAD_LAYER: [("SIG", PAD_BOX)]}
    g, _, _ = eng.build_grid(nl, placement, profile=profile, layers=layers, vias=vias,
                             device_geom=dg, device_pads=dp, terms=terms,
                             extra_pads_by_layer=extra_pads, bbox_include_um=[PAD_BOX])
    ni = flexpa_access_nets(g, nl, placement, dp, terms,
                            wire_width_um=profile.wire_width_um,
                            extra_terminals={"SIG": [PAD_BOX + (PAD_LAYER,)]})
    assert len(ni) == 1
    net_input = ni[0]
    # 2 ordinary device terminals (X1.D, X2.G) + 1 extra (pad) terminal cellset
    assert len(net_input.terminal_cells) == 3
    pad_cells = net_input.terminal_cells[-1]
    assert pad_cells, "the extra terminal must produce at least one legal grid cell"
    for (ix, iy, li) in pad_cells:
        assert g.layers[li] == PAD_LAYER
        cx, cy = g.cx(ix) / 1000.0, g.cy(iy) / 1000.0
        assert PAD_BOX[0] <= cx <= PAD_BOX[2]
        assert PAD_BOX[1] <= cy <= PAD_BOX[3]


def test_flexpa_extra_terminal_outside_grid_raises_instructive_error():
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    # grid built WITHOUT bbox_include_um -- the pad box is outside it (see
    # test_build_grid_without_bbox_include_does_not_reach_far_pad above)
    g, _, _ = eng.build_grid(nl, placement, profile=profile, layers=layers, vias=vias,
                             device_geom=dg, device_pads=dp, terms=terms)
    with pytest.raises(ValueError, match="no reachable grid cell"):
        flexpa_access_nets(g, nl, placement, dp, terms,
                           extra_terminals={"SIG": [PAD_BOX + (PAD_LAYER,)]})


# --------------------------------------------------------------------------- #
# 3. route_and_draw_flexdr's io_pads validation runs BEFORE any client RPC:
# reading layout_engine.py confirms the io_pads unknown-net / power-net
# ValueErrors (lines ~440-450) fire strictly after _geom_tables/build_grid/
# build_clean_pdn (all client-free) and strictly before ensure_pcell/draw
# (the first client use). So client=None is safe here as long as the error
# path is hit -- we assert exactly that (pytest.raises means client.* is
# never reached).
# --------------------------------------------------------------------------- #
def _cut_layer(profile):
    return {tuple(sorted((lo, up))): profile.cut_layer(lo, up) for (lo, _c, up) in profile.vias}


def test_io_pads_rejects_unknown_net_before_touching_the_client(tmp_path):
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    geom_path = tmp_path / "device_geom.json"
    geom_path.write_text(json.dumps(_tiny_geom_raw()))
    io_pads = {"pad_layer": PAD_LAYER,
              "pads": [{"id": "P1", "box_um": list(PAD_BOX), "net": "NOT_A_REAL_NET"}]}
    with pytest.raises(ValueError, match="unknown net"):
        eng.route_and_draw_flexdr(
            None, "OFFLINE_TEST_CELL", nl, placement, profile=profile, layers=layers, vias=vias,
            cut_layer=_cut_layer(profile), geom_path=str(geom_path), devices={}, io_pads=io_pads)


def test_power_pad_on_non_pdn_layer_raises_instructively(tmp_path):
    # POWER pads are ALLOWED (they feed the PDN as attach taps) -- but only on
    # the PDN rail or strap layer. A 3-layer profile gives us a routing layer
    # (M3/0) that is NEITHER: rail = sd (M1/0), strap = the rail-via-connected
    # perpendicular layer (M2/0).
    profile = ProcessProfile(
        routing_layers=("M1/0", "M2/0", "M3/0"),
        gate_layer="M1/0", sd_layer="M1/0", channel_layer="CH/0",
        vias=(("M1/0", "150/0", "M2/0"), ("M2/0", "151/0", "M3/0")),
        wire_width_um=1.0, wire_clear_um=0.5, via_pad_um=1.0, litho_tol_um=0.1,
        y_step_um=10.0, col_pitch_um=10.0,
        layer_directions={"M1/0": "H", "M2/0": "V", "M3/0": "H"},
        grid_pitch_um=1.0, margin_um=5.0, y_top_um=0.0,
    )
    placement = _tiny_placement()
    nl = _tiny_netlist(extra_nets=[{"net_id": "VDD", "terminals": []}])
    geom_path = tmp_path / "device_geom.json"
    geom_path.write_text(json.dumps(_tiny_geom_raw()))
    io_pads = {"pad_layer": "M3/0",
               "pads": [{"id": "P2", "box_um": list(PAD_BOX), "net": "VDD"}]}
    with pytest.raises(ValueError, match="neither the PDN rail"):
        eng.route_and_draw_flexdr(
            None, "OFFLINE_TEST_CELL", nl, placement, profile=profile,
            layers=list(profile.routing_layers), vias=profile.via_rules(),
            cut_layer=_cut_layer(profile), geom_path=str(geom_path), devices={},
            io_pads=io_pads)


def test_power_pad_becomes_attach_tap_without_stretching_the_rail():
    # An attach-only tap (a power pad) gets a strap + via to the tie rail but
    # must NOT drag the rail envelope out to itself (build_clean_pdn contract).
    from klink.routing.grid.clean_pdn import build_clean_pdn
    kw = dict(strap_layer="M2/0", rail_layer="M1/0", cut_layer="150/0",
              width_um=1.0, spacing_um=0.5, margin_um=5.0)
    base = build_clean_pdn({"VDD": [(0.0, 0.0), (20.0, 0.0)]}, **kw)
    with_pad = build_clean_pdn({"VDD": [(0.0, 0.0), (20.0, 0.0)]},
                               attach_taps_by_net={"VDD": [(50.0, -30.0)]}, **kw)
    rail = [it for it in base["boxes_by_layer"]["M1/0"] if it["kind"] == "tie_rail"][0]
    rail_p = [it for it in with_pad["boxes_by_layer"]["M1/0"] if it["kind"] == "tie_rail"][0]
    assert rail["box"][1] == rail_p["box"][1] and rail["box"][3] == rail_p["box"][3], \
        "attach tap must not move the rail in y"
    pad_straps = [it for it in with_pad["boxes_by_layer"]["M2/0"]
                  if it["kind"] == "strap" and it["box"][0] <= 50.0 <= it["box"][2]]
    assert pad_straps, "the attach tap must get its own strap column"
    s = pad_straps[0]["box"]
    assert s[1] <= -30.0 and s[3] >= rail["box"][1], \
        "the pad strap must span from the pad up to the rail"


def test_place_grid_forbid_y_bands_default_is_byte_identical():
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    nl2 = {"instances": [{"instance_id": "X1", "device_cell": "T"},
                         {"instance_id": "X2", "device_cell": "T"}],
           "nets": nl["nets"],
           "groups": [{"group": "g0", "gate_type": "?", "instances": ["X1"]},
                      {"group": "g1", "gate_type": "?", "instances": ["X2"]}]}
    a = eng.place_grid(nl2, 2, 1, profile=profile, row_pitch=25.0)
    b = eng.place_grid(nl2, 2, 1, profile=profile, row_pitch=25.0, forbid_y_bands=())
    assert a == b


def test_place_grid_forbid_y_bands_pushes_rows_below_the_band():
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    nl2 = {"instances": [{"instance_id": "X1", "device_cell": "T"},
                         {"instance_id": "X2", "device_cell": "T"}],
           "nets": nl["nets"],
           "groups": [{"group": "g0", "gate_type": "?", "instances": ["X1"]},
                      {"group": "g1", "gate_type": "?", "instances": ["X2"]}]}
    # row 0 spans [-25, 0] (conservative stack = row_pitch), row 1 would span
    # [-50, -25]; a band at (-45, -30) cuts only row 1 -> row 0 stays, row 1
    # lands with its TOP at the band's low edge
    band = (-45.0, -30.0)
    p = eng.place_grid(nl2, 2, 1, profile=profile, row_pitch=25.0,
                       forbid_y_bands=[band])
    assert p["X1"][2] == 0.0, "row 0 does not touch the band and must not move"
    assert p["X2"][2] == band[0], "row 1 must be pushed to just under the band"


def test_pads_from_boxes_orders_reading_order_and_spread_ports_stub_contract():
    from klink.routing.grid.pad_harvest import pads_from_boxes, spread_ports
    pads = pads_from_boxes([[0, 0, 10, 10], [20, 20, 30, 30], [0, 20, 10, 30]])
    # reading order: top row (y desc) first, then left->right
    assert [p["box_um"] for p in pads] == [[0, 20, 10, 30], [20, 20, 30, 30], [0, 0, 10, 10]]
    assert [p["id"] for p in pads] == ["PAD00", "PAD01", "PAD02"]
    ports = spread_ports([0, 0, 100, 100], ["A", "B"], side="E", size_um=2.0, clear_um=5.0)
    assert all(p["draw"] is False for p in ports), \
        "no-card stubs are wire ends: no pad box may be drawn"
    for p in ports:
        x1, y1, x2, y2 = p["box_um"]
        assert x1 == 105.0 and x2 == 107.0, "E-side stubs sit clear_um right of the bbox"
    assert [p["net"] for p in ports] == ["A", "B"]


def test_pads_from_gds_round_trip(tmp_path):
    kdb = pytest.importorskip(
        "klayout.db", reason="klayout pip package not installed (bare env)")
    from klink.routing.grid.pad_harvest import pads_from_gds
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("CARD")
    li = ly.layer(106, 0)
    for (x, y) in ((0.0, 0.0), (200.0, 0.0), (0.0, 200.0)):
        top.shapes(li).insert(kdb.DBox(x, y, x + 100.0, y + 100.0))
    top.shapes(li).insert(kdb.DBox(50.0, 150.0, 52.0, 152.0))   # sliver: dropped
    path = str(tmp_path / "card.gds")
    ly.write(path)
    pads = pads_from_gds(path, "CARD", "106/0", min_size_um=50.0)
    assert len(pads) == 3, "the 2um sliver must be filtered by min_size_um"
    assert pads[0]["box_um"] == [0.0, 200.0, 100.0, 300.0], "reading order: top first"


# --------------------------------------------------------------------------- #
# 4. End-to-end offline: the frozen route_flexdr path (use_rust=False) really
# routes the pad net's wire onto the pad box (at least one routed cell whose
# CENTER lies inside the pad box) -- the same build_grid -> flexpa_access_nets
# -> route_flexdr pipeline route_and_draw_flexdr runs internally, without the
# KLayout draw step.
# --------------------------------------------------------------------------- #
def test_route_flexdr_offline_reaches_the_pad_box():
    profile, dg, dp, terms, placement, nl, layers, vias = _grid_and_tables()
    extra_pads = {PAD_LAYER: [("SIG", PAD_BOX)]}
    g, _, _ = eng.build_grid(nl, placement, profile=profile, layers=layers, vias=vias,
                             device_geom=dg, device_pads=dp, terms=terms,
                             extra_pads_by_layer=extra_pads, bbox_include_um=[PAD_BOX])
    ni = flexpa_access_nets(g, nl, placement, dp, terms,
                            wire_width_um=profile.wire_width_um,
                            extra_terminals={"SIG": [PAD_BOX + (PAD_LAYER,)]})
    r = route_flexdr(g, ni, profile, 4, width_um=profile.wire_width_um,
                     wire_clear_um=profile.wire_clear_um, via_clear_um=profile.via_clear_um,
                     use_rust=False)
    assert r.ok, f"offline route_flexdr must connect the tiny SIG net: {r.problems}"
    cells = r.routes["SIG"]
    assert any(
        g.layers[li] == PAD_LAYER
        and PAD_BOX[0] <= g.cx(ix) / 1000.0 <= PAD_BOX[2]
        and PAD_BOX[1] <= g.cy(iy) / 1000.0 <= PAD_BOX[3]
        for (ix, iy, li) in cells
    ), "the routed SIG net must include at least one cell centered inside the pad box"
