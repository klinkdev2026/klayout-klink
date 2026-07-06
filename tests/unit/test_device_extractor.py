from pathlib import Path

import pytest

pya = pytest.importorskip("klayout.db")

from klink.domains.structdevice.device_extractor import register_device_extractors


def _write_fixture(path: Path) -> None:
    layout = pya.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    device = layout.create_cell("generic_pair")
    left_metal = layout.layer(10, 0)
    right_metal = layout.layer(20, 0)

    device.shapes(left_metal).insert(pya.Box(0, 0, 1000, 1000))
    device.shapes(right_metal).insert(pya.Box(9000, 0, 10000, 1000))

    top.insert(pya.CellInstArray(device.cell_index(), pya.Trans(pya.Vector(0, 0))))
    top.insert(pya.CellInstArray(device.cell_index(), pya.Trans(pya.Vector(20000, 0))))

    top.shapes(left_metal).insert(pya.Box(500, -2000, 20500, -1000))
    top.shapes(left_metal).insert(pya.Box(500, -1000, 600, 0))
    top.shapes(left_metal).insert(pya.Box(20500, -1000, 20600, 0))
    top.shapes(right_metal).insert(pya.Box(9500, 2000, 29500, 3000))
    top.shapes(right_metal).insert(pya.Box(9500, 1000, 9600, 2000))
    top.shapes(right_metal).insert(pya.Box(29500, 1000, 29600, 2000))
    layout.write(str(path))


def _extract(path: Path):
    layout = pya.Layout()
    layout.read(str(path))
    top = layout.cell("TOP")
    left_metal = layout.layer(10, 0)
    right_metal = layout.layer(20, 0)
    l2n = pya.LayoutToNetlist(pya.RecursiveShapeIterator(layout, top, [left_metal, right_metal]))

    registrations = register_device_extractors(
        l2n,
        device_terminals={"generic_pair": ["left", "right"]},
        terminal_layers={"generic_pair": {"left": (10, 0), "right": (20, 0)}},
        layout=layout,
        top_cell=top,
    )
    left = registrations[0].layers["left"]
    right = registrations[0].layers["right"]
    l2n.connect(left)
    l2n.connect(right)
    l2n.extract_netlist()
    return registrations, l2n, l2n.netlist()


def _top(netlist):
    circuits = list(netlist.each_circuit())
    matches = [circuit for circuit in circuits if circuit.name == "TOP"]
    assert len(matches) == 1
    return matches[0]


def test_generic_device_extractor_creates_devices_and_terminal_nets(tmp_path):
    gds = tmp_path / "device_cells.gds"
    _write_fixture(gds)

    registrations, _l2n, netlist = _extract(gds)
    circuit = _top(netlist)
    devices = list(circuit.each_device())

    assert [item.cell_name for item in registrations] == ["generic_pair"]
    assert len(devices) == 2
    assert all(device.device_class().name == "generic_pair" for device in devices)
    assert all(device.device_class().terminal_id("left") >= 0 for device in devices)
    assert all(device.device_class().terminal_id("right") >= 0 for device in devices)

    left_nets = {device.net_for_terminal("left").expanded_name() for device in devices}
    right_nets = {device.net_for_terminal("right").expanded_name() for device in devices}
    assert len(left_nets) == 1
    assert len(right_nets) == 1
    assert left_nets != right_nets


def test_generic_device_extractor_is_deterministic(tmp_path):
    gds = tmp_path / "device_cells.gds"
    _write_fixture(gds)

    _, _first_l2n, first = _extract(gds)
    _, _second_l2n, second = _extract(gds)

    def signature(netlist):
        circuit = _top(netlist)
        return sorted(
            (
                device.device_class().name,
                device.net_for_terminal("left").expanded_name(),
                device.net_for_terminal("right").expanded_name(),
            )
            for device in circuit.each_device()
        )

    assert signature(first) == signature(second)


def test_register_without_explicit_layout_matches_klayout_capability(tmp_path):
    # klayout >= 0.29 exposes LayoutToNetlist.original_layout, so layout=/
    # top_cell= may be omitted; older klayout has no original-layout accessor
    # and must get an instructive error naming the exact fix.
    gds = tmp_path / "device_cells.gds"
    _write_fixture(gds)

    layout = pya.Layout()
    layout.read(str(gds))
    top = layout.cell("TOP")
    left_metal = layout.layer(10, 0)
    right_metal = layout.layer(20, 0)
    l2n = pya.LayoutToNetlist(pya.RecursiveShapeIterator(layout, top, [left_metal, right_metal]))

    kwargs = dict(
        device_terminals={"generic_pair": ["left", "right"]},
        terminal_layers={"generic_pair": {"left": (10, 0), "right": (20, 0)}},
    )
    if hasattr(l2n, "original_layout"):
        registrations = register_device_extractors(l2n, **kwargs)
        assert len(registrations) == 1
    else:
        with pytest.raises(Exception, match="pass layout= and top_cell="):
            register_device_extractors(l2n, **kwargs)
