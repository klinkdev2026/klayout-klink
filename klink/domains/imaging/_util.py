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


#: Default hairline-slit weld tolerance (DBU) for the 3D exits: weld
#: NOTHING that has real width. ``merge()`` (always on) dissolves the
#: zero-width keyhole cut-lines GDS storage forces — pure artifact,
#: never intent. A slit that is >= 1 dbu wide, however, may be DRAWN
#: intent (nanogap electrodes are real devices), so klink keeps it and
#: reports a warning instead of deciding for the user; welding real
#: slits is the user's explicit choice via ``weld_slits_dbu``.
DEFAULT_WELD_DBU = 0

#: Closing probe used to DETECT (not weld) hairline slits: 1 dbu
#: catches the classic storage-artifact class (a keyhole cut-line left
#: exactly 1 dbu wide) without flagging any drawable gap.
SLIT_PROBE_DBU = 1


def weld_region(region, weld_dbu: int):
    """Normalize drawn polygons to AREA semantics, in place — what the
    2.5d view does before extruding, and what every 3D exit must do
    before treating layout rings as geometry.

    ``merge()`` restores in-memory holes from zero-width keyhole
    cut-lines (GDS storage artifacts — never drawn intent). With
    ``weld_dbu > 0`` a closing (grow, shrink back) then also welds
    real slits up to ~2*weld_dbu wide; that erases drawn geometry, so
    it only runs when the caller asked for it explicitly."""
    region.merge()
    if weld_dbu > 0:
        region.size(int(weld_dbu))
        region.size(-int(weld_dbu))
        region.merge()
    return region


def hairline_slit_bbox(region, probe_dbu: int = SLIT_PROBE_DBU):
    """Bounding box (dbu) of any hairline slits in ``region``, or
    ``None`` when there are none.

    Probes with a closing of ``probe_dbu`` and compares TOPOLOGY: a
    real slit welded by the closing either turns a slit-cut ring into
    a ring with one more hole, or fuses two pieces into one — so the
    hole count rises or the polygon count falls. An area diff alone
    would false-alarm on the 1-dbu quantization crumbs a closing
    leaves at non-orthogonal vertices (a 64-gon circle's rim), which
    never change topology. Detection only — the region is not
    modified. Used to WARN: a kept hairline slit renders as a real gap
    in 3D, and a tapered process widens it by 2*thickness*tan(taper)
    into a canyon (measured: a 1 nm slit became a 2.52 um canyon at
    taper=40, t=1.5 um)."""
    region.merge()
    closed = region.dup()
    closed.size(int(probe_dbu))
    closed.size(-int(probe_dbu))
    closed.merge()
    n0, h0 = region.count(), sum(p.holes() for p in region.each())
    n1, h1 = closed.count(), sum(p.holes() for p in closed.each())
    if n1 >= n0 and h1 <= h0:
        return None
    gaps = closed - region
    # locate the slit itself, not the vertex crumbs: a welded slit is
    # a run of length x width >= a few dbu^2, a crumb is ~1 dbu^2
    slit = [p for p in gaps.each() if p.area() >= 4]
    if not slit:
        slit = list(gaps.each())
    bb = slit[0].bbox()
    for p in slit[1:]:
        bb += p.bbox()
    return [bb.left, bb.bottom, bb.right, bb.top]


def slit_warning(layer: str, bbox_dbu, weld_dbu: int) -> dict:
    """The one warning record every 3D exit emits for a kept hairline
    slit — same wording everywhere so agents learn it once."""
    return {
        "kind": "hairline_slit",
        "layer": layer,
        "bbox_dbu": list(bbox_dbu),
        "note": (
            f"layer {layer} has a hairline slit (<= ~2 dbu wide) that "
            f"splits its area. It renders as a real gap in 3D, and a "
            f"tapered process widens it by 2*thickness*tan(taper) "
            f"into a canyon. If it is a storage/boolean artifact, "
            f"pass weld_slits_dbu=2 to weld it shut (currently "
            f"{weld_dbu}); if it is drawn intent (e.g. a nanogap), "
            f"keep it and use vertical sidewalls (taper=0) where the "
            f"gap matters."),
    }


def layer_region(layout, top, layer: str, err_cls,
                 weld_dbu: int = DEFAULT_WELD_DBU,
                 warnings: Optional[list] = None):
    """The cell's merged (and, if asked, welded) Region for an 'L/D'
    layer spec, or ``None`` when the layout has no such layer.

    Pass a ``warnings`` list to collect a :func:`slit_warning` when a
    hairline slit survives the weld."""
    _kdb = kdb(err_cls)
    l, d = (int(v) for v in layer.split("/"))
    li = layout.find_layer(_kdb.LayerInfo(l, d))
    if li is None:
        return None
    region = _kdb.Region(top.begin_shapes_rec(li))
    weld_region(region, weld_dbu)
    if warnings is not None:
        bb = hairline_slit_bbox(region)
        if bb is not None:
            warnings.append(slit_warning(layer, bb, weld_dbu))
    return region


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
