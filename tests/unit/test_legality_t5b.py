"""Stage T5 increment B gate -- legality/obstacle surface on TrackGrid Node fields.

Proves (docs/TRACK2_T5_FLEXDR_DRC_MAPPING.md §1 group B): channel / foreign-pad /
via-blocked obstacles written onto TrackGrid Node fixed-shape/blocked fields are
REJECTED through the adapter -- a blocked planar edge cannot be traversed and a blocked
via cannot land. Adapter-only: no maze state, no DRC rules, no route-quality or
pipeline-pass claim (verdict = live LVS at cpu4+cpu8).
"""

from collections import deque

from klink.routing.backends.pnr_multilayer.dr.legality import BLOCK, load_legality
from klink.routing.backends.pnr_multilayer.dr.trackgrid_adapter import (
    TrackGridWorkerAdapter,
)
from klink.routing.backends.pnr_multilayer.grid.track_grid import build
from klink.routing.grid.process_profile import ProcessProfile

ZH = 2   # layer 2 is an H signal layer in the fixture stack


def _grid():
    p = ProcessProfile(
        routing_layers=("20/0", "21/0", "22/0", "23/0", "24/0"),
        gate_layer="20/0", sd_layer="20/0", channel_layer="19/0",
        vias=(("20/0", "200/0", "21/0"), ("21/0", "201/0", "22/0"),
              ("22/0", "202/0", "23/0"), ("23/0", "203/0", "24/0")),
        layer_directions={"20/0": "H", "21/0": "V", "22/0": "H", "23/0": "V", "24/0": "H"},
        signal_layers=("21/0", "22/0", "23/0", "24/0"),
        wire_width_um=1.0, wire_clear_um=0.0,
        via_pad_um=5.0, litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0,
    )
    g = build(p, (0, 0, 4000, 2000))         # x:0..4000 (5), y:0..2000 (3)
    g.init_full_edges()
    return g


def _planar_nbrs(g, a, adapter, net):
    """Planar-only neighbours on a fixed layer, gating on edge present + not blocked +
    target wire_ok -- exactly the maze's legality test."""
    xi, yi, zi = a
    out = []
    if xi + 1 < g.nx and g.has_edge(xi, yi, zi, "E") and not adapter.planar_edge_blocked(xi, yi, zi, "E") and adapter.wire_ok(xi + 1, yi, zi, net):
        out.append((xi + 1, yi, zi))
    if xi - 1 >= 0 and g.has_edge(xi - 1, yi, zi, "E") and not adapter.planar_edge_blocked(xi - 1, yi, zi, "E") and adapter.wire_ok(xi - 1, yi, zi, net):
        out.append((xi - 1, yi, zi))
    if yi + 1 < g.ny and g.has_edge(xi, yi, zi, "N") and not adapter.planar_edge_blocked(xi, yi, zi, "N") and adapter.wire_ok(xi, yi + 1, zi, net):
        out.append((xi, yi + 1, zi))
    if yi - 1 >= 0 and g.has_edge(xi, yi - 1, zi, "N") and not adapter.planar_edge_blocked(xi, yi - 1, zi, "N") and adapter.wire_ok(xi, yi - 1, zi, net):
        out.append((xi, yi - 1, zi))
    return out


def _bfs(g, adapter, src, dst, net):
    seen = {src}
    q = deque([src])
    while q:
        n = q.popleft()
        if n == dst:
            return True
        for m in _planar_nbrs(g, n, adapter, net):
            if m not in seen:
                seen.add(m)
                q.append(m)
    return False


# ---- B-1: a channel keep-out blocks planar edges (maze cannot cross) --------
def test_channel_blocks_planar_edges():
    g = _grid()
    a = TrackGridWorkerAdapter(g)
    # baseline: a planar path across layer 2 exists
    assert _bfs(g, a, (0, 0, ZH), (4, 0, ZH), "n") is True

    # wall the whole xi=2 column on layer 2 (all rows) as an all-net channel keep-out
    wall = [(2, yi, ZH) for yi in range(g.ny)]
    load_legality(g, channel=wall)

    # Node fields written
    idx = g.get_idx(2, 0, ZH)
    assert g.nodes["fsc_planar_h"][idx] == BLOCK and g.nodes["blocked_E"][idx] == 1
    # cell rejected for every net, and the edges into the wall are blocked
    assert a.wire_ok(2, 0, ZH, "n") is False
    assert a.planar_edge_blocked(1, 0, ZH, "E") is True       # west neighbour -> wall
    assert a.planar_edge_blocked(2, 0, ZH, "E") is True       # wall -> east
    # the maze can no longer cross the wall
    assert _bfs(g, a, (0, 0, ZH), (4, 0, ZH), "n") is False


# ---- B-2: foreign pad rejected, owner allowed -------------------------------
def test_pad_owner_foreign_rejected():
    g = _grid()
    owner = load_legality(g, pads=[(3, 1, ZH, "netA")])
    a = TrackGridWorkerAdapter(g, pad_owner=owner)
    assert g.nodes["fsc_planar_h"][g.get_idx(3, 1, ZH)] == BLOCK
    assert a.wire_ok(3, 1, ZH, "netA") is True        # owner may occupy its own pad
    assert a.wire_ok(3, 1, ZH, "netB") is False       # foreign net rejected
    assert a.wire_ok(0, 0, ZH, "netB") is True        # clear cell unaffected


# ---- B-3: a device-body keep-out rejects a via landing ----------------------
def test_via_blocked_rejected():
    g = _grid()
    a = TrackGridWorkerAdapter(g)
    load_legality(g, via_blocked=[(2, 2, ZH)])
    assert g.nodes["fsc_via"][g.get_idx(2, 2, ZH)] == BLOCK
    assert a.via_ok(2, 2, ZH) is False                # via may not land on the body
    assert a.via_ok(0, 0, ZH) is True                 # clear cell ok


# ---- additivity: geometry (A) still faithful after loading legality (B) -----
def test_geometry_intact_after_legality():
    g = _grid()
    load_legality(g, channel=[(2, 0, ZH)], via_blocked=[(1, 1, ZH)])
    a = TrackGridWorkerAdapter(g)
    for i, x in enumerate(g.xCoords):
        assert a.cx(i) == x and a.cell_of(x, g.yCoords[0])[0] == i
