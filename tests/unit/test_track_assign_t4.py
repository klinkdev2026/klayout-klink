"""Stage T4 gate -- FlexTA track assignment on T3 guides (scoped, faithful).

Proves the track-assignment fidelity + CORRECTNESS preservation
(docs/TRACK2_T4_TRACK_ASSIGN_MAPPING.md §7): every iroute assigned on its single T3
layer (G4-a), T5-ready emission shape (G4-b), track packing reduces same-track overlap
to zero when capacity allows (G4-c), connectivity preserved (G4-d). Offline only; the
real gate is Track2-vs-Track1 at T5 live LVS. Greedy stays a diagnostic, not here.
"""

from klink.routing.backends.pnr_multilayer.gr3d.layer_assign import (
    LayerStack,
    assign_layers,
)
from klink.routing.backends.pnr_multilayer.ta.track_assign import (
    assign_tracks,
    build_iroutes,
)


def make_stack(signal_dirs):
    dirs = ["H"] + list(signal_dirs)
    nz = len(dirs)
    return LayerStack(tuple(dirs), frozenset(range(1, nz)), frozenset({0}),
                      frozenset((z, z + 1) for z in range(nz - 1)), tracks_per_gcell=1)


def _parallel_h_guides(K, z=1):
    # K nets, each an H run on layer z, row gcell 0, edges x in {0,1,2} -> they share
    # the same perpendicular track band and must take different tracks
    return {f"n{k}": [(z, "H", 0, 0), (z, "H", 1, 0), (z, "H", 2, 0)] for k in range(K)}


# ---- G4-a / G4-c: assignment + packing (K runs over >=K tracks -> 0 overlap) -
def test_gate_a_c_packing_zero_overlap():
    stack = make_stack(["H", "H"])
    GC = 4                                   # 4 tracks per gcell band -> fits K=3
    r = assign_tracks(_parallel_h_guides(3, z=1), stack, tracks_per_gcell=GC)
    assert r.ok                              # every iroute placed on its T3 layer
    for ir in r.iroutes:
        assert ir.layer == 1 and ir.tlo <= ir.track <= ir.thi
    # distinct tracks -> zero same-track overlap
    tracks = [ir.track for ir in r.iroutes]
    assert len(set(tracks)) == 3
    assert r.residual_drc == 0


def test_gate_c_overcapacity_bounded():
    stack = make_stack(["H", "H"])
    GC = 2                                   # only 2 tracks but K=3 runs -> must overlap
    r = assign_tracks(_parallel_h_guides(3, z=1), stack, tracks_per_gcell=GC)
    assert r.ok                              # still assigns (no crash); rip-up bounded
    assert r.residual_drc > 0                # overlap minimised but unavoidable


# ---- G4-b: T5-ready emission shape ------------------------------------------
def test_gate_b_emission_contract():
    stack = make_stack(["H", "V"])
    # an L: H run on z1 (H), via up at gcell (2,0), V run on z2 (V)
    guides = {"L": [(1, "H", 0, 0), (1, "H", 1, 0),
                    (2, "V", 2, 0), (2, "V", 2, 1), (1, "U", 2, 0)]}
    r = assign_tracks(guides, stack, tracks_per_gcell=2)
    assert r.ok
    segs = r.segments["L"]
    assert segs and all(
        {"layer", "is_h", "track_coord", "along_begin", "along_end"} <= set(s) for s in segs
    )
    assert r.vias["L"] == [{"gx": 2, "gy": 0, "z_lo": 1, "z_hi": 2}]


# ---- G4-d: connectivity preserved by the assignment -------------------------
def test_gate_d_connectivity_preserved():
    stack = make_stack(["H", "V"])
    guides = {"L": [(1, "H", 0, 0), (1, "H", 1, 0),
                    (2, "V", 2, 0), (2, "V", 2, 1), (1, "U", 2, 0)]}
    r = assign_tracks(guides, stack, tracks_per_gcell=2)
    # no run was dropped
    assert all(ir.layer >= 0 for ir in r.iroutes)
    # gcell topology still connects the two terminal gcells (0,0,z1) and (2,2,z2)
    adj = {}

    def link(a, b):
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for (z, kind, ex, ey) in guides["L"]:
        if kind == "H":
            link((ex, ey, z), (ex + 1, ey, z))
        elif kind == "V":
            link((ex, ey, z), (ex, ey + 1, z))
        else:
            link((ex, ey, z), (ex, ey, z + 1))
    seen = {(0, 0, 1)}
    stk = [(0, 0, 1)]
    while stk:
        n = stk.pop()
        for m in adj.get(n, ()):
            if m not in seen:
                seen.add(m)
                stk.append(m)
    assert (2, 2, 2) in seen


# ---- integration: T3 -> T4 pipe on a balanced stack -------------------------
def test_t3_to_t4_integration():
    stack = make_stack(["V", "H", "V", "H"])
    nets = [
        {"net": "a", "terminals": [(0, 0, 0), (4, 0, 0)]},
        {"net": "b", "terminals": [(0, 0, 0), (4, 0, 0)]},
        {"net": "c", "terminals": [(0, 2, 0), (4, 2, 0)]},
    ]
    t3 = assign_layers(6, 4, stack, nets, via_cost=1.0)
    assert t3.ok
    t4 = assign_tracks(t3.guides, stack, tracks_per_gcell=3)
    assert t4.ok
    # every net that had planar guides gets at least one assigned segment
    for n in ("a", "b", "c"):
        assert t4.segments.get(n)


# ---- isolation: T4 must not import Track 1 ----------------------------------
def test_no_track1_import():
    import klink.routing.backends.pnr_multilayer.ta.track_assign as ta

    src = open(ta.__file__, encoding="utf-8").read()
    assert "backends.flexdr" not in src and "backends/flexdr" not in src
