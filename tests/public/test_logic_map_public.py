"""PUBLIC test: logic_map -- expand a gate netlist into a device netlist via a
device library. Offline, no KLayout, no lab data: uses the synthetic
nmos-diode-load library from tests/public/synth_pdk.py and inline gate netlists.
"""
from __future__ import annotations

import copy

import pytest

from klink.domains.structdevice.logic_map import (
    LogicMapError,
    map_logic_to_devices,
    validate_device_netlist,
)

from synth_pdk import SYNTH_LIBRARY


def test_inv_expands_deterministically_and_validates():
    netlist = {"gates": [{"type": "INV", "inputs": {"A": "IN"}, "output": "OUT"}]}

    first = map_logic_to_devices(netlist, SYNTH_LIBRARY)
    second = map_logic_to_devices(copy.deepcopy(netlist),
                                  copy.deepcopy(SYNTH_LIBRARY))
    assert first == second
    validate_device_netlist(first)

    assert first["instances"] == [
        {"instance_id": "X1", "device_cell": "dev_load"},
        {"instance_id": "X2", "device_cell": "dev_drv"},
    ]
    nets = {n["net_id"]: n["terminals"] for n in first["nets"]}
    assert nets["OUT"] == ["X1.S", "X1.G", "X2.D"]
    assert nets["IN"] == ["X2.G"]
    assert nets["VDD"] == ["X1.D"]
    assert nets["GND"] == ["X2.S"]


def test_nand2_expands_and_validates():
    netlist = {"gates": [
        {"type": "NAND2", "inputs": {"A": "a", "B": "b"}, "output": "y"},
    ]}
    result = map_logic_to_devices(netlist, SYNTH_LIBRARY)
    validate_device_netlist(result)

    cells = sorted(i["device_cell"] for i in result["instances"])
    assert cells == ["dev_drv", "dev_drv", "dev_load"]   # 1 load + 2 drivers
    nets = {n["net_id"]: n["terminals"] for n in result["nets"]}
    assert nets["a"] == ["X2.G"]        # drvA gate
    assert nets["b"] == ["X3.G"]        # drvB gate
    assert set(nets) >= {"y", "a", "b", "VDD", "GND"}


def test_unknown_gate_is_an_instructive_error():
    with pytest.raises(LogicMapError):
        map_logic_to_devices(
            {"gates": [{"type": "XNOR3", "inputs": {}, "output": "Z"}]},
            SYNTH_LIBRARY,
        )
