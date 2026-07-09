"""Offline tests for stack25d.stack_displays (StackSpec + z table -> the
view.show_25d display list). Pure-Python; the live RPC is exercised by the
public example against a running KLayout."""
from __future__ import annotations

import pytest

from klink.process_stack import StackSpec, StackError
from klink.stack25d import stack_displays


@pytest.fixture()
def stack() -> StackSpec:
    return StackSpec.from_dict({
        "conductors": [
            {"layer": "31/0", "role": "metalA"},
            {"layer": "33/0", "role": "metalB"},
        ],
        "vias": [{"from": "31/0", "via_layer": "32/0", "to": "33/0",
                  "via_cell": "VIA_A"}],
    })


Z = {"31/0": (0.0, 0.5), "32/0": (0.5, 1.0), "33/0": (1.0, 1.5)}


def test_displays_cover_conductors_and_vias_sorted_by_z(stack):
    d = stack_displays(stack, Z)
    assert [x["layer"] for x in d] == ["31/0", "32/0", "33/0"]   # z order
    assert d[0]["name"] == "metalA"                # role becomes the name
    assert d[1]["name"] == "via 31/0<->33/0"       # via default name
    assert d[0]["zstart_um"] == 0.0 and d[2]["zstop_um"] == 1.5


def test_missing_z_entry_is_instructive(stack):
    with pytest.raises(StackError) as e:
        stack_displays(stack, {"31/0": (0.0, 0.5), "33/0": (1.0, 1.5)})
    assert "32/0" in str(e.value)
    assert "process facts" in str(e.value)
    # ...and the named escape works:
    d = stack_displays(stack, {"31/0": (0.0, 0.5), "33/0": (1.0, 1.5)},
                       include_vias=False)
    assert [x["layer"] for x in d] == ["31/0", "33/0"]


def test_inverted_z_range_rejected(stack):
    bad = dict(Z, **{"32/0": (1.0, 0.5)})
    with pytest.raises(StackError) as e:
        stack_displays(stack, bad)
    assert "zstop" in str(e.value)


def test_colors_names_and_extra_layers(stack):
    d = stack_displays(
        stack, dict(Z, **{"90/0": (-0.2, 0.0)}),
        colors={"31/0": 0x2B6CB0}, names={"33/0": "top metal"},
        extra_layers=["90/0"])
    by_layer = {x["layer"]: x for x in d}
    assert by_layer["31/0"]["color"] == 0x2B6CB0
    assert "color" not in by_layer["33/0"]
    assert by_layer["33/0"]["name"] == "top metal"
    assert by_layer["90/0"]["zstart_um"] == -0.2   # substrate below zero

def test_mapping_style_z_accepted(stack):
    d = stack_displays(stack, {
        "31/0": {"zstart_um": 0.0, "zstop_um": 0.5},
        "32/0": {"zstart_um": 0.5, "zstop_um": 1.0},
        "33/0": {"zstart_um": 1.0, "zstop_um": 1.5},
    })
    assert len(d) == 3
