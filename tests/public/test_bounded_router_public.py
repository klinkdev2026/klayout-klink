"""PUBLIC test: the bounded negotiated router + cell-box realize -- the path
that routes small digital blocks. Fast/synthetic; no KLayout, no lab data
(profile from synth_pdk)."""
from klink.routing.backends.negotiated.bounded_router import _net_bbox, route_bounded
from klink.routing.grid.capacity_grid import NetInput, build_capacity_grid

from synth_pdk import SYNTH_PROFILE as P

PITCH = P.grid_pitch_um


def _grid(nx=40, ny=40, channels=()):
    return build_capacity_grid(
        layers=P.routing_layers,
        bbox_um=(0.0, 0.0, (nx - 1) * PITCH, (ny - 1) * PITCH),
        pitch_um=PITCH,
        channel_boxes_um=list(channels),
        pad_boxes_by_layer={},
        device_body_boxes_um=[],
        via_rules=P.via_rules(),
        via_footprint_um=P.via_pad_um,
    )


def _net(name, pts, layer="101/0"):
    return NetInput(name, [(x, y, layer) for x, y in pts])


def _route(g, nets):
    return route_bounded(g, nets, width_um=P.wire_width_um,
                         wire_clear_um=P.wire_clear_um, via_clear_um=0.0,
                         max_iters=120, margin_cells=12)


def test_routes_all_nets_with_no_cross_net_cell_overlap():
    g = _grid()
    nets = [_net("A", [(20.0, 20.0), (160.0, 20.0)]),
            _net("B", [(20.0, 40.0), (160.0, 40.0)]),
            _net("C", [(20.0, 60.0), (160.0, 60.0)])]
    r = _route(g, nets)
    assert r.ok, r.problems
    assert set(r.routes) == {"A", "B", "C"}
    owner = {}
    for net, cells in r.routes.items():
        for c in cells:
            assert owner.get(c, net) == net, f"cell {c} shared by {owner.get(c)} and {net}"
            owner[c] = net


def test_net_bbox_contains_terminals_with_margin():
    g = _grid()
    from klink.routing.grid.capacity_grid import _terminal_cellsets
    n = _net("A", [(20.0, 20.0), (160.0, 60.0)])
    bbox = _net_bbox(_terminal_cellsets(g, n), g, 8)
    x0, y0, x1, y1 = bbox
    assert x0 <= 4 and y0 <= 4 and x1 >= 32 and y1 >= 12
