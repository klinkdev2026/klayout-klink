"""Synthetic, IP-free process fixture for the PUBLIC test suite.

Mirrors the SHAPE of a lab pdk -- a ``ProcessProfile`` + ``StackSpec`` + a
logic-map device library -- with obviously-synthetic, generic values, so the
public suite can exercise the shipped klink mechanisms (process profile, stack
parser, logic map) without any lab/device IP.

The 101/104/106 compact stack is the same generic public stack the shipped
`fit_device_pnr_lvs` demo uses; the device keys here (`dev_load` / `dev_drv`)
are synthetic placeholders, not real cells. Imported by the `*_public` tests in
this directory (no lab/device-IP import anywhere in the public suite).
"""
from klink.process_stack import StackSpec
from klink.routing.grid.process_profile import ProcessProfile

# A synthetic 3-layer back-gate-style process: gate 101, source/drain 104,
# one upper routing layer 106; vias 102 (101<->104) and 105 (104<->106).
SYNTH_PROFILE = ProcessProfile(
    routing_layers=("101/0", "104/0", "106/0"),
    gate_layer="101/0",
    sd_layer="104/0",
    channel_layer="103/0",
    vias=(("101/0", "102/0", "104/0"), ("104/0", "105/0", "106/0")),
    layer_directions={"101/0": "V", "104/0": "H", "106/0": "V"},
    wire_width_um=5.0,
    wire_clear_um=2.0,
    prl_spacing_um=10.0,
    prl_length_um=15.0,
    via_pad_um=5.0,
    litho_tol_um=1.0,
    y_step_um=30.0,
    col_pitch_um=100.0,
    margin_um=60.0,
)

SYNTH_STACK = StackSpec.from_dict({
    "conductors": [
        {"layer": "101/0", "role": "gate", "prefer": "crossunder"},
        {"layer": "104/0", "role": "sd", "prefer": "signal"},
        {"layer": "106/0", "role": "pad", "prefer": "power"},
    ],
    "vias": [
        {"from": "101/0", "via_layer": "102/0", "to": "104/0",
         "via_cell": "via12_cell"},
        {"from": "104/0", "via_layer": "105/0", "to": "106/0",
         "via_cell": "via_small_pad"},
    ],
    "order": ["106/0", "105/0", "104/0", "102/0", "101/0", "103/0"],
})

# Synthetic logic-map library (nmos-diode-load SHAPE) with synthetic device keys.
SYNTH_LIBRARY = {
    "family": "synthetic_diode_load",
    "gates": {
        "INV": {
            "devices": {"load": "dev_load", "drv": "dev_drv"},
            "internal_nets": [],
            "output_ports": ["Y"],
            "connect": {
                "Y": ["load.S", "load.G", "drv.D"],
                "A": ["drv.G"],
                "VDD": ["load.D"],
                "GND": ["drv.S"],
            },
        },
        "NAND2": {
            "devices": {"load": "dev_load", "drvA": "dev_drv", "drvB": "dev_drv"},
            "internal_nets": ["MID"],          # series stack -> depth 2
            "output_ports": ["Y"],
            "connect": {
                "Y": ["load.S", "load.G", "drvA.D"],
                "MID": ["drvA.S", "drvB.D"],
                "A": ["drvA.G"],
                "B": ["drvB.G"],
                "VDD": ["load.D"],
                "GND": ["drvB.S"],
            },
        },
        "NOR2": {
            "devices": {"load": "dev_load", "drvA": "dev_drv", "drvB": "dev_drv"},
            "internal_nets": [],               # parallel -> depth 1
            "output_ports": ["Y"],
            "connect": {
                "Y": ["load.S", "load.G", "drvA.D", "drvB.D"],
                "A": ["drvA.G"],
                "B": ["drvB.G"],
                "VDD": ["load.D"],
                "GND": ["drvA.S", "drvB.S"],
            },
        },
    },
}

# Synthetic device set for sizing: keys at w_um = unit(7), 2x(14), 3x(21), and
# the fixed load(45). AutoRatioSizing SELECTS the key whose w_um is the unit
# driver's w_um times the series depth (no naming convention).
SYNTH_DEVICES = {
    "dev_drv":  {"params": {"w_um": 7.0, "l_um": 5.0}},
    "dev_drv2": {"params": {"w_um": 14.0, "l_um": 5.0}},
    "dev_drv3": {"params": {"w_um": 21.0, "l_um": 5.0}},
    "dev_load": {"params": {"w_um": 45.0, "l_um": 2.0}},
}


def _synth_sizing():
    from klink.domains.structdevice.sizing import AutoRatioSizing
    return AutoRatioSizing(
        SYNTH_DEVICES, unit_key="dev_drv", load_key="dev_load",
        scale_param="w_um", max_mult=3)


SYNTH_SIZING = _synth_sizing()
