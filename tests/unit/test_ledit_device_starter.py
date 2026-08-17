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
    two_finger_mosfet)


def by_layer(items):
    out = {}
    for it in items:
        out.setdefault(it["layer"], []).append(it["bbox_um"])
    return out


@pytest.mark.parametrize("fingers", [2, 4])
def test_shape_count_law(fingers):
    # 1 implant + 1 active + N poly + (N+1) straps * (1 metal + 2 contacts)
    # + 1 gate bus
    expected = 2 + fingers + 3 * (fingers + 1) + 1
    items = two_finger_mosfet(fingers)
    assert len(items) == expected
    g = by_layer(items)
    assert len(g["POLY"]) == fingers
    assert len(g["MET1"]) == (fingers + 1) + 1        # straps + gate bus
    assert len(g["CONTACT"]) == 2 * (fingers + 1)


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
    for px0, py0, px1, py1 in g["POLY"]:
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
