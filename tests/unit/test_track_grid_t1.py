"""Stage T1 gate -- faithful TrackGrid (OpenROAD FlexGridGraph port).

Proves the data-model fidelity the handoff requires before any maze/FlexPA/3D-GR
work (docs/TRACK2_T1_GRID_MAPPING.md, gates 1-5). Gate 6 (Track-1 byte-parity
oracle) + the unit suite are run offline, not here.

The fixture is hand-checkable: a 3-layer stack (V,H,V), one global 2000 nm pitch,
bbox [0,6000]x[0,4000] nm, plus one ON-TRACK access point at (4000,1000) on the H
layer. Faithful injection (T2 / V1) adds ONLY the pref-axis coord: an AP on a
horizontal layer contributes its y (=1000) to yCoords; its x (=4000) is already a
vertical-layer track, so it adds no new x line. (docs/TRACK2_T2_FLEXPA_MAPPING.md §0)
"""

from collections import deque

import pytest

from klink.routing.backends.pnr_multilayer.grid.track_grid import (
    DIR_E,
    DIR_N,
    TrackGrid,
    add_to_byte,
    build,
    sub_from_byte,
)
from klink.routing.grid.process_profile import ProcessProfile


def _profile():
    # generic toy process: V/H/V, pitch = (2.0+0.0) um = 2000 nm
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


def _grid():
    return build(
        _profile(),
        bbox_nm=(0, 0, 6000, 4000),
        access_points=[("11/0", 4000, 1000)],   # on-track (x=4000 is a V-track)
    )


# ---- Gate 1: coord union == hand-computed sorted-unique union ---------------
def test_gate1_coord_union():
    g = _grid()
    # V layers (10,12) -> x lattice {0,2000,4000,6000}; AP adds NO new x (on-track)
    assert g.xCoords == [0, 2000, 4000, 6000]
    # H layer (11) -> y lattice {0,2000,4000}; AP injects pref-axis y=1000
    assert g.yCoords == [0, 1000, 2000, 4000]
    assert g.zCoords == [0, 1, 2]
    assert g.z_dir == ["V", "H", "V"]
    assert g.capacity == 4 * 4 * 3 == 48
    # AP registered in ap_locs on its layer (11/0 -> index 1)
    assert g.is_access_point_location(1, 4000, 1000) is True


# ---- Gate 2: get_idx flip + bijection over [0, capacity) -------------------
def test_gate2_getidx_flip_and_bijection():
    g = _grid()
    X, Y = g.nx, g.ny            # 4, 4
    plane = X * Y               # 16

    # z=0 is VERTICAL -> column-major: idx = yi + xi*Y
    assert g.get_idx(0, 0, 0) == 0
    assert g.get_idx(0, 1, 0) == 1          # +N is unit-stride on a V layer
    assert g.get_idx(1, 0, 0) == Y          # +E jumps a column (Y)
    # z=1 is HORIZONTAL -> row-major: idx = xi + yi*X + plane
    assert g.get_idx(0, 0, 1) == plane
    assert g.get_idx(1, 0, 1) == plane + 1  # +E is unit-stride on an H layer
    assert g.get_idx(0, 1, 1) == plane + X
    # z=2 is VERTICAL again, plane offset 2
    assert g.get_idx(1, 0, 2) == 2 * plane + Y

    # bijection: every (xi,yi,zi) maps to a distinct index covering [0, capacity)
    seen = {
        g.get_idx(xi, yi, zi)
        for zi in range(g.nz)
        for yi in range(g.ny)
        for xi in range(g.nx)
    }
    assert seen == set(range(g.capacity))


# ---- Gate 3: binary-search round-trip + on/off-track membership ------------
def test_gate3_bisect_roundtrip():
    g = _grid()
    for i, x in enumerate(g.xCoords):
        assert g.maze_x(x) == i
    for j, y in enumerate(g.yCoords):
        assert g.maze_y(y) == j
    # on-track point exists; an off-lattice point does not
    assert g.has_idx(4000, 1000, 1) is True   # the AP stop (x on a V-track, y injected)
    assert g.has_x(1000) is False             # not a track / AP x
    assert g.has_y(3000) is False
    # idx_box maps a rect to an inclusive maze-index box
    xi1, yi1, xi2, yi2 = g.idx_box(2000, 0, 4000, 2000)
    assert (xi1, xi2) == (1, 2)               # x in {2000,4000}
    assert (yi1, yi2) == (0, 2)               # y in {0,1000,2000}


# ---- Gate 4: node packing -- zero init, saturate, floor, decay ------------
def test_gate4_node_packing():
    g = _grid()
    # fresh grid is all zero
    assert all(b == 0 for arr in g.nodes.values() for b in arr)
    assert not g.is_blocked(0, 0, 0, DIR_E)

    idx = g.get_idx(2, 1, 1)
    # marker cost adds 10/hit and saturates at 255 (26*10 = 260 -> 255)
    for _ in range(26):
        g.add_marker_planar(idx)
    assert g.nodes["mc_planar"][idx] == 255
    # decay (xd) floors at 0 and reports reaching 0
    assert g.decay_marker_planar(idx, 0.5) is False
    assert g.nodes["mc_planar"][idx] == 127
    assert g.decay_marker_planar(idx, 0.0) is True
    assert g.nodes["mc_planar"][idx] == 0
    # route-shape sub floors at 0 (no underflow / wrap)
    g.sub_route_shape_planar(idx)
    assert g.nodes["rsc_planar"][idx] == 0
    # the byte helpers themselves
    assert add_to_byte(250, 10) == 255
    assert sub_from_byte(3, 10) == 0

    # blocked flag is per-direction and survives the get/set round-trip
    g.set_blocked(1, 1, 0, DIR_N, True)
    assert g.is_blocked(1, 1, 0, DIR_N) is True
    assert g.is_blocked(1, 1, 0, DIR_E) is False


# ---- Gate 5: minimal A*/BFS reimpl routes one net on the new grid ----------
def _neighbors(g: TrackGrid, n):
    """Faithful forward/reverse edge traversal (FlexGridGraph correct/reverse):
    an edge is passable iff hasEdge in the forward dir AND not blocked there."""
    xi, yi, zi = n
    out = []
    # E / W (planar, stored forward on the lower-x node)
    if xi + 1 < g.nx and g.has_edge(xi, yi, zi, DIR_E) and not g.is_blocked(xi, yi, zi, DIR_E):
        out.append((xi + 1, yi, zi))
    if xi - 1 >= 0 and g.has_edge(xi - 1, yi, zi, DIR_E) and not g.is_blocked(xi - 1, yi, zi, DIR_E):
        out.append((xi - 1, yi, zi))
    # N / S (forward on the lower-y node)
    if yi + 1 < g.ny and g.has_edge(xi, yi, zi, DIR_N) and not g.is_blocked(xi, yi, zi, DIR_N):
        out.append((xi, yi + 1, zi))
    if yi - 1 >= 0 and g.has_edge(xi, yi - 1, zi, DIR_N) and not g.is_blocked(xi, yi - 1, zi, DIR_N):
        out.append((xi, yi - 1, zi))
    # U / D (via, forward on the lower-z node)
    if zi + 1 < g.nz and g.has_edge(xi, yi, zi, "U") and not g.is_blocked(xi, yi, zi, "U"):
        out.append((xi, yi, zi + 1))
    if zi - 1 >= 0 and g.has_edge(xi, yi, zi - 1, "U") and not g.is_blocked(xi, yi, zi - 1, "U"):
        out.append((xi, yi, zi - 1))
    return out


def _bfs(g, src, dst):
    seen = {src}
    came = {}
    q = deque([src])
    while q:
        n = q.popleft()
        if n == dst:
            path = [n]
            while path[-1] in came:
                path.append(came[path[-1]])
            return path[::-1]
        for m in _neighbors(g, n):
            if m not in seen:
                seen.add(m)
                came[m] = n
                q.append(m)
    return None


def test_gate5_routability_smoke_and_blocked_detour():
    g = _grid()
    g.init_full_edges()
    src, dst = (0, 0, 1), (3, 0, 1)        # along y=0 on the H layer (xi 0..3)

    path = _bfs(g, src, dst)
    assert path is not None and path[0] == src and path[-1] == dst
    # every hop is a unit step in exactly one axis
    for a, b in zip(path, path[1:]):
        d = [abs(a[i] - b[i]) for i in range(3)]
        assert sum(d) == 1

    # cut the (1,0)->(2,0) edge on the H layer: the router must detour, never
    # using the blocked edge, and still connect (proves blocked semantics bite)
    g.set_blocked(1, 0, 1, DIR_E, True)
    path2 = _bfs(g, src, dst)
    assert path2 is not None and path2[-1] == dst
    assert ((1, 0, 1), (2, 0, 1)) not in list(zip(path2, path2[1:]))


# ---- isolation: Track 2 grid must not import Track 1 -----------------------
def test_no_track1_import():
    import klink.routing.backends.pnr_multilayer.grid.track_grid as tg

    src = open(tg.__file__, encoding="utf-8").read()
    assert "backends.flexdr" not in src
    assert "backends/flexdr" not in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
