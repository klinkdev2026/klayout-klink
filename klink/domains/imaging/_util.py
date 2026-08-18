"""Small shared helpers for the imaging domain (no heavy imports)."""

from __future__ import annotations

import os
import sys
from typing import Optional


def kdb(err_cls):
    """``klayout.db``, or an error that says how to get it.

    Every other optional dependency in this domain fails instructively
    (numpy/scipy/pillow name the pip command). ``klayout`` did not, and
    it is the one that is hardest to diagnose: an empty ``klayout``
    directory left on the path — a stale install, a `py.typed`-only
    folder — is a valid NAMESPACE package, so ``import klayout``
    succeeds and only ``klayout.db`` fails. The bare error then reads
    "no module named klayout.db", which says the package is installed
    and merely incomplete, and sends the reader looking in the wrong
    place. (A blind test lost real time to exactly this; `klink doctor`
    was fooled by the same shim once.)
    """
    try:
        import klayout.db as _kdb
        return _kdb
    except ImportError as exc:
        raise err_cls(_klayout_missing_message()) from exc


def _klayout_missing_message() -> str:
    """Composed separately so it can be tested without uninstalling."""
    lines = [
        "the `klayout` Python module is not usable in THIS interpreter "
        f"({sys.executable}). Install it with: pip install klayout",
    ]
    shim = _namespace_shim_path()
    if shim:
        lines.append(
            f"NOTE: a `klayout` directory WAS found at {shim}, but it "
            f"has no `db` module — it is an empty namespace placeholder, "
            f"not the package. That is why the error mentions "
            f"`klayout.db` rather than `klayout`. Installing the real "
            f"package into this interpreter fixes it; the leftover "
            f"directory can be deleted.")
    return " ".join(lines)


def _namespace_shim_path() -> Optional[str]:
    """Where an empty `klayout` namespace directory is shadowing the
    real package, if one is."""
    try:
        import klayout
    except Exception:
        return None
    paths = list(getattr(klayout, "__path__", []) or [])
    if not paths:
        return None
    for p in paths:
        try:
            entries = os.listdir(p)
        except OSError:
            continue
        # a real install carries a `db` subpackage and a compiled
        # `dbcore` extension whose suffix is platform- and
        # version-specific (dbcore.cp313-win_amd64.pyd,
        # dbcore.cpython-311-x86_64-linux-gnu.so, ...), so match by
        # PREFIX rather than by any one filename
        if "db" in entries or any(e.startswith("dbcore") for e in entries):
            return None            # a real install is present
    return paths[0]


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
