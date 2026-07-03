"""Unit tests for F3 negotiated multilayer routing."""

from klink.routing.grid.feature_grid_3d import ViaSpec
from klink.routing.backends.negotiated.negotiated_grid import NetRouteInput, negotiated_route

VIA = ViaSpec("101/0", "104/0", "via12_cell", (3.0, 3.0), 1.0)


def _no_same_layer_conflict(res, *, width_um, spacing_um):
    """True iff no two DIFFERENT nets' wire segments overlap-or-are-too-
    close on the SAME layer (no short, no spacing violation)."""
    half = width_um / 2.0
    segs = []   # (net, layer, x1, y1, x2, y2) half-width box
    for net, path in res.routes.items():
        for a, b in zip(path, path[1:]):
            if a[2] != b[2]:
                continue
            x1, y1 = min(a[0], b[0]) / 1000 - half, min(a[1], b[1]) / 1000 - half
            x2, y2 = max(a[0], b[0]) / 1000 + half, max(a[1], b[1]) / 1000 + half
            segs.append((net, a[2], x1, y1, x2, y2))
    for i in range(len(segs)):
        ni, li, ax1, ay1, ax2, ay2 = segs[i]
        for j in range(i + 1, len(segs)):
            nj, lj, bx1, by1, bx2, by2 = segs[j]
            if ni == nj or li != lj:
                continue
            # gap < spacing  <=>  inflating one box by spacing overlaps the other
            if (min(ax2 + spacing_um, bx2) > max(ax1 - spacing_um, bx1)
                    and min(ay2 + spacing_um, by2) > max(ay1 - spacing_um, by1)):
                return False
    return True


def _net(name, sx, sy, gx, gy, sl="104/0", gl="104/0", obs=None):
    return NetRouteInput(
        net=name, start_layer=sl, goal_layer=gl,
        start_um=(sx, sy), goal_um=(gx, gy),
        terminals_by_layer={
            "104/0": [{"name": f"{name}.s", "point_um": (sx, sy)},
                      {"name": f"{name}.g", "point_um": (gx, gy)}],
            "101/0": [],
        },
        obstacles_by_layer=obs or {"101/0": [], "104/0": []})


class TestNegotiation:
    def test_two_parallel_nets_each_get_their_own_track(self):
        # two nets want to run along the same corridor; greedy single-
        # track would collide, negotiation spreads them to distinct
        # tracks (no edge overused).
        a = _net("A", 0.0, 0.0, 40.0, 0.0)
        b = _net("B", 0.0, 10.0, 40.0, 10.0)
        res = negotiated_route([a, b], layers=["101/0", "104/0"],
                               vias=[VIA], width_um=5.0, min_spacing_um=2.0)
        assert res.ok, res.problems
        assert res.overused_edges == ()
        assert set(res.routes) == {"A", "B"}

    def test_disconnected_layers_fail_honestly(self):
        # start on 101, goal on 104, NO via declared -> the planes are
        # disconnected -> honest no_path, not a hang.
        a = NetRouteInput(
            net="A", start_layer="101/0", goal_layer="104/0",
            start_um=(0.0, 0.0), goal_um=(40.0, 0.0),
            terminals_by_layer={
                "101/0": [{"name": "A.s", "point_um": (0.0, 0.0)}],
                "104/0": [{"name": "A.g", "point_um": (40.0, 0.0)}]},
            obstacles_by_layer={"101/0": [], "104/0": []})
        res = negotiated_route([a], layers=["101/0", "104/0"], vias=[],
                               width_um=5.0, min_spacing_um=2.0)
        assert not res.ok
        assert res.problems[0]["type"] == "no_path"

    def test_deterministic(self):
        a = _net("A", 0.0, 0.0, 40.0, 0.0)
        b = _net("B", 0.0, 10.0, 40.0, 10.0)
        kw = dict(layers=["101/0", "104/0"], vias=[VIA], width_um=5.0,
                  min_spacing_um=2.0)
        r1 = negotiated_route([a, b], **kw)
        r2 = negotiated_route([a, b], **kw)
        assert r1.ok == r2.ok
        assert {k: list(v) for k, v in r1.routes.items()} == \
               {k: list(v) for k, v in r2.routes.items()}

    def test_routing_spacing_prevents_same_layer_crossing_short(self):
        # the user's ACTUAL bug: a horizontal net and a vertical net both
        # on 104 cross at a point -> their metal merges = a source/drain
        # short. Terminals are far apart (pads don't overlap), so a
        # short-free solution exists: with a routing spacing the
        # negotiation must keep them apart on 104 -> one crosses UNDER on
        # 101 via the declared via (the intended "drop a layer at the
        # crossing" behaviour). Either separation or crossunder is valid;
        # the invariant is no same-layer conflict.
        a = _net("A", 0.0, 0.0, 40.0, 0.0)            # horizontal on 104
        b = _net("B", 20.0, -20.0, 20.0, 20.0)        # vertical on 104
        res = negotiated_route([a, b], layers=["101/0", "104/0"],
                               vias=[VIA], width_um=5.0, min_spacing_um=2.0,
                               routing_spacing_um=10.0, max_iters=14)
        assert res.ok, res.problems
        assert _no_same_layer_conflict(res, width_um=5.0, spacing_um=10.0)

    def test_spacing_is_a_tunable_parameter(self):
        # the limit is a parameter: with NO routing spacing the crossing
        # nets touch on 104 (the short); with spacing the negotiation
        # resolves it (separation or crossunder).
        a = _net("A", 0.0, 0.0, 40.0, 0.0)
        b = _net("B", 20.0, -20.0, 20.0, 20.0)
        loose = negotiated_route([a, b], layers=["101/0", "104/0"],
                                 vias=[VIA], width_um=5.0, min_spacing_um=2.0,
                                 routing_spacing_um=0.0, max_iters=14)
        tight = negotiated_route([a, b], layers=["101/0", "104/0"],
                                 vias=[VIA], width_um=5.0, min_spacing_um=2.0,
                                 routing_spacing_um=10.0, max_iters=14)
        # spacing=0 allows the crossing short; spacing=10 forbids it
        assert not _no_same_layer_conflict(loose, width_um=5.0, spacing_um=10.0)
        assert _no_same_layer_conflict(tight, width_um=5.0, spacing_um=10.0)

    def test_single_net_unconstrained_routes_first_iter(self):
        res = negotiated_route([_net("A", 0.0, 0.0, 30.0, 0.0)],
                               layers=["101/0", "104/0"], vias=[VIA],
                               width_um=5.0, min_spacing_um=2.0)
        assert res.ok
        assert res.iterations == 1
