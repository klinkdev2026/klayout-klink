"""Offline tests for the L-Edit device starter's geometry.

`examples_klink/public/ledit_bridge/draw_device_demo.py` keeps its geometry
in a pure function, so the numbers a user copies can be checked without
L-Edit, a bridge, or a design. Run base + expanded per
docs/TESTING_PLAYBOOK.md: the finger count is the parameter that changes
how many shapes exist, so an off-by-one in the count law is exactly the
bug this catches.
"""
from __future__ import annotations

import pytest

from examples_klink.public.ledit_bridge.draw_device_demo import (
    CONTACT_UM, DIFF_H_UM, GATE_W_UM, LAYERS, POLY_OVERHANG_UM,
    diff_width, sd_regions, two_finger_mosfet)


def by_layer(items):
    out = {}
    for it in items:
        out.setdefault(it["layer"], []).append(it["bbox_um"])
    return out


@pytest.mark.parametrize("fingers", [2, 4])
def test_shape_count_law(fingers):
    # implant + active
    #   + N poly fingers + N poly landing pads (a contact is wider than a gate)
    #   + (N+1) source/drain straps + 1 gate bus
    #   + 2*(N+1) source/drain contacts + N gate contacts
    expected = 2 + 2 * fingers + (fingers + 2) + (2 * (fingers + 1) + fingers)
    items = two_finger_mosfet(fingers)
    assert len(items) == expected == 6 * fingers + 6
    g = by_layer(items)
    assert len(g["POLY"]) == 2 * fingers
    assert len(g["MET1"]) == (fingers + 1) + 1        # straps + gate bus
    assert len(g["CONTACT"]) == 2 * (fingers + 1) + fingers


@pytest.mark.parametrize("fingers", [2, 4])
def test_layers_used_are_all_declared(fingers):
    declared = {name for name, _, _, _ in LAYERS}
    used = set(by_layer(two_finger_mosfet(fingers)))
    assert used <= declared, "starter draws on a layer it never ensures"


@pytest.mark.parametrize("fingers", [2, 4])
def test_poly_crosses_the_active_edge_to_edge(fingers):
    """A gate that does not overhang the diffusion is not a transistor."""
    g = by_layer(two_finger_mosfet(fingers))
    (ax0, ay0, ax1, ay1), = g["ACTIVE"]
    fingers_only = [p for p in g["POLY"] if p[1] < ay0]   # pads start above
    assert len(fingers_only) == fingers
    for px0, py0, px1, py1 in fingers_only:
        assert py0 < ay0 and py1 > ay1, "poly must overhang active on both ends"
        assert ax0 < px0 and px1 < ax1, "gate must sit inside the diffusion"
        assert px1 - px0 == pytest.approx(GATE_W_UM)
    assert ay1 - ay0 == pytest.approx(DIFF_H_UM)
    assert POLY_OVERHANG_UM > 0


@pytest.mark.parametrize("fingers", [2, 4])
def test_implant_encloses_active(fingers):
    g = by_layer(two_finger_mosfet(fingers))
    (nx0, ny0, nx1, ny1), = g["NPLUS"]
    (ax0, ay0, ax1, ay1), = g["ACTIVE"]
    assert nx0 < ax0 and ny0 < ay0 and nx1 > ax1 and ny1 > ay1


@pytest.mark.parametrize("fingers", [2, 4])
def test_every_contact_sits_inside_a_metal_strap(fingers):
    """A floating contact is the classic silent layout bug."""
    g = by_layer(two_finger_mosfet(fingers))
    straps = g["MET1"]
    for cx0, cy0, cx1, cy1 in g["CONTACT"]:
        assert cx1 - cx0 == pytest.approx(CONTACT_UM)
        assert any(mx0 <= cx0 and cx1 <= mx1 and my0 <= cy0 and cy1 <= my1
                   for mx0, my0, mx1, my1 in straps), \
            "contact at %s is not covered by any MET1 strap" % ((cx0, cy0),)


def test_rejects_zero_fingers():
    with pytest.raises(ValueError):
        two_finger_mosfet(0)


# --------------------------------------------------------------------------
# Blind tests found two real defects here that the checks above all passed:
# source/drain straps placed on a fixed pitch rode OVER the gates and off the
# active entirely, and the gate contact landed on nothing because a contact is
# wider than the gate it sits on. Both are invisible to "is the contact inside
# some metal" -- these pin the relationships that actually make it a device.
# --------------------------------------------------------------------------

def inside(inner, outer):
    return (outer[0] <= inner[0] and inner[2] <= outer[2]
            and outer[1] <= inner[1] and inner[3] <= outer[3])


def split(fingers):
    g = by_layer(two_finger_mosfet(fingers))
    active = g["ACTIVE"][0]
    sd = [c for c in g["CONTACT"] if c[3] <= active[3]]
    gate = [c for c in g["CONTACT"] if c[1] >= active[3]]
    return g, active, sd, gate


@pytest.mark.parametrize("fingers", [1, 2, 4])
def test_source_drain_contacts_land_on_the_silicon(fingers):
    """A contact off the active connects to nothing at all."""
    _g, active, sd, _gate = split(fingers)
    assert len(sd) == 2 * (fingers + 1)
    off = [c for c in sd if not inside(c, active)]
    assert not off, "source/drain contacts outside ACTIVE %s: %s" % (active, off)


@pytest.mark.parametrize("fingers", [1, 2, 4])
def test_source_drain_straps_never_cross_a_gate(fingers):
    """A strap riding over the poly shorts source to drain through metal."""
    g, active, _sd, _gate = split(fingers)
    straps = [m for m in g["MET1"] if m[3] <= active[3]]
    assert len(straps) == fingers + 1
    for s in straps:
        assert active[0] <= s[0] and s[2] <= active[2],             "strap %s leaves the active %s" % (s, active)
        for poly in g["POLY"]:
            apart = (s[2] <= poly[0] or s[0] >= poly[2]
                     or s[3] <= poly[1] or s[1] >= poly[3])
            assert apart, "strap %s overlaps poly %s" % (s, poly)


@pytest.mark.parametrize("fingers", [1, 2, 4])
def test_every_gate_is_actually_contacted(fingers):
    """The contact is WIDER than the gate, so the poly needs a landing pad;
    without one the gate contact sits on nothing and the gate floats."""
    g, _active, _sd, gate = split(fingers)
    assert len(gate) == fingers
    for c in gate:
        assert any(inside(c, p) for p in g["POLY"]),             "gate contact %s lands on no poly" % (c,)
        assert any(inside(c, m) for m in g["MET1"]),             "gate contact %s is not covered by metal" % (c,)


@pytest.mark.parametrize("fingers", [1, 2, 4])
def test_source_drain_regions_tile_the_gaps_between_gates(fingers):
    """The regions are derived from the gate positions; a fixed pitch is what
    let the straps drift off the device in the first place."""
    regions = sd_regions(fingers)
    assert len(regions) == fingers + 1
    assert regions[0][0] == 0.0
    assert regions[-1][1] == pytest.approx(diff_width(fingers))
    for (a0, a1), (b0, b1) in zip(regions, regions[1:]):
        assert a1 < b0, "regions %s and %s are not separated by a gate" % (
            (a0, a1), (b0, b1))
        assert b0 - a1 == pytest.approx(GATE_W_UM)


def test_a_region_too_narrow_for_a_contact_is_refused():
    """Silence here is how the bad layout shipped: better to fail loudly."""
    import examples_klink.public.ledit_bridge.draw_device_demo as demo
    old = demo.DIFF_MARGIN_UM
    demo.DIFF_MARGIN_UM = 0.05          # cannot hold a contact + enclosure
    try:
        with pytest.raises(ValueError) as exc:
            demo.two_finger_mosfet(2)
        assert "too narrow" in str(exc.value)
    finally:
        demo.DIFF_MARGIN_UM = old
