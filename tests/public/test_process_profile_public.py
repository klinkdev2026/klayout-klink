"""PUBLIC test: ProcessProfile -- the single editable home of all layer/via/
spacing/placement config. The algorithm holds no process constants; everything
derives from a profile instance (offline, no KLayout, no lab data).

Synthetic version of the dev suite's test_process_profile: sources its profile
from tests/public/synth_pdk.py instead of a lab pdk.
"""
import pytest

from klink.routing.grid.process_profile import ProcessProfile

from synth_pdk import SYNTH_PROFILE


def test_synth_profile_roles():
    p = SYNTH_PROFILE
    assert p.routing_layers == ("101/0", "104/0", "106/0")
    assert p.gate_layer == "101/0" and p.sd_layer == "104/0"
    assert p.cut_layer("101/0", "104/0") == 102
    assert p.cut_layer("104/0", "101/0") == 102        # order-free
    assert p.cut_layer("104/0", "106/0") == 105


def test_via_rules_and_connectivity_derive_from_profile():
    p = SYNTH_PROFILE
    rules = p.via_rules()
    assert {(r.a, r.b) for r in rules} == {("101/0", "104/0"), ("104/0", "106/0")}
    assert all(r.footprint_um == (p.via_pad_um, p.via_pad_um) for r in rules)
    spec = p.connectivity_spec()
    assert spec.conductors == p.routing_layers
    assert spec.vias == p.vias


def test_profile_rejects_via_on_non_routing_layer():
    with pytest.raises(ValueError):
        ProcessProfile(routing_layers=("1/0", "2/0"), gate_layer="1/0", sd_layer="2/0",
                       channel_layer="3/0", vias=(("1/0", "9/0", "8/0"),),  # 8/0 not routing
                       wire_width_um=5.0, wire_clear_um=2.0, via_pad_um=5.0,
                       litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0)


def test_profile_is_general_over_a_different_process():
    # a different process (e.g. a fine-pitch tier): zero code change
    p = ProcessProfile(
        routing_layers=("201/0", "204/0"), gate_layer="201/0", sd_layer="204/0",
        channel_layer="203/0", vias=(("201/0", "202/0", "204/0"),),
        wire_width_um=0.2, wire_clear_um=0.1, via_pad_um=0.15,
        litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0)
    assert p.cut_layer("201/0", "204/0") == 202
    assert p.connectivity_spec().conductors == ("201/0", "204/0")
