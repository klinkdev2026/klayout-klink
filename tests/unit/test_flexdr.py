"""FlexDR detailed-router test harness.

Methodology (no shortcuts): auto-generate many SMALL routable routing problems,
solve each with the GOLDEN router (pathfinder.route_negotiated -- proven correct,
the LVS-clean reference), and validate every property against it. Each FlexDR
worker-box piece is then built and checked against this same oracle before it is
trusted. Start small, verify each step.

This file is Step 0: the generator + golden oracle + validators + an oracle
sanity test. Later steps add worker-box partition / boundary-pin / box-reroute /
schedule tests in the same harness.
"""
from __future__ import annotations

import random
from collections import defaultdict, deque
from typing import Dict, List, Sequence, Set, Tuple

import pytest

from klink.routing.grid.capacity_grid import (
    Cell, CapacityGrid, NetInput, ViaRule, _terminal_cellsets, _wire_ok,
    build_capacity_grid,
)
from klink.routing.grid.pathfinder import _congestion, _halos, route_negotiated
from klink.routing.backends.flexdr.flexdr import (
    Box, BoxCost, boxes_touch, box_maze, cell_in_box, checkerboard_batches,
    extract_box, flexgc_lite, route_box, route_flexdr, worker_boxes,
    _invert_pad_cells, _MULTI_OWNER,
)


def _wire_ok_scan(pad_cells, lyr_i, ix, iy, net):
    """The original O(num_nets) reference semantics for _wire_ok's pad check."""
    for owner, cells in pad_cells.get(lyr_i, {}).items():
        if owner != net and (ix, iy) in cells:
            return False
    return True


def test_invert_pad_cells_matches_scan_incl_multiowner():
    """O(1) pad_owner index must be byte-equivalent to the O(num_nets) scan,
    INCLUDING cells owned by 2+ nets (overlapping device pad / PDN keep-out on the
    coarse grid). Regression for the add4/alu4 VDD<->signal LVS short: a naive
    {cell:owner} last-wins inverse let a foreign net route into a shared pad."""
    # layer 0: (1,1) owned by both VDD and a signal; (2,2) only VDD; (3,3) free.
    pad_cells = {0: {"VDD": {(1, 1), (2, 2)}, "sig": {(1, 1)}}}
    po = _invert_pad_cells(pad_cells)
    assert po[0][(1, 1)] == _MULTI_OWNER          # 2 owners -> sentinel
    assert po[0][(2, 2)] == "VDD"                 # single owner kept
    cells_nets = [((1, 1), "VDD"), ((1, 1), "sig"), ((1, 1), "other"),
                  ((2, 2), "VDD"), ((2, 2), "sig"), ((3, 3), "anyone")]
    for (ix, iy), net in cells_nets:
        scan = _wire_ok_scan(pad_cells, 0, ix, iy, net)
        fast = (po[0].get((ix, iy)) in (None, net))   # _wire_ok's O(1) rule
        assert fast == scan, f"mismatch at {(ix, iy)} net={net}: fast={fast} scan={scan}"
    # the shared cell must be BLOCKED for its own owners too (the bug)
    assert _wire_ok_scan(pad_cells, 0, 1, 1, "VDD") is False
    assert (po[0].get((1, 1)) in (None, "VDD")) is False


# --- case generator ----------------------------------------------------------

def gen_case(seed: int, *, layers=("M1/0", "M2/0"), nx=14, ny=14, n_nets=6,
             max_term=3, pitch=1.0, n_obstacles=0):
    """A small random routing problem on a real CapacityGrid. General over layer
    count (via rules between consecutive layers). Terminals are distinct cells so
    no case is short-by-construction; obstacles are channel keep-outs."""
    rng = random.Random(seed)
    via_rules = [ViaRule(layers[i], layers[i + 1], f"v{i}", (0.0, 0.0), 2.0)
                 for i in range(len(layers) - 1)]
    chan = []
    for _ in range(n_obstacles):
        x = rng.randint(1, nx - 2); y = rng.randint(1, ny - 2)
        chan.append((float(x), float(y), float(x), float(y)))
    # First pass: choose terminal cells, then register them as per-net PAD
    # keep-outs (a foreign net may not route through another net's terminal --
    # exactly the real build_grid semantics, otherwise the golden can route
    # through a foreign terminal and produce a short that _congestion hides).
    used: Set[Tuple[int, int]] = set()
    chan_cells = {(int(round(c[0])), int(round(c[1]))) for c in chan}
    chosen: List[Tuple[str, List[set]]] = []
    pad_boxes: Dict[str, List] = {ly: [] for ly in layers}
    for k in range(n_nets):
        name = f"N{k}"
        want = rng.randint(2, max_term)
        cells = []
        for _ in range(want):
            for _try in range(80):
                x = rng.randint(0, nx - 1); y = rng.randint(0, ny - 1)
                li = rng.randint(0, len(layers) - 1)
                if (x, y) in used or (x, y) in chan_cells:
                    continue
                used.add((x, y)); cells.append({(x, y, li)})
                pad_boxes[layers[li]].append((name, (float(x), float(y), float(x), float(y))))
                break
        if len(cells) >= 2:
            chosen.append((name, cells))
    g = build_capacity_grid(
        layers=layers, bbox_um=(0.0, 0.0, float(nx - 1), float(ny - 1)),
        pitch_um=pitch, channel_boxes_um=chan, pad_boxes_by_layer=pad_boxes,
        device_body_boxes_um=[], via_rules=via_rules, via_footprint_um=0.0,
    )
    nets = [NetInput(name, terminal_cells=cells) for name, cells in chosen]
    return g, nets


# --- validators (the oracle's correctness contract) --------------------------

def net_connected(g: CapacityGrid, net: NetInput, routes, edges) -> bool:
    """Every terminal set of the net is joined through its routed edges."""
    cells = set(routes.get(net.net, ()))
    termsets = _terminal_cellsets(g, net)
    if not all(t & cells for t in termsets):
        return False
    adj: Dict[Cell, Set[Cell]] = defaultdict(set)
    for a, b in edges.get(net.net, ()):
        adj[a].add(b); adj[b].add(a)
    start = next(iter(termsets[0] & cells))
    seen = {start}; q = deque([start])
    while q:
        c = q.popleft()
        for nb in adj[c]:
            if nb not in seen:
                seen.add(nb); q.append(nb)
    return all(t & seen for t in termsets)


def overlap_cells(g, routes, edges, wh, vh, termsets):
    bad, _inv, _own = _congestion(g, routes, edges, wh, vh, termsets)
    return bad


def all_legal(g, routes) -> bool:
    return all(_wire_ok(g, ix, iy, li, net)
               for net, cells in routes.items() for (ix, iy, li) in cells)


def literal_shared_cells(routes):
    """Cells that literally appear in >=2 nets' routes -- a short, regardless of
    the terminal/route bookkeeping _congestion uses."""
    owners = defaultdict(set)
    for net, cells in routes.items():
        for c in cells:
            owners[c].add(net)
    return {c for c, o in owners.items() if len(o) > 1}


def validate(g, nets, result, wh, vh):
    """Return a dict of the routing's correctness properties."""
    termsets = {n.net: _terminal_cellsets(g, n) for n in nets}
    routed = sum(1 for n in nets if n.net in result.routes and result.routes[n.net])
    connected = sum(1 for n in nets if net_connected(g, n, result.routes, result.edges))
    return {
        "routed": routed,
        "connected": connected,
        "n_nets": len(nets),
        "overlaps": len(overlap_cells(g, result.routes, result.edges, wh, vh, termsets)),
        "literal": len(literal_shared_cells(result.routes)),
        "legal": all_legal(g, result.routes),
    }


def golden(g, nets, *, width=0.0, clear=0.0, via=0.0, max_iters=120):
    return route_negotiated(g, nets, width_um=width, wire_clear_um=clear,
                            via_clear_um=via, max_iters=max_iters)


# --- Step 0: oracle sanity ---------------------------------------------------

@pytest.mark.parametrize("layers", [("M1/0", "M2/0"), ("M1/0", "M2/0", "M3/0")])
def test_golden_oracle_is_valid_when_it_succeeds(layers):
    """For every case the golden solves, the result MUST be fully connected,
    overlap-free and legal -- otherwise it is not a trustworthy oracle. Also
    require a healthy solve rate so the generator yields real (routable) cases."""
    solved = 0
    total = 0
    for seed in range(80):
        g, nets = gen_case(seed, layers=layers, n_nets=6, nx=14, ny=14, n_obstacles=4)
        if len(nets) < 4:
            continue
        total += 1
        wh, vh = _halos(g, 0.0, 0.0, 0.0)
        r = golden(g, nets)
        if not r.ok:
            continue
        solved += 1
        v = validate(g, nets, r, wh, vh)
        assert v["routed"] == v["n_nets"], (seed, v)
        assert v["connected"] == v["n_nets"], (seed, v)
        assert v["overlaps"] == 0, (seed, v)
        assert v["literal"] == 0, (seed, v)
        assert v["legal"], (seed, v)
    assert total >= 40
    assert solved >= int(0.7 * total), f"golden solved only {solved}/{total}"


# --- Step 1: worker-box partition + checkerboard ----------------------------

@pytest.mark.parametrize("ngx,ngy,size", [(10, 10, 3), (17, 9, 4), (1, 1, 1),
                                          (20, 20, 7), (5, 13, 2), (8, 8, 1)])
def test_worker_boxes_tile_the_grid_at_offset0(ngx, ngy, size):
    boxes = worker_boxes(ngx, ngy, size, offset=0)
    covered = {}
    for b in boxes:
        assert 0 <= b.gx0 <= b.gx1 < ngx and 0 <= b.gy0 <= b.gy1 < ngy
        for gx in range(b.gx0, b.gx1 + 1):
            for gy in range(b.gy0, b.gy1 + 1):
                assert (gx, gy) not in covered, f"overlap at {(gx, gy)}"
                covered[(gx, gy)] = b
    assert len(covered) == ngx * ngy, "boxes must tile every gcell exactly once"


@pytest.mark.parametrize("ngx,ngy,size,offset", [(20, 20, 3, 0), (20, 20, 5, -2),
                                                 (17, 23, 4, 0), (31, 11, 7, -5),
                                                 (12, 12, 1, 0)])
def test_checkerboard_batches_are_non_adjacent(ngx, ngy, size, offset):
    boxes = worker_boxes(ngx, ngy, size, offset)
    batches = checkerboard_batches(boxes)
    assert sum(len(b) for b in batches) == len(boxes)
    assert len(batches) <= 4
    for batch in batches:
        for i in range(len(batch)):
            for j in range(i + 1, len(batch)):
                assert not boxes_touch(batch[i], batch[j]), \
                    f"same-batch boxes touch: {batch[i]} {batch[j]}"


# --- Step 2: boundary-pin extraction ----------------------------------------

def _augmented_reconnects(keep_edges, pins, in_box_terms, terminals):
    """Contract: if a fresh in-box route joins all pins + in-box terminals (a
    virtual BOX node), is every terminal set reconnected into one component?"""
    adj = defaultdict(set)
    for a, b in keep_edges:
        adj[a].add(b); adj[b].add(a)
    BOX = ("__BOX__", 0, 0)
    for p in pins:
        adj[BOX].add(p); adj[p].add(BOX)
    for t in in_box_terms:
        for c in t:
            adj[BOX].add(c); adj[c].add(BOX)
    # start from any cell of terminal 0 that exists in the graph (or BOX)
    starts = [c for c in terminals[0] if c in adj] or ([BOX] if terminals[0] & set().union(*([set(t) for t in in_box_terms] or [set()])) else [])
    if not starts:
        starts = [next(iter(terminals[0]))]
    seen = set(); q = deque(starts)
    seen.update(starts)
    while q:
        c = q.popleft()
        for nb in adj[c]:
            if nb not in seen:
                seen.add(nb); q.append(nb)
    # every terminal set must have a representative reachable
    return all(any(c in seen for c in t) for t in terminals)


def _collect_extract_scenarios():
    """Many (net, box) extractions over golden routes; tag crossing/in/out."""
    scen = {"cross": 0, "full_in": 0, "full_out": 0}
    cases = []
    for seed in range(40):
        g, nets = gen_case(seed, n_nets=6, nx=16, ny=16, n_obstacles=3)
        if len(nets) < 4:
            continue
        r = golden(g, nets)
        if not r.ok:
            continue
        GC = 4
        ngx = (g.nx + GC - 1) // GC; ngy = (g.ny + GC - 1) // GC
        for size in (2, 3):
            for box in worker_boxes(ngx, ngy, size, 0):
                for n in nets:
                    cells = r.routes[n.net]; edges = r.edges[n.net]
                    if not cells:
                        continue
                    inb = [cell_in_box(c, box, GC) for c in cells]
                    cases.append((g, n, cells, edges, _terminal_cellsets(g, n), box, GC))
                    if any(inb) and not all(inb):
                        scen["cross"] += 1
                    elif all(inb):
                        scen["full_in"] += 1
                    else:
                        scen["full_out"] += 1
    return cases, scen


def test_extract_box_is_lossless_partition_and_reconnects():
    cases, scen = _collect_extract_scenarios()
    assert scen["cross"] > 0 and scen["full_in"] > 0 and scen["full_out"] > 0, scen
    for g, n, cells, edges, terms, box, GC in cases:
        ex = extract_box(cells, edges, terms, box, GC)
        # lossless cell partition: keep (out + pins) ∪ ripped == all cells
        assert ex.keep_cells | ex.ripped_cells == set(cells)
        assert not (ex.ripped_cells & ex.pins)
        # lossless edge partition
        assert len(ex.keep_edges) + len(ex.ripped_edges) == len(edges)
        # pins/ripped are in-box; pins came from real cells
        assert all(cell_in_box(p, box, GC) for p in ex.pins)
        assert all(cell_in_box(c, box, GC) for c in ex.ripped_cells)
        assert ex.pins <= set(cells)
        # CONTRACT: reconnecting pins + in-box terminals restores the net
        assert _augmented_reconnects(ex.keep_edges, ex.pins, ex.in_box_terms, terms), n.net


# --- M1: faithful per-box maze (additive cost) ------------------------------

def _grid1(n=7):
    """Tiny single-layer grid; one gcell at GC=8 so corridor={(0,0)} covers all."""
    return build_capacity_grid(
        layers=("M1/0",), bbox_um=(0.0, 0.0, float(n - 1), float(n - 1)),
        pitch_um=1.0, channel_boxes_um=[], pad_boxes_by_layer={},
        device_body_boxes_um=[], via_rules=[], via_footprint_um=0.0)


def _route1(g, bc, start=(0, 3, 0), goal=(6, 3, 0)):
    return box_maze(g, "A", [start], {goal}, bc, {(0, 0)}, GC=8, via_halo=0)


def test_box_maze_shortest_straight_without_adj():
    g = _grid1()
    p = _route1(g, BoxCost())
    assert p is not None
    assert p[0] == (0, 3, 0) and p[-1] == (6, 3, 0)
    assert all(c[1] == 3 for c in p)          # straight row, no bend
    assert len(p) == 7


def test_box_maze_detours_around_marker():
    g = _grid1()
    bc = BoxCost(marker={(3, 3, 0): 1}, gg_marker=100.0)
    p = _route1(g, bc)
    assert p is not None
    assert (3, 3, 0) not in p                  # marker cost forced a detour
    assert p[0] == (0, 3, 0) and p[-1] == (6, 3, 0)


def test_box_maze_crosses_cheap_fixed_shape_but_detours_expensive():
    g = _grid1()
    cheap = _route1(g, BoxCost(fixed_shape={(3, 3, 0)}, gg_fixed=1.0))
    assert cheap is not None and (3, 3, 0) in cheap        # cheaper to cross
    pricey = _route1(g, BoxCost(fixed_shape={(3, 3, 0)}, gg_fixed=100.0))
    assert pricey is not None and (3, 3, 0) not in pricey  # too dear -> detour


def test_box_maze_never_crosses_hard_and_fails_when_walled():
    g = _grid1()
    p = _route1(g, BoxCost(hard={(3, 3, 0)}))
    assert p is not None and (3, 3, 0) not in p
    walled = BoxCost(hard={(3, y, 0) for y in range(7)})   # full column wall
    assert _route1(g, walled) is None


def test_flexgc_lite_reports_overlap_with_sources_and_region():
    g = _grid1()
    routes = {"A": [(2, 2, 0), (3, 2, 0)], "B": [(3, 2, 0), (3, 3, 0)]}  # share (3,2,0)
    edges = {"A": [((2, 2, 0), (3, 2, 0))], "B": [((3, 2, 0), (3, 3, 0))]}
    wh, vh = _halos(g, 0.0, 0.0, 0.0)
    ms = flexgc_lite(g, routes, edges, wh, vh)
    assert any(m.sources == frozenset({"A", "B"}) and (3, 2, 0) in m.cells for m in ms)
    assert flexgc_lite(g, routes, edges, wh, vh, region=(0, 0, 1, 1)) == []  # far region: none


# --- M2: faithful worker route_queue (rip-up + reroute + accept) -------------

def _grid2(n=9):
    via = ViaRule("M1/0", "M2/0", "v", (0.0, 0.0), 2.0)
    return build_capacity_grid(
        layers=("M1/0", "M2/0"), bbox_um=(0.0, 0.0, float(n - 1), float(n - 1)),
        pitch_um=1.0, channel_boxes_um=[], pad_boxes_by_layer={},
        device_body_boxes_um=[], via_rules=[via], via_footprint_um=0.0)


def test_route_box_repairs_injected_overlap():
    """Two nets forced to cross on the SAME layer (a short); the worker must
    rip+reroute (via the other layer) down to 0 markers and keep both connected."""
    g = _grid2(9)
    A = [(x, 4, 0) for x in range(9)]
    B = [(4, y, 0) for y in range(9)]
    routes = {"A": A, "B": B}
    edges = {"A": list(zip(A, A[1:])), "B": list(zip(B, B[1:]))}
    termsets = {"A": [{(0, 4, 0)}, {(8, 4, 0)}], "B": [{(4, 0, 0)}, {(4, 8, 0)}]}
    wh, vh = _halos(g, 0.0, 0.0, 0.0)
    before = len(flexgc_lite(g, routes, edges, wh, vh))
    assert before >= 1, "test must start with a real overlap"

    box = Box(0, 0, 0, 0, 0, 0)   # whole 9x9 grid as one box at GC=16
    out = route_box(g, box, 16, routes, edges, termsets, wh, vh,
                    ripup_mode="DRC", maze_end_iter=8, gg_drc=8.0, gg_marker=8.0)
    assert out is not None, "worker should have improved the box"
    nr = dict(routes); ne = dict(edges)
    nr.update(out[0]); ne.update(out[1])
    after = len(flexgc_lite(g, nr, ne, wh, vh))
    assert after < before
    assert after == 0, f"expected 0 markers, got {after}"
    for name in ("A", "B"):
        assert net_connected(g, NetInput(name, terminal_cells=termsets[name]), nr, ne)


def test_route_box_drc_never_worsens_golden():
    """On already-clean (golden) routings, a DRC worker over any box never
    increases overlaps and never disconnects a net."""
    checked = 0
    for seed in range(20):
        g, nets = gen_case(seed, n_nets=6, nx=16, ny=16, n_obstacles=3)
        if len(nets) < 4:
            continue
        r = golden(g, nets)
        if not r.ok:
            continue
        checked += 1
        wh, vh = _halos(g, 0.0, 0.0, 0.0)
        termsets = {n.net: _terminal_cellsets(g, n) for n in nets}
        GC = 4
        ngx = (g.nx + GC - 1) // GC; ngy = (g.ny + GC - 1) // GC
        base = len(flexgc_lite(g, r.routes, r.edges, wh, vh))
        for box in worker_boxes(ngx, ngy, 4, 0):
            out = route_box(g, box, GC, r.routes, r.edges, termsets, wh, vh,
                            ripup_mode="DRC", maze_end_iter=6)
            if out is None:
                continue
            nr = dict(r.routes); ne = dict(r.edges)
            nr.update(out[0]); ne.update(out[1])
            after = len(flexgc_lite(g, nr, ne, wh, vh))
            assert after <= base, (seed, box, base, after)
            for n in nets:
                assert net_connected(g, n, nr, ne), (seed, box, n.net)
    assert checked >= 10


# --- M3: schedule loop converges ---------------------------------------------

def test_flexdr_schedule_converges_on_generated_cases():
    """The full strategy() schedule must drive generated cases to 0 markers
    wherever the golden solves them; whenever it reports ok the routing is
    overlap-free, short-free, connected, legal."""
    total = 0
    solved = 0
    for seed in range(30):
        g, nets = gen_case(seed, n_nets=6, nx=16, ny=16, n_obstacles=3)
        if len(nets) < 4:
            continue
        if not golden(g, nets).ok:
            continue
        total += 1
        wh, vh = _halos(g, 0.0, 0.0, 0.0)
        r = route_flexdr(g, nets, None, 4)
        v = validate(g, nets, r, wh, vh)
        if r.ok:
            solved += 1
            assert v["overlaps"] == 0, (seed, v)
            assert v["literal"] == 0, (seed, v)
            assert v["connected"] == v["n_nets"], (seed, v)
            assert v["routed"] == v["n_nets"], (seed, v)
            assert v["legal"], (seed, v)
    assert total >= 15
    assert solved >= int(0.9 * total), f"flexdr converged only {solved}/{total}"


def test_generator_produces_distinct_terminals():
    """No case is short-by-construction: terminal cells are globally distinct."""
    for seed in range(30):
        g, nets = gen_case(seed, n_nets=6)
        seen = set()
        for n in nets:
            for t in n.terminal_cells:
                for (ix, iy, li) in t:
                    assert (ix, iy) not in seen, (seed, n.net)
                    seen.add((ix, iy))
