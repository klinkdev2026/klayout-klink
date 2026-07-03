"""PUBLIC test: the sparse power grid (clean_pdn) -- layers DERIVED from the
profile (no dedicated power layer, no hard-coded numbers), VDD/GND straps kept
apart by the power gap. Offline, no lab data (profile from synth_pdk)."""
from dataclasses import replace

from klink.routing.grid.clean_pdn import build_clean_pdn, derive_pdn_layers

from synth_pdk import SYNTH_PROFILE as P


def test_derive_pdn_layers_reuses_routing_stack_3layer():
    rail, strap, cut = derive_pdn_layers(P)
    assert rail == P.sd_layer == "104/0"          # rail = device S/D layer
    assert strap in P.routing_layers and strap != rail   # strap reuses a routing layer
    assert P.layer_direction(strap) != P.layer_direction(rail)  # perpendicular
    assert strap == "106/0" and cut == "105/0"
    assert strap in P.routing_layers and rail in P.routing_layers


def test_derive_pdn_layers_two_layer_profile():
    P2 = replace(P, routing_layers=("101/0", "104/0"), vias=(("101/0", "102/0", "104/0"),))
    rail, strap, cut = derive_pdn_layers(P2)
    assert (rail, strap, cut) == ("104/0", "101/0", "102/0")   # re-derives, still no new layer


def test_build_clean_pdn_vdd_gnd_straps_do_not_overlap():
    taps = {"VDD": [(0.0, 10.0), (0.0, 30.0), (100.0, 10.0)],
            "GND": [(0.0, -10.0), (0.0, -30.0), (100.0, -10.0)]}
    rail, strap, cut = derive_pdn_layers(P)
    pdn = build_clean_pdn(taps, strap_layer=strap, rail_layer=rail, cut_layer=cut,
                          width_um=P.wire_width_um, spacing_um=P.wire_clear_um,
                          margin_um=P.margin_um, strap_gap_um=15.0)
    vdd = [it["box"] for it in pdn["boxes_by_layer"][strap] if it["net"] == "VDD"]
    gnd = [it["box"] for it in pdn["boxes_by_layer"][strap] if it["net"] == "GND"]
    assert vdd and gnd

    def overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    for a in vdd:
        for b in gnd:
            assert not overlap(a, b)
    vx = min(b[0] for b in vdd if b[0] > -20 and b[2] < 20)
    gx = max(b[2] for b in gnd if b[0] > -20 and b[2] < 20)
    assert abs(vx - gx) >= 14.0
    assert all(v["from"] == rail and v["to"] == strap and v["cut"] == cut for v in pdn["vias"])
    assert any(it["kind"] == "tie_rail" for it in pdn["boxes_by_layer"][rail])
