"""PUBLIC test: pluggable transistor sizing (series drivers widened by stack
depth; load fixed). Scaling is by the NAMED parameter w_um -- no naming
convention. Offline, no lab data (library + devices + policy from synth_pdk).
"""
import pytest

from klink.domains.structdevice.logic_map import map_logic_to_devices
from klink.domains.structdevice.sizing import (
    AutoRatioSizing,
    ExplicitSizing,
    SimulationSizing,
    SizingError,
    apply_sizing,
    series_depth,
)

from synth_pdk import SYNTH_DEVICES, SYNTH_LIBRARY as LIB, SYNTH_SIZING


def test_series_depth_from_library():
    assert series_depth(LIB["gates"]["NAND2"]) == 2   # internal MID -> series
    assert series_depth(LIB["gates"]["NOR2"]) == 1     # parallel
    assert series_depth(LIB["gates"]["INV"]) == 1


def test_auto_ratio_selects_existing_keys_by_scaled_param():
    pol = SYNTH_SIZING
    assert pol.cell_for("NAND2", "drvA", LIB["gates"]["NAND2"]) == "dev_drv2"   # 7 * 2
    assert pol.cell_for("NAND2", "drvB", LIB["gates"]["NAND2"]) == "dev_drv2"
    assert pol.cell_for("NOR2", "drvA", LIB["gates"]["NOR2"]) == "dev_drv"       # 7 * 1
    assert pol.cell_for("INV", "drv", LIB["gates"]["INV"]) == "dev_drv"
    assert pol.cell_for("NAND2", "load", LIB["gates"]["NAND2"]) == "dev_load"


def test_auto_ratio_errors_when_scaled_device_absent():
    devs = {"u": {"params": {"w_um": 7.0, "l_um": 5.0}},
            "ld": {"params": {"w_um": 45.0, "l_um": 2.0}}}
    pol = AutoRatioSizing(devs, unit_key="u", load_key="ld", scale_param="w_um")
    assert pol.cell_for("INV", "drv", LIB["gates"]["INV"]) == "u"
    with pytest.raises(SizingError):
        pol.cell_for("NAND2", "drvA", LIB["gates"]["NAND2"])   # needs a 2x device


def test_auto_ratio_validates_keys_and_param():
    with pytest.raises(SizingError):
        AutoRatioSizing(SYNTH_DEVICES, unit_key="nope",
                        load_key="dev_load", scale_param="w_um")
    with pytest.raises(SizingError):
        AutoRatioSizing(SYNTH_DEVICES, unit_key="dev_drv",
                        load_key="dev_load", scale_param="nope")


def test_apply_sizing_nand2_chain():
    netlist = {"gates": [
        {"type": "NAND2", "inputs": {"A": "a", "B": "b"}, "output": "m1"},
        {"type": "NAND2", "inputs": {"A": "c", "B": "d"}, "output": "m2"},
        {"type": "NAND2", "inputs": {"A": "m1", "B": "m2"}, "output": "y"},
    ]}
    nl = map_logic_to_devices(netlist, LIB)      # 3 NAND2
    sized = apply_sizing(nl, LIB, SYNTH_SIZING)

    from collections import Counter
    counts = Counter(i["device_cell"] for i in sized["instances"])
    assert counts["dev_load"] == 3           # 3 loads (fixed)
    assert counts["dev_drv2"] == 6           # 3 NAND2 * 2 series drivers, widened 2x
    assert "dev_drv" not in counts           # no unit-width driver remains
    assert set(sized["required_cells"]) == {"dev_drv2", "dev_load"}
    assert len(sized["instances"]) == len(nl["instances"])


def test_explicit_override():
    pol = ExplicitSizing({("NAND2", "drvA"): "dev_drv3"}, SYNTH_SIZING)
    assert pol.cell_for("NAND2", "drvA", LIB["gates"]["NAND2"]) == "dev_drv3"
    assert pol.cell_for("NAND2", "drvB", LIB["gates"]["NAND2"]) == "dev_drv2"


def test_simulation_sizing_is_reserved_interface():
    with pytest.raises(SizingError):
        SimulationSizing().cell_for("INV", "drv", LIB["gates"]["INV"])
