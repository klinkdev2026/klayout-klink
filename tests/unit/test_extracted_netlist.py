from pathlib import Path

import pytest

pya = pytest.importorskip("klayout.db")

from klink.domains.structdevice.extracted_netlist import build_extracted_netlist


DEVICE_INSTANCES = [
    {"instance_id": "X1", "device_cell": "two_terminal"},
    {"instance_id": "X2", "device_cell": "two_terminal"},
]
DEVICE_TERMINALS = {"two_terminal": ["left", "right"]}


def _write_fixture(path: Path) -> None:
    layout = pya.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    m1 = layout.layer(10, 0)
    via = layout.layer(15, 0)
    m2 = layout.layer(20, 0)
    top.shapes(m1).insert(pya.Box(0, 0, 20000, 2000))
    top.shapes(m1).insert(pya.Box(0, 10000, 4000, 12000))
    top.shapes(via).insert(pya.Box(1000, 10500, 3000, 11500))
    top.shapes(m2).insert(pya.Box(1000, 10000, 20000, 12000))
    layout.write(str(path))


def _top(netlist):
    circuits = list(netlist.each_circuit())
    assert len(circuits) == 1
    return circuits[0]


def test_extracted_netlist_binds_terminals_to_layout_nets(tmp_path):
    gds = tmp_path / "two_net.gds"
    _write_fixture(gds)

    netlist = build_extracted_netlist(
        gds,
        "TOP",
        conductors=("10/0", "20/0"),
        vias=(("10/0", "15/0", "20/0"),),
        device_instances=DEVICE_INSTANCES,
        device_terminals=DEVICE_TERMINALS,
        terminal_points={
            "X1.left": (1.0, 1.0, "10/0"),
            "X2.left": (19.0, 1.0, "10/0"),
            "X1.right": (1.0, 11.0, "10/0"),
            "X2.right": (19.0, 11.0, "20/0"),
        },
    )
    circuit = _top(netlist)

    assert len(list(circuit.each_device())) == 2
    assert len(list(circuit.each_net())) == 2
    assert circuit.device_by_name("X1").net_for_terminal("left").name == circuit.device_by_name("X2").net_for_terminal("left").name
    assert circuit.device_by_name("X1").net_for_terminal("right").name == circuit.device_by_name("X2").net_for_terminal("right").name
    assert circuit.device_by_name("X1").net_for_terminal("left").name != circuit.device_by_name("X1").net_for_terminal("right").name


def test_extracted_netlist_rejects_floating_terminal(tmp_path):
    gds = tmp_path / "two_net.gds"
    _write_fixture(gds)

    with pytest.raises(Exception, match="floating"):
        build_extracted_netlist(
            gds,
            "TOP",
            conductors=("10/0", "20/0"),
            vias=(("10/0", "15/0", "20/0"),),
            device_instances=DEVICE_INSTANCES,
            device_terminals=DEVICE_TERMINALS,
            terminal_points={
                "X1.left": (1.0, 1.0, "10/0"),
                "X1.right": (99.0, 99.0, "10/0"),
                "X2.left": (19.0, 1.0, "10/0"),
                "X2.right": (19.0, 11.0, "20/0"),
            },
        )
