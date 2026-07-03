"""Optical-net keep-out = the device's WHOLE body (not just its route-layer
footprint), with a port-entry NOTCH for a net's OWN endpoint device instead
of the old blanket own-device exemption.

Locks the bug this fixes: a photonic module's arm_in net ran a horizontal
approach segment that half-cut into a vertically-placed phase shifter's
FULL (all-layer) footprint before turning into its own south port. The old
checker missed this two ways: (1) it only compared a route against device
footprints ON ITS OWN ROUTE LAYER, and (2) a net's OWN endpoint device was
exempted from the obstacle list ENTIRELY (not just at its port).

The new contract: for an optical net,
  * a FOREIGN device contributes its full (all-layer) bbox UNCHANGED --
    even if that device has no drawn geometry on the route's own layer;
  * the net's OWN endpoint device(s) contribute their full bbox with a
    small entry NOTCH cut at the port face (`subtract_port_notch`); routing
    through the notch is fine, grazing the rest of the body is still a hit.
Electrical nets are UNCHANGED (per-layer footprint, own devices fully
exempt) -- one test below locks that explicitly.

Pure offline: no client, no gdsfactory. Tests call `_optical_keepouts` and
`_route_device_hits` directly with hand-built bbox/mark dicts.
"""
from __future__ import annotations

from klink.domains.photonics.net_intent import _optical_keepouts, _route_device_hits


# A TOPS-like device: full-layer body x:70..145, y:-110..190 (vertically
# placed phase shifter), with a south port at (80, -110) facing south.
TOPS_BBOX = [70.0, -110.0, 145.0, 190.0]
SOUTH_PORT = {"name": "TOPS0_0", "center_um": [80.0, -110.0],
             "orientation": 270.0, "width_um": 0.5, "port_type": "optical"}
FAR_PORT = {"name": "GC0_0", "center_um": [-200.0, -110.0],
           "orientation": 0.0, "width_um": 0.5, "port_type": "optical"}


def _full_entries(*, own_owner="TOPS0", own_bbox=TOPS_BBOX,
                  foreign_owner="GC0", foreign_bbox=None):
    entries = [{"owner": own_owner, "bbox": list(own_bbox)}]
    if foreign_bbox is not None:
        entries.append({"owner": foreign_owner, "bbox": list(foreign_bbox)})
    return entries


# --------------------------------------------------------------------
# _optical_keepouts: geometry-level notch behavior
# --------------------------------------------------------------------

def test_optical_keepouts_notches_own_device_at_its_port():
    entries = _full_entries()
    obstacles = _optical_keepouts(
        entries, {"TOPS0"}, SOUTH_PORT, FAR_PORT,
        notch_halfwidth=1.0, notch_depth=0.5)

    def _covers(x, y):
        return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in obstacles)

    assert not _covers(80.0, -109.9)   # inside the notch mouth: open
    assert _covers(85.0, -109.9)       # same depth, OUTSIDE the notch: blocked
    assert _covers(75.0, -105.0)       # elsewhere on the body: still blocked


def test_optical_keepouts_foreign_device_kept_whole():
    foreign_bbox = [200.0, 200.0, 210.0, 210.0]
    entries = _full_entries(foreign_bbox=foreign_bbox)
    obstacles = _optical_keepouts(
        entries, {"TOPS0"}, SOUTH_PORT, FAR_PORT,
        notch_halfwidth=1.0, notch_depth=0.5)
    assert foreign_bbox in obstacles


# --------------------------------------------------------------------
# _route_device_hits: the checker used both by phase-2 detection and the
# collective post-route verdict.
# --------------------------------------------------------------------

def _entry_and_marks():
    entry_by_net = {"n_arm_in": {"a": "GC0_0", "b": "TOPS0_0"}}
    mark_by_name = {"GC0_0": FAR_PORT, "TOPS0_0": SOUTH_PORT}
    return entry_by_net, mark_by_name


def test_own_device_edge_crawl_outside_the_notch_is_a_hit():
    """(1) A route grazing its OWN endpoint device's body away from the
    port face -- the bug scenario -- must now be caught."""
    entry_by_net, mark_by_name = _entry_and_marks()
    device_bboxes = {"1002/0": [], "__full__": _full_entries()}
    route = {"route_id": "n_arm_in", "net": "n_arm_in", "layer": "1002/0",
             "width_um": 0.5, "points_um": [[100.0, -105.0], [120.0, -105.0]],
             "source": "GC0_0", "target": "TOPS0_0"}
    hits = _route_device_hits([route], device_bboxes,
                              entry_by_net=entry_by_net, mark_by_name=mark_by_name)
    assert hits


def test_own_device_notch_approach_is_not_a_hit():
    """(2) The same net's real approach -- straight into its own port
    through the notch, terminating exactly on the port (the boundary,
    matching how klink actually draws a detour: the last point is always
    `mark["center_um"]`, never past it) -- is clean."""
    entry_by_net, mark_by_name = _entry_and_marks()
    device_bboxes = {"1002/0": [], "__full__": _full_entries()}
    route = {"route_id": "n_arm_in", "net": "n_arm_in", "layer": "1002/0",
             "width_um": 0.5, "points_um": [[80.0, -115.0], [80.0, -110.0]],
             "source": "GC0_0", "target": "TOPS0_0"}
    hits = _route_device_hits([route], device_bboxes,
                              entry_by_net=entry_by_net, mark_by_name=mark_by_name)
    assert not hits


def test_own_device_parallel_run_along_true_edge_outside_notch_is_a_hit():
    """The exact bug shape: a route approaches level with its own
    port's y-coordinate, which is ALSO the device's true bbox edge, and
    runs along that edge for a long stretch OUTSIDE the notch mouth before
    turning in. The old blanket own-device exemption missed this; the flat
    -0.1um flush-boundary tolerance (right for a route ending AT a point)
    must not mask it either, since it never actually reaches the port."""
    entry_by_net, mark_by_name = _entry_and_marks()
    device_bboxes = {"1002/0": [], "__full__": _full_entries()}
    # Runs at y=-110.0 -- EXACTLY TOPS's true south edge -- from x=-260 to
    # x=100 (crossing x=70..100, well outside the notch mouth centered on
    # the port at x=80 with halfwidth 0.55), then turns away.
    route = {"route_id": "n_arm_in", "net": "n_arm_in", "layer": "1002/0",
             "width_um": 0.5,
             "points_um": [[-260.0, -110.0], [100.0, -110.0],
                          [100.0, -130.0], [120.0, -130.0], [120.0, -110.0]],
             "source": "GC0_0", "target": "TOPS0_0"}
    hits = _route_device_hits([route], device_bboxes,
                              entry_by_net=entry_by_net, mark_by_name=mark_by_name)
    assert hits


def test_foreign_full_body_blocks_even_with_no_route_layer_geometry():
    """(3) A FOREIGN device with no shapes on the route's own layer (the
    per-layer bucket for it is empty) still blocks via its full-layer body
    -- this is the whole point of the layer-independent optical keep-out."""
    entry_by_net, mark_by_name = _entry_and_marks()
    foreign_bbox = [40.0, -20.0, 60.0, 20.0]
    device_bboxes = {
        "1002/0": [],  # no per-layer geometry registered for this layer
        "__full__": _full_entries(foreign_bbox=foreign_bbox),
    }
    route = {"route_id": "n_arm_in", "net": "n_arm_in", "layer": "1002/0",
             "width_um": 0.5, "points_um": [[30.0, 0.0], [70.0, 0.0]],
             "source": "GC0_0", "target": "TOPS0_0"}
    hits = _route_device_hits([route], device_bboxes,
                              entry_by_net=entry_by_net, mark_by_name=mark_by_name)
    assert hits


def test_electrical_net_behavior_is_unchanged():
    """(4) Electrical nets keep the OLD per-layer + own-exemption behavior:
    a device body that only exists in `__full__` (e.g. a much larger
    all-layer envelope) must NOT block an electrical route -- only a
    per-layer entry on the route's OWN layer can."""
    electrical_a = {"name": "PADA_e1", "center_um": [-200.0, -110.0],
                    "orientation": 0.0, "width_um": 2.0, "port_type": "electrical"}
    electrical_b = {"name": "PADB_e1", "center_um": [200.0, -110.0],
                    "orientation": 180.0, "width_um": 2.0, "port_type": "electrical"}
    entry_by_net = {"n_metal": {"a": "PADA_e1", "b": "PADB_e1"}}
    mark_by_name = {"PADA_e1": electrical_a, "PADB_e1": electrical_b}
    device_bboxes = {
        "49/0": [],                        # no metal footprint here
        "__full__": _full_entries(),       # TOPS's full body spans the path
    }
    route = {"route_id": "n_metal", "net": "n_metal", "layer": "49/0",
             "width_um": 2.0, "points_um": [[-200.0, -110.0], [200.0, -110.0]],
             "source": "PADA_e1", "target": "PADB_e1"}
    hits = _route_device_hits([route], device_bboxes,
                              entry_by_net=entry_by_net, mark_by_name=mark_by_name)
    assert not hits
