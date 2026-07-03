"""Stage T2 gate -- FlexPA access points (OpenROAD FlexPA_acc_point port).

Proves the scoped FlexPA fidelity (docs/TRACK2_T2_FLEXPA_MAPPING.md §4): on-track AP
generation + cost tiers + center fallback + access directions, and the V1-resolved
faithful injection (pref-axis only + ap_locs). These gates prove FlexPA fidelity, NOT
a pipeline pass -- the only "pass" is live KLayout LVS at T5.

Fixture = the T1 toy stack (V/H/V, 2000 nm pitch). Track lattices:
  x_tracks (vertical-layer x lines) = {0,2000,4000,6000}
  y_tracks (horizontal-layer y lines) = {0,2000,4000}
"""

from klink.routing.backends.pnr_multilayer.grid.track_grid import build
from klink.routing.backends.pnr_multilayer.pa.flexpa import (
    CENTER,
    ONGRID,
    AccessPoint,
    gen_access_points,
)
from klink.routing.grid.process_profile import ProcessProfile

X_TRACKS = [0, 2000, 4000, 6000]
Y_TRACKS = [0, 2000, 4000]


def _profile():
    return ProcessProfile(
        routing_layers=("10/0", "11/0", "12/0"),
        gate_layer="10/0",
        sd_layer="11/0",
        channel_layer="9/0",
        vias=(("10/0", "100/0", "11/0"), ("11/0", "101/0", "12/0")),
        layer_directions={"10/0": "V", "11/0": "H", "12/0": "V"},
        wire_width_um=2.0,
        wire_clear_um=0.0,
        via_pad_um=5.0, litho_tol_um=1.0, y_step_um=30.0, col_pitch_um=100.0,
    )


# ---- Gate 1: AP positions / count / cost tiers on a wide pin (all OnGrid) ---
def test_gate1_ap_positions_and_tiers():
    p = _profile()
    # pin rect on the H layer (11/0) spanning >=3 tracks on both axes
    aps = gen_access_points(
        (0, 0, 6000, 4000), "11/0", p, x_tracks=X_TRACKS, y_tracks=Y_TRACKS
    )
    pts = {(a.x, a.y) for a in aps}
    expected = {(x, y) for x in X_TRACKS for y in Y_TRACKS}   # 4 x 3 cross product
    assert pts == expected
    assert len(aps) == 12
    # wide pin -> >=3 on-grid on each axis -> NO center fallback, all OnGrid
    assert all(a.lower_type == ONGRID and a.upper_type == ONGRID for a in aps)


# ---- Gate 2: access directions (pref planar + via, no wrongway) -------------
def test_gate2_access_directions():
    p = _profile()
    # H layer pin: pref planar {E,W}, via {U,D}, NO wrongway {N,S}
    ah = gen_access_points((0, 0, 6000, 4000), "11/0", p,
                           x_tracks=X_TRACKS, y_tracks=Y_TRACKS)[0]
    assert ah.access == frozenset({"E", "W", "U", "D"})
    assert ah.has_access("U") and not ah.has_access("N")

    # V layer pin: pref planar {N,S}, via {U,D}
    av = gen_access_points((0, 0, 6000, 4000), "10/0", p,
                           x_tracks=X_TRACKS, y_tracks=Y_TRACKS)[0]
    assert av.access == frozenset({"N", "S", "U", "D"})

    # wrongway opens up only with use_nonpref
    aw = gen_access_points((0, 0, 6000, 4000), "11/0", p, x_tracks=X_TRACKS,
                           y_tracks=Y_TRACKS, use_nonpref=True)[0]
    assert {"N", "S"} <= aw.access


# ---- Gate 3: every pin yields >=1 AP with via access ------------------------
def test_gate3_reachable_ap_per_pin():
    p = _profile()
    for layer in ("10/0", "11/0", "12/0"):
        aps = gen_access_points((0, 0, 6000, 4000), layer, p,
                                x_tracks=X_TRACKS, y_tracks=Y_TRACKS)
        assert aps
        assert any(a.allow_via and a.has_access("U") for a in aps)


# ---- Gate 4: center fallback when <3 on-grid on an axis ---------------------
def test_gate4_center_fallback():
    p = _profile()
    # thin pin on H layer: x in [0,1000] hits only x-track {0} (1 on-grid < 3)
    # -> a Center coord at the manufacturing-grid-snapped midpoint (500) appears
    aps = gen_access_points((0, 0, 1000, 4000), "11/0", p,
                            x_tracks=X_TRACKS, y_tracks=Y_TRACKS, mfg_grid_nm=5)
    xs = {a.x for a in aps}
    assert 0 in xs and 500 in xs                       # on-grid + center
    centered = [a for a in aps if a.x == 500]
    assert centered and all(a.upper_type == CENTER for a in centered)  # via-up axis = center


# ---- Gate 5: V1 -- faithful injection adds pref-axis coord only + ap_locs ---
def test_gate5_faithful_injection():
    p = _profile()
    aps = gen_access_points((0, 0, 6000, 4000), "11/0", p,
                            x_tracks=X_TRACKS, y_tracks=Y_TRACKS)
    g = build(p, (0, 0, 6000, 4000), access_points=[a.as_coord() for a in aps])

    # all APs are ON-TRACK -> the union is unchanged (no scattered off-track lines)
    assert g.xCoords == [0, 2000, 4000, 6000]
    assert g.yCoords == [0, 2000, 4000]
    # node addressing is still a clean bijection with AP coords present (T1 gate 2)
    seen = {g.get_idx(xi, yi, zi)
            for zi in range(g.nz) for yi in range(g.ny) for xi in range(g.nx)}
    assert seen == set(range(g.capacity))
    # every AP point is registered in ap_locs on the H layer (index 1)
    for a in aps:
        assert g.is_access_point_location(1, a.x, a.y)

    # an AP on the H layer must NOT have injected its x as a new line; prove by a
    # case where the AP x is off the V-track lattice -> it stays OUT of xCoords,
    # while its pref-axis y is injected.
    g2 = build(p, (0, 0, 6000, 4000), access_points=[("11/0", 1234, 1000)])
    assert 1234 not in g2.xCoords          # cross-axis x NOT injected (C1)
    assert 1000 in g2.yCoords              # pref-axis y injected
    assert g2.is_access_point_location(1, 1234, 1000)


# ---- isolation: T2 must not import Track 1 ----------------------------------
def test_no_track1_import():
    import klink.routing.backends.pnr_multilayer.pa.flexpa as fp

    src = open(fp.__file__, encoding="utf-8").read()
    assert "backends.flexdr" not in src and "backends/flexdr" not in src
