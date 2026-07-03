"""PUBLIC test: build a KLayout-native reference netlist from a device netlist.
Offline (klayout.db only), no lab data: maps an inline gate netlist via the
synthetic library and asserts the pya netlist structure + terminal wiring.
"""
import pytest

pya = pytest.importorskip("klayout.db")

from klink.domains.structdevice.logic_map import map_logic_to_devices
from klink.domains.structdevice.reference_netlist import (
    build_reference_netlist,
    device_classes,
)

from synth_pdk import SYNTH_LIBRARY

DEVICE_TERMINALS = {"dev_load": ["G", "S", "D"], "dev_drv": ["G", "S", "D"]}


def _top(netlist):
    circuits = list(netlist.each_circuit())
    assert len(circuits) == 1
    return circuits[0]


def test_device_classes_are_parameterized_by_terminal_names():
    classes = device_classes({"triode_like": ["alpha", "beta", "sense"]})
    cls = classes["triode_like"]
    assert cls.name == "triode_like"
    assert [term.name for term in cls.terminal_definitions()] == ["alpha", "beta", "sense"]


def test_inv_device_netlist_builds_reference_pya_netlist():
    device_netlist = map_logic_to_devices(
        {"gates": [{"type": "INV", "inputs": {"A": "IN"}, "output": "OUT"}]},
        SYNTH_LIBRARY,
    )
    netlist = build_reference_netlist(device_netlist, DEVICE_TERMINALS, top_name="INV_TOP")
    circuit = _top(netlist)

    assert circuit.name == "INV_TOP"
    assert len(list(circuit.each_device())) == 2          # dev_load + dev_drv
    assert len(list(circuit.each_net())) == 4             # OUT, IN, VDD, GND
    assert circuit.device_by_name("X2").net_for_terminal("G").name == "IN"   # driver gate
    assert circuit.device_by_name("X1").net_for_terminal("D").name == "VDD"  # load drain
    assert circuit.device_by_name("X2").net_for_terminal("S").name == "GND"  # driver source
    assert circuit.net_by_name("OUT").terminal_count() == 3                  # X1.S, X1.G, X2.D


def test_unknown_terminal_is_rejected():
    with pytest.raises(Exception, match="not declared"):
        build_reference_netlist(
            {
                "instances": [{"instance_id": "X1", "device_cell": "box_device"}],
                "nets": [{"net_id": "N1", "terminals": ["X1.RIGHT"]}],
            },
            {"box_device": ["LEFT"]},
        )
