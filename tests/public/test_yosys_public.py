"""PUBLIC test: the Verilog -> device-netlist flow (yosys techmap + liberty
generation + the existing logic mapper). The script/liberty generators are
pure-Python and always run; the yosys-invoking cases skip when no yosys binary
is discoverable (install yowasp-yosys). No lab data (library from synth_pdk).
"""
import pytest

from klink.domains.structdevice.logic_map import map_logic_to_devices
from klink.domains.structdevice.yosys_flow import (
    YosysFlowError,
    discover_yosys,
    run_yosys,
    verilog_to_device_netlist,
    write_liberty,
    write_techmap_script,
)

from synth_pdk import SYNTH_LIBRARY


def _yosys_or_skip():
    try:
        return discover_yosys()
    except YosysFlowError:
        pytest.skip("no yosys binary discoverable (install yowasp-yosys)")


def test_inline_gate_netlist_maps_through_logic_mapper():
    result = map_logic_to_devices(
        {"gates": [{"type": "INV", "inputs": {"A": "A"}, "output": "Y"}]},
        SYNTH_LIBRARY,
    )
    assert result["instances"] == [
        {"instance_id": "X1", "device_cell": "dev_load"},
        {"instance_id": "X2", "device_cell": "dev_drv"},
    ]
    nets = {n["net_id"]: n["terminals"] for n in result["nets"]}
    assert nets == {
        "Y": ["X1.S", "X1.G", "X2.D"],
        "A": ["X2.G"],
        "VDD": ["X1.D"],
        "GND": ["X2.S"],
    }


def test_write_techmap_script_contains_required_yosys_steps(tmp_path):
    script = write_techmap_script(
        tmp_path / "top.v", "top", tmp_path / "top.json", tmp_path / "gates.lib",
        gate_set=("INV", "NAND2"),
    )
    assert "read_verilog" in script
    assert "hierarchy -check -top top" in script
    assert "\nproc\n" in script
    assert "\nopt\n" in script
    assert "\ntechmap\n" in script
    assert "abc -liberty" in script and "gates.lib" in script
    assert "\nsplitnets -ports\n" in script
    assert "write_json" in script


def test_write_liberty_defines_named_cells_and_adds_buffer(tmp_path):
    lib = tmp_path / "gates.lib"
    text = write_liberty(("INV", "NAND2", "NOR2"), lib)
    assert lib.exists()
    for cell in ("cell(INV)", "cell(NAND2)", "cell(NOR2)", "cell(BUF)"):
        assert cell in text
    assert "!(A*B)" in text


def test_write_liberty_requires_inverter():
    with pytest.raises(YosysFlowError):
        write_liberty(("NAND2",), "ignored.lib")


def test_run_yosys_runs_when_discoverable():
    _yosys_or_skip()
    proc = run_yosys("help\n")
    assert proc.returncode == 0


def test_verilog_to_device_netlist_when_yosys_discoverable(tmp_path):
    _yosys_or_skip()
    verilog = tmp_path / "top.v"
    verilog.write_text("module top(input A, output Y); assign Y = ~A; endmodule\n",
                       encoding="utf-8")
    result = verilog_to_device_netlist(verilog, "top", SYNTH_LIBRARY,
                                       out_json=tmp_path / "top.json")
    assert len(result["instances"]) >= 1
