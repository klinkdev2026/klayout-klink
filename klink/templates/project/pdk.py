"""Your process — the ONLY place process-specific facts live.

klink ships pure mechanism and holds ZERO process data. Every process fact
(layer numbers, via stacks, wire width/spacing, the device library, DRC
numbers) belongs HERE, and your build code in ``custom_devices/`` passes it
EXPLICITLY into the klink APIs. You never edit klink itself.

This file is a SKELETON. The values below are placeholders — an agent fills
them in for your domain during onboarding, or you edit them directly. It
imports cleanly so tooling works, but the numbers are not real until you
replace the ``TODO`` values with your process.

See ``recipes/README.md`` for which extra pieces each domain needs (e.g. a
device library for the P&R recipe, EBL geometry params for the EBL recipe).
"""

from __future__ import annotations

from klink.routing.grid.process_profile import ProcessProfile

# --- Routing process profile -------------------------------------------------
# layer strings are "layer/datatype". Replace every value with your process.
PROCESS = ProcessProfile(
    # TODO: your routing layers, coarse -> fine (or by stack order).
    routing_layers=("1/0", "2/0", "3/0"),
    # TODO: device-terminal layers (only needed if you place devices, e.g. P&R).
    gate_layer="1/0",
    sd_layer="2/0",
    channel_layer="1/0",
    # TODO: via stacks as (lower_layer, via_layer, upper_layer) tuples.
    vias=(("1/0", "12/0", "2/0"), ("2/0", "23/0", "3/0")),
    # TODO: preferred routing direction per layer ("H" or "V").
    layer_directions={"1/0": "V", "2/0": "H", "3/0": "V"},
    # TODO: real dimensions in microns.
    wire_width_um=1.0,
    wire_clear_um=1.0,
    prl_spacing_um=2.0,
    prl_length_um=4.0,
    via_pad_um=1.0,
    litho_tol_um=0.2,
    # --- FLOORPLAN DENSITY (P&R placement spacing) -- SMALLER = TIGHTER/DENSER ---
    # These set how far apart the placed devices sit. Shrink them to pack the
    # layout denser (less area); if it gets too tight the router leaves DRC
    # markers or fails to route, so back off until it is clean (94/94, 0 markers,
    # LVS match). The real row pitch is derived from y_step_um + device height +
    # routing channels.
    y_step_um=10.0,      # vertical: row-to-row spacing (rows closer together)
    col_pitch_um=20.0,   # horizontal: column-to-column spacing (gates closer)
)

# LVS connectivity is DERIVED from the profile — never duplicate it by hand.
CONNECTIVITY = PROCESS.connectivity_spec()


# --- Device library (ONLY for the digital P&R -> LVS recipe) ------------------
# A "device" is ANY cell with an arbitrary parameter set + terminals. klink
# assumes NO parameter names and NO count: a back-gate transistor uses
# (w_um, l_um); yours might use (w, l, fingers) or (w_nm, l_nm, ...). List YOUR
# devices below. Delete this whole section if you are not doing P&R.
#
# UNITS ARE YOURS, carried by the parameter NAME (w_um vs w_nm) + the fit
# coefficients. For a nm-scale process use integer nm (w_nm=70), NOT um decimals
# (w_um=0.07) — the plugin imposes no unit, so you avoid a pile of decimals.
#
# Each device KEY maps to its DRAW spec: the parameters + the fitted PCell that
# renders it. The PCell fit table + the harvested device geometry are YOUR
# confidential data, produced by the klink fitter from your exemplar cells and
# referenced BY PATH at run time (never committed).
DEVICES = {
    # "drv_unit": {
    #     "params": {"w_um": 1.0, "l_um": 1.0},   # TODO your params (any names/count)
    #     "pcell": "my_device", "library": "klink_structdevice",
    #     "style": "default",
    #     "fit_table": "out/my_device_fit.json",  # your fitter output, by path
    # },
    # "drv_2x": {"params": {"w_um": 2.0, "l_um": 1.0}, "pcell": "my_device",
    #            "library": "klink_structdevice", "style": "default",
    #            "fit_table": "out/my_device_fit.json"},
    # "load":   {"params": {"w_um": 4.0, "l_um": 1.0}, "pcell": "my_device",
    #            "library": "klink_structdevice", "style": "default",
    #            "fit_table": "out/my_device_fit.json"},
}

# Gate -> device-role expansion (Verilog flow, map_logic_to_devices). Each gate
# names the device KEY per role, its internal stacking nets, and the net wiring.
LIBRARY = {
    "family": "my_family",
    "gates": {
        # "INV": {"devices": {"load": "load", "drv": "drv_unit"},
        #         "internal_nets": [], "output_ports": ["Y"],
        #         "connect": {"Y": ["load.S", "load.G", "drv.D"], "A": ["drv.G"],
        #                     "VDD": ["load.D"], "GND": ["drv.S"]}},
        # "NAND2": {"devices": {"load": "load", "drvA": "drv_unit", "drvB": "drv_unit"},
        #           "internal_nets": ["MID"], "output_ports": ["Y"], "connect": {...}},
    },
}

# Sizing policy — MANDATORY for P&R, and a DESIGN choice you MUST make for YOUR
# circuit family. There is NO safe default: skipping sizing leaves every driver
# unit-width, which is electrically WRONG for any series (NAND/AND) stack — yet
# it still builds and passes topology LVS, so the error is silent. Set it.
#
# AutoRatioSizing widens series-stack drivers by scaling ONE NAMED parameter (no
# naming convention — it SELECTS the library key whose scaled param matches);
# ExplicitSizing maps (gate, role) -> key by hand. Review the result for YOUR
# devices — do not assume the example numbers fit your process.
#
# from klink.domains.structdevice.sizing import AutoRatioSizing
# SIZING = AutoRatioSizing(DEVICES, unit_key="drv_unit", load_key="load",
#                          scale_param="w_um", max_mult=3)

# Terminal source for LVS. The BUILD path is RECIPE-FREE: it reads terminals
# straight from your harvested device geometry (device_geom.json):
#
# from klink.domains.structdevice import layout_engine as eng
# from klink.domains.structdevice.recipes import geom_terminal_provider
# raw_geom = eng.load_device_geom("out/device_geom.json")   # your harvested data
# TERMINAL_PROVIDER = geom_terminal_provider(raw_geom)
#
# To derive terminals from HAND-DRAWN cells instead, write a recipe with the
# klink recipe toolkit (klink.domains.structdevice.recipes: DerivedTerminal +
# the box-geometry helpers) and inject it — see the recipes reference in
# the klink examples.

__all__ = ["PROCESS", "CONNECTIVITY", "DEVICES", "LIBRARY"]
