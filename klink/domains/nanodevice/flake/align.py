"""GDS/image alignment namespace for future KlayoutClaw port."""

from __future__ import annotations

from .._deps import load_np


def apply_affine(matrix, points):
    """Apply a 2x3 affine matrix to Nx2 points using numpy lazily."""

    np = load_np()
    m = np.asarray(matrix, dtype=float)
    pts = np.asarray(points, dtype=float)
    return ((m[:2, :2] @ pts.T).T + m[:2, 2]).tolist()
