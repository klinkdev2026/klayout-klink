"""Stage T5 increment A gate -- TrackGridWorkerAdapter geometry + T4 seed round-trip.

Proves (docs/TRACK2_T5_FLEXDR_DRC_MAPPING.md §1): the worker's geometry surface
(nx/ny/cx/cy/cell_of/in_bounds/layers/pitch) over a NON-UNIFORM TrackGrid is faithful
(cx(ix)==xCoords[ix], never x0+ix*pitch), and a REAL T4 seed segment/via round-trips
through coordinate<->index. Geometry only -- no legality, no maze, no DRC, no quality or
pipeline-pass claim (the verdict is live LVS at T5 cpu4+cpu8).
"""

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


def _p_vhv():
    # V/H/V toy stack (T2 fixture), 1000 nm pitch
    return ProcessProfile(
        routing_layers=("10/0", "11/0", "12/0"),
        gate_layer="10/0", sd_layer="11/0", channel_layer="9/0",
        vias=(("10/0", "100/0", "11/0"), ("11/0", "101/0", "12/0")),
        layer_directions={"10/0": "V", "11/0": "H", "12/0": "V"},
        wire_width_um=1.0, wire_clear_um=0.0,
        via_pad_um=5.0, litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0,
    )


def _p_5layer():
    # terminal(H) + 2V + 2H signal; 1000 nm pitch
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


# ---- A-1: geometry faithful on a NON-UNIFORM grid (cx != x0 + ix*pitch) -----
def test_adapter_geometry_nonuniform_faithful():
    p = _p_vhv()
    # AP on the V layer (10/0) injects an OFF-LATTICE x=1234 -> xCoords irregular
    g = build(p, (0, 0, 6000, 4000), access_points=[("10/0", 1234, 0)])
    assert 1234 in g.xCoords                      # V-layer pref-axis x injected
    a = TrackGridWorkerAdapter(g)

    ix = g.xCoords.index(1234)
    assert a.cx(ix) == 1234 == g.xCoords[ix]      # faithful to the sorted array
    # a uniform worker would compute x0 + ix*pitch -- and get the WRONG value here
    x0, pitch = g.xCoords[0], a.pitch_nm
    assert x0 + ix * pitch != a.cx(ix)            # NON-uniform: arithmetic is wrong
    # every index round-trips through cell_of
    for i, x in enumerate(g.xCoords):
        assert a.cx(i) == x and a.cell_of(x, g.yCoords[0])[0] == i
    for j, y in enumerate(g.yCoords):
        assert a.cy(j) == y and a.cell_of(g.xCoords[0], y)[1] == j
    assert a.nx == len(g.xCoords) and a.ny == len(g.yCoords)
    assert a.in_bounds(0, 0) and not a.in_bounds(a.nx, 0)
    assert a.layers == g.layers
    # geometry ONLY -- no legality / maze surface leaked into increment A
    assert not hasattr(a, "pad_cells") and not hasattr(a, "box_maze")


# ---- A-2: a REAL T4 seed (segment + via) round-trips on TrackGrid -----------
def test_t4_seed_consumption_roundtrip():
    p = _p_5layer()
    # aligned TrackGrid: 6 x-coords (0..5000) x 4 y-coords (0..3000), gcell idx == grid idx
    g = build(p, (0, 0, 5000, 3000))
    a = TrackGridWorkerAdapter(g)
    stack = stack_from_profile(p)

    # T3 -> T4 on a matching 6x4 gcell grid for one net
    nets = [{"net": "w", "terminals": [(0, 0, 0), (4, 0, 0)]}]
    t3 = assign_layers(6, 4, stack, nets, via_cost=1.0)
    assert t3.ok
    t4 = assign_tracks(t3.guides, stack, tracks_per_gcell=1)
    assert t4.ok and t4.segments.get("w") and t4.vias.get("w")

    checked_nodes = 0
    for seg in t4.segments["w"]:
        perp, b, e = seg["track_coord"], seg["along_begin"], seg["along_end"]
        for k in range(b, e + 1):
            xi, yi = (k, perp) if seg["is_h"] else (perp, k)
            assert a.in_bounds(xi, yi)
            x, y = a.cx(xi), a.cy(yi)                 # coordinate from index
            assert x == g.xCoords[xi] and y == g.yCoords[yi]
            assert a.cell_of(x, y) == (xi, yi)         # index back from coordinate
            checked_nodes += 1
    assert checked_nodes > 0

    for v in t4.vias["w"]:
        gx, gy = v["gx"], v["gy"]
        assert a.in_bounds(gx, gy)
        assert a.cell_of(a.cx(gx), a.cy(gy)) == (gx, gy)   # via gcell round-trip
        assert 0 <= v["z_lo"] < a.nz and 0 <= v["z_hi"] < a.nz


# ---- isolation: T5 adapter must not import Track 1 --------------------------
def test_no_track1_import():
    import klink.routing.backends.pnr_multilayer.dr.trackgrid_adapter as ad

    src = open(ad.__file__, encoding="utf-8").read()
    assert "backends.flexdr" not in src and "backends/flexdr" not in src
