"""Demand-driven floorplan derivation -- no hand-picked magic numbers.

Row pitch and the rows x cols grid are COMPUTED from the netlist + device
geometry + process spacings, so the same code scales without anyone tuning a
constant (the standing user ruling: placement/routing parameters must be decided
by the algorithm). The robust negotiated router then converges at the derived
pitch because the channel is sized to the actual crossing demand.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence


def derive_grid(n_gates: int) -> tuple[int, int]:
    """rows x cols that keep the outline roughly square (slightly wider than
    tall), row-major fill. General over gate count."""
    cols = max(1, math.ceil(math.sqrt(n_gates)))
    rows = math.ceil(n_gates / cols)
    return rows, cols


def gate_stack_height_um(netlist: Mapping[str, Any], terms: Mapping[str, Any],
                         y_step: float) -> float:
    """Vertical extent of a gate's device stack: (slots-1)*y_step plus the
    topmost and bottommost terminal reach, from the harvested device geometry.
    GENERIC over terminal NAMES: devices are N-ary with arbitrary terminal
    labels, so the reach is max/min over every terminal's y -- for a classic
    S/G/D transistor that is exactly the old top-drain/bottom-source span."""
    max_slots = max(len(g["instances"]) for g in netlist["groups"])
    cells = {i["device_cell"] for i in netlist["instances"]}
    top = max(td["center"][1] for c in cells for td in terms[c].values())
    bot = min(td["center"][1] for c in cells for td in terms[c].values())
    return (max_slots - 1) * y_step + top - bot


def peak_crossing(netlist: Mapping[str, Any], rows: int, cols: int,
                  *, exclude: Sequence[str] = ()) -> int:
    """Max number of nets that must cross any inter-row boundary, from the
    row assignment -- the horizontal-track demand in the worst channel."""
    row_of: dict[str, int] = {}
    for g, grp in enumerate(netlist["groups"]):
        for xi in grp["instances"]:
            row_of[xi] = g // cols
    net_rows: dict[str, set] = defaultdict(set)
    excl = set(exclude)
    for n in netlist["nets"]:
        if n["net_id"] in excl:
            continue
        for ref in n["terminals"]:
            net_rows[n["net_id"]].add(row_of[ref.split(".")[0]])
    peak = 0
    for b in range(rows - 1):
        crossing = sum(1 for rs in net_rows.values()
                       if any(r <= b for r in rs) and any(r >= b + 1 for r in rs))
        peak = max(peak, crossing)
    return peak


def derive_row_pitch(netlist: Mapping[str, Any], rows: int, cols: int,
                     terms: Mapping[str, Any], *, y_step: float, width_um: float,
                     wire_clear_um: float, via_pad_um: float,
                     n_horiz_layers: int) -> float:
    """row_pitch = gate stack + routing channel, the channel sized to the peak
    crossing demand divided by ``n_horiz_layers`` (the number of routing layers
    the caller actually provides -- NOT a hardcoded 2/3).

    General over layer count: 2, 3, ... N layers all use the same formula. All
    nets route in ONE pass (a single negotiated router resolves every conflict,
    which is why this works where a two-pass split shorts). More layers => the
    same crossing demand fits in a NARROWER channel => smaller row pitch =>
    smaller layout."""
    if n_horiz_layers < 1:
        raise ValueError("n_horiz_layers must be >= 1")
    stack = gate_stack_height_um(netlist, terms, y_step)
    track = width_um + wire_clear_um
    peak = peak_crossing(netlist, rows, cols)               # all nets, one pass
    channel = math.ceil(peak / n_horiz_layers) * track + via_pad_um
    return round(stack + channel, 1)


def layer_demand_report(netlist: Mapping[str, Any], terms: Mapping[str, Any],
                        candidates: Sequence) -> dict:
    """PRE-ROUTE layer-count advisor: for each candidate stack (an
    example-owned ProcessProfile -- klink ships no stacks), estimate what the
    floorplan looks like on it, so the CALLER (a demo talking to the user, an
    agent in chat) can pick the FEWEST layers that fit and confirm with the
    user. Fewer layers is always better in the lab: every extra layer is a
    real deposition/litho/via step. klink only reports arithmetic; the choice
    and the candidate list are the user's.

    ``candidates`` = [(label, profile)], leanest first. Returns::

        {"gates": G, "rows": R, "cols": C, "peak_crossing": P,
         "candidates": [{"label", "n_routing_layers", "n_signal_layers",
                         "signal_h": nH, "signal_v": nV,
                         "row_pitch_um", "core_w_um", "core_h_um",
                         "core_area_mm2"}, ...]}

    The estimate is the same arithmetic ``derive_row_pitch`` uses (peak
    crossing / horizontal capacity), so it is honest about the floorplan the
    router will actually get -- it is NOT a routability guarantee; the gate
    stays live LVS."""
    n_gates = len(netlist["groups"])
    rows, cols = derive_grid(n_gates)
    peak = peak_crossing(netlist, rows, cols)
    out = []
    for label, p in candidates:
        layers = list(p.routing_layers)
        sig = list(p.signal_routing_layers()) if hasattr(p, "signal_routing_layers") \
            else layers
        nh = sum(1 for l in sig if p.layer_direction(l) == "H")
        nv = len(sig) - nh
        rp = derive_row_pitch(netlist, rows, cols, terms, y_step=p.y_step_um,
                              width_um=p.wire_width_um, wire_clear_um=p.wire_clear_um,
                              via_pad_um=p.via_pad_um, n_horiz_layers=len(layers))
        core_w = cols * p.col_pitch_um + 2 * p.margin_um
        core_h = rows * rp + 2 * p.margin_um
        out.append({"label": label, "n_routing_layers": len(layers),
                    "n_signal_layers": len(sig), "signal_h": nh, "signal_v": nv,
                    "row_pitch_um": rp, "core_w_um": round(core_w, 1),
                    "core_h_um": round(core_h, 1),
                    "core_area_mm2": round(core_w * core_h / 1e6, 3)})
    return {"gates": n_gates, "rows": rows, "cols": cols, "peak_crossing": peak,
            "candidates": out}
