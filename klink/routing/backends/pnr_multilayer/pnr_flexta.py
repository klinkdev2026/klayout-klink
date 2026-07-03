"""The multilayer engine (multi-layer P&R) -- FlexTA engine, OWNED BY the
multilayer engine. Self-contained (no import from the frozen single-stack
engine's frozen `flexdr/`). See pnr_multilayer/README.md.

FlexTA (track assignment) — faithful Python golden port of OpenROAD `drt`'s
FlexTA, adapted to klink's coarse cell grid. Runs BETWEEN global routing
(`route_global` guides) and detailed routing (FlexDR worker), producing a
TRACK-ASSIGNED seed instead of the greedy net-by-net seed -> far fewer initial
overlaps for FlexDR to grind out.

Source of truth: OpenROAD src/drt/src/ta/FlexTA*.cpp.
Contract distilled in this module's docstring set (TA-1 section) plus the
faithful-port spec referenced there.

GENERALITY (binding): no process facts in the kernel. The H/V layers, the via
stack, spacing/halo and fixed obstacles all derive from the live grid +
`dir_by_li` (profile-derived) and are passed as DATA. Works for an N-layer stack
unchanged. A run of one preferred direction is assigned to a layer of THAT
direction (taken from dir_by_li), never a hardcoded layer number.

Faithful pieces (cross-checked against the source):
- taPin/iroute model + priority `operator<` (higher cost first, tie LOWER id;
  id assigned in a CANONICAL portable order, lesson #88).
- main = initTA (pure greedy, one placement each) then ONE searchRepair (rip-up,
  ring buffer 20, maxRetry 1).
- cost = max(drcW*drc + nextDir + pin - align, 0); drcW = 0.05 initTA / TADRCCOST
  searchRepair; pin = TAPINCOST*pitch + |t-pinCoord|; align = TAALIGNCOST*pitch if
  a same-net wire already on the track (subtracted); pitch = 1 cell.
- bestTrack search order by hasPinCoord + nextDir sign, break on drc==0; initTA
  minimises total cost, searchRepair minimises drc.
- candidate tracks = the iroute's own gcell band (availTracks), not the panel.
- drcCost reads a TA-local occupancy seeded with the grid's FIXED obstacles
  (wire_blocked_all + foreign pad keep-outs) = OpenROAD initFixedObjs, generic.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

# OpenROAD drt-global.h cost weights (process-independent constants).
TAPINCOST = 4
TAALIGNCOST = 4
TADRCCOST = 32
_PITCH = 1          # one cell between adjacent tracks on the coarse grid
_FIXED = "\x00__fixed__\x00"   # sentinel owner for fixed obstacles in the occ

Cell = Tuple[int, int, int]
Edge = Tuple[Cell, Cell]


@dataclass
class Iroute:
    """One net's maximal straight run on one layer = a wire to drop onto a track
    (faithful taPin). `is_h` True -> runs along X on an H-preferred layer, track
    is a Y row; else runs along Y, track is an X column."""
    net: str
    cand_layers: Tuple[int, ...]   # all same-direction layers this run may use
                                   # (OpenROAD initTracks scans every matching
                                   # layer; missing layer-assignment in our 2D grt
                                   # -> TA chooses the layer too, by cost)
    is_h: bool
    begin: int                  # FAITHFUL along-axis span (minBegin..maxEnd of the
    end: int                    # real connections); USED by cost/overlap/rip-up
    tlo: int                    # candidate track range (perpendicular), inclusive
    thi: int                    # (geometric gcell band -- same on every cand layer)
    full_begin: int = -1        # the run's full gcell-band along extent (clamp
    full_end: int = -1          # bound + via reachability + gcell crossing test)
    via_alongs: Tuple[int, ...] = ()   # along positions that connect to perp runs
    has_pin: bool = False
    pin_coord: int = 0
    pin_alongs: Tuple[int, ...] = ()   # along coords of this run's terminals (so
                                       # the span trim keeps the terminal-reaching
                                       # part -> no LVS opens)
    next_dir: int = 0
    id: int = 0
    # mutable assignment state
    cost: int = 0               # priority cost (initCosts) / current drc (searchRepair)
    num_assigned: int = 0
    layer: int = -1             # assigned layer (chosen from cand_layers)
    track: int = -1             # assigned perpendicular coord


# --------------------------------------------------------------------------- #
# TA-local occupancy (the worker region query analogue)
# --------------------------------------------------------------------------- #
class _Occ:
    """Per-layer occupancy of placed iroute wires, keyed by (track, along) ->
    Counter(net). Seeded with fixed obstacles (owner _FIXED). drcCost = number of
    occupied cells in the bloated query box owned by a FOREIGN net (the iroute
    being costed is removed first, so any occupancy is foreign or fixed)."""

    def __init__(self, nlayers: int):
        self.cell: List[Dict[Tuple[int, int], Counter]] = [defaultdict(Counter) for _ in range(nlayers)]

    def add(self, li: int, track: int, alo: int, ahi: int, net: str) -> None:
        c = self.cell[li]
        for a in range(alo, ahi + 1):
            c[(track, a)][net] += 1

    def remove(self, li: int, track: int, alo: int, ahi: int, net: str) -> None:
        c = self.cell[li]
        for a in range(alo, ahi + 1):
            d = c.get((track, a))
            if d:
                d[net] -= 1
                if d[net] <= 0:
                    del d[net]
                    if not d:
                        del c[(track, a)]

    def add_fixed(self, li: int, track: int, along: int) -> None:
        self.cell[li][(track, along)][_FIXED] += 1

    def drc(self, li: int, track: int, alo: int, ahi: int, net: str,
            ph: int, ah: int) -> int:
        """Count occupied cells (foreign or fixed) in the box bloated by the
        halo, over [track-ph, track+ph] x [alo-ah, ahi+ah]."""
        c = self.cell[li]
        n = 0
        for t in range(track - ph, track + ph + 1):
            for a in range(alo - ah, ahi + ah + 1):
                d = c.get((t, a))
                if d and (len(d) > 1 or net not in d):
                    n += 1
        return n

    def has_self(self, li: int, track: int, alo: int, ahi: int, net: str) -> bool:
        c = self.cell[li]
        for a in range(alo, ahi + 1):
            d = c.get((track, a))
            if d and net in d:
                return True
        return False


# --------------------------------------------------------------------------- #
# guide -> iroutes (the adapted initIroute)
# --------------------------------------------------------------------------- #
def _dir_layers(dir_by_li: Mapping[int, str]) -> Tuple[List[int], List[int]]:
    h = sorted(li for li, d in dir_by_li.items() if (d or "H").upper() == "H")
    v = sorted(li for li, d in dir_by_li.items() if (d or "H").upper() == "V")
    return h, v


def _runs_from_guide(edges: Sequence[Tuple[str, int, int]], ngx: int, ngy: int):
    """Decompose a net's gcell-edge guide into maximal straight runs. An H edge
    (k='H', x, y) spans gcells x..x+1 on row y; a V edge spans y..y+1 on col x.
    Returns (h_runs, v_runs): h_run = (gy, gx0, gx1), v_run = (gx, gy0, gy1)."""
    rows: Dict[int, Set[int]] = defaultdict(set)   # gy -> gx cells covered by H edges
    cols: Dict[int, Set[int]] = defaultdict(set)   # gx -> gy cells covered by V edges
    for e in edges:
        k = str(e[0]); x = int(e[1]); y = int(e[2])
        if k == "H":
            rows[y].add(x); rows[y].add(min(x + 1, ngx - 1))
        else:
            cols[x].add(y); cols[x].add(min(y + 1, ngy - 1))

    def _intervals(cells: Set[int]) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for c in sorted(cells):
            if out and c <= out[-1][1] + 1:
                out[-1] = (out[-1][0], max(out[-1][1], c))
            else:
                out.append((c, c))
        return out

    h_runs = [(gy, lo, hi) for gy, xs in rows.items() for (lo, hi) in _intervals(xs)]
    v_runs = [(gx, lo, hi) for gx, ys in cols.items() for (lo, hi) in _intervals(ys)]
    return h_runs, v_runs


def _band(gi: int, GC: int, n: int) -> Tuple[int, int]:
    """Fine-cell [lo, hi] inclusive covered by gcell index gi (clamped to n)."""
    return gi * GC, min((gi + 1) * GC, n) - 1


def _assign_run_layers(raw, h_layers, v_layers) -> List[int]:
    """3D layer assignment (the step OpenROAD's global router does; our 2D grt does
    not). Give each guide RUN one layer of its direction, CONGESTION-BALANCED across
    the same-direction layers so runs spread instead of piling on the lowest one.

    Greedy + deterministic: process runs in the canonical order, assign each to the
    same-direction layer with the least current usage over the gcells it spans, then
    book that usage (ties -> lower layer, via the sorted layer lists). Returns a list
    of assigned layer ids parallel to `raw`. With one layer per direction this is a
    no-op (the single layer always wins) -> identical to the old all-layers cand."""
    usage: Dict[int, Counter] = defaultdict(Counter)   # layer -> Counter[(gx, gy)]
    out: List[int] = []
    for (_net, is_h, perp_gi, ag0, ag1) in raw:
        layers = h_layers if is_h else v_layers
        gcells = [((a, perp_gi) if is_h else (perp_gi, a)) for a in range(ag0, ag1 + 1)]
        best_l, best_c = layers[0], None
        for L in layers:                       # sorted -> ties keep the lower layer
            u = usage[L]
            c = sum(u[gc] for gc in gcells)
            if best_c is None or c < best_c:
                best_c, best_l = c, L
        bu = usage[best_l]
        for gc in gcells:
            bu[gc] += 1
        out.append(best_l)
    return out


def _build_iroutes(g, nets, termsets, guides, GC, dir_by_li):
    """Build the iroute list (canonical id order) from the per-net gcell guides.
    Returns (iroutes, net_handled, via_links) where via_links[net] is a list of
    (h_iroute_idx, v_iroute_idx) pairs that must be stitched with a via."""
    ngx = (g.nx + GC - 1) // GC
    ngy = (g.ny + GC - 1) // GC
    h_layers, v_layers = _dir_layers(dir_by_li)
    if not h_layers or not v_layers:
        return [], set(), {}

    # net -> flattened terminal cells (for pinCoord)
    term_cells: Dict[str, List[Cell]] = {}
    for n in nets:
        cells = [c for tset in termsets.get(n.net, ()) for c in tset]
        term_cells[n.net] = cells

    iroutes: List[Iroute] = []
    raw: List[Tuple] = []   # (net, is_h, perp_gi, along_g0, along_g1)
    for n in nets:
        edges = guides.get(n.net)
        if not edges:
            continue
        h_runs, v_runs = _runs_from_guide(edges, ngx, ngy)
        if not h_runs and not v_runs:
            continue
        for (gy, gx0, gx1) in h_runs:
            raw.append((n.net, True, gy, gx0, gx1))
        for (gx, gy0, gy1) in v_runs:
            raw.append((n.net, False, gx, gy0, gy1))

    # CANONICAL portable order for ids (lesson #88): (net, is_h, perp, lo)
    raw.sort(key=lambda r: (r[0], not r[1], r[2], r[3]))

    # 3D LAYER ASSIGNMENT (the step OpenROAD's global router does; our 2D grt skips
    # it). Give each run ONE layer of its direction, balancing congestion across the
    # same-direction layers -> runs SPREAD instead of piling on the lowest layer (the
    # measured failure mode on this engine). FlexTA then assigns only the TRACK
    # within that layer.
    run_layer = _assign_run_layers(raw, h_layers, v_layers)

    for idx, (net, is_h, perp_gi, ag0, ag1) in enumerate(raw):
        if is_h:                       # along = X, track = Y(row) in perp_gi band
            alo, ahi = _band(ag0, GC, g.nx)[0], _band(ag1, GC, g.nx)[1]
            tlo, thi = _band(perp_gi, GC, g.ny)
        else:                          # along = Y, track = X(col) in perp_gi band
            alo, ahi = _band(ag0, GC, g.ny)[0], _band(ag1, GC, g.ny)[1]
            tlo, thi = _band(perp_gi, GC, g.nx)
        cand = (run_layer[idx],)       # grt-assigned layer (TA picks the track only)
        ir = Iroute(net=net, cand_layers=cand, is_h=is_h, begin=alo, end=ahi,
                    tlo=tlo, thi=thi, full_begin=alo, full_end=ahi, id=idx)
        # pinCoord + pin alongs: terminals of this net whose cell lies on this
        # run's band. pin_coord (perpendicular) pulls the track; pin_alongs keep
        # the trim from cutting off the terminal-reaching span.
        palongs: List[int] = []
        for (cx, cy, cl) in term_cells.get(net, ()):
            along = cx if is_h else cy
            perp = cy if is_h else cx
            if alo <= along <= ahi and tlo <= perp <= thi:
                if not ir.has_pin:
                    ir.has_pin = True
                    ir.pin_coord = perp
                palongs.append(along)
        ir.pin_alongs = tuple(palongs)
        iroutes.append(ir)

    net_handled = {ir.net for ir in iroutes}
    return iroutes, net_handled, (h_layers, v_layers)


# NOTE on next_iroute_dir: OpenROAD biases the track toward the perpendicular
# neighbour guides (getNextIrouteDirCost). For TA-1 we leave next_dir=0 (the value
# OpenROAD itself uses when there are no already-routed neighbour guides) -> the
# bestTrack search goes middle-outward, which is a valid faithful state. A wrong
# heuristic here would hurt more than help; the faithful neighbour-bias is a
# refinement to add once the pipeline is measured.


# --------------------------------------------------------------------------- #
# assign (the core rip-up loop)
# --------------------------------------------------------------------------- #
def _compute_spans(iroutes: List[Iroute], GC: int, use_tracks: bool) -> None:
    """Set each iroute's FAITHFUL begin/end = minBegin..maxEnd of its real
    connections (OpenROAD initIroute_helper_generic), clamped to its full gcell
    extent. Connections = same-net perpendicular runs crossing it at GCELL level +
    its own pins. The connection along-coord uses the perpendicular run's ASSIGNED
    track when available (use_tracks=True, neighbour routed) else its gcell-column
    CENTRE (OpenROAD "via location assumed in center" when the neighbour is not yet
    routed). A run with no connection keeps its full extent (= guide bp..ep)."""
    by_net: Dict[str, List[Iroute]] = defaultdict(list)
    for ir in iroutes:
        by_net[ir.net].append(ir)
    conn: Dict[int, List[int]] = defaultdict(list)
    for net, irs in by_net.items():
        hs = [i for i in irs if i.is_h]
        vs = [i for i in irs if not i.is_h]
        for h in hs:
            for v in vs:
                vgx, hgy = v.tlo // GC, h.tlo // GC
                if (h.full_begin // GC <= vgx <= h.full_end // GC
                        and v.full_begin // GC <= hgy <= v.full_end // GC):
                    conn[h.id].append(v.track if (use_tracks and v.track >= 0) else v.tlo + GC // 2)
                    conn[v.id].append(h.track if (use_tracks and h.track >= 0) else h.tlo + GC // 2)
    for ir in iroutes:
        cs = conn.get(ir.id, []) + list(ir.pin_alongs)
        if cs:
            ir.begin = max(ir.full_begin, min(cs))
            ir.end = min(ir.full_end, max(cs))
        else:
            ir.begin, ir.end = ir.full_begin, ir.full_end


def _seed_occ(g, iroutes: List[Iroute], h_layers, v_layers, place: bool) -> _Occ:
    """A fresh _Occ seeded with the grid's FIXED obstacles on EVERY TA layer
    (generic, is_h per layer). If place, also add each assigned iroute's wire at
    its (layer, track, begin..end)."""
    occ = _Occ(len(g.layers))
    h_set = set(h_layers)
    for li in (list(h_layers) + list(v_layers)):
        is_h = li in h_set
        for (ix, iy) in g.wire_blocked_all.get(li, ()):
            t, a = (iy, ix) if is_h else (ix, iy)
            occ.add_fixed(li, t, a)
        for owner, cells in g.pad_cells.get(li, {}).items():
            for (ix, iy) in cells:
                t, a = (iy, ix) if is_h else (ix, iy)
                occ.add_fixed(li, t, a)
    if place:
        for ir in iroutes:
            if ir.layer >= 0:
                occ.add(ir.layer, ir.track, ir.begin, ir.end, ir.net)
    return occ


def _get_cost(ir: Iroute, layer: int, track: int, occ: _Occ, is_init: bool,
              ph: int, ah: int) -> Tuple[int, int]:
    """Return (total_cost, drc_cost) for placing `ir` on (layer, track). Faithful
    getCost (FlexTA_assign.cpp:920)."""
    drc = occ.drc(layer, track, ir.begin, ir.end, ir.net, ph, ah)
    drc_w = (0.05 * drc) if is_init else (TADRCCOST * drc)
    # nextIrouteDirCost: |next_dir| * distance toward the biased gcell edge.
    if ir.next_dir <= 0:
        ndc = abs(ir.next_dir) * (track - ir.tlo)
    else:
        ndc = abs(ir.next_dir) * (ir.thi - track)
    ndc = max(ndc, 0)
    # pinCost
    if ir.has_pin:
        t = abs(track - ir.pin_coord)
        pin = 0 if t == 0 else (TAPINCOST * _PITCH + t)
    else:
        pin = 0
    # alignCost (subtracted): reward a same-net wire already on this (layer, track)
    align = (TAALIGNCOST * _PITCH) if occ.has_self(layer, track, ir.begin, ir.end, ir.net) else 0
    total = int(drc_w) + ndc + pin - align
    return (total if total > 0 else 0), drc


def _best_track(ir: Iroute, occ: _Occ, is_init: bool, ph: int, ah: int) -> Tuple[int, int, int]:
    """assignIroute_bestTrack, extended across the run's candidate LAYERS (our 2D
    grt has no layer assignment, so TA chooses the layer too -- OpenROAD initTracks
    scans every matching layer). Per layer: the faithful track search order by
    hasPin + next_dir sign, break as soon as drc==0. initTA minimises total cost,
    searchRepair minimises drc; strict < so the first (layer, track) in order wins
    ties (candidate layers iterated in sorted order). Returns (layer, track, drc)."""
    idx1, idx2 = ir.tlo, ir.thi
    best_cost = None
    best_layer = ir.cand_layers[0]
    best_track = idx1
    best_drc = 0

    def consider(layer: int, t: int) -> int:
        nonlocal best_cost, best_layer, best_track, best_drc
        total, drc = _get_cost(ir, layer, t, occ, is_init, ph, ah)
        key = total if is_init else drc
        if best_cost is None or key < best_cost:
            best_cost = key
            best_layer = layer
            best_track = t
            best_drc = drc
        return drc

    for layer in ir.cand_layers:
        def sweep(seq) -> bool:
            for t in seq:
                if consider(layer, t) == 0:
                    return True
            return False

        if ir.has_pin:
            start = min(max(ir.pin_coord, idx1), idx2)
            if ir.next_dir > 0:
                if not sweep(range(start, idx2 + 1)):
                    sweep(range(start - 1, idx1 - 1, -1))
            elif ir.next_dir == 0:
                for i in range(0, idx2 - idx1 + 1):
                    up = start + i
                    if idx1 <= up <= idx2 and consider(layer, up) == 0:
                        break
                    dn = start - i - 1
                    if idx1 <= dn <= idx2 and consider(layer, dn) == 0:
                        break
            else:
                if not sweep(range(start, idx1 - 1, -1)):
                    sweep(range(start + 1, idx2 + 1))
        else:
            if ir.next_dir > 0:
                sweep(range(idx2, idx1 - 1, -1))
            elif ir.next_dir == 0:
                mid = (idx1 + idx2) // 2
                if not sweep(range(mid, idx2 + 1)):
                    sweep(range(mid - 1, idx1 - 1, -1))
            else:
                sweep(range(idx1, idx2 + 1))
    return best_layer, best_track, best_drc


_MAXRETRY = 1   # OpenROAD FlexTAWorker::maxRetry_
_RING = 20      # OpenROAD assign() ring buffer size


def _assign(iroutes: List[Iroute], occ: _Occ, is_init: bool, ph: int, ah: int) -> None:
    """One worker phase. initTA (is_init=True): pop all iroutes by priority, place
    each ONCE, no rip-up. searchRepair: rip-up loop -- re-cost overlapped iroutes
    and re-queue the still-violating, unassigned ones. Ring buffer 20, maxRetry 1.

    Priority queue = lazy-deletion max-heap on (cost, -id): an entry is live only
    while `queued[id] == its cost` (faithful taPinComp: higher cost first, tie
    LOWER id -> we push (-cost, id) so heapq pops highest cost then lowest id)."""
    import heapq
    by_id = {ir.id: ir for ir in iroutes}
    queued: Dict[int, int] = {}        # id -> the cost it is currently queued at
    heap: List[Tuple[int, int]] = []   # (-cost, id)

    def push(ir: Iroute) -> None:
        if ir.num_assigned >= _MAXRETRY:
            return
        queued[ir.id] = ir.cost
        heapq.heappush(heap, (-ir.cost, ir.id))

    def pop() -> Optional[Iroute]:
        while heap:
            negc, iid = heapq.heappop(heap)
            if queued.get(iid) != -negc:    # stale (re-queued at a new cost, or gone)
                continue
            del queued[iid]
            return by_id[iid]
        return None

    for ir in iroutes:
        if is_init or ir.cost > 0:
            push(ir)

    buffers: List[int] = []
    ir = pop()
    while ir is not None:
        if ir.id not in buffers and ir.num_assigned < _MAXRETRY:
            _assign_one(ir, iroutes, occ, is_init, ph, ah, push)
            buffers.append(ir.id)
            if len(buffers) > _RING:
                buffers.pop(0)
        ir = pop()


def _assign_one(ir: Iroute, iroutes: List[Iroute], occ: _Occ, is_init: bool,
                ph: int, ah: int, push) -> None:
    # assignIroute_init: in searchRepair remove the old placement before re-costing
    if not is_init and ir.layer >= 0:
        occ.remove(ir.layer, ir.track, ir.begin, ir.end, ir.net)
    layer, track, drc = _best_track(ir, occ, is_init, ph, ah)
    ir.layer = layer
    ir.track = track
    occ.add(layer, track, ir.begin, ir.end, ir.net)
    ir.num_assigned += 1
    ir.cost = drc
    # assignIroute_updateOthers (searchRepair only): re-cost the overlapped iroutes,
    # re-queue the still-violating + not-yet-assigned ones.
    if is_init:
        return
    for other in iroutes:
        if other.id == ir.id or other.layer != layer or other.track < 0:
            continue
        if abs(other.track - track) > ph:
            continue
        if other.end < ir.begin - ah or other.begin > ir.end + ah:
            continue
        _, odrc = _get_cost(other, other.layer, other.track, occ, is_init, ph, ah)
        other.cost = odrc
        if odrc > 0 and other.num_assigned < _MAXRETRY:
            push(other)


# --------------------------------------------------------------------------- #
# orchestration + seed emission
# --------------------------------------------------------------------------- #
def _via_stack(g, lo: int, hi: int) -> Optional[List[int]]:
    """A layer path lo..hi using via_index adjacency (BFS). Returns the list of
    layer indices [lo, ..., hi] or None if unreachable. Generic (no hardcoded
    layers)."""
    if lo == hi:
        return [lo]
    adj: Dict[int, Set[int]] = defaultdict(set)
    for a, b, _c in g.via_index():
        adj[a].add(b); adj[b].add(a)
    from collections import deque
    q = deque([[lo]])
    seen = {lo}
    while q:
        path = q.popleft()
        if path[-1] == hi:
            return path
        for nb in sorted(adj[path[-1]]):
            if nb not in seen:
                seen.add(nb)
                q.append(path + [nb])
    return None


def _forbidden_fn(g):
    """A (net, li, ix, iy) -> bool test: True if a wire of `net` may NOT occupy
    that cell (a channel blockage, or a FOREIGN net's device pad / PDN keep-out).
    Uses the O(1) pad_owner inverse when present. Generic (no layer literals)."""
    pad_owner = getattr(g, "pad_owner", None)
    multi = "\x00__multi_owner__\x00"

    def f(net, li, ix, iy):
        if (ix, iy) in g.wire_blocked_all.get(li, ()):
            return True
        if pad_owner is not None:
            owner = pad_owner.get(li, {}).get((ix, iy))
            return owner is not None and owner != net
        for owner, cells in g.pad_cells.get(li, {}).items():
            if owner != net and (ix, iy) in cells:
                return True
        return False
    _ = multi
    return f


def _emit_seed(g, iroutes: List[Iroute], GC: int):
    """Turn assigned iroutes into per-net seed routes/edges: each run = a straight
    line of cells at its assigned (layer, track); turn vias stitch every same-net
    (H run, V run) that share a gcell, via the layer stack between their ASSIGNED
    layers (from via_index, generic). Returns (routes, edges).

    FAITHFUL EMIT (Stage D, §1 fix): the run is emitted WHOLE over minBegin..maxEnd
    with NO obstacle avoidance -- exactly OpenROAD's TA, which writes the full
    frPathSeg on the assigned track and leaves obstacle/short repair to FlexDR. The
    old version SKIPPED cells on a foreign device pad / blockage to avoid shorting,
    but on the coarse grid a signal run crosses many foreign pads, so that broke
    each run into multi-cell-gap pieces and FRAGMENTED 50-65% of net backbones
    (measured: cpu4 154/306). A connected backbone is the precondition for D1's
    short-via AP connect; the seed overlaps it creates are ripped-up/rerouted by the
    FlexDR worker (proven in TA-0.5). This also REMOVES the only process/device
    binding from emit (the foreign-pad reference), keeping the kernel generic."""
    routes: Dict[str, Set[Cell]] = defaultdict(set)
    edges: Dict[str, List[Edge]] = defaultdict(list)
    by_net: Dict[str, List[Iroute]] = defaultdict(list)
    _stack_cache: Dict[Tuple[int, int], Optional[List[int]]] = {}

    def via_stack(a: int, b: int) -> Optional[List[int]]:
        key = (a, b)
        if key not in _stack_cache:
            _stack_cache[key] = _via_stack(g, a, b)
        return _stack_cache[key]

    for ir in iroutes:
        if ir.layer >= 0:
            by_net[ir.net].append(ir)

    # The iroute's begin/end is ALREADY the faithful minBegin..maxEnd span (set in
    # flexta_seed). Here we draw that span, but EXTEND it to reach the actual via
    # columns (assigned tracks may sit a little outside the centre-estimate span)
    # so the turn vias never float. Turn between H run h and V run v exists when
    # they cross at GCELL level (full extents); the via lands at (v.track, h.track).
    via_cols: Dict[int, List[int]] = defaultdict(list)
    turns: List[Tuple[Iroute, Iroute, int, int]] = []
    for net, irs in by_net.items():
        hs = [ir for ir in irs if ir.is_h]
        vs = [ir for ir in irs if not ir.is_h]
        for h in hs:
            for v in vs:
                # SAME gcell-level crossing test as _compute_spans, so every turn's
                # via column (v.track / h.track) is already inside the faithful
                # begin..end (it was a connection that set the span) -> no spurious
                # wire extension, consistent assignment vs emission.
                vgx, hgy = v.tlo // GC, h.tlo // GC
                if (h.full_begin // GC <= vgx <= h.full_end // GC
                        and v.full_begin // GC <= hgy <= v.full_end // GC):
                    tV, tH = v.track, h.track
                    via_cols[h.id].append(tV)
                    via_cols[v.id].append(tH)
                    turns.append((h, v, tV, tH))

    for ir in iroutes:
        if ir.layer < 0:
            continue
        cols = via_cols.get(ir.id, ())
        lo = max(ir.full_begin, min([ir.begin] + list(cols)))
        hi = min(ir.full_end, max([ir.end] + list(cols)))
        cells = routes[ir.net]
        prev = None
        for a in range(lo, hi + 1):
            ix, iy = (a, ir.track) if ir.is_h else (ir.track, a)
            c = (ix, iy, ir.layer)         # emit WHOLE -- FlexDR repairs overlaps
            cells.add(c)
            if prev is not None:
                edges[ir.net].append((prev, c))
            prev = c

    # turn vias: stitch via the layer stack between the two runs' assigned layers.
    # Only emit if every cell of the stack is usable (not a foreign pad).
    for (h, v, tV, tH) in turns:
        path = via_stack(v.layer, h.layer)
        if path is None:               # emit the via WHOLE -- FlexDR repairs overlaps
            continue
        for k in range(len(path) - 1):
            ca = (tV, tH, path[k])
            cb = (tV, tH, path[k + 1])
            routes[h.net].add(ca); routes[h.net].add(cb)
            edges[h.net].append((ca, cb))
    return ({n: sorted(cs) for n, cs in routes.items()},
            {n: es for n, es in edges.items()})


def flexta_seed(g, nets, profile, GC, guides, termsets, dir_by_li,
                wire_halo: int, verbose: bool = False):
    """Produce a track-assigned seed (routes, edges) for the nets FlexTA can
    handle (those with a usable gcell guide). Nets it skips are left for the
    greedy seed. Returns (seed_routes, seed_edges, handled_nets)."""
    iroutes, handled, layers = _build_iroutes(g, nets, termsets, guides, GC, dir_by_li)
    if not iroutes:
        return {}, {}, set()
    h_layers, v_layers = layers
    # On the coarse TRACK grid (grid_pitch = wire + clear) two wires ONE track
    # apart are already at exactly min-spacing = legal, so the basic overlap halo
    # is the grid's own wire_halo (typically 0), NOT a forced >=1 (that would
    # falsely treat adjacent tracks as conflicting and halve the usable tracks).
    ph = ah = wire_halo

    # initTA: faithful spans estimated with the perpendicular runs' gcell CENTRES
    # (neighbours not yet routed -> OpenROAD "via location assumed in center"),
    # then pure-greedy place. _seed_occ seeds the grid's FIXED obstacles on every
    # TA layer (generic; a PDN-shared V layer is avoided WHERE occupied, no layer
    # hardcoding).
    _compute_spans(iroutes, GC, use_tracks=False)
    occ = _seed_occ(g, iroutes, h_layers, v_layers, place=False)
    for ir in iroutes:                       # initCosts: wirelength + pin bonus
        ir.cost = (ir.end - ir.begin) + (1000 * _PITCH if ir.has_pin else 0)
    _assign(iroutes, occ, is_init=True, ph=ph, ah=ah)

    # neighbours are now routed -> recompute PRECISE spans from the assigned tracks
    # (OpenROAD initIroute uses the real via coord once the neighbour has routes),
    # rebuild the occupancy, then searchRepair rip-up -- so cost / overlap / rip-up
    # / residual all use the real connection extent (minBegin..maxEnd), not the
    # full gcell band. This is the Stage C faithfulness fix.
    _compute_spans(iroutes, GC, use_tracks=True)
    occ = _seed_occ(g, iroutes, h_layers, v_layers, place=True)
    for ir in iroutes:                       # initCosts searchRepair: current drc
        if ir.layer < 0:
            continue
        _, drc = _get_cost(ir, ir.layer, ir.track, occ, is_init=False, ph=ph, ah=ah)
        ir.cost = drc
    for ir in iroutes:                       # fresh worker -> reset numAssigned
        ir.num_assigned = 0
    _assign(iroutes, occ, is_init=False, ph=ph, ah=ah)

    routes, edges = _emit_seed(g, iroutes, GC)
    if verbose:
        placed = sum(1 for ir in iroutes if ir.layer >= 0)
        resid = sum(ir.cost for ir in iroutes)
        print(f"  flexta: {len(iroutes)} iroutes ({placed} placed) over "
              f"{len(handled)} nets, residual drc={resid}", flush=True)
    return routes, edges, handled
