"""Bridge: drive the Rust TrackGrid maze kernel (klink_trackmaze_rs, SEPARATE from
the frozen single-stack engine's klink_boxmaze) from the Python TrackMaze. Ports
ONLY route_all's initial per-net routing (the A* hot path); consume_seed + the
checkerboard overlap loop + route_box stay in Python. The Python path is the
untouched fallback -- this must be byte-parity (or at least LVS-clean) against
it before any semantic change.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence


def available() -> bool:
    try:
        import klink_trackmaze_rs  # noqa: F401
        return True
    except Exception:
        return False


def _pack(n) -> int:                       # (xi,yi,zi) -> i64  xi|yi<<16|zi<<32
    return (n[0] & 0xFFFF) | ((n[1] & 0xFFFF) << 16) | (n[2] << 32)


def _unpack(v: int):
    return (v & 0xFFFF, (v >> 16) & 0xFFFF, v >> 32)


def _xy(x: int, y: int) -> int:            # (xi,yi) -> i64  xi|yi<<16
    return (x & 0xFFFF) | ((y & 0xFFFF) << 16)


def rust_initial_route(maze, terminals: Mapping[str, Sequence],
                       seed: Optional[Mapping[str, Sequence]] = None,
                       seed_edges=None) -> None:
    """Run the Rust per-net initial routing on ``maze`` IN PLACE (same effect as the
    Python loop in route_all lines 471-484): consume seed, route each net's pins to its
    seed tree, add_route the result, set maze.unrouted. Leaves the checkerboard loop to
    the caller (Python)."""
    import klink_trackmaze_rs as rs

    g = maze.g
    if seed:
        maze.consume_seed(seed, seed_edges)

    ids: dict = {}

    def nid(name) -> int:
        i = ids.get(name)
        if i is None:
            i = len(ids)
            ids[name] = i
        return i

    net_names = sorted(terminals)
    for nm in net_names:                   # routing nets get the low ids, in sorted order
        nid(nm)

    pad = [(g.get_idx(xi, yi, zi), nid(owner))
           for (zi, xi, yi), owner in maze._pad_owner.items()]

    seed_occ = [(g.get_idx(xi, yi, zi), nid(nm))
                for nm, nodes in maze.routes.items() for (xi, yi, zi) in nodes]

    nets = []
    for nm in net_names:
        existing = maze.routes.get(nm, [])
        nets.append((
            nid(nm),
            [_pack(n) for n in terminals[nm]],
            [_xy(x, y) for (x, y) in maze._portals.get(nm, ())],
            [_xy(x, y) for (x, y) in maze._window.get(nm, ())],
            [_pack(n) for n in existing],
        ))

    sig = maze.planar_layers
    via_lo = sorted({min(a, b) for (a, b) in g.via_z_pairs})
    N = g.nodes
    import os as _os, time as _t
    _timed = _os.environ.get("TG_RUST_TIME") == "1"
    _t0 = _t.time()
    res, expanded, exp_init, ovlp_passes_run, born_overlaps = rs.route_all(
        g._nx, g._ny, g._nz, list(g._zh), via_lo,
        bytes(N["edge_E"]), bytes(N["edge_N"]), bytes(N["blocked_E"]), bytes(N["blocked_N"]),
        bytes(N["fsc_planar_h"]), bytes(N["fsc_planar_v"]), bytes(N["fsc_via"]), bytes(N["mc_planar"]),
        pad,
        float(maze.marker_weight), float(maze.occ_penalty), float(maze.via_cost),
        float(maze.jog_cost), int(maze.spacing_halo),
        list(sig) if sig is not None else [],
        list(maze.no_jog_layers),
        nets, seed_occ,
        int(getattr(maze, "rust_ovlp_passes", 0)),   # 0 = initial only (byte-parity)
        # T3 guide corridors: (net_id, [packed gcell gx|gy<<32]); empty = unbounded
        ([(nid(nm), [(gx & 0xFFFFFFFF) | (gy << 32) for (gx, gy) in corr])
          for nm, corr in maze.corridors.items() if nm in ids] if maze.corridors else []),
        int(getattr(maze, "gcell", 1) or 1),
        float(_os.environ.get("TG_TRACKBAL", 0.0)),
    )
    if _timed:
        print(f"  RUST route_all wall={_t.time() - _t0:.2f}s expanded={expanded} "
              f"(initial={exp_init} overlap={expanded - exp_init} in {ovlp_passes_run} passes) "
              f"born_overlaps={born_overlaps} nets={len(nets)}", flush=True)

    id2name = {i: nm for nm, i in ids.items()}
    unrouted = []
    for (net_id, nodes_packed, edges_packed) in res:
        nm = id2name[net_id]
        if not nodes_packed:
            if not maze.routes.get(nm):
                unrouted.append(nm)
            continue
        nodes = [_unpack(v) for v in nodes_packed]
        edges = [(_unpack(a), _unpack(b)) for (a, b) in edges_packed]
        maze.add_route(nm, nodes, edges)
    maze.unrouted = unrouted
    maze.expanded += expanded
