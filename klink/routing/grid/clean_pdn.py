"""Sparse power grid that coexists with signals on the SAME 101/104/106 stack
(no new layers). Faithful to the Virtuoso reference: vertical power straps on
106 + a peripheral tie-rail on 104, joined by the existing 105 via.

Why sparse: device power terminals (VDD/GND) sit on 104, which is also a signal
S/D layer. A full-row 104 followpin rail blankets 104 and blocks signal pin
access. Instead, exploit the column placement -- every gate in a column shares
an x, so all of a column's VDD terminals share an x (load.D) and all GND
terminals share an x (driver.S). One vertical 106 strap per (column, net) at
that x catches every terminal in the column via a 105 via right at the terminal.
104 stays almost entirely free (only a peripheral tie-rail), so signals keep
their 104 pin access + via escape, and route on 101 + the gaps.

Connectivity: each net's column straps are tied by ONE peripheral 104 rail
(VDD above the devices, GND below) via 105 vias. -> every VDD terminal is one
net, every GND terminal is one net, VDD != GND. All on 104/106/105.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Mapping, Sequence, Tuple

Point = Tuple[float, float]


def derive_pdn_layers(profile) -> Tuple[str, str, str]:
    """Pick the power (rail, strap, cut) layers from the PROCESS PROFILE -- no
    hard-coded layer numbers, and NO dedicated power layer: the PDN reuses the
    signal routing stack. Rail = the device source/drain layer (where VDD/GND
    device terminals physically are, so the rail/via lands on them). Strap = a
    routing layer that is via-connected to the rail and runs the PERPENDICULAR
    direction (so vertical straps cross horizontal rails). Cut = the via between
    them. A different process -> different layers, zero code change.
    """
    rail = profile.sd_layer
    rdir = profile.layer_direction(rail)

    def via_between(a: str, b: str):
        for lo, cut, up in profile.vias:
            if {lo, up} == {a, b}:
                return cut
        return None

    cands = []
    for layer in profile.routing_layers:
        if layer == rail:
            continue
        cut = via_between(rail, layer)
        if cut is None:
            continue
        perpendicular = profile.layer_direction(layer) != rdir
        cands.append((perpendicular, profile.routing_layers.index(layer), layer, cut))
    if not cands:
        raise ValueError(
            "no routing layer is via-connected to the device S/D layer "
            f"{rail!r}; cannot place power straps without a dedicated layer")
    cands.sort()  # prefer perpendicular (True>False) then the upper layer
    _perp, _idx, strap, cut = cands[-1]
    return rail, strap, cut


def build_clean_pdn(
    taps_by_net: Mapping[str, Sequence[Point]],
    *,
    strap_layer: str,        # vertical power straps (a routing layer)
    rail_layer: str,         # device S/D layer: short tap stubs + peripheral tie-rail
    cut_layer: str,          # via between rail and strap layers
    width_um: float,
    spacing_um: float,
    margin_um: float,
    strap_gap_um: float = 0.0,
    x_tol: float = 0.5,
    attach_taps_by_net: Mapping[str, Sequence[Point]] = None,
) -> dict:
    """Sparse power grid: one vertical strap per (net, column), offset to a
    distinct x per net so different nets never overlap; each terminal reaches
    its strap by a short rail-layer stub + a via; columns tied by a peripheral
    rail. Returns {"boxes_by_layer", "vias", "metal_obstacles_by_layer"}.

    The per-net x offset and the tie-rail side are derived (net index, pitch),
    not hard-coded. All geometry stays on the given rail/strap/cut layers.

    ``attach_taps_by_net`` are ATTACH-ONLY taps (e.g. a pre-placed power pad's
    centre): they get a strap + vias to the net's tie rail like any tap, but
    they do NOT stretch the tie-rail envelope -- the rail stays where the
    DEVICE taps put it, and a far-away pad reaches it with its own strap. This
    keeps device straps from having to cross a pad ring to find the rail.
    """
    boxes: Dict[str, List[dict]] = defaultdict(list)
    vias: List[dict] = []
    half = width_um / 2.0
    # VDD/GND straps in a column are separated by a POWER gap (>= signal spacing;
    # long parallel power lines want a wider gap). Falls back to signal spacing.
    pitch = width_um + max(spacing_um, strap_gap_um)
    attach = attach_taps_by_net or {}
    nets_sorted = sorted(k for k in set(taps_by_net) | set(attach)
                         if taps_by_net.get(k) or attach.get(k))
    n = len(nets_sorted)
    # rail envelope from the DEVICE taps only (attach taps reach the rail with
    # their own strap instead of dragging the rail out to themselves)
    all_ys = [y for ts in taps_by_net.values() for _x, y in ts]
    top = (max(all_ys) if all_ys else 0.0) + margin_um
    bot = (min(all_ys) if all_ys else 0.0) - margin_um

    for idx, net in enumerate(nets_sorted):
        taps = list(taps_by_net.get(net, ())) + list(attach.get(net, ()))
        # distinct x offset per net so VDD/GND straps never coincide; tie-rail
        # side alternates so different nets' rails do not overlap either.
        x_off = (idx - (n - 1) / 2.0) * pitch
        rail_y = top if idx % 2 == 0 else bot

        cols: Dict[float, List[Point]] = defaultdict(list)
        for x, y in taps:
            cols[round(x / x_tol) * x_tol].append((x, y))

        strap_xs = []
        for cx in sorted(cols):
            col = cols[cx]
            sx = cx + x_off                      # strap x for THIS net (offset)
            strap_xs.append(sx)
            ys = [y for _x, y in col]
            y0, y1 = min(min(ys), rail_y), max(max(ys), rail_y)
            boxes[strap_layer].append({"net": net, "kind": "strap",
                                       "box": (sx - half, y0 - half, sx + half, y1 + half)})
            for tx, ty in col:
                # short rail-layer stub from the device terminal to the strap x
                if abs(sx - tx) > 1e-6:
                    boxes[rail_layer].append({"net": net, "kind": "stub",
                                              "box": (min(tx, sx) - half, ty - half, max(tx, sx) + half, ty + half)})
                vias.append({"net": net, "point": (sx, ty), "from": rail_layer, "to": strap_layer,
                             "cut": cut_layer, "size": width_um})
            vias.append({"net": net, "point": (sx, rail_y), "from": rail_layer, "to": strap_layer,
                         "cut": cut_layer, "size": width_um})

        if strap_xs:
            boxes[rail_layer].append({"net": net, "kind": "tie_rail",
                                      "box": (min(strap_xs) - half, rail_y - half, max(strap_xs) + half, rail_y + half)})

    metal = {layer: [it["box"] for it in items] for layer, items in boxes.items()}
    return {"boxes_by_layer": {k: list(v) for k, v in boxes.items()},
            "vias": vias, "metal_obstacles_by_layer": metal}
