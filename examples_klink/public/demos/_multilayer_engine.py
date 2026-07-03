"""Example-owned glue that routes on the multilayer routing engine's track-grid
maze, delegating pin access and the DRC oracle to the proven single-stack
helpers.

This is GLUE for the live-LVS verdict (examples only): it mirrors the uniform live
CapacityGrid into a TrackGrid (indices identical on the min-pitch grid), transfers
legality onto Node fields, and routes with `TrackMaze`. AP generation
(`flexpa_access_nets`), the DRC oracle (`flexgc_lite`), and PRL params are delegated to
the proven single-stack helpers so the live test isolates the TrackGrid MAZE on real
device data. The honest boundary: the AP/layer/track front is toy-grid-validated, not
yet wired to real geometry -- so this run validates the maze-on-TrackGrid + draw + LVS
path, not the full multilayer pipeline. (This glue imports the frozen single-stack
engine's real helpers; the multilayer engine's core does not.)
"""

from __future__ import annotations

from klink.routing.backends.flexdr import flexdr as _t1   # real FlexPA / flexgc / PRL (glue only)
from klink.routing.backends.pnr_multilayer.dr.legality import load_legality
from klink.routing.backends.pnr_multilayer.dr.maze import TrackMaze, checkerboard_tiles
from klink.routing.backends.pnr_multilayer.dr.trackgrid_adapter import TrackGridWorkerAdapter
from klink.routing.backends.pnr_multilayer.grid.track_grid import TrackGrid
from klink.routing.grid.capacity_grid import RouteResult, _terminal_cellsets
from klink.routing.grid.pathfinder import _halos
from klink.routing.backends.pnr_multilayer.pnr_flexta import flexta_seed
from klink.routing.backends.pnr_multilayer.pnr_flexdr import _grt_guides, _grt_guide_corridors

# delegated to the proven real-data helpers (the maze is what we are testing)
_real_flexgc = _t1.flexgc_lite


def flexgc_lite(*a, **k):
    """The DRC oracle route_and_draw_flexdr calls to gate drawing. TG_DRAW_ANYWAY=1 makes
    it report NO markers so the route is drawn despite residual DRC spacing/PRL markers
    (which are NOT electrical shorts) -> we then get the real LVS verdict. The worker-box
    loop uses the REAL oracle (_real_flexgc) regardless."""
    import os as _o
    if _o.environ.get("TG_DRAW_ANYWAY") == "1":
        return []
    return _real_flexgc(*a, **k)
flexpa_access_nets = _t1.flexpa_access_nets
_prl_params = _t1._prl_params


def _mirror(g, profile) -> TrackGrid:
    layers = tuple(g.layers)
    li = {l: i for i, l in enumerate(layers)}
    via_pairs = set()
    for lo, _c, up in profile.vias:
        if lo in li and up in li:
            a, b = sorted((li[lo], li[up]))
            if b == a + 1:
                via_pairs.add((a, b))
    return TrackGrid(
        xCoords=[g.cx(i) for i in range(g.nx)],
        yCoords=[g.cy(j) for j in range(g.ny)],
        zCoords=list(range(len(layers))),
        z_dir=[profile.layer_direction(l) for l in layers],
        z_height=[(i + 1) for i in range(len(layers))],
        layers=layers,
        via_z_pairs=frozenset(via_pairs),
    )


def _legality(g, tg):
    nz = len(g.layers)
    channel = [(ix, iy, li) for li, cells in g.wire_blocked_all.items() for (ix, iy) in cells]
    pads = [(ix, iy, li, owner)
            for li, owners in g.pad_cells.items()
            for owner, cells in owners.items() for (ix, iy) in cells]
    via_blocked = [(ix, iy, zi) for zi in range(nz) for (ix, iy) in g.via_blocked]
    return load_legality(tg, channel=channel, pads=pads, via_blocked=via_blocked)


def _terminals(ni):
    out = {}
    for n in ni:
        ts = []
        for tset in (n.terminal_cells or []):
            if tset:
                ts.append(min(tset))
        out[n.net] = ts
    return out


def _recon_edges(nodes):
    s = set(nodes)
    es = []
    for (x, y, z) in nodes:
        for nb in ((x + 1, y, z), (x, y + 1, z), (x, y, z + 1)):
            if nb in s:
                es.append(((x, y, z), nb))
    return es


def route_flexdr(g, ni, profile, gcell, *, width_um=0.0, wire_clear_um=0.0,
                 via_clear_um=0.0, verbose=False, **kw):
    """Route on the TrackGrid maze worker. Returns a CapacityGrid-style RouteResult in
    cell space (TrackGrid indices == the mirrored CapacityGrid cells)."""
    import os as _os
    import time as _ti
    _rt = _ti.time()
    _rstage = _os.environ.get("TG_STAGE_TIME") == "1"

    def _rmark(lbl):
        nonlocal _rt
        if _rstage:
            print(f"    RT {lbl}: {_ti.time() - _rt:.1f}s", flush=True)
        _rt = _ti.time()

    tg = _mirror(g, profile)
    _rmark("mirror")
    tg.init_full_edges()
    _rmark("init_full_edges")
    owner = _legality(g, tg)
    # block ALL PDN (GND/VDD) cells planar UPFRONT (via-through still allowed) so signals never
    # land on a rail -> no GND short -> no GND-fix -> no fix-introduced overlap to grind.
    _gnd = [(ix, iy, li) for li, owners in g.pad_cells.items()
            for own, cells in owners.items() if own in ("GND", "VDD")
            for (ix, iy) in cells]
    if _gnd:
        load_legality(tg, channel=_gnd)
    _rmark("legality")
    adapter = TrackGridWorkerAdapter(tg, pad_owner=owner)
    terminals = _terminals(ni)

    # confine signal backbones to the clean signal layers; allow a via descent onto the
    # terminal/PDN layers ONLY at each net's AP columns (portals) -- via-clear pin access
    # from the proven FlexPA APs, never free planar signal routing on terminal layers.
    li = {l: i for i, l in enumerate(g.layers)}
    sig_z = {li[l] for l in profile.signal_routing_layers() if l in li}
    portals = {net: {(c[0], c[1]) for c in cs} for net, cs in terminals.items()}
    # AP-local portal WINDOW: a small box (radius R) around each AP where a bounded
    # terminal-layer jog is allowed (sidestep a blocked pin column to a via-clear one).
    R = 3
    window = {net: {(c[0] + dx, c[1] + dy) for c in cs
                    for dx in range(-R, R + 1) for dy in range(-R, R + 1)}
              for net, cs in terminals.items()}
    import os as _os
    if _os.environ.get("TG_DIAG"):
        for net in sorted(terminals)[:4]:
            for c in terminals[net]:
                col = " ".join(
                    f"z{z}{'W' if adapter.wire_ok(c[0], c[1], z, net) else '.'}"
                    f"{'V' if adapter.via_ok(c[0], c[1], z) else '.'}"
                    for z in range(tg.nz))
                print(f"  DIAG {net} AP{c} sig={sorted(sig_z)}: {col}", flush=True)

    _rmark("portals_window")
    wh, vh = _halos(g, width_um, wire_clear_um, via_clear_um)
    ph, pl, dbl = _prl_params(g, profile, width_um)
    _rmark("halos_prl")

    # GUIDE CORRIDORS (T3 global route): {net: set[gcell]} bounding each net's detailed search
    # to its planned thin band (scope: bound the maze, NOT seed it). Measure-first via TG_GUIDE_STATS.
    corridors = None
    if _os.environ.get("TG_GUIDE") == "1" or _os.environ.get("TG_GUIDE_STATS") == "1":
        import time as _t
        _tg0 = _t.time()
        _ordered = sorted(ni, key=lambda n: n.net)
        _termsets = {n.net: _terminal_cellsets(g, n) for n in _ordered}
        corridors = _grt_guide_corridors(g, _ordered, _termsets, gcell, profile,
                                          halo=int(_os.environ.get("TG_GUIDE_HALO", 1)))
        if verbose and corridors:
            import statistics as _st
            ngx = (max(x for s in corridors.values() for (x, y) in s) + 1) if corridors else 1
            ngy = (max(y for s in corridors.values() for (x, y) in s) + 1) if corridors else 1
            sizes = [len(corridors[n.net]) for n in _ordered if n.net in corridors]
            print(f"  GUIDE: {len(corridors)}/{len(_ordered)} nets have corridors, "
                  f"grid ~{ngx}x{ngy}={ngx * ngy} gcells, corridor median={int(_st.median(sizes))} "
                  f"max={max(sizes)} -> median {_st.median(sizes) / (ngx * ngy) * 100:.1f}% of grid "
                  f"(grt {_t.time() - _tg0:.1f}s)", flush=True)
        if _os.environ.get("TG_GUIDE_STATS") == "1":
            corridors = None                     # stats only -- don't bound yet

    # T4 TRACK-ASSIGNED SEED (the whole point of T1-T4): run the proven FlexTA on the real
    # grid to spread parallel runs onto distinct tracks BEFORE detailed routing, so the
    # maze inherits a low-PRL backbone (far less for the last-mile rip-up to fix). Confined
    # to the signal layers; pins are stitched to it by route_all. Falls back to no seed.
    seed_routes = seed_edges_ta = None
    try:
        if _os.environ.get("TG_TASEED") != "1":
            raise RuntimeError("flexta seed disabled (set TG_TASEED=1 to enable)")
        ordered = sorted(ni, key=lambda n: n.net)
        termsets = {n.net: _terminal_cellsets(g, n) for n in ordered}
        guides = _grt_guides(g, ordered, termsets, gcell, profile)
        _sigset = set(profile.signal_routing_layers())
        _ta_dir = {layer_i: d for layer_i, d in dbl.items() if g.layers[layer_i] in _sigset}
        seed_routes, seed_edges_ta, handled = flexta_seed(
            g, ordered, profile, gcell, guides, termsets, _ta_dir, wh, verbose=verbose)
        if verbose:
            print(f"  flexta seed: {len(seed_routes)} nets track-assigned "
                  f"({len(handled)} handled)", flush=True)
    except Exception as exc:                          # seed is an optimisation, not required
        if verbose:
            print(f"  flexta seed skipped: {exc!r}", flush=True)

    m = TrackMaze(tg, adapter, planar_layers=sig_z, portals=portals, portal_window=window,
                  jog_cost=8.0,
                  marker_weight=float(_os.environ.get("TG_MW", 40)),
                  occ_penalty=float(_os.environ.get("TG_OCC", 50)),
                  via_cost=float(_os.environ.get("TG_VIA", 1.0)))
    m.spacing_halo = int(_os.environ.get("TG_HALO", 0))   # spacing-aware -> born low-DRC
    m.use_corridor = _os.environ.get("TG_CORRIDOR") == "1"   # off by default (net-negative:
    # typical nets are heuristic-bounded; hard nets fall through to whole-grid anyway)
    from klink.routing.backends.pnr_multilayer.dr.rust_bridge import available as _rust_ok
    m.use_rust = _os.environ.get("TG_RUST") != "0" and _rust_ok()   # Rust per-net A* by default
    # (klink_trackmaze_rs, byte-parity); graceful Python fallback if the kernel isn't built
    if m.use_rust:                               # 3b: resolve overlaps in Rust too (the cpu8
        m.rust_ovlp_passes = int(_os.environ.get("TG_RUST_OVLP", 80))   # ~21s Python -> Rust
    if corridors:                                # T3 guide corridors bound the detailed search
        m.corridors = corridors
        m.gcell = gcell
    if _os.environ.get("TG_PROFILE") == "1":
        import cProfile, pstats, io as _io, time as _tt
        _pr = cProfile.Profile()
        _t0 = _tt.time()
        _pr.enable()
        m.route_all(terminals, seed=seed_routes, seed_edges=seed_edges_ta, max_passes=40)
        _pr.disable()
        print(f"  PROFILE route_all wall={_tt.time() - _t0:.1f}s", flush=True)
        _s = _io.StringIO()
        pstats.Stats(_pr, stream=_s).sort_stats("tottime").print_stats(12)
        print(_s.getvalue(), flush=True)
    else:
        m.route_all(terminals, seed=seed_routes, seed_edges=seed_edges_ta,
                    max_passes=int(_os.environ.get("TG_OVLP_PASSES", 0)))   # skip the redundant
        #  maze.py overlap loop by default -> the unified worker-box+overlap loop below resolves
        #  overlaps (bounded route_box, whole-grid reroute when stuck) far faster
    _rmark("route_all+seed")

    # WORKER-BOX loop (faithful FlexDR): global flexgc finds markers; the fix is LOCAL --
    # per checkerboard tile, only the in-box segments of the marked nets are ripped up and
    # rerouted within the tile's extBox (route_box, boundary-anchored + accept-or-revert),
    # so a fix never perturbs the whole net or ripples globally (kills the thrash).
    BOX, MARGIN = int(_os.environ.get("TG_BOX", 8)), int(_os.environ.get("TG_MARGIN", 4))
    GROW = int(_os.environ.get("TG_GROW", 6))
    DIM = max(g.nx, g.ny)
    # NEVER-HANG time budget: the repair loops must terminate in bounded wall
    # time. On exhaustion we STOP GRINDING and return an instructive failure
    # (where the residual overlaps are + what to change) instead of burning an
    # hour ripping up a design whose channels are simply over-subscribed.
    import time as _bt
    _budget_s = float(_os.environ.get("TG_TIME_BUDGET", 900))
    _budget0 = _bt.time()
    _budget_hit = False

    def _over_budget():
        return _bt.time() - _budget0 > _budget_s

    markers = []
    soft_next = False
    for dp in range(int(_os.environ.get("TG_PASSES", 90))):
        if _over_budget():
            _budget_hit = True
            print(f"  TIME BUDGET {_budget_s:.0f}s exhausted in worker-box loop "
                  f"(pass {dp + 1}) -> stop grinding", flush=True)
            break
        routes = {net: nodes for net, nodes in m.routes.items() if nodes}
        edges = {net: m.edges.get(net, []) for net in routes}
        import time as _t
        _tgc = _t.time()
        markers = _real_flexgc(g, routes, edges, wh, vh, prl_halo=ph, prl_len=pl, dir_by_li=dbl)
        _gc_s = _t.time() - _tgc
        if not markers:
            break
        _tbox = _t.time()
        for mk in markers:                       # marker cost steers the local reroutes
            for cell in mk.cells:
                m.add_marker(cell)
        box_size = min(DIM, BOX + dp * GROW, int(_os.environ.get("TG_BOXMAX", DIM)))
        hard = not soft_next                     # a soft "kick" pass follows a stuck pass
        soft_next = False
        fixed = 0
        done = set()                             # each net box-routed at most once per pass
        for box, _color in checkerboard_tiles(g.nx, g.ny, box_size):
            ext = (max(0, box[0] - MARGIN), max(0, box[1] - MARGIN),
                   min(g.nx - 1, box[2] + MARGIN), min(g.ny - 1, box[3] + MARGIN))
            ext_nets = set()
            for mk in markers:
                if any(ext[0] <= c[0] <= ext[2] and ext[1] <= c[1] <= ext[3] for c in mk.cells):
                    ext_nets |= set(mk.sources)
            for net in sorted(ext_nets - done, key=m.net_id):
                if net in terminals and m.route_box(net, ext, terminals[net], hard_avoid=hard):
                    fixed += 1
                done.add(net)
        nov = len(m.overlaps_nodes())
        if verbose:
            print(f"  worker-box pass {dp + 1}: markers={len(markers)} ov={nov} boxfixed={fixed}"
                  f" gc={_gc_s:.1f}s box={_t.time() - _tbox:.1f}s bs={box_size}"
                  f"{' (soft kick)' if not hard else ''}", flush=True)
        # LVS only needs 0 OVERLAPS (no shorts) + full routing -> stop as soon as that holds,
        # without grinding the (LVS-irrelevant) residual DRC spacing markers. Big speed win.
        if _os.environ.get("TG_DRAW_ANYWAY") == "1" and nov == 0 and not getattr(m, "unrouted", []):
            if verbose:
                print(f"  LVS-clean (0 overlaps) at pass {dp + 1} -> stop", flush=True)
            break
        if fixed == 0:
            if nov > 0:
                # bounded box can't fix this overlap -> whole-grid reroute the overlapped
                # nets (the markers added above now steer them apart), then keep grinding
                ov_nodes = m.overlaps_nodes()
                fixnets = {sorted(m.occ[n], key=m.net_id)[-1]
                           for n in ov_nodes if m.occ.get(n)}
                for net in sorted(fixnets, key=m.net_id):
                    if net in terminals:
                        m.remove_route(net)
                        res = m.route_net_bounded(net, terminals[net])
                        m.add_route(net, *(res if res is not None else ([], [])))
                continue                         # re-run flexgc next pass
            elif box_size < DIM:
                pass                             # stuck on DRC markers only -> grow the box
                                                 # (born-low-DRC, cheap) toward 0 markers
            elif hard and _os.environ.get("TG_KICK") == "1":
                soft_next = True
            else:
                break                            # whole-grid box AND stuck -> truly done

    # CLEANUP: resolve residual signal-signal OVERLAPS and GND shorts TOGETHER -- each reroute
    # can introduce the other (a GND-fix reroute may overlap a signal; an overlap reroute may
    # land on an unregistered GND cell), so iterate until BOTH are 0. Rust already resolved the
    # bulk of overlaps (the 3b win); this handles the few stragglers + cross-introductions.
    pwr = {(ix, iy, li) for li, owners in g.pad_cells.items()
           for owner, cells in owners.items() if owner in ("GND", "VDD")
           for (ix, iy) in cells}
    for _cyc in range(int(_os.environ.get("TG_CLEANUP", 24))):
        if _over_budget():
            _budget_hit = True
            print(f"  TIME BUDGET {_budget_s:.0f}s exhausted in cleanup "
                  f"(cycle {_cyc + 1}) -> stop grinding", flush=True)
            break
        gnd = {net for net, nodes in m.routes.items()
               for nd in nodes if (nd[0], nd[1], nd[2]) in pwr}
        # reroute avoiding ALL foreign nets (hard_avoid) so the fix never INTRODUCES an
        # overlap (kills the oscillation); fall back to the soft bounded route only if no
        # overlap-free path exists.
        def _reroute(net):
            m.remove_route(net)
            # escalate: bounded hard-avoid (cheap) -> whole-grid hard-avoid (rare, breaks a
            # stuck congested overlap in ONE reroute instead of oscillating) -> soft fallback.
            res = m.route_net_bounded(net, terminals[net], margins=(8, 24, 64), hard_avoid=True)
            if res is None:
                res = m.route_net(net, terminals[net], hard_avoid=True)   # whole-grid, no overlap
            if res is None:
                res = m.route_net_bounded(net, terminals[net])
            m.add_route(net, *(res if res is not None else ([], [])))

        if gnd:                                       # block the hit GND cells + reroute
            hit = [nd for net in gnd for nd in m.routes[net] if (nd[0], nd[1], nd[2]) in pwr]
            load_legality(tg, channel=hit)
            for net in sorted(gnd, key=m.net_id):
                _reroute(net)
        ov_nodes = m.overlaps_nodes()
        if ov_nodes:                                  # the few overlap stragglers
            for n in ov_nodes:
                m.add_marker(n)
            fix = {sorted(m.occ[n], key=m.net_id)[-1] for n in ov_nodes if m.occ.get(n)}
            for net in sorted(fix, key=m.net_id):
                if net in terminals:
                    _reroute(net)
        if not gnd and not ov_nodes:
            break
        if verbose:
            print(f"  cleanup {_cyc + 1}: gnd={len(gnd)} overlaps={len(ov_nodes)}", flush=True)

    routes = {net: nodes for net, nodes in m.routes.items() if nodes}
    edges = {net: m.edges.get(net, []) for net in routes}
    ov = m.overlaps_nodes()
    unrouted = list(getattr(m, "unrouted", []))
    for net, nodes in routes.items():
        bad = [n for n in nodes if (n[0], n[1], n[2]) in pwr]
        if bad:
            print(f"  GND-OVERLAP net {net[:34]}: {len(bad)} node(s) on PDN cells, "
                  f"e.g. {bad[:3]}", flush=True)
    if _os.environ.get("TG_AUDIT") and unrouted:
        for net in unrouted[:6]:
            cols = []
            for c in terminals.get(net, []):
                reach = [z for z in range(tg.nz)
                         if adapter.wire_ok(c[0], c[1], z, net) and (z in sig_z)]
                cols.append(f"AP{c}->sigreach={reach}")
            print(f"  UNROUTED {net[:30]}: {cols}", flush=True)

    if _os.environ.get("TG_AUDIT"):
        from collections import Counter
        layer_nodes = Counter()
        layer_wire = Counter()
        layer_via = Counter()
        for net, nodes in routes.items():
            for (x, y, z) in nodes:
                layer_nodes[z] += 1
        for net, es in edges.items():
            for (a, b) in es:
                if a[2] != b[2]:
                    layer_via[min(a[2], b[2])] += 1     # via straddles two layers
                else:
                    layer_wire[a[2]] += 1
        print("AUDIT per-layer (index -> GDS): nodes / wire-edges / via-up", flush=True)
        for z in range(len(g.layers)):
            tag = "SIG" if z in sig_z else "term/pdn"
            print(f"  z{z} {g.layers[z]:>6} [{tag:>8}]: nodes={layer_nodes[z]:5d} "
                  f"wires={layer_wire[z]:5d} vias_up={layer_via[z]:5d}", flush=True)
        sig_n = sum(layer_nodes[z] for z in sig_z)
        term_n = sum(layer_nodes[z] for z in range(len(g.layers)) if z not in sig_z)
        print(f"  INVARIANT signal-layer nodes={sig_n}  terminal/pdn-layer nodes={term_n}",
              flush=True)
        for net in sorted(routes)[:8]:
            zs = sorted({n[2] for n in routes[net]})
            nv = sum(1 for (a, b) in edges[net] if a[2] != b[2])
            print(f"  NET {net[:40]}: layers={[g.layers[z] for z in zs]} "
                  f"wire_edges={len(edges[net]) - nv} vias={nv}", flush=True)
        # pin-access coverage for the ROUTED nets: every terminal pin must be in its
        # net's route AND have a via up (the access stack 101/104 -> signal).
        tot = cov = via_up = 0
        pin_layer = Counter()
        uncov = []
        for net in routes:
            rset = set(routes[net])
            eset = set(edges[net]) | {(b, a) for (a, b) in edges[net]}
            for ap in terminals.get(net, []):
                tot += 1
                pin_layer[g.layers[ap[2]]] += 1
                in_route = ap in rset
                up = (ap, (ap[0], ap[1], ap[2] + 1)) in eset
                cov += in_route
                via_up += up
                if not in_route:
                    uncov.append((net[:30], ap, g.layers[ap[2]]))
        print(f"  PIN-ACCESS (routed nets): pins={tot} in_route={cov} via_up={via_up} "
              f"pin_layers={dict(pin_layer)}", flush=True)
        for u in uncov[:12]:
            print(f"    UNCOVERED pin: {u}", flush=True)
    if _os.environ.get("TG_DRAW_ANYWAY") == "1":
        ok = not ov and not unrouted          # draw despite DRC spacing markers (LVS verdict)
    elif _os.environ.get("TG_FORCE_DRAW"):
        ok = not markers and not ov
    else:
        ok = not markers and not ov and not unrouted
    problems = ()
    if markers or ov or unrouted:
        ov_sample = [(round(g.cx(x) / 1000.0, 1), round(g.cy(y) / 1000.0, 1),
                      g.layers[z], sorted(m.occ.get((x, y, z), ())))
                     for (x, y, z) in sorted(ov)[:5]]
        problems = ({"type": "trackmaze", "markers": len(markers),
                     "overlaps": len(ov), "unrouted": len(unrouted),
                     "unrouted_sample": unrouted[:5],
                     "overlap_sample_um": ov_sample,
                     "time_budget_hit": _budget_hit,
                     "next_action": (
                         "residual overlaps = over-subscribed channels at the "
                         "listed spots: widen the floorplan there (row_pitch / "
                         "col_pitch), snap port stubs to routing-channel ys "
                         "(spread_ports snap=), add a signal layer, or raise "
                         "TG_TIME_BUDGET if it was hit")},)
    if verbose:
        print(f"  trackmaze: {len(routes)}/{len(ni)} nets, unrouted={len(unrouted)}, "
              f"final markers={len(markers)} overlaps={len(ov)}", flush=True)
        exps = sorted(m.net_expansions)
        if exps:
            med = exps[len(exps) // 2]
            print(f"  STATS expanded={m.expanded} reroutes={m.reroutes} "
                  f"per-net A* expansions: max={max(exps)} median={med} "
                  f"(grid {g.nx}x{g.ny}x{len(g.layers)})", flush=True)
    return RouteResult(ok, routes, len(markers), problems, edges)
