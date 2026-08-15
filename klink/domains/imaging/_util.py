"""Small shared helpers for the imaging domain (no heavy imports)."""

from __future__ import annotations

import os
from typing import Optional


def top_cell_of(layout, cell: Optional[str], err_cls, source: str):
    """Resolve the target cell instructively.

    A multi-top-cell layout with ``cell=None`` must not surface pya's
    raw RuntimeError — the error names the candidates and the fix."""
    if cell is not None:
        top = layout.cell(cell)
        if top is None:
            raise err_cls(
                f"cell {cell!r} not in {source}; cells: "
                f"{[c.name for c in layout.top_cells()]}")
        return top
    tops = layout.top_cells()
    if len(tops) != 1:
        raise err_cls(
            f"{source} has {len(tops)} top cells "
            f"({[c.name for c in tops]}); pass cell=<name> to choose")
    return tops[0]


def require_plain_basename(basename: str, err_cls) -> str:
    """basename is a filename stem; writes must stay in output_dir."""
    if (os.path.isabs(basename) or os.path.dirname(basename)
            or basename in (".", "..") or not basename.strip()):
        raise err_cls(
            f"basename must be a plain filename stem (no path "
            f"separators, not absolute), got {basename!r}")
    return basename
