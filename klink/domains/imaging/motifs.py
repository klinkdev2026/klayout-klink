"""Material structural-motif library — procedural atomic lattices for
device figures (graphene honeycomb, 2H-MoS2 sandwich; extendable to
other TMDs / hBN / CIF-driven structures).

Figure-scale honesty: at device scale (um) a real lattice is ~1e7
atoms; figures draw the lattice ENLARGED.  ``a_um`` is therefore a
VISUAL parameter; the motif (honeycomb topology, sublattices, vertical
structure) is what stays true to the material.  Literature in-plane
lattice constants are recorded in ``REAL_A_NM`` for captions/scale bars
(public physics facts, not process data).

Optional deps: numpy, scipy, shapely — imported lazily; a missing one
raises an instructive error naming the exact pip command.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

REAL_A_NM = {"graphene": 0.246, "mos2": 0.316, "ws2": 0.315,
             "wse2": 0.328, "hbn": 0.250}


def _deps():
    try:
        import numpy as np
        from scipy.spatial import cKDTree
        from shapely.geometry import Point, Polygon
    except ImportError as exc:
        raise ImportError(
            f"lattice motifs need numpy, scipy and shapely in THIS "
            f"interpreter ({exc.name} is missing). Install with: "
            f"pip install numpy scipy shapely") from exc
    return np, cKDTree, Point, Polygon


def honeycomb(polygon_um, a_um: float):
    """Honeycomb inside a polygon (point sequence OR a shapely Polygon
    — pass the latter to keep holes atom-free): returns (A, B)
    sublattice position arrays (N, 2)."""
    np, _cKDTree, Point, Polygon = _deps()
    poly = (polygon_um if hasattr(polygon_um, "contains")
            else Polygon(polygon_um))
    minx, miny, maxx, maxy = poly.bounds
    a1 = np.array([a_um, 0.0])
    a2 = np.array([a_um / 2, a_um * np.sqrt(3) / 2])
    d = a_um / np.sqrt(3)
    basis_b = np.array([0.0, d])
    ni = int((maxx - minx) / a_um) + 3
    nj = int((maxy - miny) / a2[1]) + 3
    A, B = [], []
    org = np.array([minx - a_um, miny - a_um])
    for j in range(nj + 1):
        for i in range(-nj, ni + 1):
            r = org + i * a1 + j * a2
            if poly.contains(Point(r)):
                A.append(r)
            rb = r + basis_b
            if poly.contains(Point(rb)):
                B.append(rb)
    return np.array(A), np.array(B)


def bonds_between(p_from, p_to, max_dist: float) -> List[Tuple[int, int]]:
    np, cKDTree, _Point, _Polygon = _deps()
    if len(p_from) == 0 or len(p_to) == 0:
        return []
    tree = cKDTree(p_to)
    out = []
    for i, p in enumerate(p_from):
        for j in tree.query_ball_point(p, max_dist):
            out.append((i, j))
    return out


def graphene(polygon_um, a_um: float = 0.05,
             z_um: float = 0.0) -> Dict[str, Any]:
    """Single-layer graphene: C atoms, in-plane bonds."""
    np, *_ = _deps()
    A, B = honeycomb(polygon_um, a_um)
    d = a_um / np.sqrt(3)
    atoms = np.vstack([A, B])
    pos = np.column_stack([atoms, np.full(len(atoms), z_um)])
    pairs = bonds_between(A, B, d * 1.1)
    bonds = [(np.append(A[i], z_um), np.append(B[j], z_um))
             for i, j in pairs]
    return {"species": [{"name": "C", "positions": pos,
                         "radius_um": a_um * 0.16,
                         "color": (0.16, 0.17, 0.19, 1.0)}],
            "bonds": bonds, "bond_radius_um": a_um * 0.055}


def mos2(polygon_um, a_um: float = 0.08, z_um: float = 0.0,
         dz_um: float = None) -> Dict[str, Any]:
    """Monolayer 2H-MoS2: Mo plane sandwiched by two S planes."""
    np, *_ = _deps()
    A, B = honeycomb(polygon_um, a_um)
    d = a_um / np.sqrt(3)
    dz = dz_um if dz_um is not None else a_um * 0.5
    mo = np.column_stack([A, np.full(len(A), z_um)])
    s_up = np.column_stack([B, np.full(len(B), z_um + dz)])
    s_dn = np.column_stack([B, np.full(len(B), z_um - dz)])
    pairs = bonds_between(A, B, d * 1.1)
    bonds = []
    for i, j in pairs:
        bonds.append((mo[i], s_up[j]))
        bonds.append((mo[i], s_dn[j]))
    return {"species": [
                {"name": "Mo", "positions": mo,
                 "radius_um": a_um * 0.22,
                 "color": (0.29, 0.46, 0.58, 1.0)},
                {"name": "S",
                 "positions": np.vstack([s_up, s_dn]),
                 "radius_um": a_um * 0.16,
                 "color": (0.95, 0.83, 0.28, 1.0)}],
            "bonds": bonds, "bond_radius_um": a_um * 0.05}


MOTIFS = {"graphene": graphene, "mos2": mos2}
