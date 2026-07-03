"""Unit tests for the demand-driven floorplan derivation (general, offline).

These lock the algorithm's generality: it computes the grid + row pitch from
ANY netlist's gate count, crossing demand, and device geometry -- no
circuit-specific constants. No KLayout, no routing.
"""

import math

import pytest

from klink.routing.grid.floorplan import (
    derive_grid, derive_row_pitch, gate_stack_height_um, peak_crossing,
)

# minimal device geometry stand-in: D on +y, S on -y (only fields the code reads)
TERMS = {
    "t_small": {"D": {"center": [0.0, 5.5]}, "S": {"center": [0.0, -5.5]}},
    "t_big":   {"D": {"center": [0.0, 7.5]}, "S": {"center": [0.0, -7.5]}},
}


def _netlist(gates):
    """gates: list of (gate_id, [instance device cells], {net: [terminal refs]}).
    Build the minimal {groups, nets, instances} the derivation reads."""
    groups, instances, nets = [], [], {}
    n = 0
    for gi, (cells, conn) in enumerate(gates):
        ids = []
        for c in cells:
            n += 1
            iid = f"X{n}"
            ids.append(iid)
            instances.append({"instance_id": iid, "device_cell": c})
        groups.append({"group": f"g{gi}", "gate_type": "X", "instances": ids})
        for net, refs in conn.items():
            nets.setdefault(net, []).extend(refs)
    return {
        "groups": groups,
        "instances": instances,
        "nets": [{"net_id": k, "terminals": v} for k, v in nets.items()],
    }


@pytest.mark.parametrize("n,expect_min_cells", [(1, 1), (9, 9), (12, 12), (27, 27), (100, 100)])
def test_derive_grid_fits_and_squareish(n, expect_min_cells):
    rows, cols = derive_grid(n)
    assert rows * cols >= n                  # every gate has a slot
    assert cols >= rows                       # slightly wider than tall
    # near-square: neither dimension wildly larger than sqrt(n)
    assert cols <= math.ceil(math.sqrt(n)) + 1


def test_derive_grid_known():
    assert derive_grid(12) == (3, 4)
    assert derive_grid(9) == (3, 3)


def test_gate_stack_height_scales_with_slots_and_device():
    one = _netlist([(["t_big"], {})])
    three = _netlist([(["t_big", "t_big", "t_big"], {})])
    h1 = gate_stack_height_um(one, TERMS, y_step=30.0)
    h3 = gate_stack_height_um(three, TERMS, y_step=30.0)
    assert h1 == pytest.approx(15.0)          # 0*30 + 7.5 - (-7.5)
    assert h3 == pytest.approx(75.0)          # 2*30 + 7.5 - (-7.5)
    # bigger device -> taller stack
    small = _netlist([(["t_small"], {})])
    assert gate_stack_height_um(small, TERMS, y_step=30.0) == pytest.approx(11.0)


def test_peak_crossing_counts_boundary_demand():
    # 4 gates, 2x2 grid: row0 = g0,g1 ; row1 = g2,g3
    # net "a" touches g0 (row0) and g2 (row1) -> crosses; "b" only row0 -> no.
    nl = _netlist([
        (["t_big"], {"a": ["X1.G"], "b": ["X1.S"]}),   # g0 row0
        (["t_big"], {"c": ["X2.G"]}),                   # g1 row0
        (["t_big"], {"a": ["X3.D"]}),                   # g2 row1 (a crosses)
        (["t_big"], {"c": ["X4.D"]}),                   # g3 row1 (c crosses)
    ])
    assert peak_crossing(nl, rows=2, cols=2) == 2       # a and c cross 0->1
    assert peak_crossing(nl, rows=2, cols=2, exclude=("a",)) == 1


def test_derive_row_pitch_general_over_layer_count():
    # more routing layers -> same crossing demand fits a narrower channel ->
    # smaller (or equal) row pitch. Layer count is an INPUT, never hardcoded.
    nl = _netlist([
        (["t_big", "t_big"], {"VDD": ["X1.D"], "n1": ["X1.S", "X2.G"]}),
        (["t_big", "t_big"], {"VDD": ["X3.D"], "n1": ["X3.G"]}),
    ])
    common = dict(y_step=30.0, width_um=5.0, wire_clear_um=2.0, via_pad_um=5.0)
    stack = gate_stack_height_um(nl, TERMS, y_step=30.0)
    rp2 = derive_row_pitch(nl, 2, 1, TERMS, n_horiz_layers=2, **common)
    rp3 = derive_row_pitch(nl, 2, 1, TERMS, n_horiz_layers=3, **common)
    rp5 = derive_row_pitch(nl, 2, 1, TERMS, n_horiz_layers=5, **common)
    assert rp2 >= rp3 >= rp5 >= stack          # monotone in layer count, clears stack


def test_derive_row_pitch_is_pure_function_of_inputs():
    # generality: same inputs -> same number, run to run (no hidden state)
    nl = _netlist([(["t_big"], {"a": ["X1.G"]}), (["t_big"], {"a": ["X2.D"]})])
    common = dict(y_step=30.0, width_um=5.0, wire_clear_um=2.0, via_pad_um=5.0, n_horiz_layers=2)
    a = derive_row_pitch(nl, 2, 1, TERMS, **common)
    b = derive_row_pitch(nl, 2, 1, TERMS, **common)
    assert a == b
