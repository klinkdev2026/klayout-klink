"""FlexTA track assignment on T3 guides (Stage T4, scoped).

Turns the T3 layer-assigned guides into TRACK-assigned wires by running the faithful
FlexTA cost/assign engine (ported in `pnr_flexta`) over SINGLE-LAYER iroutes on real
`TrackGrid` tracks, then emits a T5-ready seed. See the mapping table in this
module's docstring first (data/cost model + scope + the framing of this engine
against the frozen single-stack engine).

The one T4 change vs `pnr_flexta.flexta_seed`: `cand_layers = (the T3 layer,)` -- exactly
one, because T3 (3D global route) already assigned layers (G3). `_best_track` then reduces
to pure track assignment within a clean layer. Everything else (getCost, initTA +
searchRepair, priority, rip-up) is REUSED verbatim.

Scope: track assignment ONLY. No FlexDR worker / detailed route (T5); no fixed-obstacle
DRC (real DRC = G4 = T5). The drc term here is iroute-vs-iroute overlap, exactly the
packing FlexTA exists to minimise. Generic: layers/dirs come from the T3 `LayerStack`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from klink.routing.backends.pnr_multilayer.pnr_flexta import (
    _PITCH,
    Iroute,
    _Occ,
    _assign,
    _get_cost,
)


def _intervals(vals: Sequence[int]) -> List[Tuple[int, int]]:
    """Contiguous [lo, hi] runs over a set of integer edge indices."""
    out: List[Tuple[int, int]] = []
    for v in sorted(set(vals)):
        if out and v == out[-1][1] + 1:
            out[-1] = (out[-1][0], v)
        else:
            out.append((v, v))
    return out


def build_iroutes(
    guides: Mapping[str, Sequence[Tuple[int, str, int, int]]],
    stack,
    *,
    tracks_per_gcell: int,
    terminals_by_net: Optional[Mapping[str, Sequence[Tuple[int, int, int]]]] = None,
) -> List[Iroute]:
    """Decompose T3 ``guides`` (per net, 3D edges) into single-layer iroutes.

    An H run = consecutive H edges on one layer/row; a V run = consecutive V edges on
    one layer/column. Candidate tracks = the fine tracks of the run's perpendicular
    gcell band (``tracks_per_gcell`` wide). ``terminals_by_net`` (optional, from T2 APs)
    biases the track toward a terminal on the run's perpendicular gcell (``has_pin``).
    """
    GC = tracks_per_gcell
    raw: List[Iroute] = []
    for net in sorted(guides):
        edges = guides[net]
        # bin planar edges: H by (z, row y); V by (z, col x)
        h_by: Dict[Tuple[int, int], List[int]] = {}
        v_by: Dict[Tuple[int, int], List[int]] = {}
        for (z, kind, ex, ey) in edges:
            if kind == "H":
                h_by.setdefault((z, ey), []).append(ex)
            elif kind == "V":
                v_by.setdefault((z, ex), []).append(ey)
        terms = list(terminals_by_net.get(net, ())) if terminals_by_net else []

        for (z, ey), exs in h_by.items():
            for (a, b) in _intervals(exs):           # edges a..b -> nodes x in [a, b+1]
                tlo, thi = ey * GC, ey * GC + GC - 1
                has_pin, pin_coord = _pin_on(terms, perp_gcell=ey, along_lo=a,
                                             along_hi=b + 1, is_h=True, GC=GC)
                raw.append(Iroute(net=net, cand_layers=(z,), is_h=True,
                                  begin=a, end=b + 1, tlo=tlo, thi=thi,
                                  has_pin=has_pin, pin_coord=pin_coord))
        for (z, ex), eys in v_by.items():
            for (a, b) in _intervals(eys):
                tlo, thi = ex * GC, ex * GC + GC - 1
                has_pin, pin_coord = _pin_on(terms, perp_gcell=ex, along_lo=a,
                                             along_hi=b + 1, is_h=False, GC=GC)
                raw.append(Iroute(net=net, cand_layers=(z,), is_h=False,
                                  begin=a, end=b + 1, tlo=tlo, thi=thi,
                                  has_pin=has_pin, pin_coord=pin_coord))

    # canonical, portable id order (lesson #88): deterministic across platforms
    raw.sort(key=lambda ir: (ir.net, ir.cand_layers[0], ir.is_h, ir.tlo, ir.begin, ir.end))
    for i, ir in enumerate(raw):
        ir.id = i
    return raw


def _pin_on(terms, *, perp_gcell, along_lo, along_hi, is_h, GC):
    """If a terminal sits on this run's perpendicular gcell within its along extent,
    return (True, pin_track) biasing TA toward the pin; else (False, 0)."""
    for (gx, gy, _z) in terms:
        perp = gy if is_h else gx
        along = gx if is_h else gy
        if perp == perp_gcell and along_lo <= along <= along_hi:
            return True, perp_gcell * GC + GC // 2   # center track of the pin's gcell
    return False, 0


@dataclass
class TAResult:
    ok: bool
    # net -> assigned wire segments (T5-ready seed)
    segments: Dict[str, List[dict]] = field(default_factory=dict)
    vias: Dict[str, List[dict]] = field(default_factory=dict)
    residual_drc: int = 0
    iroutes: List[Iroute] = field(default_factory=list)


def assign_tracks(
    guides: Mapping[str, Sequence[Tuple[int, str, int, int]]],
    stack,
    *,
    tracks_per_gcell: int,
    terminals_by_net: Optional[Mapping[str, Sequence[Tuple[int, int, int]]]] = None,
    wire_halo: int = 0,
) -> TAResult:
    """Run faithful FlexTA (initTA + searchRepair) on the T3 guides and emit a
    T5-ready, track-assigned seed. ``wire_halo`` = planar overlap halo in tracks (0 on a
    min-pitch track grid: adjacent tracks are already at min spacing = legal)."""
    iroutes = build_iroutes(guides, stack, tracks_per_gcell=tracks_per_gcell,
                            terminals_by_net=terminals_by_net)
    nlayers = stack.nz
    ph = ah = wire_halo
    occ = _Occ(nlayers)

    # initCosts (FlexTA_init:602): wirelength + pin bonus -> priority order.
    for ir in iroutes:
        ir.cost = (ir.end - ir.begin) + (1000 * _PITCH if ir.has_pin else 0)
    _assign(iroutes, occ, is_init=True, ph=ph, ah=ah)

    # searchRepair: re-cost placed iroutes to current drc, reset numAssigned, rip-up once.
    for ir in iroutes:
        if ir.layer < 0:
            continue
        _, drc = _get_cost(ir, ir.layer, ir.track, occ, is_init=False, ph=ph, ah=ah)
        ir.cost = drc
    for ir in iroutes:
        ir.num_assigned = 0
    _assign(iroutes, occ, is_init=False, ph=ph, ah=ah)

    # emit T5-ready seed
    segments: Dict[str, List[dict]] = {}
    for ir in iroutes:
        segments.setdefault(ir.net, []).append({
            "layer": ir.layer,
            "is_h": ir.is_h,
            "track_coord": ir.track,
            "along_begin": ir.begin,
            "along_end": ir.end,
        })
    vias: Dict[str, List[dict]] = {}
    for net in sorted(guides):
        for (z, kind, ex, ey) in guides[net]:
            if kind == "U":
                vias.setdefault(net, []).append(
                    {"gx": ex, "gy": ey, "z_lo": z, "z_hi": z + 1})

    placed = all(ir.layer >= 0 for ir in iroutes)
    residual = sum(ir.cost for ir in iroutes if ir.cost > 0)
    return TAResult(ok=placed, segments=segments, vias=vias,
                    residual_drc=residual, iroutes=iroutes)
