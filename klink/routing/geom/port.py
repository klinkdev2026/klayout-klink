"""
Port matching and sorting strategies (client-side).

All functions operate on port dicts as returned by the `port.list` RPC.
Design principle: the KLayout plugin only does mechanical geometry ops;
all "smart" logic — net assignment, pairing, sorting — lives here.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def _dist(p1, p2) -> float:
    """Euclidean distance between two port centres (in microns)."""
    c1 = p1.get("center_um", [0, 0])
    c2 = p2.get("center_um", [0, 0])
    return math.sqrt((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2)


def _cost_matrix(ports_a: list, ports_b: list) -> list:
    """Build a |A| x |B| distance matrix."""
    return [[_dist(a, b) for b in ports_b] for a in ports_a]


# ---------------------------------------------------------------------------
# Hungarian algorithm (linear_sum_assignment) — self-contained fallback
# ---------------------------------------------------------------------------

def _hungarian(cost: list) -> List[tuple]:
    """Simple Hungarian implementation for small port counts.

    Tries scipy.optimize.linear_sum_assignment first; falls back to a
    greedy row-by-row minimum assignment that works for most practical
    port arrays.
    """
    try:
        from scipy.optimize import linear_sum_assignment
        import numpy as np
        arr = np.array(cost, dtype=float)
        ri, ci = linear_sum_assignment(arr)
        return list(zip((int(r) for r in ri), (int(c) for c in ci)))
    except ImportError:
        pass

    # Greedy fallback: assign each row to its nearest unassigned column.
    n = len(cost)
    m = len(cost[0]) if cost else 0
    if n == 0 or m == 0:
        return []

    assigned_cols: set = set()
    pairs: list = []
    rows = sorted(range(n), key=lambda r: min(cost[r]))
    for r in rows:
        best_c = -1
        best_v = float("inf")
        for c in range(m):
            if c not in assigned_cols and cost[r][c] < best_v:
                best_v = cost[r][c]
                best_c = c
        if best_c >= 0:
            pairs.append((r, best_c))
            assigned_cols.add(best_c)
    return pairs


# ---------------------------------------------------------------------------
# Matching strategies
# ---------------------------------------------------------------------------

def match_ports(
    ports_a: list,
    ports_b: list,
    strategy: str = "distance",
) -> List[tuple]:
    """
    Match ports from group A to group B using one of four strategies.

    Parameters
    ----------
    ports_a, ports_b : lists of port dicts
        Each port dict must have at least: name, center_um, net.
    strategy : str
        "name"     — match by port name (name_a == name_b).
        "distance" — Hungarian algorithm minimising total centre distance.
        "clockwise"— sort both clockwise around their centroid, then zip.
        "net"      — match by net field (net_a == net_b). Supports 1:N.

    Returns
    -------
    List of (port_a_dict, port_b_dict) pairs.
    """
    strat = strategy.lower()

    if strat == "name":
        return _match_by_name(ports_a, ports_b)
    elif strat == "distance":
        return _match_by_distance(ports_a, ports_b)
    elif strat == "clockwise":
        return _match_by_clockwise(ports_a, ports_b)
    elif strat == "net":
        return _match_by_net(ports_a, ports_b)
    else:
        raise ValueError(
            "unknown matching strategy: %r (use name, distance, clockwise, net)"
            % strategy
        )


def _match_by_name(ports_a: list, ports_b: list) -> List[tuple]:
    """Match ports with identical name fields."""
    b_by_name: Dict[str, dict] = {p.get("name", ""): p for p in ports_b}
    pairs: list = []
    for pa in ports_a:
        pb = b_by_name.get(pa.get("name", ""))
        if pb is not None:
            pairs.append((pa, pb))
    return pairs


def _match_by_distance(ports_a: list, ports_b: list) -> List[tuple]:
    """Hungarian minimum-total-distance matching."""
    if not ports_a or not ports_b:
        return []
    cost = _cost_matrix(ports_a, ports_b)
    idx_pairs = _hungarian(cost)
    return [(ports_a[i], ports_b[j]) for i, j in idx_pairs
            if i < len(ports_a) and j < len(ports_b)]


def _match_by_clockwise(ports_a: list, ports_b: list) -> List[tuple]:
    """Sort both lists clockwise, then zip 1:1."""
    sorted_a = sort_ports_clockwise(ports_a)
    sorted_b = sort_ports_clockwise(ports_b)
    n = min(len(sorted_a), len(sorted_b))
    return list(zip(sorted_a[:n], sorted_b[:n]))


def _match_by_net(ports_a: list, ports_b: list) -> List[tuple]:
    """Match ports by net field. Each port in A with a non-empty net
    is paired with ALL ports in B with the same net (1:N)."""
    b_by_net: Dict[str, list] = {}
    for pb in ports_b:
        net = pb.get("net", "")
        if net:
            b_by_net.setdefault(net, []).append(pb)

    pairs: list = []
    for pa in ports_a:
        net = pa.get("net", "")
        if net and net in b_by_net:
            for pb in b_by_net[net]:
                pairs.append((pa, pb))
    return pairs


# ---------------------------------------------------------------------------
# Clockwise sort
# ---------------------------------------------------------------------------

def sort_ports_clockwise(ports: list) -> list:
    """Sort ports clockwise around their centroid.

    The first port is the one nearest to another port (a proxy for
    finding a sensible start point in a ring of ports). The rest are
    ordered by clockwise angle from the centroid.
    """
    if len(ports) <= 2:
        return list(ports)

    xs = [p["center_um"][0] for p in ports]
    ys = [p["center_um"][1] for p in ports]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)

    def cw_angle(p):
        dx = p["center_um"][0] - cx
        dy = p["center_um"][1] - cy
        return (180.0 - math.degrees(math.atan2(dy, dx))) % 360.0

    return sorted(ports, key=cw_angle)


# ---------------------------------------------------------------------------
# gdsfactory gf.Port conversion (optional — no hard dependency)
# ---------------------------------------------------------------------------

def port_to_gf(port_dict: dict):
    """Convert a klink port dict to a gdsfactory gf.Port.

    Requires gdsfactory to be installed. Returns None if not available.
    The port_dict should have: name, center_um, orientation, width_um,
    target_layer.
    """
    try:
        import gdsfactory as gf
    except ImportError:
        return None

    center = port_dict.get("center_um", [0, 0])
    orient = float(port_dict.get("orientation", 0.0))
    width = float(port_dict.get("width_um", 5.0))

    # Parse target_layer "L/D" → (layer, datatype)
    tl = port_dict.get("target_layer", "1/0")
    try:
        parts = tl.split("/")
        layer = (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        layer = (1, 0)

    try:
        layer_info = gf.kdb.LayerInfo(int(layer[0]), int(layer[1]))
    except Exception:
        layer_info = None

    kwargs = {
        "name": port_dict.get("name", "P0"),
        "center": tuple(center),
        "orientation": orient,
        "width": width,
        "port_type": port_dict.get("port_type", "optical"),
    }
    if layer_info is not None:
        kwargs["layer_info"] = layer_info
    else:
        # kfactory wants an INTEGER layer index, never a (layer, datatype)
        # tuple -- passing a tuple raises deep inside Layout.get_info.
        kwargs["layer"] = layer[0]
    port = gf.Port(**kwargs)

    # Version-proof the POSITION. `gf.Port(center=...)` is interpreted in the
    # port's ACTIVE unit, and that contract is NOT stable across gdsfactory /
    # kfactory versions: on some it is um, on others `center` is raw dbu while
    # `dcenter` is the um value. Passing a um coordinate into a version where
    # `center` means dbu is the classic 1000x-off bug (routes/ports collapse to
    # a thousandth of their size while device bodies stay put -- "half tiny,
    # half huge"). `dcenter` is the um accessor on every kfactory version, so
    # assign it explicitly. On versions where `center` was already um this is a
    # no-op (byte-identical), so nothing that currently passes regresses.
    try:
        port.dcenter = (float(center[0]), float(center[1]))
    except Exception:
        pass
    return port


def ports_to_gf(port_dicts: list) -> list:
    """Convert a list of klink port dicts to gf.Port objects.

    Ports that fail conversion are silently skipped.
    """
    result = []
    for p in port_dicts:
        gf_port = port_to_gf(p)
        if gf_port is not None:
            result.append(gf_port)
    return result
