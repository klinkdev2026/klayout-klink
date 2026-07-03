"""Stage T5 increment C gate -- Python-first maze state on TrackGrid.

Proves (docs/TRACK2_T5_FLEXDR_DRC_MAPPING.md group C): the maze routes on TrackGrid via
the A(geometry)+B(legality) adapter -- occupancy conflicts are avoided, rip-up/retry
resolves a seeded overlap, the via ladder is used to change layers, and B-blocked cells
are detoured. It consumes a REAL T4 seed and produces routed geometry. DRC/G4 stays out
(only B legality is a hard blocker); no Rust; no pipeline-pass or speed claim (verdict =
live LVS at cpu4+cpu8).
"""

from klink.routing.backends.pnr_multilayer.dr.legality import load_legality
from klink.routing.backends.pnr_multilayer.dr.maze import (
    TrackMaze,
    checkerboard_tiles,
    nodes_from_t4,
)
from klink.routing.backends.pnr_multilayer.dr.trackgrid_adapter import (
    TrackGridWorkerAdapter,
)
from klink.routing.backends.pnr_multilayer.gr3d.layer_assign import (
    assign_layers,
    stack_from_profile,
)
from klink.routing.backends.pnr_multilayer.grid.track_grid import build
from klink.routing.backends.pnr_multilayer.ta.track_assign import assign_tracks
from klink.routing.grid.process_profile import ProcessProfile

ZH = 2   # an H signal layer


def _profile():
    return ProcessProfile(
        routing_layers=("20/0", "21/0", "22/0", "23/0", "24/0"),
        gate_layer="20/0", sd_layer="20/0", channel_layer="19/0",
        vias=(("20/0", "200/0", "21/0"), ("21/0", "201/0", "22/0"),
              ("22/0", "202/0", "23/0"), ("23/0", "203/0", "24/0")),
        layer_directions={"20/0": "H", "21/0": "V", "22/0": "H", "23/0": "V", "24/0": "H"},
        signal_layers=("21/0", "22/0", "23/0", "24/0"),
        wire_width_um=1.0, wire_clear_um=0.0,
        via_pad_um=5.0, litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0,
    )


def _grid(bbox=(0, 0, 4000, 2000)):
    g = build(_profile(), bbox)
    g.init_full_edges()
    return g


def _shared(routes):
    seen, dup = set(), set()
    for net, nodes in routes.items():
        for n in nodes:
            (dup if n in seen else seen).add(n)
    # a node is a real overlap only if owned by >1 net
    owners = {}
    for net, nodes in routes.items():
        for n in nodes:
            owners.setdefault(n, set()).add(net)
    return {n for n, w in owners.items() if len(w) > 1}


# ---- checkerboard schedule surface -----------------------------------------
def test_checkerboard_schedule():
    tiles = checkerboard_tiles(8, 6, 4)
    assert tiles and all(len(b) == 4 for b, _c in tiles)
    colors = {c for _b, c in tiles}
    assert colors == {0, 1}                         # two-colour
    assert tiles == checkerboard_tiles(8, 6, 4)     # deterministic


# ---- C-1: occupancy conflict avoided (no shared node) ----------------------
def test_occupancy_conflict_avoided():
    g = _grid()
    a = TrackGridWorkerAdapter(g)
    m = TrackMaze(g, a)
    terminals = {
        "A": [(0, 1, ZH), (4, 1, ZH)],     # horizontal through (2,1)
        "B": [(2, 0, ZH), (2, 2, ZH)],     # vertical through (2,1) -> conflict
    }
    r = m.route_all(terminals)
    assert r.ok and r.overlap_count == 0
    assert not _shared(r.routes)            # the two nets share no node


# ---- C-2: rip-up / retry resolves a SEEDED overlap -------------------------
def test_ripup_retry_resolves_seeded_overlap():
    g = _grid()
    a = TrackGridWorkerAdapter(g)
    m = TrackMaze(g, a)
    terminals = {
        "A": [(0, 1, ZH), (4, 1, ZH)],
        "B": [(2, 0, ZH), (2, 2, ZH)],
    }
    # seed both straight through (2,1,ZH) -> a guaranteed initial overlap
    seed = {
        "A": [(x, 1, ZH) for x in range(5)],
        "B": [(2, y, ZH) for y in range(3)],
    }
    r = m.route_all(terminals, seed=seed)
    assert r.ok and r.overlap_count == 0
    assert r.passes >= 1                    # it took at least one rip-up pass


# ---- C-3: via ladder used to change layers ---------------------------------
def test_via_ladder():
    g = _grid()
    a = TrackGridWorkerAdapter(g)
    m = TrackMaze(g, a)
    # terminals on layer 0 and layer 2 at the same (x,y) -> must climb the ladder
    r = m.route_all({"v": [(1, 1, 0), (1, 1, 2)]})
    assert r.ok
    route = set(r.routes["v"])
    assert {(1, 1, 0), (1, 1, 1), (1, 1, 2)} <= route   # 0->1->2 ladder


# ---- C-4: B-blocked cells are detoured -------------------------------------
def test_blocked_detour():
    g = _grid()
    # wall xi=2 at rows 0 and 1 on layer 2 (leave row 2 open) -> must go around
    walled = [(2, 0, ZH), (2, 1, ZH)]
    load_legality(g, channel=walled)
    a = TrackGridWorkerAdapter(g)
    m = TrackMaze(g, a)
    r = m.route_all({"w": [(0, 0, ZH), (4, 0, ZH)]})
    assert r.ok
    route = set(r.routes["w"])
    assert not (route & set(walled))                   # never enters a blocked cell
    assert (0, 0, ZH) in route and (4, 0, ZH) in route  # still connects


# ---- C-5: consume a REAL T4 seed and route geometry on TrackGrid ------------
def test_consume_real_t4_seed():
    g = _grid((0, 0, 5000, 3000))                      # aligned grid (gcell idx == grid idx)
    a = TrackGridWorkerAdapter(g)
    stack = stack_from_profile(_profile())
    t3 = assign_layers(6, 4, stack, [{"net": "w", "terminals": [(0, 0, 0), (4, 0, 0)]}],
                       via_cost=1.0)
    assert t3.ok
    t4 = assign_tracks(t3.guides, stack, tracks_per_gcell=1)
    assert t4.ok

    seed = {"w": nodes_from_t4(t4.segments["w"], t4.vias["w"])}
    assert seed["w"]                                    # real seed nodes
    m = TrackMaze(g, a)
    r = m.route_all({"w": [(0, 0, 0), (4, 0, 0)]}, seed=seed)
    assert r.ok and r.routes["w"]                       # routed geometry produced
    assert (0, 0, 0) in r.routes["w"] and (4, 0, 0) in r.routes["w"]


# ---- worker-box: route_box preserves full-net connectivity (no open) -------
def test_route_box_accept_preserves_connectivity():
    g = _grid()
    g.init_full_edges()
    m = TrackMaze(g, TrackGridWorkerAdapter(g))
    nodes = [(x, 0, ZH) for x in range(6)]                  # straight wire x0..x5
    edges = [((x, 0, ZH), (x + 1, 0, ZH)) for x in range(5)]
    m.add_route("w", nodes, edges)
    terms = [(0, 0, ZH), (5, 0, ZH)]
    assert m._connected("w", terms)
    changed = m.route_box("w", (2, 0, 3, 0), terms)          # rip+reroute the middle
    assert changed and m._connected("w", terms)              # pins 0 and 5 still joined


def test_route_box_revert_on_unroutable_keeps_net():
    g = _grid()
    g.init_full_edges()
    a = TrackGridWorkerAdapter(g)
    m = TrackMaze(g, a)
    nodes = [(x, 0, ZH) for x in range(6)]
    edges = [((x, 0, ZH), (x + 1, 0, ZH)) for x in range(5)]
    m.add_route("w", nodes, edges)
    terms = [(0, 0, ZH), (5, 0, ZH)]
    # wall the middle so a local reconnect is impossible -> route_box must REVERT
    load_legality(g, channel=[(2, 0, ZH), (3, 0, ZH)])
    changed = m.route_box("w", (2, 0, 3, 0), terms)
    assert changed is False                                  # reverted (could not reconnect)
    assert m._connected("w", terms)                         # original geometry intact
